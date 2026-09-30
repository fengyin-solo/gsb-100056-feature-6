"""权限域数据模型：单位、账号、采样区、项目组、样品包、授权项、会话与审计记录。

全部为内存结构，配合单进程串行锁使用；落库时可一一对应成关系表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AccessLevel = Literal["readonly", "readwrite"]
ScopeKind = Literal["area", "package"]


@dataclass(frozen=True)
class Unit:
    code: str
    name: str


@dataclass(frozen=True)
class Account:
    username: str
    name: str
    unit_code: str
    is_admin: bool = False


@dataclass(frozen=True)
class Area:
    """采样区：授权矩阵的顶层区间。"""

    code: str
    name: str
    owner_unit: str


@dataclass(frozen=True)
class ProjectGroup:
    """项目组：默认整体继承所属采样区权限，不单独出现在矩阵里。"""

    code: str
    name: str
    area_code: str


@dataclass(frozen=True)
class SamplePackage:
    """样品包：归属项目组，可挂只读例外收窄继承来的读写权。"""

    code: str
    name: str
    group_code: str
    area_code: str


@dataclass
class Grant:
    """授权矩阵条目：某账号在某区间（采样区或样品包）上的权限档位。"""

    id: int
    account: str
    kind: ScopeKind
    scope: str
    level: AccessLevel
    unit_code: str
    """区间所属单位：判定“本单位账号才能调整区间”的依据。"""
    source: Literal["matrix", "backfill"] = "matrix"
    ref_id: int | None = None
    """source=backfill 时记录回填自哪条历史共享引用。"""
    updated_by: str | None = None
    updated_at: str | None = None


@dataclass
class Session:
    token: str
    account: str
    opened_at: str
    status: Literal["active", "closed"] = "active"
    close_reason: str | None = None
    closed_seq: int | None = None


@dataclass
class AuditRecord:
    """操作时序日志：调班与撤权严格按 op_time 定序，历史经手关系归原账号。"""

    seq: int
    op_time: str
    action: str
    actor: str
    target: str | None
    detail: dict[str, object] = field(default_factory=dict)
    applied: bool = True
    rejected_reason: str | None = None
