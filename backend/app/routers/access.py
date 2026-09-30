"""三级权限域接口：会话、授权矩阵、调班/撤权定序、审计与缓存观测。"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.access import AccessError, access

router = APIRouter(prefix="/api/access", tags=["化探授权"])


class LoginPayload(BaseModel):
    username: str = Field(min_length=1)


class GrantPayload(BaseModel):
    account: str = Field(min_length=1, description="被授权账号（须为区间本单位账号）")
    kind: str = Field(description="area 采样区 / package 样品包；项目组不接受授权")
    scope: str = Field(description="区间编码")
    level: str = Field(description="readonly 或 readwrite")


class RevokePayload(BaseModel):
    op_time: str = Field(description="操作时间，ISO8601；迟到操作按冲突拒绝并关闭旧会话")


class HandoverPayload(BaseModel):
    new_account: str = Field(min_length=1, description="接班人账号，须同单位")
    op_time: str = Field(description="操作时间，ISO8601；并发调班按它定序")


def _current(x_session_token: str | None):
    try:
        return access.authenticate(x_session_token)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/sessions")
def login(payload: LoginPayload) -> dict:
    """登录获取会话令牌；未认证访问化探接口会被 401 拦下。"""
    try:
        return access.login(payload.username)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/me")
def me(x_session_token: str | None = Header(default=None)) -> dict:
    account = _current(x_session_token)
    return {"username": account.username, "name": account.name,
            "unit_code": account.unit_code, "is_admin": account.is_admin}


@router.get("/catalog")
def catalog(x_session_token: str | None = Header(default=None)) -> dict:
    """三级归属树与账号清单（用于矩阵页与归属展示）。"""
    account = _current(x_session_token)
    return access.scope_catalog(account)


@router.get("/matrix")
def matrix(x_session_token: str | None = Header(default=None)) -> dict:
    """授权矩阵：跨单位账号看到的行 can_manage=false，只读不可调整。"""
    account = _current(x_session_token)
    return access.matrix(account)


@router.put("/grants")
def update_grant(payload: GrantPayload,
                 x_session_token: str | None = Header(default=None)) -> dict:
    """调整区间授权；非区间本单位账号一律 403 拒绝。"""
    try:
        return access.set_grant(x_session_token or "", payload.account,
                                payload.kind, payload.scope, payload.level)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/grants/{grant_id}/revoke")
def revoke(grant_id: int, payload: RevokePayload,
           x_session_token: str | None = Header(default=None)) -> dict:
    """撤权：按 op_time 与调班共用一条时间线定序。"""
    try:
        return access.revoke_grant(x_session_token or "", grant_id, payload.op_time)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/handover")
def handover(payload: HandoverPayload,
             x_session_token: str | None = Header(default=None)) -> dict:
    """并发调班：成功返回接班人新会话；冲突失败时旧会话立即失效。"""
    try:
        return access.handover(x_session_token or "", payload.new_account, payload.op_time)
    except AccessError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/share-refs")
def share_refs(resolved: bool | None = None,
               x_session_token: str | None = Header(default=None)) -> dict:
    """历史共享引用及其回填状态；经手人 shared_by 始终保留原值。"""
    account = _current(x_session_token)
    return access.list_share_refs(account, resolved=resolved)


@router.get("/audit")
def audit(x_session_token: str | None = Header(default=None)) -> dict:
    """操作时序日志：含被拒绝的调班/撤权记录，actor 保留原账号。"""
    account = _current(x_session_token)
    return access.audit_log(account)


@router.get("/cache-status")
def cache_status(x_session_token: str | None = Header(default=None)) -> dict:
    """授权结论缓存版本与失效计数，便于验证“调整后清理失效缓存”。"""
    account = _current(x_session_token)
    return access.cache_status(account)
