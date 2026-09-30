"""化探分析业务规则：状态流转、字段校验与筛选口径都收在这里。

样品挂在“样品包”上，所有读写都先走 security 的三级授权结论：
不可见样品按 404 处理（不泄露样品类型等字段），只读样品写操作按 403 拒绝。
"""
from __future__ import annotations

from typing import Any

from app.services.security import (
    SENSITIVE_FIELDS,
    SecurityError,
    security,
)
from app.store import store

MODULE = "geochem"
REQUIRED_FIELDS = ["样品编号", "样品类型", "采样点位"]
STATUS_ORDER = ["待送样", "分析中", "已完成", "需复检"]
ACTION_RULES = {"送样检测": "分析中", "登记结果": "已完成", "发起复检": "需复检"}
NEGATIVE_ACTIONS = []


class GeochemService:
    def _visible_rows(self, account: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """返回当前账号可见的 (样品, 授权结论) 列表；无权限样品直接消失。"""
        visible: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in store.rows(MODULE):
            pid = security.package_of_sample(row)
            if pid is None:
                continue
            access = security.effective_for_package(str(account["id"]), pid)
            if access["can_read"]:
                visible.append((row, access))
        return visible

    def _masked(self, row: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        if access["cross_unit"]:
            for field in SENSITIVE_FIELDS:
                item.pop(field, None)
        item["_access_level"] = access["level"]
        item["_can_write"] = access["can_write"]
        return item

    def list_entries(
        self,
        account: dict[str, Any],
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._visible_rows(account)
        if keyword:
            rows = [
                (row, access)
                for row, access in rows
                if keyword in str(row.get("样品编号", "")) or keyword in str(row.get("采样点位", ""))
            ]
        if status:
            rows = [(row, access) for row, access in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        return [self._masked(row, access) for row, access in page_rows], total

    def get_entry(self, account: dict[str, Any], entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        pid = security.package_of_sample(row)
        if pid is None:
            return None
        access = security.effective_for_package(str(account["id"]), pid)
        if not access["can_read"]:
            # 不可见即不存在：绝不回显样品类型等字段
            return None
        return self._masked(row, access)

    def create_entry(
        self,
        account: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        package_id = str(values.get("package_id") or "").strip()
        if not package_id:
            missing.append("package_id（样品包）")
        if missing:
            return None, missing
        # 在登记前就要拿到样品包写权限
        probe = {"package_id": package_id}
        try:
            security.require_sample_write(account, probe)
        except SecurityError:
            raise
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["package_id"] = package_id
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        account: dict[str, Any],
        entry_id: int,
        action: str,
        *,
        op_at: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"化探样品 {entry_id} 不存在或已归档"
        pid = security.package_of_sample(entry)
        if pid is None:
            return None, "样品未归属样品包，无法执行操作"
        access = security.effective_for_package(str(account["id"]), pid)
        if not access["can_read"]:
            return None, f"化探样品 {entry_id} 不存在或已归档"
        # 送样/登记/复检都是写操作：只读账号（含跨单位）一律拒绝
        security.require_sample_write(account, entry)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于化探分析可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        security._commit_order(str(account["id"]), "sample_update",
                               security._check_order(str(account["id"]), "sample_update", op_at))
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"化探样品已{action}"

    def update_point(
        self,
        account: dict[str, Any],
        entry_id: int,
        new_point: str,
        *,
        op_at: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """修改采样点位：越权（只读/跨单位）必须拒绝。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"化探样品 {entry_id} 不存在或已归档"
        try:
            security.update_sample_point(
                account=account, sample=entry, new_point=new_point, op_at=op_at,
            )
        except SecurityError:
            raise
        return entry, "采样点位已更新"
