"""化探分析接口：维护化探样品，覆盖送样检测、登记结果、发起复检、修改采样点位等动作。

所有接口都要求会话：读用 current_account（旧快照可读），写用 current_writer
（调班/撤权后的旧会话直接 409）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.routers.security import current_account, current_writer
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.geochem import GeochemService
from app.services.security import SecurityError

router = APIRouter(prefix="/api/geochem", tags=["化探分析"])

service = GeochemService()

LIST_FIELDS = ["样品编号", "样品类型", "采样点位", "分析元素", "检测方法", "检出限", "分析日期", "样品状态"]
STATUSES = ["待送样", "分析中", "已完成", "需复检"]


class PointPayload(BaseModel):
    采样点位: str
    op_at: str | None = None


def _denied(exc: SecurityError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按样品编号/采样点位检索"),
    status: str | None = Query(default=None, description="待送样、分析中、已完成、需复检"),
    page: int = 1,
    size: int = 20,
    account: dict[str, Any] = Depends(current_account),
) -> PageResult[dict]:
    """按样品编号与状态过滤化探分析列表；无权限的样品不会出现在结果里。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(account, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(
    entry_id: int,
    account: dict[str, Any] = Depends(current_account),
) -> dict:
    """读取单条化探样品明细；无权查看时按不存在处理，不回显样品类型等字段。"""
    entry = service.get_entry(account, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "sample_not_found", "message": f"化探样品 {entry_id} 不存在或已归档"},
        )
    return entry


@router.post("", response_model=ActionResult)
def create_entry(
    payload: EntryPayload,
    account: dict[str, Any] = Depends(current_writer),
) -> ActionResult:
    """登记一条化探样品；对样品包无写权限时拒绝，缺字段时说明原因。"""
    try:
        entry, missing = service.create_entry(account, payload.values)
    except SecurityError as exc:
        raise _denied(exc)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="化探样品已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    account: dict[str, Any] = Depends(current_writer),
) -> ActionResult:
    """对单条化探样品执行送样检测、登记结果、发起复检；只读/越权账号会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    op_at = payload.values.get("op_at")
    try:
        entry, message = service.run_action(account, entry_id, action, op_at=op_at)
    except SecurityError as exc:
        raise _denied(exc)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}/point", response_model=ActionResult)
def update_point(
    entry_id: int,
    payload: PointPayload,
    account: dict[str, Any] = Depends(current_writer),
) -> ActionResult:
    """修改采样点位：越权（含跨单位只读）必须拒绝，且错误信息不泄露样品类型。"""
    new_point = payload.采样点位.strip()
    if not new_point:
        return ActionResult(ok=False, message="采样点位不能为空")
    try:
        entry, message = service.update_point(
            account, entry_id, new_point, op_at=payload.op_at,
        )
    except SecurityError as exc:
        raise _denied(exc)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """导出化探分析清单：只导出当前账号有权查看的样品（跨单位字段打码）。"""
    items, total = service.list_entries(account, page=1, size=10000)
    return {"module": "geochem", "total": total, "items": items}
