"""化探分析业务规则：状态流转、字段校验与筛选口径。

样品行的可见性、字段脱敏、采样点位写保护统一走三级权限域（app.access），
台账列表只投影当前账号被授权样品包内的样品。
"""
from __future__ import annotations

from typing import Any

from app.access import AccessError, access
from app.access.service import AREA_FIELD, GROUP_FIELD, PACKAGE_FIELD
from app.store import store

MODULE = "geochem"
REQUIRED_FIELDS = ["样品编号", "样品类型", "采样点位", PACKAGE_FIELD]
STATUS_ORDER = ["待送样", "分析中", "已完成", "需复检"]
ACTION_RULES = {"送样检测": "分析中", "登记结果": "已完成", "发起复检": "需复检"}
NEGATIVE_ACTIONS = []


class GeochemService:
    def list_entries(
        self,
        account,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = access.visible_rows(account, include_access=True)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("样品编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, account, entry_id: int) -> dict[str, Any]:
        row, decision = access.authorize_sample(account, entry_id, write=False)
        return access._redact(dict(row), decision)

    def create_entry(self, account, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            label = {PACKAGE_FIELD: "样品包编号"}.get
            return None, [label(field) or field for field in missing]
        package = access.package_for_create(account, str(values[PACKAGE_FIELD]).strip())
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        writable_fields = ["样品编号", "样品类型", "采样点位", "分析元素", "检测方法",
                           "检出限", "分析日期"]
        entry.update({field: values.get(field) for field in writable_fields})
        entry[PACKAGE_FIELD] = package.code
        entry[GROUP_FIELD] = package.group_code
        entry[AREA_FIELD] = package.area_code
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, account, entry_id: int,
                     values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改样品：写权校验在权限域完成；采样点位越权修改会被单独拒绝。"""
        row = store.find(MODULE, entry_id)
        if row is None:
            raise AccessError(404, "sample_not_found",
                              f"化探样品 {entry_id} 不存在或无权访问")
        # 先判点位（只读账号与跨单位账号一律给出“越权改点位”口径），再做通用写权校验
        access.assert_point_mutation_allowed(account, row, values)
        access.authorize_sample(account, entry_id, write=True)
        editable = ["样品类型", "采样点位", "分析元素", "检测方法", "检出限", "分析日期"]
        for field in editable:
            if field in values and str(values[field] or "").strip():
                row[field] = values[field]
        return row, f"化探样品 {entry_id} 已更新"

    def run_action(self, account, entry_id: int,
                   action: str) -> tuple[dict[str, Any] | None, str]:
        row, _decision = access.authorize_sample(account, entry_id, write=True)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于化探分析可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        row["status"] = target
        row["pending"] = target != STATUS_ORDER[-1]
        row["abnormal"] = action in NEGATIVE_ACTIONS
        return row, f"化探样品已{action}"
