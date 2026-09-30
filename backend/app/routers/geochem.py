"""化探分析接口：维护化探样品，覆盖送样检测、登记结果、发起复检等动作。

所有接口携带 ``X-Session-Token``；可见范围、字段脱敏、采样点位写保护
由三级权限域（项目组—采样区—样品包）统一裁决。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.access import AccessError, access
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.geochem import GeochemService

router = APIRouter(prefix="/api/geochem", tags=["化探分析"])

service = GeochemService()

LIST_FIELDS = ["样品编号", "样品类型", "采样点位", "分析元素", "检测方法", "检出限", "分析日期", "样品状态"]
STATUSES = ["待送样", "分析中", "已完成", "需复检"]


def _current_account(x_session_token: str | None):
    try:
        return access.authenticate(x_session_token)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按样品编号检索"),
    status: str | None = Query(default=None, description="待送样、分析中、已完成、需复检"),
    page: int = 1,
    size: int = 20,
    x_session_token: str | None = Header(default=None),
) -> PageResult[dict]:
    """按样品编号与状态过滤化探分析列表；无授权样品整条不可见。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    account = _current_account(x_session_token)
    items, total = service.list_entries(account, keyword=keyword, status=status,
                                        page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload,
                 x_session_token: str | None = Header(default=None)) -> ActionResult:
    """登记一条化探样品，缺字段时说明原因而不是静默丢弃。"""
    account = _current_account(x_session_token)
    try:
        entry, missing = service.create_entry(account, payload.values)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="化探样品已登记", entry=entry)


@router.get("/export")
def export_entries(x_session_token: str | None = Header(default=None)) -> dict[str, Any]:
    """导出当前账号可见的化探分析清单；跨单位行已脱敏。"""
    account = _current_account(x_session_token)
    items, total = service.list_entries(account, page=1, size=10000)
    return {"module": "geochem", "total": total, "items": items}


@router.get("/access-view")
def access_view(view: str | None = Query(default=None,
                                         description="ledger/point_map/bag_todos，留空返回全量"),
                x_session_token: str | None = Header(default=None)) -> dict[str, Any]:
    """台账、点位图、样品袋追溯待办的同源授权结论投影。

    三处共用权限域同一份快照与 decision_version，保证结果同源。
    """
    _current_account(x_session_token)
    snapshot = access.snapshot(x_session_token)
    if view in (None, "", "all"):
        return snapshot
    if view not in ("ledger", "point_map", "bag_todos"):
        raise HTTPException(status_code=400,
                            detail="view 只能取 ledger、point_map、bag_todos")
    return {
        "decision_version": snapshot["decision_version"],
        "generated_at": snapshot["generated_at"],
        "account": snapshot["account"],
        view: snapshot[view],
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int,
              x_session_token: str | None = Header(default=None)) -> dict:
    """读取单条化探样品明细；不存在或无授权时统一 404，不泄露样品类型等字段。"""
    account = _current_account(x_session_token)
    try:
        return service.get_entry(account, entry_id)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.patch("/{entry_id}", response_model=ActionResult)
def patch_entry(entry_id: int, payload: EntryPayload,
                x_session_token: str | None = Header(default=None)) -> ActionResult:
    """修改样品字段；越权改采样点位在权限域内拒绝。"""
    account = _current_account(x_session_token)
    try:
        entry, message = service.update_entry(account, entry_id, payload.values)
    except AccessError as exc:
        return ActionResult(ok=False, message=exc.message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload,
               x_session_token: str | None = Header(default=None)) -> ActionResult:
    """对单条化探样品执行送样检测、登记结果、发起复检；不允许的动作会被拦下并说明原因。"""
    account = _current_account(x_session_token)
    try:
        entry, message = service.run_action(account, entry_id,
                                            str(payload.values.get("action") or "").strip())
    except AccessError as exc:
        return ActionResult(ok=False, message=exc.message)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
