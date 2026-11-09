"""飞书文档同步的公共 Python API。"""

from .api import (
    compare_file,
    pull_file,
    pull_tree,
    push_file,
    push_tree,
    tree_status,
)
from .models import (
    DiffDetail,
    DiffResultData,
    SyncAction,
    SyncApplyResult,
    SyncPlan,
    SyncWarning,
    TreeStatus,
    TreeStatusItem,
)
from .result import ApiResult
from .skillkit import (
    SkillPackage,
    discover_builtin_skills,
    install_skill,
    resolve_skill_root,
    skill_status,
    uninstall_skill,
    update_skill,
    validate_skill,
)

__version__ = "1.0.1"

__all__ = [
    "ApiResult",
    "DiffDetail",
    "DiffResultData",
    "SkillPackage",
    "SyncAction",
    "SyncApplyResult",
    "SyncPlan",
    "SyncWarning",
    "TreeStatus",
    "TreeStatusItem",
    "compare_file",
    "discover_builtin_skills",
    "install_skill",
    "pull_file",
    "pull_tree",
    "push_file",
    "push_tree",
    "resolve_skill_root",
    "skill_status",
    "tree_status",
    "uninstall_skill",
    "update_skill",
    "validate_skill",
]
