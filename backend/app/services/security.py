"""化探样品权限域：项目组—采样区—样品包三级归属与授权矩阵。

口径汇总（与需求逐条对应）：

* 三级归属：group(项目组) -> area(采样区) -> package(样品包)，样品挂在包上。
* 授权矩阵只允许“节点本单位”账号调整区间；跨单位账号一律只读（即使矩阵里写成
  read_write，解析时也强制压到 read）。
* 项目组默认继承父级采样区权限、采样区默认继承项目组权限：沿归属链取最近的显式
  例外；单个样品包可以挂只读/无权限例外，覆盖继承来的读写。
* 越权修改采样点位一律拒绝（HTTP 403），错误信息不回显样品类型等敏感字段。
* 授权结论是唯一事实源，台账(化探清单)、点位图、样品袋追溯待办三处都从
  effective_access 投影，保证同源，并提供一致性校验。
* 矩阵调整后：把历史共享引用按新结论回填 current_*，失效相关缓存；历史记录的
  created_by（原经手账号）永不改写。
* 调班/撤权按操作时间 op_at 定序，迟到操作 409；成功后抬升目标账号的
  auth_epoch，旧会话写操作一律 409 拦下，必须重新同步后才能继续提交。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from app.store import store

# ---------------------------------------------------------------------------
# 常量与异常
# ---------------------------------------------------------------------------

NONE = "none"
READ = "read"
READ_WRITE = "read_write"
LEVEL_ORDER = {NONE: 0, READ: 1, READ_WRITE: 2}

GROUP = "group"
AREA = "area"
PACKAGE = "package"
NODE_KINDS = (GROUP, AREA, PACKAGE)

# 跨单位只读账号可见样品时必须打码的字段（“不泄露样品类型等字段”）
SENSITIVE_FIELDS = ["样品类型", "分析元素", "检测方法", "检出限"]
MASK = "******"

TABLES = {
    GROUP: "geochem_group",
    AREA: "geochem_area",
    PACKAGE: "geochem_package",
}


class SecurityError(Exception):
    """权限/会话/定序类失败；code 供前端精确分支处理。"""

    def __init__(self, code: str, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _node_index(kind: str) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in store.rows(TABLES[kind])}


def _owner_unit_of(kind: str, node_id: str) -> str:
    """沿归属链回到项目组，取归属单位。"""
    current_kind, current_id = kind, node_id
    while current_kind != GROUP:
        rows = store.rows(TABLES[current_kind])
        row = next((r for r in rows if str(r["id"]) == current_id), None)
        if row is None:
            raise SecurityError("node_not_found", "授权节点不存在或已归档", 404)
        current_id = str(row["parent_id"])
        current_kind = GROUP if current_kind == AREA else AREA
    group = _node_index(GROUP).get(current_id)
    if group is None:
        raise SecurityError("node_not_found", "授权节点不存在或已归档", 404)
    return str(group["owner_unit_id"])


def _ancestor_chain(kind: str, node_id: str) -> list[tuple[str, str]]:
    """从当前节点到项目组的归属链（含自身）。"""
    chain: list[tuple[str, str]] = [(kind, node_id)]
    current_kind, current_id = kind, node_id
    while current_kind != GROUP:
        rows = store.rows(TABLES[current_kind])
        row = next((r for r in rows if str(r["id"]) == current_id), None)
        if row is None:
            raise SecurityError("node_not_found", "授权节点不存在或已归档", 404)
        current_id = str(row["parent_id"])
        current_kind = GROUP if current_kind == AREA else AREA
        chain.append((current_kind, current_id))
    return chain


def _descendants(kind: str, node_id: str) -> set[tuple[str, str]]:
    """节点自身 + 全部下级（用于缓存定向失效）。"""
    touched = {(kind, node_id)}
    if kind == PACKAGE:
        return touched
    child_kind = AREA if kind == GROUP else PACKAGE
    table = TABLES[child_kind]
    for row in store.rows(table):
        if str(row.get("parent_id")) == node_id:
            touched |= _descendants(child_kind, str(row["id"]))
    return touched


# ---------------------------------------------------------------------------
# 安全服务
# ---------------------------------------------------------------------------


class SecurityService:
    def __init__(self) -> None:
        # 会话 -> 建立时锁定的授权快照版本
        self._sessions: dict[str, dict[str, Any]] = {}
        # 每个账号当前的授权版本：调班/撤权成功即抬升
        self._auth_epoch: dict[str, int] = {}
        # effective_access 的缓存：(账号, kind, id) -> 结论
        self._cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        # 操作时间定序：(账号, 操作类型) -> 最近一次成功操作的 (op_at, seq)
        self._op_clock: dict[tuple[str, str], tuple[str, int]] = {}
        self._seq = 0

    # ---------------- 账号 / 会话 ----------------

    def _account(self, account_id: str) -> dict[str, Any]:
        row = next(
            (r for r in store.rows("security_account") if str(r["id"]) == account_id),
            None,
        )
        if row is None:
            raise SecurityError("account_not_found", "账号不存在", 404)
        return row

    def _unit_name(self, unit_id: str) -> str:
        row = next(
            (r for r in store.rows("security_unit") if str(r["id"]) == unit_id),
            None,
        )
        return str(row["name"]) if row else unit_id

    def login(self, account_id: str) -> dict[str, Any]:
        account = self._account(account_id)
        if not account.get("active"):
            raise SecurityError("account_inactive", "账号已停用，无法建立会话", 403)
        sid = secrets.token_hex(16)
        epoch = self._auth_epoch.get(account_id, 0)
        self._sessions[sid] = {"account_id": account_id, "auth_epoch": epoch, "created_at": _now()}
        return {
            "session_id": sid,
            "account_id": account_id,
            "account_name": account["name"],
            "unit_id": account["unit_id"],
            "unit_name": self._unit_name(str(account["unit_id"])),
            "auth_epoch": epoch,
        }

    def session_account(self, sid: str | None, *, write: bool = False) -> dict[str, Any]:
        """取会话对应的账号；写操作额外校验授权快照是否已过期。"""
        if not sid:
            raise SecurityError("unauthenticated", "未提供会话凭证，请先登录", 401)
        session = self._sessions.get(sid)
        if session is None:
            raise SecurityError("session_invalid", "会话不存在或已失效，请重新登录", 401)
        account = self._account(str(session["account_id"]))
        if not account.get("active"):
            raise SecurityError("account_inactive", "账号已停用，会话终止", 403)
        if write:
            current_epoch = self._auth_epoch.get(str(account["id"]), 0)
            if current_epoch != int(session["auth_epoch"]):
                raise SecurityError(
                    "session_stale",
                    "授权状态已发生调班或撤权，本会话为旧版本，请重新同步后再提交",
                    409,
                )
        return account

    def resync(self, sid: str) -> dict[str, Any]:
        session = self._sessions.get(sid)
        if session is None:
            raise SecurityError("session_invalid", "会话不存在或已失效，请重新登录", 401)
        account = self._account(str(session["account_id"]))
        if not account.get("active"):
            raise SecurityError("account_inactive", "账号已停用，会话终止", 403)
        epoch = self._auth_epoch.get(str(account["id"]), 0)
        session["auth_epoch"] = epoch
        return {
            "session_id": sid,
            "account_id": account["id"],
            "account_name": account["name"],
            "unit_id": account["unit_id"],
            "unit_name": self._unit_name(str(account["unit_id"])),
            "auth_epoch": epoch,
        }

    # ---------------- 操作时间定序 ----------------

    def _check_order(self, account_id: str, op: str, op_at: str | None) -> str:
        """只校验时间是否迟到，不抬时钟（全部校验通过后再 commit）。"""
        ts = op_at or _now()
        last = self._op_clock.get((account_id, op))
        if last is not None and ts < last[0]:
            raise SecurityError(
                "order_stale",
                f"操作时间 {ts} 早于该账号最近一次{op}的成功时间 {last[0]}，迟到操作拒绝",
                409,
            )
        return ts

    def _commit_order(self, account_id: str, op: str, ts: str) -> None:
        self._seq += 1
        self._op_clock[(account_id, op)] = (ts, self._seq)

    # ---------------- 授权解析（唯一事实源） ----------------

    def effective_access(self, account_id: str, kind: str, node_id: str) -> dict[str, Any]:
        """计算账号在节点上的有效授权结论。台账/点位图/样品袋都从这里取数。"""
        cache_key = (account_id, kind, node_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        account = self._account(account_id)
        if not account.get("active"):
            result = self._build_access(NONE, "账号已停用", [], cross_unit=True)
            self._cache[cache_key] = result
            return result

        chain = _ancestor_chain(kind, node_id)
        owner_unit = _owner_unit_of(kind, node_id)
        same_unit = str(account["unit_id"]) == owner_unit

        grants = store.rows("security_grant")

        def explicit_for(k: str, n: str) -> dict[str, Any] | None:
            return next(
                (
                    g
                    for g in grants
                    if g["account_id"] == account_id
                    and g["node_kind"] == k
                    and str(g["node_id"]) == n
                ),
                None,
            )

        # 沿链找最近的显式授权
        explicit: tuple[str, str, dict[str, Any]] | None = None
        for k, n in chain:
            grant = explicit_for(k, n)
            if grant is not None:
                explicit = (k, n, grant)
                break

        if same_unit:
            # 本单位账号默认读写；显式区间只能收窄（只读/无权限例外）
            if explicit is None:
                level, source = READ_WRITE, f"默认：本单位({self._unit_name(owner_unit)})读写"
                basis: list[dict[str, Any]] = []
            else:
                k, n, grant = explicit
                level = str(grant["level"])
                basis = [self._grant_basis(k, n, grant)]
                source = f"显式例外：{self._node_label(k, n)} -> {self._level_label(level)}"
            cross_unit = False
        else:
            # 跨单位账号默认无权限；显式授权封顶只读
            if explicit is None:
                level, source, basis = NONE, "默认：跨单位账号无授权", []
            else:
                k, n, grant = explicit
                raw = str(grant["level"])
                level = READ_WRITE if raw == READ_WRITE else raw
                # 跨单位一律只读：即使矩阵写 read_write 也强制降级
                level = READ if level == READ_WRITE else level
                basis = [self._grant_basis(k, n, grant)]
                tag = "继承" if (k, n) != (kind, node_id) else "显式"
                source = f"{tag}授权：{self._node_label(k, n)}，跨单位封顶只读"
            cross_unit = True

        result = self._build_access(level, source, basis, cross_unit=cross_unit,
                                    owner_unit_id=owner_unit)
        self._cache[cache_key] = result
        return result

    def package_of_sample(self, sample: dict[str, Any]) -> str | None:
        pid = sample.get("package_id")
        return str(pid) if pid is not None else None

    def effective_for_package(self, account_id: str, package_id: str) -> dict[str, Any]:
        return self.effective_access(account_id, PACKAGE, package_id)

    @staticmethod
    def _build_access(
        level: str,
        source: str,
        basis: list[dict[str, Any]],
        *,
        cross_unit: bool,
        owner_unit_id: str | None = None,
    ) -> dict[str, Any]:
        rank = LEVEL_ORDER[level]
        return {
            "level": level,
            "can_read": rank >= LEVEL_ORDER[READ],
            "can_write": rank >= LEVEL_ORDER[READ_WRITE],
            "cross_unit": cross_unit,
            "owner_unit_id": owner_unit_id,
            "source": source,
            "basis": basis,
        }

    @staticmethod
    def _level_label(level: str) -> str:
        return {NONE: "无权限", READ: "只读", READ_WRITE: "读写"}[level]

    @staticmethod
    def _node_label(kind: str, node_id: str) -> str:
        prefix = {GROUP: "项目组", AREA: "采样区", PACKAGE: "样品包"}[kind]
        return f"{prefix} {node_id}"

    @staticmethod
    def _grant_basis(kind: str, node_id: str, grant: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_kind": kind,
            "node_id": node_id,
            "grant_id": grant["id"],
            "grant_version": grant["version"],
            "level": grant["level"],
        }

    def _invalidate(self, kind: str, node_id: str, account_ids: set[str]) -> None:
        """让受影响节点子树上的授权结论缓存失效。"""
        touched_nodes = _descendants(kind, node_id)
        for key in list(self._cache):
            account_id, k, n = key
            if account_id in account_ids and (k, n) in touched_nodes:
                self._cache.pop(key, None)

    # ---------------- 授权矩阵 ----------------

    def list_matrix(self, viewer: dict[str, Any]) -> dict[str, Any]:
        """授权矩阵视图：跨单位账号只读，且只看得到自己相关的区间。"""
        grants = store.rows("security_grant")
        rows: list[dict[str, Any]] = []
        for grant in grants:
            owner_unit = _owner_unit_of(str(grant["node_kind"]), str(grant["node_id"]))
            same_unit = str(viewer["unit_id"]) == owner_unit
            is_self = grant["account_id"] == viewer["id"]
            # 非本单位且与本人无关的区间不展示，避免矩阵内容外泄
            if not same_unit and not is_self:
                continue
            target = self._account(str(grant["account_id"]))
            effective = self.effective_access(
                str(target["id"]), str(grant["node_kind"]), str(grant["node_id"])
            )
            rows.append(
                {
                    "grant_id": grant["id"],
                    "account_id": target["id"],
                    "account_name": target["name"],
                    "account_unit_id": target["unit_id"],
                    "account_unit_name": self._unit_name(str(target["unit_id"])),
                    "node_kind": grant["node_kind"],
                    "node_id": grant["node_id"],
                    "node_label": self._node_label(str(grant["node_kind"]), str(grant["node_id"])),
                    "owner_unit_id": owner_unit,
                    "configured_level": grant["level"],
                    "effective_level": effective["level"],
                    "effective_source": effective["source"],
                    "version": grant["version"],
                    "granted_by": grant["grant_by"],
                    "granted_at": grant["granted_at"],
                    "can_manage": same_unit,
                }
            )
        return {
            "items": rows,
            "viewer": {"account_id": viewer["id"], "unit_id": viewer["unit_id"],
                       "unit_name": self._unit_name(str(viewer["unit_id"]))},
        }

    def adjust_grant(
        self,
        *,
        operator: dict[str, Any],
        account_id: str,
        node_kind: str,
        node_id: str,
        level: str,
        op_at: str | None,
        expected_version: int | None,
        delete: bool = False,
    ) -> dict[str, Any]:
        """在授权矩阵里调整区间（新增/抬版本/撤销）。仅节点本单位账号可操作。"""
        if node_kind not in NODE_KINDS:
            raise SecurityError("bad_node_kind", f"节点层级「{node_kind}」不合法", 422)
        if level not in LEVEL_ORDER:
            raise SecurityError("bad_level", f"授权档位「{level}」不合法", 422)

        target = self._account(account_id)
        owner_unit = _owner_unit_of(node_kind, node_id)
        if str(operator["unit_id"]) != owner_unit:
            # 跨单位账号调整矩阵：拒绝，且错误文案不涉及任何样品字段
            self._audit(operator, "grant_adjust_denied", node_kind, node_id,
                        detail={"target_account": account_id})
            raise SecurityError(
                "cross_unit_readonly",
                "跨单位账号在授权矩阵中仅可查看，调整区间必须由归属单位账号执行",
                403,
            )

        # 跨单位账号一律只读：本单位也不能把外部账号调成读写
        if not delete and str(target["unit_id"]) != owner_unit and level == READ_WRITE:
            raise SecurityError(
                "cross_unit_readonly",
                f"账号 {target['name']} 属于外单位，跨单位授权封顶只读，不允许授予读写",
                422,
            )

        grants = store.rows("security_grant")
        grant = next(
            (
                g
                for g in grants
                if g["account_id"] == account_id
                and g["node_kind"] == node_kind
                and str(g["node_id"]) == node_id
            ),
            None,
        )

        if delete:
            if grant is None:
                raise SecurityError("grant_not_found", "该区间不存在，无可撤销的授权", 404)
            if expected_version is not None and int(grant["version"]) != expected_version:
                raise SecurityError("version_conflict", "授权区间已被他人更新，请刷新后重试", 409)
            ts = self._check_order(str(operator["id"]), "grant_change", op_at)
            self._commit_order(str(operator["id"]), "grant_change", ts)
            grants.remove(grant)
            self._audit(operator, "grant_revoked", node_kind, node_id,
                        detail={"account_id": account_id, "grant_id": grant["id"]},
                        op_at=ts)
        else:
            if grant is not None and expected_version is not None \
                    and int(grant["version"]) != expected_version:
                raise SecurityError("version_conflict", "授权区间已被他人更新，请刷新后重试", 409)
            ts = self._check_order(str(operator["id"]), "grant_change", op_at)
            self._commit_order(str(operator["id"]), "grant_change", ts)
            if grant is None:
                grant = {
                    "id": max((int(g["id"]) for g in grants), default=0) + 1,
                    "account_id": account_id,
                    "node_kind": node_kind,
                    "node_id": node_id,
                    "level": level,
                    "version": 1,
                    "grant_by": operator["id"],
                    "granted_at": ts,
                }
                grants.append(grant)
                action = "grant_created"
            else:
                grant["level"] = level
                grant["version"] = int(grant["version"]) + 1
                grant["grant_by"] = operator["id"]
                grant["granted_at"] = ts
                action = "grant_updated"
            self._audit(operator, action, node_kind, node_id,
                        detail={"account_id": account_id, "grant_id": grant["id"],
                                "version": grant["version"], "level": level},
                        op_at=ts)

        # 调整完成：定向清理缓存 + 回填历史共享引用
        self._invalidate(node_kind, node_id, {str(account_id)})
        backfilled = self._backfill_references(account_id, node_kind, node_id, operator, ts)

        effective = self.effective_access(account_id, node_kind, node_id)
        return {
            "ok": True,
            "message": "授权区间已撤销" if delete else "授权矩阵已更新",
            "grant_id": None if delete else grant["id"],
            "version": None if delete else grant["version"],
            "effective_level": effective["level"],
            "references_backfilled": backfilled,
            "cache_invalidated": True,
        }

    # ---------------- 历史共享引用回填 ----------------

    def _backfill_references(
        self,
        account_id: str,
        kind: str,
        node_id: str,
        operator: dict[str, Any],
        ts: str,
    ) -> int:
        """把命中本次区间变更的历史共享引用按新结论回填；原经手账号不动。"""
        affected_nodes = _descendants(kind, node_id)
        grants = store.rows("security_grant")
        current = next(
            (
                g
                for g in grants
                if g["account_id"] == account_id
                and g["node_kind"] == kind
                and str(g["node_id"]) == node_id
            ),
            None,
        )
        version = int(current["version"]) if current else None

        count = 0
        for ref in store.rows("geochem_share_reference"):
            if ref["account_id"] != account_id:
                continue
            ref_node = (str(ref["node_kind"]), str(ref["node_id"]))
            # 直接命中本次节点，或引用挂在其下级链路上
            hit = ref_node == (kind, node_id) or ref_node in affected_nodes
            if not hit:
                continue
            access = self.effective_access(
                account_id, str(ref["node_kind"]), str(ref["node_id"])
            )
            # 只回填 current_* 审计列；created_by/created_at/snapshot_level 原样保留
            ref["current_level"] = access["level"]
            ref["backfill_grant_version"] = version
            ref["backfill_by"] = operator["id"]
            ref["backfill_at"] = ts
            ref["stale"] = LEVEL_ORDER[access["level"]] < LEVEL_ORDER[str(ref["snapshot_level"])]
            count += 1
        return count

    def list_references(self, viewer: dict[str, Any]) -> list[dict[str, Any]]:
        """共享引用列表：本单位看全量，外单位只看自己经手/被授权的。"""
        owner_accounts = {
            str(a["id"])
            for a in store.rows("security_account")
            if str(a["unit_id"]) == viewer["unit_id"]
        }
        rows = []
        for ref in store.rows("geochem_share_reference"):
            is_self = str(ref["account_id"]) == str(viewer["id"])
            is_owner_handled = str(ref["created_by"]) in owner_accounts
            if not (is_self or is_owner_handled):
                continue
            rows.append(dict(ref))
        return rows

    # ---------------- 调班 / 撤权 ----------------

    def reassign(
        self,
        *,
        operator: dict[str, Any],
        account_id: str,
        new_unit_id: str,
        op_at: str | None,
    ) -> dict[str, Any]:
        """调班：变更账号归属单位。按操作时间定序，成功后旧会话立即失效。"""
        target = self._account(account_id)
        # 调班与撤权共享同一条时间线：迟到者（无论谁发起）先被定序规则拒绝
        ts = self._check_order(account_id, "lifecycle", op_at)
        if str(operator["id"]) == account_id:
            raise SecurityError(
                "self_reassign_forbidden",
                "账号不得给自己调班，调班必须由同单位其他账号执行",
                403,
            )
        # 只有账号当前单位的本单位账号可以发起调班
        if str(operator["unit_id"]) != str(target["unit_id"]):
            raise SecurityError(
                "cross_unit_reassign",
                "调班必须由账号当前归属单位的账号发起",
                403,
            )
        if not next((u for u in store.rows("security_unit") if str(u["id"]) == new_unit_id), None):
            raise SecurityError("unit_not_found", "目标单位不存在", 404)

        self._commit_order(account_id, "lifecycle", ts)
        old_unit = str(target["unit_id"])
        target["unit_id"] = new_unit_id
        self._auth_epoch[account_id] = self._auth_epoch.get(account_id, 0) + 1
        # 归属变了，全部结论都要重算
        self._cache = {k: v for k, v in self._cache.items() if k[0] != account_id}
        self._audit(operator, "account_reassigned", GROUP, "-",
                    detail={"account_id": account_id, "old_unit": old_unit,
                            "new_unit": new_unit_id},
                    op_at=ts)
        return {
            "ok": True,
            "message": f"账号 {target['name']} 已调入 {self._unit_name(new_unit_id)}，其旧会话需重新同步",
            "account_id": account_id,
            "new_unit_id": new_unit_id,
            "new_unit_name": self._unit_name(new_unit_id),
            "auth_epoch": self._auth_epoch[account_id],
        }

    def revoke_account(
        self,
        *,
        operator: dict[str, Any],
        account_id: str,
        op_at: str | None,
    ) -> dict[str, Any]:
        """撤权：停用账号。按操作时间定序，旧会话即刻不得继续提交。"""
        target = self._account(account_id)
        ts = self._check_order(account_id, "lifecycle", op_at)
        if str(operator["unit_id"]) != str(target["unit_id"]):
            raise SecurityError("cross_unit_revoke", "撤权必须由账号归属单位的账号执行", 403)
        if not target.get("active"):
            raise SecurityError("account_inactive", "账号已处于停用状态", 409)

        self._commit_order(account_id, "lifecycle", ts)
        target["active"] = False
        self._auth_epoch[account_id] = self._auth_epoch.get(account_id, 0) + 1
        # 终止该账号全部在线会话
        for sid, session in list(self._sessions.items()):
            if str(session["account_id"]) == account_id:
                self._sessions.pop(sid, None)
        self._cache = {k: v for k, v in self._cache.items() if k[0] != account_id}
        self._audit(operator, "account_revoked", GROUP, "-",
                    detail={"account_id": account_id}, op_at=ts)
        return {"ok": True, "message": f"账号 {target['name']} 已撤权停用，历史经手关系保留",
                "account_id": account_id}

    # ---------------- 写操作闸门：采样点位 ----------------

    def require_sample_write(self, account: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        package_id = self.package_of_sample(sample)
        if package_id is None:
            raise SecurityError("package_missing", "样品未归属样品包，无法判定授权", 422)
        access = self.effective_for_package(str(account["id"]), package_id)
        if not access["can_read"]:
            # 不可见即不存在：不回显样品类型、点位等任何字段
            raise SecurityError("sample_not_found", "化探样品不存在或已归档", 404)
        if not access["can_write"]:
            self._audit(account, "sample_write_denied", PACKAGE, package_id,
                        detail={"sample_id": sample.get("id")})
            # 越权改采样点位：拒绝，文案绝不带出样品类型等字段
            raise SecurityError(
                "point_write_denied",
                f"当前账号对{self._node_label(PACKAGE, package_id)}仅为只读，"
                "不得修改采样点位等内容",
                403,
            )
        return access

    def update_sample_point(
        self,
        *,
        account: dict[str, Any],
        sample: dict[str, Any],
        new_point: str,
        op_at: str | None,
    ) -> dict[str, Any]:
        self.require_sample_write(account, sample)
        ts = self._check_order(str(account["id"]), "sample_update", op_at)
        self._commit_order(str(account["id"]), "sample_update", ts)
        old_point = sample.get("采样点位")
        sample["采样点位"] = new_point
        self._audit(account, "sample_point_updated", PACKAGE, str(sample.get("package_id")),
                    detail={"sample_id": sample.get("id"), "old": old_point, "new": new_point},
                    op_at=ts)
        return {"ok": True, "message": "采样点位已更新", "sample_id": sample.get("id"),
                "采样点位": new_point}

    # ---------------- 三处同源投影 ----------------

    def _masked(self, sample: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
        row = dict(sample)
        if access["cross_unit"]:
            for field in SENSITIVE_FIELDS:
                if field in row:
                    row[field] = MASK
        return row

    def ledger_view(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        """化探台账投影：无权限的样品整行不可见；跨单位只读的敏感字段打码。"""
        items: list[dict[str, Any]] = []
        for sample in store.rows("geochem"):
            pid = self.package_of_sample(sample)
            if pid is None:
                continue
            access = self.effective_for_package(str(account["id"]), pid)
            if not access["can_read"]:
                continue
            row = self._masked(sample, access)
            row["_access_level"] = access["level"]
            row["_access_source"] = access["source"]
            items.append(row)
        return items

    def map_view(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        """点位图投影：结论与台账同源；跨单位看不到样品类型。"""
        items: list[dict[str, Any]] = []
        for sample in store.rows("geochem"):
            pid = self.package_of_sample(sample)
            if pid is None:
                continue
            access = self.effective_for_package(str(account["id"]), pid)
            if not access["can_read"]:
                continue
            items.append(
                {
                    "sample_id": sample["id"],
                    "sample_code": sample.get("样品编号"),
                    "point": sample.get("采样点位"),
                    "package_id": pid,
                    "sample_type": MASK if access["cross_unit"] else sample.get("样品类型"),
                    "level": access["level"],
                    "can_write": access["can_write"],
                    "fingerprint": self.fingerprint(str(account["id"]), pid),
                }
            )
        return items

    def bag_todo_view(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        """样品袋追溯待办投影：按样品包授权过滤，结论同源。"""
        items: list[dict[str, Any]] = []
        for todo in store.rows("geochem_bag_todo"):
            pid = str(todo["package_id"])
            access = self.effective_for_package(str(account["id"]), pid)
            if not access["can_read"]:
                continue
            items.append(
                {
                    **todo,
                    "level": access["level"],
                    "can_write": access["can_write"],
                    "fingerprint": self.fingerprint(str(account["id"]), pid),
                }
            )
        return items

    def fingerprint(self, account_id: str, package_id: str) -> str:
        """授权结论指纹：三处投影对同一包必须一致。"""
        access = self.effective_for_package(account_id, package_id)
        basis = access["basis"]
        if basis:
            b = basis[0]
            tail = f"{b['node_kind']}:{b['node_id']}#g{b['grant_id']}v{b['grant_version']}"
        else:
            tail = "default"
        return f"{account_id}|{package_id}|{access['level']}|{tail}"

    def consistency_check(self, account: dict[str, Any]) -> dict[str, Any]:
        """核对台账、点位图、样品袋三处对每个样品包的结论是否同源。"""
        ledger_levels: dict[str, str] = {}
        for row in self.ledger_view(account):
            pid = str(row.get("package_id"))
            ledger_levels[pid] = str(row["_access_level"])
        map_levels = {str(item["package_id"]): str(item["level"])
                      for item in self.map_view(account)}

        problems: list[dict[str, Any]] = []
        bag_rows = self.bag_todo_view(account)
        bag_levels = {str(todo["package_id"]): str(todo["level"]) for todo in bag_rows}

        packages = {str(p["id"]) for p in store.rows("geochem_package")}
        for pid in sorted(packages):
            access = self.effective_for_package(str(account["id"]), pid)
            if not access["can_read"]:
                continue
            expected = access["level"]
            if ledger_levels.get(pid) != expected:
                problems.append({"package_id": pid, "view": "ledger",
                                 "expected": expected, "actual": ledger_levels.get(pid)})
            if map_levels.get(pid) != expected:
                problems.append({"package_id": pid, "view": "point_map",
                                 "expected": expected, "actual": map_levels.get(pid)})
        for pid, level in bag_levels.items():
            expected = self.effective_for_package(str(account["id"]), pid)["level"]
            if level != expected:
                problems.append({"package_id": pid, "view": "bag_todo",
                                 "expected": expected, "actual": level})

        return {
            "ok": not problems,
            "checked_packages": len(packages),
            "ledger_items": len(ledger_levels),
            "map_points": len(map_levels),
            "bag_todos": len(bag_rows),
            "problems": problems,
        }

    # ---------------- 组织架构只读 ----------------

    def hierarchy(self) -> dict[str, Any]:
        def node(kind: str, row: dict[str, Any]) -> dict[str, Any]:
            return {"kind": kind, "id": str(row["id"]), "code": row.get("code"),
                    "name": row.get("name"), "parent_id": row.get("parent_id"),
                    "owner_unit_id": row.get("owner_unit_id")}

        groups = [node(GROUP, r) for r in store.rows(TABLES[GROUP])]
        areas = [node(AREA, r) for r in store.rows(TABLES[AREA])]
        packages = [node(PACKAGE, r) for r in store.rows(TABLES[PACKAGE])]
        return {"groups": groups, "areas": areas, "packages": packages,
                "units": [dict(r) for r in store.rows("security_unit")],
                "accounts": [dict(r) for r in store.rows("security_account")]}

    # ---------------- 审计 ----------------

    def _audit(
        self,
        operator: dict[str, Any],
        action: str,
        node_kind: str,
        node_id: str,
        *,
        detail: dict[str, Any] | None = None,
        op_at: str | None = None,
    ) -> dict[str, Any]:
        rows = store.rows("security_audit")
        entry = {
            "id": max((int(r["id"]) for r in rows), default=0) + 1,
            "at": op_at or _now(),
            "operator_id": operator["id"],
            "operator_name": operator.get("name"),
            "operator_unit_id": operator.get("unit_id"),
            "action": action,
            "node_kind": node_kind,
            "node_id": node_id,
            "detail": detail or {},
        }
        rows.append(entry)
        return entry

    def audit_logs(self) -> list[dict[str, Any]]:
        return list(store.rows("security_audit"))


security = SecurityService()
