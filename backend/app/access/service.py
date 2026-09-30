"""三级权限域核心规则。

需求口径（实现一一对应）：

1. 归属链为「采样区 → 项目组 → 样品包」；项目组默认继承父级采样区权限，
   单个样品包允许只读例外（包级 grant 覆盖区级继承结果）。
2. 授权矩阵里只有区间所属单位的本单位账号能调整授权；跨单位账号一律只读。
3. 越权改采样点位直接拒绝；跨单位账号读取时样品类型等敏感字段整体脱敏。
4. 授权结论（台账 / 点位图 / 样品袋追溯待办）由同一份快照投影，保证三处同源。
5. 授权调整后回填历史共享引用、清理失效缓存；历史经手关系仍归原账号。
6. 调班与撤权按操作时间 op_time 定序：迟到的操作判冲突失败，
   失败后其旧会话立即关闭，不得再提交。
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from app.access.models import (
    Account,
    Area,
    AuditRecord,
    Grant,
    ProjectGroup,
    SamplePackage,
    Session,
    Unit,
)
from app.store import store

# 化探样品行里承载三级归属与历史共享的字段
PACKAGE_FIELD = "样品包编号"
GROUP_FIELD = "项目组编号"
AREA_FIELD = "采样区编号"
SHARE_REF_FIELD = "共享引用号"

# 跨单位只读账号不得看到的敏感字段
SENSITIVE_FIELDS = ("样品类型", "分析元素", "检测方法", "检出限", "分析日期")
REDACTED = "******"


class AccessError(Exception):
    """权限域业务错误：status 映射 HTTP 状态码，code 供前端分支处理。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class AccessService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------ 装配

    def reset(self) -> None:
        """恢复到种子状态；供测试间隔离与“清缓存”观测使用。"""
        with self._lock:
            store.reset()
            self.units: dict[str, Unit] = {}
            self.accounts: dict[str, Account] = {}
            self.areas: dict[str, Area] = {}
            self.groups: dict[str, ProjectGroup] = {}
            self.packages: dict[str, SamplePackage] = {}
            self.grants: dict[int, Grant] = {}
            self.sessions: dict[str, Session] = {}
            self.audit: list[AuditRecord] = []
            self.share_refs: list[dict[str, Any]] = []

            self._grant_seq = 0
            self._seq = 0
            self._last_timed_op: str | None = None
            self._decision_version = 0
            self._cache_revision = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._snapshot_cache: dict[str, dict[str, Any]] = {}
            self._load_seed()

    def _load_seed(self) -> None:
        from app.seed import ACCESS_SEED

        seed = ACCESS_SEED
        for raw in seed["units"]:
            unit = Unit(**raw)
            self.units[unit.code] = unit
        for raw in seed["accounts"]:
            account = Account(**raw)
            self.accounts[account.username] = account
        for raw in seed["areas"]:
            area = Area(**raw)
            self.areas[area.code] = area
        for raw in seed["groups"]:
            group = ProjectGroup(**raw)
            self.groups[group.code] = group
        for raw in seed["packages"]:
            package = SamplePackage(**raw)
            self.packages[package.code] = package
        for raw in seed["grants"]:
            self._grant_seq += 1
            grant = Grant(id=self._grant_seq, **raw)
            self.grants[grant.id] = grant
        for raw in seed.get("share_refs", []):
            self.share_refs.append(dict(raw))

    # ------------------------------------------------------------------ 身份

    def _account(self, username: str) -> Account:
        account = self.accounts.get(username)
        if account is None:
            raise AccessError(404, "account_not_found", f"账号 {username} 不存在")
        return account

    def authenticate(self, token: str | None) -> Account:
        if not token:
            raise AccessError(401, "missing_session", "缺少会话令牌，请先登录或交班")
        session = self.sessions.get(token)
        if session is None:
            raise AccessError(401, "session_not_found", "会话不存在或已关闭，请重新登录")
        if session.status != "active":
            raise AccessError(
                401,
                "session_closed",
                f"会话已关闭（{session.close_reason or '未知原因'}），请重新登录",
            )
        return self._account(session.account)

    def login(self, username: str) -> dict[str, Any]:
        with self._lock:
            account = self._account(username)
            token = secrets.token_hex(16)
            session = Session(token=token, account=username, opened_at=_now())
            self.sessions[token] = session
            return {
                "token": token,
                "account": self._account_view(account),
                "opened_at": session.opened_at,
            }

    def _close_session(self, token: str, reason: str) -> None:
        session = self.sessions.get(token)
        if session and session.status == "active":
            session.status = "closed"
            session.close_reason = reason
            session.closed_seq = self._seq

    def _close_account_sessions(self, username: str, reason: str) -> int:
        count = 0
        for session in self.sessions.values():
            if session.account == username and session.status == "active":
                session.status = "closed"
                session.close_reason = reason
                session.closed_seq = self._seq
                count += 1
        return count

    def _account_view(self, account: Account) -> dict[str, Any]:
        unit = self.units.get(account.unit_code)
        return {
            "username": account.username,
            "name": account.name,
            "unit_code": account.unit_code,
            "unit_name": unit.name if unit else account.unit_code,
            "is_admin": account.is_admin,
        }

    # ------------------------------------------------------------- 授权判定

    def _scope_owner(self, kind: str, scope: str) -> str:
        if kind == "area":
            area = self.areas.get(scope)
            if area is None:
                raise AccessError(404, "scope_not_found", f"采样区 {scope} 不存在")
            return area.owner_unit
        if kind == "package":
            package = self.packages.get(scope)
            if package is None:
                raise AccessError(404, "scope_not_found", f"样品包 {scope} 不存在")
            return self.areas[package.area_code].owner_unit
        raise AccessError(400, "bad_scope", "授权区间只能是采样区或样品包，项目组随采样区继承")

    def _find_grant(self, account: str, kind: str, scope: str) -> Grant | None:
        for grant in self.grants.values():
            if grant.account == account and grant.kind == kind and grant.scope == scope:
                return grant
        return None

    def decide_package(self, account: Account, package_code: str) -> dict[str, Any] | None:
        """返回账号在样品包上的有效授权；无授权返回 None。

        优先级：本单位管理员 > 样品包只读/读写例外 > 采样区继承 > 拒绝；
        跨单位账号恒为只读，矩阵授权对其不产生提权效果。
        """
        package = self.packages.get(package_code)
        if package is None:
            return None
        owner = self.areas[package.area_code].owner_unit

        if account.unit_code != owner:
            # 跨单位账号：数据可只读浏览（敏感字段脱敏），但绝无写权，
            # 且在授权矩阵里只能看不能调（can_manage=false）。
            return {"level": "readonly", "same_unit": False, "via": "cross_unit",
                    "grant_id": None, "package": package_code, "area": package.area_code}
        if account.is_admin:
            return {"level": "readwrite", "same_unit": True, "via": "unit_admin",
                    "grant_id": None, "package": package_code, "area": package.area_code}

        package_grant = self._find_grant(account.username, "package", package_code)
        if package_grant:
            return {"level": package_grant.level, "same_unit": True,
                    "via": "package_exception" if package_grant.level == "readonly" else "package_grant",
                    "grant_id": package_grant.id, "package": package_code,
                    "area": package.area_code}

        area_grant = self._find_grant(account.username, "area", package.area_code)
        if area_grant:
            return {"level": area_grant.level, "same_unit": True, "via": "group_inherits_area",
                    "grant_id": area_grant.id, "package": package_code,
                    "area": package.area_code}
        return None

    def can_write(self, account: Account, package_code: str) -> dict[str, Any] | None:
        decision = self.decide_package(account, package_code)
        if decision and decision["level"] == "readwrite":
            return decision
        return None

    # ------------------------------------------------------------- 矩阵维护

    def matrix(self, actor: Account) -> dict[str, Any]:
        """授权矩阵视图：跨单位账号行级标注 can_manage=false，前端只读呈现。"""
        rows: list[dict[str, Any]] = []
        for area in sorted(self.areas.values(), key=lambda item: item.code):
            owner = area.owner_unit
            rows.append({
                "kind": "area",
                "scope": area.code,
                "scope_name": area.name,
                "owner_unit": owner,
                "owner_unit_name": self.units[owner].name,
                "accounts": self._grants_for(actor, "area", area.code, owner),
                "can_manage": actor.unit_code == owner,
                "inherits_hint": "区内全部项目组与样品包默认继承",
            })
        for package in sorted(self.packages.values(), key=lambda item: item.code):
            owner = self.areas[package.area_code].owner_unit
            rows.append({
                "kind": "package",
                "scope": package.code,
                "scope_name": package.name,
                "group_code": package.group_code,
                "area_code": package.area_code,
                "owner_unit": owner,
                "owner_unit_name": self.units[owner].name,
                "accounts": self._grants_for(actor, "package", package.code, owner),
                "can_manage": actor.unit_code == owner,
                "inherits_hint": "留空继承采样区；设为只读即只读例外",
            })
        return {"rows": rows}

    def _grants_for(self, actor: Account, kind: str, scope: str, owner: str) -> list[dict[str, Any]]:
        result = []
        for grant in sorted(
            (g for g in self.grants.values() if g.kind == kind and g.scope == scope),
            key=lambda item: item.id,
        ):
            account = self.accounts.get(grant.account)
            result.append({
                "grant_id": grant.id,
                "account": grant.account,
                "account_name": account.name if account else grant.account,
                "unit_code": grant.unit_code,
                "level": grant.level,
                "source": grant.source,
                "ref_id": grant.ref_id,
                "updated_by": grant.updated_by,
                "updated_at": grant.updated_at,
                "can_manage": actor.unit_code == owner,
            })
        return result

    def set_grant(self, token: str, account_name: str, kind: str, scope: str,
                  level: str) -> dict[str, Any]:
        """在授权矩阵里调整区间档位。

        只有区间所属单位的本单位账号可操作；跨单位账号只读，写入一律拒绝。
        项目组不接受独立授权（继承采样区）。授权对象必须是本单位账号。
        """
        with self._lock:
            actor = self.authenticate(token)
            target = self._account(account_name)
            if level not in ("readonly", "readwrite"):
                raise AccessError(400, "bad_level", "权限档位只能是 readonly 或 readwrite")
            if kind == "group":
                raise AccessError(
                    400, "group_not_a_scope",
                    "项目组默认继承父级采样区权限，不能在矩阵中单独授权",
                )
            owner = self._scope_owner(kind, scope)
            if actor.unit_code != owner:
                raise AccessError(
                    403, "cross_unit_readonly",
                    "跨单位账号在授权矩阵中一律只读，不能调整该区间",
                )
            if target.unit_code != owner:
                raise AccessError(
                    400, "grant_target_cross_unit",
                    "授权对象不属于该区间所属单位，跨单位账号仅享有只读",
                )

            now = _now()
            grant = self._find_grant(account_name, kind, scope)
            created = grant is None
            if grant is None:
                self._grant_seq += 1
                grant = Grant(id=self._grant_seq, account=account_name, kind=kind,
                              scope=scope, level=level, unit_code=owner,
                              updated_by=actor.username, updated_at=now)
                self.grants[grant.id] = grant
            else:
                grant.level = level  # type: ignore[assignment]
                grant.source = "matrix"
                grant.updated_by = actor.username
                grant.updated_at = now

            backfilled = self._backfill_share_refs(grant)
            self._bump_decision()
            self._log("grant_adjust", actor=actor.username,
                      target=account_name,
                      detail={"kind": kind, "scope": scope, "level": level,
                              "grant_id": grant.id, "created": created,
                              "backfilled_refs": backfilled})
            return {"ok": True, "grant_id": grant.id, "created": created,
                    "backfilled_refs": backfilled, "decision_version": self._decision_version}

    def revoke_grant(self, token: str, grant_id: int, op_time: str) -> dict[str, Any]:
        """撤权：走 op_time 定序通道；撤掉全部授权后关闭目标账号旧会话。"""
        with self._lock:
            actor = self.authenticate(token)
            self._enforce_timeline(op_time, token, action="revoke", actor=actor.username,
                                   target=str(grant_id))
            grant = self.grants.get(grant_id)
            if grant is None:
                raise AccessError(404, "grant_not_found", f"授权项 {grant_id} 不存在或已撤销")
            if actor.unit_code != grant.unit_code:
                raise AccessError(403, "cross_unit_readonly",
                                  "跨单位账号不能撤销该区间的授权")

            target = grant.account
            del self.grants[grant_id]
            closed = 0
            if not self.accounts[target].is_admin and not self._remaining_grants(target):
                closed = self._close_account_sessions(target, "revoked")
            self._bump_decision()
            self._log("revoke", actor=actor.username, target=target,
                      detail={"grant_id": grant_id, "kind": grant.kind,
                              "scope": grant.scope, "sessions_closed": closed})
            return {"ok": True, "revoked_grant": grant_id, "sessions_closed": closed,
                    "decision_version": self._decision_version}

    def _remaining_grants(self, username: str) -> int:
        return sum(1 for grant in self.grants.values() if grant.account == username)

    # ----------------------------------------------------- 历史共享引用回填

    def _backfill_share_refs(self, grant: Grant) -> list[int]:
        """授权调整成功后，把同账号同区间的历史共享引用回填到该授权。

        仅补“解决授权”的关联，引用上的经手账号（shared_by）保持原样不改写。
        """
        stamped: list[int] = []
        for ref in self.share_refs:
            if ref.get("resolved_grant_id") is not None:
                continue
            if ref.get("account") != grant.account or ref.get("kind") != grant.kind:
                continue
            if ref.get("scope") != grant.scope:
                continue
            ref["resolved_grant_id"] = grant.id
            ref["resolved_at"] = _now()
            stamped.append(ref["id"])
        return stamped

    def list_share_refs(self, actor: Account, resolved: bool | None = None) -> dict[str, Any]:
        refs = []
        for ref in self.share_refs:
            if resolved is not None and bool(ref.get("resolved_grant_id")) != resolved:
                continue
            refs.append(dict(ref))
        return {"items": refs, "total": len(refs)}

    # ------------------------------------------------------------- 调班定序

    def handover(self, token: str, new_account: str, op_time: str) -> dict[str, Any]:
        """并发调班：按 op_time 定序。

        成功：交班人与接班会话都重开（旧会话关闭，杜绝旧会话继续提交）。
        失败（目标非法/时序冲突）：不落任何变更；时序冲突同时关闭来话旧会话。
        """
        with self._lock:
            actor = self.authenticate(token)
            self._enforce_timeline(op_time, token, action="handover", actor=actor.username,
                                   target=new_account)
            successor = self._account(new_account)
            if successor.unit_code != actor.unit_code:
                # 调班必须在同一单位内完成；失败不关闭交班人会话（交班未成行）。
                raise AccessError(
                    400, "cross_unit_handover",
                    "调班对象与交班人不在同一单位，调班未生效",
                )

            closed = self._close_account_sessions(actor.username, "handover")
            self._log("handover", actor=actor.username, target=new_account,
                      detail={"sessions_closed": closed})
            login = self.login(new_account)
            login["ok"] = True
            login["previous_sessions_closed"] = closed
            login["decision_version"] = self._decision_version
            return login

    def _enforce_timeline(self, op_time: str, token: str, *, action: str,
                          actor: str, target: str) -> None:
        """服务端按操作时间单调定序：迟到操作判冲突，并关闭其旧会话。"""
        if not op_time:
            raise AccessError(400, "missing_op_time", "调班与撤权必须携带操作时间 op_time")
        if self._last_timed_op is not None and op_time <= self._last_timed_op:
            self._log(f"{action}_rejected", actor=actor, target=target,
                      detail={"op_time": op_time, "last_op_time": self._last_timed_op},
                      applied=False, rejected_reason="stale_op_time")
            # 未成功（时序冲突）时，旧会话立即失效，不得继续提交。
            self._close_session(token, "stale_op_time")
            raise AccessError(
                409, "stale_op_time",
                "操作时间早于已生效操作，按时间定序判定为冲突，会话已关闭请重新登录",
            )
        self._last_timed_op = op_time

    # ----------------------------------------------------------------- 审计

    def _log(self, action: str, *, actor: str, target: str | None,
             detail: dict[str, Any] | None = None, applied: bool = True,
             rejected_reason: str | None = None) -> AuditRecord:
        self._seq += 1
        record = AuditRecord(
            seq=self._seq, op_time=self._last_timed_op or _now(), action=action,
            actor=actor, target=target, detail=dict(detail or {}), applied=applied,
            rejected_reason=rejected_reason,
        )
        self.audit.append(record)
        return record

    def audit_log(self, actor: Account) -> dict[str, Any]:
        return {"items": [
            {
                "seq": record.seq,
                "op_time": record.op_time,
                "action": record.action,
                "actor": record.actor,
                "target": record.target,
                "detail": record.detail,
                "applied": record.applied,
                "rejected_reason": record.rejected_reason,
            }
            for record in self.audit
        ]}

    # ----------------------------------------------------- 同源授权快照

    def snapshot(self, token: str) -> dict[str, Any]:
        """台账、点位图、样品袋追溯待办共用的唯一授权结论来源。

        命中版本缓存即复用；授权调整/撤权/调班会 bump 版本并清空缓存。
        """
        with self._lock:
            account = self.authenticate(token)
            cache_key = f"{account.username}:{self._decision_version}"
            cached = self._snapshot_cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached
            self._cache_misses += 1

            ledger: list[dict[str, Any]] = []
            point_map: list[dict[str, Any]] = []
            bags: dict[str, dict[str, Any]] = {}

            for row in store.rows("geochem"):
                package_code = str(row.get(PACKAGE_FIELD) or "")
                decision = self.decide_package(account, package_code)
                if decision is None:
                    # 本单位无授权：整条不可见（404 口径），不出现在任何投影中。
                    continue
                visible = self._redact(dict(row), decision)
                package = self.packages.get(package_code)
                group = self.groups.get(package.group_code) if package else None
                envelope = {
                    "样品编号": visible.get("样品编号"),
                    "采样点位": visible.get("采样点位"),
                    "样品类型": visible.get("样品类型"),
                    "分析元素": visible.get("分析元素"),
                    "status": visible.get("status"),
                    "pending": visible.get("pending"),
                    "package_code": package_code,
                    "package_name": package.name if package else package_code,
                    "group_code": package.group_code if package else None,
                    "group_name": group.name if group else None,
                    "area_code": package.area_code if package else None,
                    "access": decision,
                }
                ledger.append(envelope)
                # 点位图只投影坐标/点位与授权结论；敏感字段不进图元数据。
                point_map.append({
                    "样品编号": envelope["样品编号"],
                    "采样点位": envelope["采样点位"],
                    "package_code": package_code,
                    "area_code": envelope["area_code"],
                    "status": envelope["status"],
                    "access": decision,
                })
                bag = bags.setdefault(package_code, {
                    "package_code": package_code,
                    "package_name": package.name if package else package_code,
                    "group_code": package.group_code if package else None,
                    "group_name": group.name if group else None,
                    "area_code": package.area_code if package else None,
                    "access": decision,
                    "total": 0, "pending": 0, "todo": [],
                })
                bag["total"] += 1
                if envelope["pending"]:
                    bag["pending"] += 1
                    bag["todo"].append({"样品编号": envelope["样品编号"],
                                        "采样点位": envelope["采样点位"],
                                        "样品类型": envelope["样品类型"],
                                        "status": envelope["status"]})

            snapshot = {
                "decision_version": self._decision_version,
                "account": self._account_view(account),
                "generated_at": _now(),
                "ledger": ledger,
                "point_map": point_map,
                "bag_todos": sorted(bags.values(), key=lambda item: item["package_code"]),
            }
            self._snapshot_cache.clear()
            self._snapshot_cache[cache_key] = snapshot
            return snapshot

    def _redact(self, row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        if decision["same_unit"]:
            return row
        for field in SENSITIVE_FIELDS:
            if field in row:
                row[field] = REDACTED
        return row

    def _bump_decision(self) -> None:
        self._decision_version += 1
        self._clear_cache()

    def _clear_cache(self) -> None:
        if self._snapshot_cache:
            self._snapshot_cache.clear()
        self._cache_revision += 1

    def cache_status(self, actor: Account) -> dict[str, Any]:
        return {
            "decision_version": self._decision_version,
            "cache_revision": self._cache_revision,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "entries": len(self._snapshot_cache),
        }

    # ----------------------------------------------- 化探样品行级访问与写保护

    def visible_rows(self, account: Account,
                     include_access: bool = False) -> list[dict[str, Any]]:
        """化探台账列表：无授权样品整条剔除；跨单位行敏感字段脱敏。"""
        items: list[dict[str, Any]] = []
        for row in store.rows("geochem"):
            decision = self.decide_package(account, str(row.get(PACKAGE_FIELD) or ""))
            if decision is None:
                continue
            visible = self._redact(dict(row), decision)
            if include_access:
                visible["access"] = decision
            items.append(visible)
        return items

    def authorize_sample(self, account: Account, sample_id: int,
                         *, write: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        """取样品并鉴权；无授权按 404 处理（不存在/无权限同一口径，不泄露字段）。"""
        row = store.find("geochem", sample_id)
        if row is None:
            raise AccessError(404, "sample_not_found", f"化探样品 {sample_id} 不存在或无权访问")
        decision = self.decide_package(account, str(row.get(PACKAGE_FIELD) or ""))
        if decision is None:
            raise AccessError(404, "sample_not_found", f"化探样品 {sample_id} 不存在或无权访问")
        if write and decision["level"] != "readwrite":
            raise AccessError(403, "sample_readonly",
                              "当前账号对该样品包为只读，不能修改样品数据")
        return row, decision

    def assert_point_mutation_allowed(self, account: Account, row: dict[str, Any],
                                      values: dict[str, Any]) -> None:
        """越权改采样点位必须拒绝：任何非本单位读写会话都拦下。"""
        if "采样点位" not in values:
            return
        if str(values.get("采样点位") or "") == str(row.get("采样点位") or ""):
            return
        package_code = str(row.get(PACKAGE_FIELD) or "")
        decision = self.can_write(account, package_code)
        if decision is None:
            raise AccessError(
                403, "point_change_forbidden",
                "越权修改采样点位已被拒绝：仅本单位具备读写授权的账号可调整点位",
            )

    def package_for_create(self, account: Account, package_code: str) -> SamplePackage:
        if not package_code:
            raise AccessError(400, "package_required", "登记化探样品必须指定样品包编号")
        package = self.packages.get(package_code)
        if package is None or self.can_write(account, package_code) is None:
            # 无写权也按 404 口径，不暴露样品包是否存在。
            raise AccessError(404, "package_not_writable",
                              f"样品包 {package_code} 不存在或当前账号无登记权限")
        return package

    def package_chain(self, package_code: str) -> dict[str, str] | None:
        package = self.packages.get(package_code)
        if package is None:
            return None
        return {"package": package.code, "group": package.group_code,
                "area": package.area_code}

    def scope_catalog(self, actor: Account) -> dict[str, Any]:
        """给前端提供三级归属树与可登录账号（管理用，登录页同样消费）。"""
        return {
            "units": [{"code": u.code, "name": u.name} for u in self.units.values()],
            "accounts": [self._account_view(a) for a in sorted(self.accounts.values(),
                                                               key=lambda x: x.username)],
            "tree": [
                {
                    "area_code": area.code,
                    "area_name": area.name,
                    "owner_unit": area.owner_unit,
                    "groups": [
                        {
                            "group_code": group.code,
                            "group_name": group.name,
                            "packages": [
                                {"package_code": p.code, "package_name": p.name}
                                for p in self.packages.values()
                                if p.group_code == group.code
                            ],
                        }
                        for group in self.groups.values()
                        if group.area_code == area.code
                    ],
                }
                for area in sorted(self.areas.values(), key=lambda item: item.code)
            ],
        }


access = AccessService()
