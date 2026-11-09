"""CLI 与公共 Python API 共用的结果类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class ApiResult(Generic[T]):
    """表示一次公开 API 调用的成功数据或失败原因。"""

    ok: bool
    status: int
    data: T | None = None
    error: str = ""
