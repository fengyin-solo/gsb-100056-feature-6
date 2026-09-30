"""化探权限域接口：会话、授权矩阵、三处同源投影、调班/撤权与一致性校验。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.services.security import NONE, READ, READ_WRITE, SecurityError, security

router = APIRouter(prefix="/api/security", tags=["化探权限"])

LEVELS = [NONE, READ, READ_WRITE]


class LoginPayload(BaseModel):
    account_id: str


class GrantPayload(BaseModel):
    account_id: str
    node_kind: str
    node_id: str
    level: str = READ
    op_at: str | None = None
    expected_version: int | None = None
    delete: bool = False


class ReassignPayload(BaseModel):
    account_id: str
    new_unit_id: str
    op_at: str | None = None


class RevokePayload(BaseModel):
    account_id: str
    op_at: str | None = None


def current_account(
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> dict[str, Any]:
    """读操作依赖：校验会话有效，不拦截旧快照（读允许继续）。"""
    try:
        return security.session_account(x_session_id, write=False)
    except SecurityError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message})


def current_writer(
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> dict[str, Any]:
    """写操作依赖：会话旧快照直接 409，必须重新同步。"""
    try:
        return security.session_account(x_session_id, write=True)
    except SecurityError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message})


def _raise(exc: SecurityError) -> None:
    raise HTTPException(status_code=exc.status_code,
                        detail={"code": exc.code, "message": exc.message})


@router.post("/sessions")
def login(payload: LoginPayload) -> dict[str, Any]:
    """建立会话；返回 auth_epoch，后续写请求会校验它是否仍为最新。"""
    try:
        return security.login(payload.account_id)
    except SecurityError as exc:
        _raise(exc)


@router.post("/sessions/resync")
def resync(x_session_id: str | None = Header(default=None, alias="X-Session-Id")) -> dict[str, Any]:
    """调班/撤权后用旧会话提交拿到 409，调用这里刷新 auth_epoch 再重试。"""
    if not x_session_id:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated",
                                                     "message": "未提供会话凭证"})
    try:
        return security.resync(x_session_id)
    except SecurityError as exc:
        _raise(exc)


@router.get("/hierarchy")
def hierarchy() -> dict[str, Any]:
    """三级归属树：项目组—采样区—样品包，附单位与账号。"""
    return security.hierarchy()


@router.get("/matrix")
def list_matrix(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """授权矩阵：跨单位账号只读，且只返回与其相关的区间。"""
    return security.list_matrix(account)


@router.put("/matrix")
def adjust_grant(
    payload: GrantPayload,
    operator: dict[str, Any] = Depends(current_writer),
) -> dict[str, Any]:
    """调整授权区间：本单位账号专属；跨单位调用一律 403。"""
    try:
        return security.adjust_grant(
            operator=operator,
            account_id=payload.account_id,
            node_kind=payload.node_kind,
            node_id=payload.node_id,
            level=payload.level,
            op_at=payload.op_at,
            expected_version=payload.expected_version,
            delete=payload.delete,
        )
    except SecurityError as exc:
        _raise(exc)


@router.get("/references")
def list_references(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """历史共享引用：created_by 永远是原经手账号，current_* 是回填后的结论。"""
    return {"items": security.list_references(account)}


@router.post("/reassign")
def reassign(
    payload: ReassignPayload,
    operator: dict[str, Any] = Depends(current_writer),
) -> dict[str, Any]:
    """调班：按操作时间定序，成功后目标账号旧会话立即失效。"""
    try:
        return security.reassign(
            operator=operator,
            account_id=payload.account_id,
            new_unit_id=payload.new_unit_id,
            op_at=payload.op_at,
        )
    except SecurityError as exc:
        _raise(exc)


@router.post("/revoke")
def revoke(
    payload: RevokePayload,
    operator: dict[str, Any] = Depends(current_writer),
) -> dict[str, Any]:
    """撤权：按操作时间定序，停用账号并终止其全部会话。"""
    try:
        return security.revoke_account(
            operator=operator, account_id=payload.account_id, op_at=payload.op_at,
        )
    except SecurityError as exc:
        _raise(exc)


# ---- 三处同源投影 ---------------------------------------------------------


@router.get("/projections/ledger")
def ledger(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """化探台账投影（已按权限过滤、跨单位敏感字段打码）。"""
    return {"view": "ledger", "items": security.ledger_view(account)}


@router.get("/projections/map")
def point_map(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """点位图投影：授权结论与台账同源。"""
    return {"view": "point_map", "items": security.map_view(account)}


@router.get("/projections/bag-todo")
def bag_todo(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """样品袋追溯待办投影：授权结论与台账同源。"""
    return {"view": "bag_todo", "items": security.bag_todo_view(account)}


@router.get("/projections/consistency")
def consistency(account: dict[str, Any] = Depends(current_account)) -> dict[str, Any]:
    """核对三处结论对每个样品包是否完全一致。"""
    return security.consistency_check(account)


@router.get("/audit")
def audit_logs() -> dict[str, Any]:
    """权限域审计轨迹：调整、拒绝、调班、撤权、点位越权尝试都有留痕。"""
    return {"items": security.audit_logs()}
