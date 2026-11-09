"""内置 Agent Skill 的发现、校验和安全安装生命周期。"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

try:
    from importlib.resources.abc import Traversable
except ImportError:  # Python 3.10
    from importlib.abc import Traversable


INSTALL_METADATA = ".agent-skill-install.json"
MANIFEST_VERSION = "1"
AGENT_SKILL_DIRS = {
    "codex": {
        "user": (".agents", "skills"),
        "project": (".agents", "skills"),
    },
    "claude": {
        "user": (".claude", "skills"),
        "project": (".claude", "skills"),
    },
}


@dataclass(frozen=True)
class SkillPackage:
    """Python 包中的一个内置 Skill。"""

    name: str
    description: str
    version: str
    root: Traversable


@dataclass(frozen=True)
class InstallMetadata:
    """已安装 Skill 的来源、版本和原始内容哈希。"""

    manifest_version: str
    installer: str
    cli_version: str
    skill_version: str
    source: str
    installed_at: str
    content_hash: str


def _frontmatter(root: Traversable) -> dict[str, Any]:
    entrypoint = root.joinpath("SKILL.md")
    if not entrypoint.is_file():
        raise ValueError(f"{root.name} 缺少 SKILL.md")
    lines = entrypoint.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{root.name}/SKILL.md 缺少 YAML frontmatter")
    try:
        end = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError(f"{root.name}/SKILL.md frontmatter 未闭合") from exc
    value = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(value, dict):
        raise ValueError(f"{root.name}/SKILL.md frontmatter 必须是映射")
    return value


def validate_skill(root: Traversable) -> dict[str, Any]:
    """校验内置 Skill 的名称、描述和独立版本。"""
    frontmatter = _frontmatter(root)
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    metadata = frontmatter.get("metadata")
    if name != root.name:
        raise ValueError(f"Skill name {name!r} 与目录名 {root.name!r} 不一致")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{root.name} 缺少有效 description")
    if not isinstance(metadata, dict) or not isinstance(
        metadata.get("skill-version"), str
    ):
        raise ValueError(f"{root.name} 缺少 metadata.skill-version")
    return frontmatter


def discover_builtin_skills(resource_package: str) -> dict[str, SkillPackage]:
    """发现 `<package>.skills` 中随发行包发布的所有 Skill。"""
    catalog: dict[str, SkillPackage] = {}
    for child in sorted(files(resource_package).iterdir(), key=lambda item: item.name):
        if not child.is_dir() or not child.joinpath("SKILL.md").is_file():
            continue
        frontmatter = validate_skill(child)
        skill = SkillPackage(
            name=frontmatter["name"],
            description=frontmatter["description"],
            version=frontmatter["metadata"]["skill-version"],
            root=child,
        )
        catalog[skill.name] = skill
    return catalog


def resolve_skill_root(
    agent: str,
    scope: str,
    *,
    project: Path | None = None,
    home: Path | None = None,
) -> Path:
    """解析已知 Agent 的用户级或项目级 Skill 根目录。"""
    if agent not in AGENT_SKILL_DIRS:
        raise ValueError(f"不支持 Agent：{agent}")
    if scope not in ("user", "project"):
        raise ValueError(f"不支持安装范围：{scope}")
    if scope == "user":
        root = (home or Path.home()).expanduser().resolve()
    else:
        if project is None:
            raise ValueError("项目级安装需要项目根目录")
        root = project.expanduser().resolve()
    return root.joinpath(*AGENT_SKILL_DIRS[agent][scope])


def _digest(items: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(items):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def _resource_items(
    root: Traversable,
    prefix: PurePosixPath = PurePosixPath(),
) -> Iterable[tuple[str, bytes]]:
    for child in root.iterdir():
        relative = prefix / child.name
        if relative.as_posix() == INSTALL_METADATA:
            continue
        if child.is_dir():
            yield from _resource_items(child, relative)
        elif child.is_file():
            yield relative.as_posix(), child.read_bytes()


def _path_items(root: Path) -> Iterable[tuple[str, bytes]]:
    for child in root.rglob("*"):
        relative = child.relative_to(root).as_posix()
        if relative == INSTALL_METADATA:
            continue
        if child.is_symlink():
            raise ValueError(f"Skill 中不允许符号链接：{relative}")
        if child.is_file():
            yield relative, child.read_bytes()


def _resource_hash(root: Traversable) -> str:
    return _digest(_resource_items(root))


def _path_hash(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Skill 目标不是普通目录：{root}")
    return _digest(_path_items(root))


def _metadata_path(skill_dir: Path) -> Path:
    return skill_dir / INSTALL_METADATA


def _read_metadata(skill_dir: Path) -> InstallMetadata | None:
    path = _metadata_path(skill_dir)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"安装清单不是 JSON 对象：{path}")
    try:
        metadata = InstallMetadata(**value)
    except TypeError as exc:
        raise ValueError(f"安装清单字段无效：{path}") from exc
    if metadata.manifest_version != MANIFEST_VERSION:
        raise ValueError(f"不支持安装清单版本：{metadata.manifest_version}")
    return metadata


def _write_metadata(skill_dir: Path, metadata: InstallMetadata) -> None:
    payload = json.dumps(
        asdict(metadata),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    _metadata_path(skill_dir).write_text(f"{payload}\n", encoding="utf-8")


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _unique_sibling(target: Path, suffix: str) -> Path:
    candidate = target.with_name(f"{target.name}{suffix}")
    index = 1
    while _present(candidate):
        candidate = target.with_name(f"{target.name}{suffix}.{index}")
        index += 1
    return candidate


def _copy_resource(source: Traversable, destination: Path) -> None:
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            target.mkdir()
            _copy_resource(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def skill_status(
    skill: SkillPackage,
    target_root: Path,
    *,
    installer: str,
) -> dict[str, object]:
    """检查一个目标中的安装来源、版本和本地修改。"""
    target = target_root.expanduser().resolve() / skill.name
    if not _present(target):
        return {
            "installed": False,
            "path": str(target),
            "available_version": skill.version,
        }
    metadata = None
    current_hash = None
    error = None
    try:
        metadata = _read_metadata(target)
        current_hash = _path_hash(target)
    except (OSError, ValueError) as exc:
        error = str(exc)
    return {
        "installed": True,
        "path": str(target),
        "owned": metadata is not None and metadata.installer == installer,
        "modified": (
            True
            if metadata is None or current_hash is None
            else current_hash != metadata.content_hash
        ),
        "installed_version": metadata.skill_version if metadata else None,
        "available_version": skill.version,
        "error": error,
    }


def install_skill(
    skill: SkillPackage,
    target_root: Path,
    *,
    installer: str,
    cli_version: str,
    force: bool = False,
    backup: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """首次安装 Skill；已有其他内容时默认拒绝。"""
    validate_skill(skill.root)
    state = skill_status(skill, target_root, installer=installer)
    source_hash = _resource_hash(skill.root)
    if state["installed"]:
        metadata = (
            _read_metadata(Path(str(state["path"]))) if state.get("owned") else None
        )
        if (
            metadata
            and not state.get("modified")
            and metadata.content_hash == source_hash
        ):
            return {"changed": False, "action": "none", **state}
        if state.get("owned") and not state.get("modified") and not (force or backup):
            raise FileExistsError("已安装其他版本；请使用 skill update")
        if not (force or backup):
            raise FileExistsError(
                "目标来源未知或已被修改；使用 --backup 或明确 --force"
            )
    return _replace(
        skill,
        target_root,
        installer=installer,
        cli_version=cli_version,
        source_hash=source_hash,
        action="install",
        backup=backup,
        dry_run=dry_run,
    )


def update_skill(
    skill: SkillPackage,
    target_root: Path,
    *,
    installer: str,
    cli_version: str,
    force: bool = False,
    backup: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """更新由当前安装器拥有的 Skill。"""
    validate_skill(skill.root)
    state = skill_status(skill, target_root, installer=installer)
    if not state["installed"]:
        raise FileNotFoundError(f"Skill 尚未安装：{state['path']}")
    if not state.get("owned"):
        raise PermissionError(f"Skill 不归 {installer} 管理：{state['path']}")
    if state.get("modified") and not (force or backup):
        raise FileExistsError("Skill 已被本地修改；使用 --backup 或明确 --force")
    source_hash = _resource_hash(skill.root)
    target = Path(str(state["path"]))
    metadata = _read_metadata(target)
    if metadata and not state.get("modified") and metadata.content_hash == source_hash:
        return {"changed": False, "action": "none", **state}
    return _replace(
        skill,
        target_root,
        installer=installer,
        cli_version=cli_version,
        source_hash=source_hash,
        action="update",
        backup=backup,
        dry_run=dry_run,
    )


def uninstall_skill(
    skill: SkillPackage,
    target_root: Path,
    *,
    installer: str,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """卸载当前安装器拥有且未被修改的 Skill。"""
    state = skill_status(skill, target_root, installer=installer)
    if not state["installed"]:
        return {"changed": False, "action": "none", **state}
    if not state.get("owned"):
        raise PermissionError(f"拒绝删除来源未知的 Skill：{state['path']}")
    if state.get("modified") and not force:
        raise FileExistsError(f"Skill 已被本地修改，默认保留：{state['path']}")
    if not dry_run:
        _remove(Path(str(state["path"])))
    return {
        "changed": True,
        "action": "uninstall",
        "dry_run": dry_run,
        "path": state["path"],
    }


def _replace(
    skill: SkillPackage,
    target_root: Path,
    *,
    installer: str,
    cli_version: str,
    source_hash: str,
    action: str,
    backup: bool,
    dry_run: bool,
) -> dict[str, object]:
    target = target_root.expanduser().resolve() / skill.name
    backup_path = (
        _unique_sibling(target, ".backup") if _present(target) and backup else None
    )
    if dry_run:
        return {
            "changed": True,
            "action": action,
            "dry_run": True,
            "path": str(target),
            "backup_path": str(backup_path) if backup_path else None,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{skill.name}.staging-", dir=target.parent))
    displaced = None
    try:
        _copy_resource(skill.root, stage)
        if _path_hash(stage) != source_hash:
            raise RuntimeError("暂存 Skill 与包资源哈希不一致")
        _write_metadata(
            stage,
            InstallMetadata(
                manifest_version=MANIFEST_VERSION,
                installer=installer,
                cli_version=cli_version,
                skill_version=skill.version,
                source="builtin",
                installed_at=datetime.now(timezone.utc).isoformat(),
                content_hash=source_hash,
            ),
        )
        if _present(target):
            displaced = backup_path or _unique_sibling(target, ".previous")
            target.rename(displaced)
        try:
            stage.rename(target)
        except Exception:
            if displaced is not None and not _present(target):
                displaced.rename(target)
            raise
        if displaced is not None and backup_path is None:
            _remove(displaced)
    finally:
        if _present(stage):
            _remove(stage)
    return {
        "changed": True,
        "action": action,
        "dry_run": False,
        "path": str(target),
        "backup_path": str(backup_path) if backup_path else None,
        "skill_version": skill.version,
        "content_hash": source_hash,
    }
