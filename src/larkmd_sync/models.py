"""公开业务 API 的稳定数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SyncWarning:
    """同步计划或结果中的警告。"""

    code: str
    message: str
    path: str | None = None
    blocking: bool = False


@dataclass(frozen=True, slots=True)
class SyncAction:
    """计划中的一次本地或远端写入。"""

    action: str
    local_path: str
    document_url: str
    remote_revision: int = 0
    overwrites: bool = False


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """可序列化的两阶段同步计划。"""

    plan_id: str
    operation: str
    target: str
    actions: tuple[SyncAction, ...]
    risks: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    warnings: tuple[SyncWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncApplyResult:
    """执行同步计划的结果。"""

    plan_id: str
    operation: str
    changed: int
    local_paths: tuple[str, ...] = ()
    document_urls: tuple[str, ...] = ()
    assets_changed: int = 0
    asset_paths: tuple[str, ...] = ()
    warnings: tuple[SyncWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class DiffDetail:
    """远端和本地 Markdown 的差异摘要。"""

    text_changed: bool = False
    assets_changed: bool = False
    size_remote: int = 0
    size_local: int = 0
    remote_assets: int = 0
    local_assets: int = 0


@dataclass(frozen=True, slots=True)
class DiffResultData:
    """单文档比较结果。"""

    identical: bool
    changes: int = 0
    diff_lines: list[str] | None = None
    detail: DiffDetail = field(default_factory=DiffDetail)
    warnings: tuple[SyncWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeStatusItem:
    """一个带 front matter 映射的 Markdown 比较结果。"""

    local_path: str
    document_url: str
    identical: bool
    changes: int


@dataclass(frozen=True, slots=True)
class TreeStatus:
    """目录中所有映射 Markdown 的比较结果。"""

    root: str
    items: tuple[TreeStatusItem, ...]
    warnings: tuple[SyncWarning, ...] = ()
