"""化探样品三级权限域：项目组—采样区—样品包。

对外只暴露单例 ``access`` 与领域错误 ``AccessError``，具体规则见 ``service``。
"""
from __future__ import annotations

from app.access.service import AccessError, AccessService, access

__all__ = ["AccessError", "AccessService", "access"]
