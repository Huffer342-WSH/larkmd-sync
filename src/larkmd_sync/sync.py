"""仅使用 Markdown 和文档 front matter 的安全两阶段同步。"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .board_sync import _update, plan_boards, restore_and_bind, rewrite_keeping_boards
from .core.board_assets import svg_display_size, validate_metadata
from .core.callouts import LocalCallout, callout_replacements, materialize_callouts
from .core.doc_id import DocIdType, parse_doc_id
from .core.heading_numbers import (
    heading_number_replacements,
    prepare_heading_numbers,
    restore_heading_numbers,
)
from .core.markdown_correction import correct_markdown
from .core.ordered_lists import ordered_list_replacements
from .core.preprocess import (
    preprocess_markdown,
    rewrite_interrupted_ordered_lists,
    split_front_matter,
)
from .core.urls import base_url_from_document, build_document_url
from .lark_cli import (
    LarkCliError,
    download_image_url,
    download_media,
    export_whiteboard_source,
    export_whiteboard_svg,
    fetch_document_xml,
    fetch_markdown,
    get_wiki_node,
    insert_local_image,
    list_wiki_children,
    materialize_addon_mermaid,
    overwrite_doc,
    remove_image_marker,
    replace_block_xml,
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
from .progress import report
from .resources import (
    LocalAsset,
    LocalImage,
    materialize_remote_links,
    materialize_remote_resources,
    prepare_markdown_resources,
)
from .result import ApiResult

PLAN_SCHEMA_VERSION = "4"
_REMOTE_FEATURES = {
    "readonly_block": re.compile(r"<readonly-block\b", re.IGNORECASE),
    "unresolved_cite": re.compile(r"<cite\b", re.IGNORECASE),
    "image": re.compile(r"<img\b", re.IGNORECASE),
    "whiteboard": re.compile(r"<whiteboard\b", re.IGNORECASE),
    "synced_reference": re.compile(r"<synced[_-]reference\b", re.IGNORECASE),
    "synced_source": re.compile(r"<synced-source\b", re.IGNORECASE),
    "base": re.compile(r"<base_refer\b", re.IGNORECASE),
    "sheet": re.compile(r"<sheet\b", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class _RemoteSnapshot:
    document_url: str
    document_id: str
    revision_id: int
    title: str
    markdown: str
    comparison_markdown: str
    raw_hash: str
    assets: tuple[LocalAsset, ...]
    warnings: tuple[SyncWarning, ...]
    document_xml: str = ""


@dataclass(frozen=True, slots=True)
class _PreparedPush:
    plan: SyncPlan
    document_url: str
    document_id: str
    markdown_file: Path
    content: str
    comparison_markdown: str
    images: tuple[LocalImage, ...]
    callouts: tuple[LocalCallout, ...]
    board_operations: tuple = ()
    document_xml: str = ""
    text_unchanged: bool = False
    text_updates: tuple = ()
    headings: tuple = ()


@dataclass(frozen=True, slots=True)
class _TreeDocument:
    relative_path: Path
    snapshot: _RemoteSnapshot


@dataclass(frozen=True, slots=True)
class _MappedDocument:
    path: Path
    document_url: str


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _bytes_hash(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _file_hash(path: Path) -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        return "invalid"
    return _bytes_hash(path.read_bytes())


def _canonical_document_url(value: str) -> str:
    parsed = parse_doc_id(value)
    if not parsed.is_valid or parsed.type is DocIdType.UNKNOWN:
        raise ValueError(f"无效的飞书/Lark 文档 URL 或 token：{value}")
    if parsed.type in {DocIdType.DOCX, DocIdType.WIKI}:
        return parsed.raw
    return parsed.url


def _frontmatter_url(markdown_file: Path) -> str | None:
    if not markdown_file.is_file() or markdown_file.is_symlink():
        return None
    metadata, _ = split_front_matter(markdown_file.read_text(encoding="utf-8"))
    value = metadata.get("feishu_url") or metadata.get("lark_url")
    return value.strip() if value else None


def resolve_document_url(
    markdown_file: str | Path,
    document_url: str | None = None,
) -> str:
    """从显式参数或 Markdown front matter 解析目标文档。"""
    path = _absolute(markdown_file)
    declared = _frontmatter_url(path)
    if document_url:
        explicit = _canonical_document_url(document_url)
        if declared:
            declared_url = _canonical_document_url(declared)
            if parse_doc_id(explicit).token != parse_doc_id(declared_url).token:
                raise ValueError(
                    "--doc 与 Markdown front matter 中的文档地址不一致；"
                    "请先确认目标，避免覆盖错误文档"
                )
        return explicit
    if declared:
        return _canonical_document_url(declared)
    raise ValueError(
        f"{path} 没有 feishu_url/lark_url front matter；请在本次调用中传入 --doc"
    )


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        value = line.lstrip("#").strip()
        if value:
            return value[:200]
    return fallback


def _front_matter(title: str, document_url: str) -> str:
    safe_title = title.replace("\r", " ").replace("\n", " ")
    return (
        "---\n"
        f"title: {json.dumps(safe_title, ensure_ascii=False)}\n"
        f"feishu_url: {json.dumps(document_url, ensure_ascii=False)}\n"
        "---\n\n"
    )


def _reference_count(value: object) -> int:
    if isinstance(value, dict):
        return sum(_reference_count(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return sum(_reference_count(item) for item in value)
    return 0 if value in {None, ""} else 1


def _remote_warnings(
    remote: dict[str, Any],
    path: Path,
    *,
    markdown: str | None = None,
    handled_reference_types: frozenset[str] = frozenset(),
) -> tuple[SyncWarning, ...]:
    markdown = str(remote.get("content", "")) if markdown is None else markdown
    warnings: list[SyncWarning] = [
        SyncWarning(
            code="text_color_not_restored",
            message=message,
            path=str(path),
            blocking=True,
        )
        for message in remote.get("color_warnings", ())
    ]
    detected = {
        feature: len(pattern.findall(markdown))
        for feature, pattern in _REMOTE_FEATURES.items()
        if pattern.search(markdown)
    }
    for feature, count in detected.items():
        warnings.append(
            SyncWarning(
                code="markdown_feature_degraded",
                message=(
                    f"远端包含暂不支持的 {feature} × {count}；本工具不能保证该结构"
                    "能够在本地显示或完整回写"
                ),
                path=str(path),
                blocking=True,
            )
        )

    reference_map = remote.get("reference_map")
    if isinstance(reference_map, dict):
        reference_map = {
            key: value
            for key, value in reference_map.items()
            if str(key).lower().replace("-", "_") not in handled_reference_types
        }
    reference_count = _reference_count(reference_map)
    if reference_count and not detected:
        warnings.append(
            SyncWarning(
                code="markdown_reference_degraded",
                message=(
                    f"远端返回 {reference_count} 个 Markdown 引用映射；本工具不保存"
                    " reference_map，回写前请确认文档不依赖云端专有结构"
                ),
                path=str(path),
                blocking=True,
            )
        )

    tips = str(remote.get("tips", "")).strip()
    if tips:
        warnings.append(
            SyncWarning(
                code="lark_cli_markdown_tip",
                message=tips,
                path=str(path),
            )
        )
    return tuple(warnings)


def _fetch_snapshot(
    lark_cli: str,
    document_url: str,
    target: Path,
    *,
    identity: str,
    include_front_matter: bool,
) -> _RemoteSnapshot:
    canonical_url = _canonical_document_url(document_url)
    report(f"读取文档：{canonical_url} → {target.name}")
    remote = fetch_markdown(
        lark_cli,
        canonical_url,
        identity=identity,
        detail="simple",
    )
    raw_markdown, heading_warnings = restore_heading_numbers(
        materialize_addon_mermaid(lark_cli, remote, identity=identity),
        str(remote.get("document_xml", "")),
    )
    raw_markdown = correct_markdown(raw_markdown)
    remote_callouts = materialize_callouts(
        materialize_remote_links(raw_markdown, base_url_from_document(canonical_url))
    )
    materialized = materialize_remote_resources(
        remote_callouts.markdown,
        target,
        download_media=lambda token: download_media(
            lark_cli,
            token,
            identity=identity,
        ),
        document_id=str(remote.get("document_id", "")),
        document_url=canonical_url,
        download_url=download_image_url,
        download_whiteboard_svg=lambda token, digest: export_whiteboard_svg(
            lark_cli, token, identity=identity, cache_key=canonical_url + ":" + digest
        ),
        download_whiteboard=lambda token: download_media(
            lark_cli, token, identity=identity, media_type="whiteboard"
        ),
        export_whiteboard=lambda token: export_whiteboard_source(
            lark_cli,
            token,
            identity=identity,
        ),
    )
    markdown = rewrite_interrupted_ordered_lists(materialized.markdown)
    document_id = (
        str(remote.get("document_id", "")) or parse_doc_id(canonical_url).token
    )
    title = _extract_title(markdown, document_id)
    if include_front_matter:
        markdown = _front_matter(title, canonical_url) + markdown.lstrip("\r\n")
    if markdown and not markdown.endswith("\n"):
        markdown += "\n"
    comparison_markdown = rewrite_interrupted_ordered_lists(
        materialized.comparison_markdown
    )
    if comparison_markdown and not comparison_markdown.endswith("\n"):
        comparison_markdown += "\n"
    callout_warnings: tuple[SyncWarning, ...] = ()
    if remote_callouts.count:
        message = (
            "飞书高亮块已映射为 Markdown Alert；飞书自定义背景色、边框色和"
            "文字颜色无法保存在 Markdown 中，回写时会归一化为固定样式"
        )
        if remote_callouts.unknown_emojis:
            message += "；无法识别的图标已按 NOTE 处理：" + "、".join(
                remote_callouts.unknown_emojis
            )
        callout_warnings = (
            SyncWarning(
                code="callout_style_normalized",
                message=message,
                path=str(target),
            ),
        )
    resource_warnings = tuple(
        SyncWarning(
            code=warning.code,
            message=warning.message,
            path=str(target),
            blocking=warning.blocking,
        )
        for warning in materialized.warnings
    )
    return _RemoteSnapshot(
        document_url=canonical_url,
        document_id=document_id,
        revision_id=int(remote.get("revision_id", 0)),
        title=title,
        markdown=markdown,
        comparison_markdown=comparison_markdown,
        raw_hash=_json_hash(
            {
                "markdown": comparison_markdown,
                "assets": [asset.digest for asset in materialized.assets],
            }
        ),
        assets=materialized.assets,
        document_xml=str(remote.get("document_xml", "")),
        warnings=callout_warnings
        + tuple(SyncWarning(code="heading_number_unresolved", message=message,
                            path=str(target), blocking=True) for message in heading_warnings)
        + resource_warnings
        + _remote_warnings(
            remote,
            target,
            markdown=materialized.markdown,
            handled_reference_types=materialized.handled_reference_types,
        ),
    )


def _native_pull_conflicts(asset: LocalAsset) -> tuple[str, ...]:
    if (
        asset.role == "board_source"
        and asset.path.is_file()
        and not asset.path.is_symlink()
    ):
        previous = json.loads(asset.path.read_text())
        try:
            validate_metadata(previous)
        except ValueError as exc:
            return (str(exc),)
        # Before migrating JPG/PNG references to SVG, also protect edited old previews.
        svg = asset.path.with_suffix(".svg")
        if not svg.exists():
            for suffix in (".jpg", ".png"):
                legacy = asset.path.with_suffix(suffix)
                if legacy.is_file() and _file_hash(legacy) != previous.get("preview_hash"):
                    return ("本地原生画板预览已修改，拒绝迁移覆盖",)

    if asset.role == "board_preview" and asset.path.is_file():
        source = asset.path.with_suffix(".json")
        if source.is_file() and not source.is_symlink():
            previous = json.loads(source.read_text())
            if _file_hash(asset.path) != previous.get("preview_hash"):
                return ("本地原生画板预览已修改，拒绝拉取覆盖",)
    return ()


def _target_conflicts(path: Path) -> tuple[str, ...]:
    if path.is_symlink():
        return (f"拒绝覆盖符号链接：{path}",)
    if path.exists() and not path.is_file():
        return (f"目标不是普通文件：{path}",)
    if path.parent.exists() and (path.parent.is_symlink() or not path.parent.is_dir()):
        return (f"目标父目录不可安全写入：{path.parent}",)
    return ()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError(f"拒绝写入符号链接目录：{path.parent}")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ValueError(f"拒绝替换非普通文件：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _pull_plan_internal(
    lark_cli: str,
    document_url: str,
    markdown_file: str | Path,
    *,
    identity: str,
    include_front_matter: bool,
) -> tuple[SyncPlan, _RemoteSnapshot]:
    target = _absolute(markdown_file)
    snapshot = _fetch_snapshot(
        lark_cli,
        document_url,
        target,
        identity=identity,
        include_front_matter=include_front_matter,
    )
    current_hash = _file_hash(target)
    asset_entries = [
        {
            "path": str(asset.path),
            "remote_hash": asset.digest,
            "local_before_hash": _file_hash(asset.path),
        }
        for asset in snapshot.assets
    ]
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "operation": "pull",
        "document_url": snapshot.document_url,
        "remote_revision": snapshot.revision_id,
        "remote_hash": snapshot.raw_hash,
        "rendered_hash": _bytes_hash(snapshot.markdown.encode("utf-8")),
        "local_path": str(target),
        "local_before_hash": current_hash,
        "include_front_matter": include_front_matter,
        "assets": asset_entries,
    }
    risks: list[str] = []
    if current_hash not in {None, "invalid"}:
        risks.append("将原子替换现有本地 Markdown；未提交的本地修改会丢失")
    if any(
        item["local_before_hash"] not in {None, "invalid"} for item in asset_entries
    ):
        risks.append("将原子替换文档引用的现有本地图片；不会删除未再引用的旧图片")
    actions = [
        SyncAction(
            action="write_local_markdown",
            local_path=str(target),
            document_url=snapshot.document_url,
            remote_revision=snapshot.revision_id,
            overwrites=current_hash is not None,
        )
    ]
    actions.extend(
        SyncAction(
            action="write_local_board_source"
            if asset.role == "board_source"
            else "write_local_image",
            local_path=str(asset.path),
            document_url=snapshot.document_url,
            remote_revision=snapshot.revision_id,
            overwrites=_file_hash(asset.path) is not None,
        )
        for asset in snapshot.assets
    )
    conflicts = list(_target_conflicts(target))
    for asset in snapshot.assets:
        conflicts.extend(_target_conflicts(asset.path))
        conflicts.extend(_native_pull_conflicts(asset))
    plan = SyncPlan(
        plan_id=_json_hash(payload),
        operation="pull",
        target=str(target),
        actions=tuple(actions),
        risks=tuple(risks),
        conflicts=tuple(conflicts),
        warnings=snapshot.warnings,
    )
    return plan, snapshot


def plan_pull_file(
    lark_cli: str,
    document_url: str,
    markdown_file: str | Path,
    *,
    identity: str = "user",
    include_front_matter: bool = True,
) -> ApiResult[SyncPlan]:
    """只读生成单文档拉取计划。"""
    plan, _ = _pull_plan_internal(
        lark_cli,
        document_url,
        markdown_file,
        identity=identity,
        include_front_matter=include_front_matter,
    )
    return ApiResult(ok=True, status=200, data=plan)


def apply_pull_file(
    lark_cli: str,
    document_url: str,
    markdown_file: str | Path,
    plan_id: str | None = None,
    *,
    identity: str = "user",
    include_front_matter: bool = True,
) -> ApiResult[SyncApplyResult]:
    """重新校验计划并原子写入唯一的 Markdown 文件。"""
    target = _absolute(markdown_file)
    local_before = _file_hash(target)
    plan, snapshot = _pull_plan_internal(
        lark_cli,
        document_url,
        markdown_file,
        identity=identity,
        include_front_matter=include_front_matter,
    )
    if _file_hash(target) != local_before:
        return ApiResult(ok=False, status=409, error="拉取期间本地文件已修改，拒绝覆盖")
    if plan_id is not None and plan.plan_id != plan_id:
        return ApiResult(
            ok=False,
            status=409,
            error="同步计划已过期：本地文件或远端 revision 已变化，请重新预览",
        )
    if plan.conflicts:
        return ApiResult(ok=False, status=409, error="；".join(plan.conflicts))
    report(f"写入文档：{target}（{len(snapshot.assets)} 个资源）")
    for asset in snapshot.assets:
        _atomic_write(asset.path, asset.content)
    _atomic_write(target, snapshot.markdown.encode("utf-8"))
    return ApiResult(
        ok=True,
        status=200,
        data=SyncApplyResult(
            plan_id=plan.plan_id,
            operation="pull",
            changed=1,
            local_paths=(str(target),),
            document_urls=(snapshot.document_url,),
            assets_changed=len(snapshot.assets),
            asset_paths=tuple(str(asset.path) for asset in snapshot.assets),
            warnings=plan.warnings,
        ),
    )


def _push_plan_internal(
    lark_cli: str,
    markdown_file: str | Path,
    document_url: str | None,
    *,
    identity: str,
    document_urls: Mapping[Path, str] | None = None,
) -> _PreparedPush:
    local_path = _absolute(markdown_file)
    conflicts = list(_target_conflicts(local_path))
    if not local_path.exists():
        conflicts.append(f"本地 Markdown 不存在：{local_path}")
    if conflicts:
        plan = SyncPlan(
            plan_id=_json_hash(
                {"operation": "push", "path": str(local_path), "conflicts": conflicts}
            ),
            operation="push",
            target=str(local_path),
            actions=(),
            conflicts=tuple(conflicts),
        )
        return _PreparedPush(
            plan=plan,
            document_url=document_url or "",
            document_id="",
            markdown_file=local_path,
            content="",
            comparison_markdown="",
            images=(),
            callouts=(),
        )

    resolved_url = resolve_document_url(local_path, document_url)
    local_markdown = local_path.read_text(encoding="utf-8")
    body = preprocess_markdown(
        local_markdown,
        source_path=local_path,
        document_urls=document_urls,
    )
    try:
        local_resources = prepare_markdown_resources(body, local_path)
    except ValueError as exc:
        message = str(exc)
        plan = SyncPlan(
            plan_id=_json_hash(
                {"operation": "push", "path": str(local_path), "conflicts": [message]}
            ),
            operation="push",
            target=resolved_url,
            actions=(),
            conflicts=(message,),
        )
        return _PreparedPush(
            plan=plan,
            document_url=resolved_url,
            document_id="",
            markdown_file=local_path,
            content="",
            comparison_markdown="",
            images=(),
            callouts=(),
        )
    snapshot = _fetch_snapshot(
        lark_cli,
        resolved_url,
        local_path,
        identity=identity,
        include_front_matter=False,
    )
    remote_revision = snapshot.revision_id
    board_conflicts = []
    try:
        board_operations = plan_boards(local_resources.images, snapshot)
    except ValueError as exc:
        board_operations = ()
        board_conflicts.append(str(exc))
    text_unchanged = _normalize_text(
        local_resources.comparison_markdown
    ) == _normalize_text(snapshot.comparison_markdown)
    local_other = tuple(
        image.digest for image in local_resources.images if image.board is None
    )
    remote_other = tuple(
        asset.digest for asset in snapshot.assets if asset.role == "image"
    )
    text_unchanged = (text_unchanged and local_other == remote_other
                      and not any(op.action == "create" for op in board_operations))
    text_updates = ()
    if (board_operations and not text_unchanged and local_other == remote_other
            and not any(op.action == "create" for op in board_operations)):
        edits = []
        before = snapshot.comparison_markdown.splitlines()
        after = local_resources.comparison_markdown.splitlines()
        for tag, a, b, c, d in difflib.SequenceMatcher(
            a=before, b=after, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if (
                tag != "replace"
                or b - a != 1
                or d - c != 1
                or not before[a].strip()
                or any(ch in before[a] + after[c] for ch in "<>[]`|")
                or snapshot.comparison_markdown.count(before[a]) != 1
            ):
                edits = []
                break
            edits.append((before[a], after[c]))
        text_updates = tuple(edits)
    heading_upload, headings = prepare_heading_numbers(local_resources.upload_markdown)
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "operation": "push",
        "document_url": resolved_url,
        "remote_revision": remote_revision,
        "remote_hash": snapshot.raw_hash,
        "local_path": str(local_path),
        "local_hash": _file_hash(local_path),
        "upload_hash": _bytes_hash(heading_upload.encode("utf-8")),
        "images": [
            {
                "path": str(image.path),
                "hash": image.digest,
                "marker": image.marker,
                "insert_after": image.insert_after,
                "sequence_marker": image.sequence_marker,
                "resume_sequence": image.resume_sequence,
                "board_source_hash": _file_hash(image.board.path)
                if image.board
                else None,
            }
            for image in local_resources.images
        ],
        "callouts": [
            {"kind": callout.kind, "marker": callout.marker}
            for callout in local_resources.callouts
        ],
        "board_operations": [
            (op.action, op.token, op.remote_hash) for op in board_operations
        ],
        "text_unchanged": text_unchanged,
        "text_updates": text_updates,
        "mermaid_count": local_resources.mermaid_count,
        "mermaid_types": local_resources.mermaid_types,
    }
    actions = [
        SyncAction(
            action="overwrite_remote_markdown",
            local_path=str(local_path),
            document_url=resolved_url,
            remote_revision=remote_revision,
            overwrites=True,
        )
    ]
    actions.extend(
        SyncAction(
            action="insert_remote_image",
            local_path=str(image.path),
            document_url=resolved_url,
            remote_revision=remote_revision,
        )
        for image in local_resources.images
        if image.board is None
    )
    if board_operations:
        if text_unchanged:
            actions = []
        elif text_updates:
            actions = [
                SyncAction(
                    action="update_text_blocks",
                    local_path=str(local_path),
                    document_url=resolved_url,
                    remote_revision=remote_revision,
                )
            ]
        elif any(op.block_id for op in board_operations):
            actions[0] = SyncAction(
                action="rewrite_text_keep_native_boards",
                local_path=str(local_path),
                document_url=resolved_url,
                remote_revision=remote_revision,
            )
        for op in board_operations:
            actions.append(
                SyncAction(
                    action=op.action + "_native_board",
                    local_path=str(op.image.path if op.action == "create" else op.image.board.path),
                    document_url=resolved_url,
                    remote_revision=remote_revision,
                )
            )
            actions.append(
                SyncAction(
                    action="write_local_board_source" if not op.image.board.value else "update_local_board_binding",
                    local_path=str(op.image.board.path),
                    document_url=resolved_url,
                )
            )
    risks = ["远端 Markdown 正文将被整体替换"]
    if local_resources.images:
        risks.append(
            "本地图片会在正文覆盖后逐张上传；中途失败时远端可能暂时保留图片占位文本"
        )
    if board_operations and (text_unchanged or text_updates):
        risks = ["更新原生画板同步基准；本地和远端画板同时修改时拒绝覆盖"]
    elif board_operations and any(op.block_id for op in board_operations):
        risks = ["保留原生画板块及 token，重建其余正文；多步操作失败时需检查实际进度"]
    if any(op.image.board.svg is not None for op in board_operations):
        risks.append("SVG 将导入为原生画板节点；更新已有画板时保留 token，但不保证分组和连接绑定语义")
    for op in board_operations:
        if op.action == "create":
            width, height = svg_display_size(op.image.path.read_text())
            risks.append(f"新画板显示区域为 {width} × {height} px；实际宽度受文档页宽限制")
        elif op.action in {"update", "remote_changed"}:
            risks.append("保留已有画板块尺寸；若图形比例变化，请在飞书中调整画板显示区域")
    plan = SyncPlan(
        plan_id=_json_hash(payload),
        operation="push",
        target=resolved_url,
        actions=tuple(actions),
        risks=tuple(risks),
        conflicts=tuple(board_conflicts),
        warnings=snapshot.warnings
        + tuple(
            SyncWarning(
                code="text_color_palette_normalized",
                message="字体颜色按 RGB 距离映射到飞书七色色板：" + value,
                path=str(local_path),
            )
            for value in local_resources.color_normalizations
        ),
    )
    return _PreparedPush(
        plan=plan,
        document_url=resolved_url,
        document_id=snapshot.document_id,
        markdown_file=local_path,
        content=heading_upload,
        comparison_markdown=local_resources.comparison_markdown,
        images=local_resources.images,
        callouts=local_resources.callouts,
        board_operations=board_operations,
        document_xml=snapshot.document_xml,
        text_unchanged=text_unchanged,
        text_updates=text_updates,
        headings=headings,
    )


def plan_push_file(
    lark_cli: str,
    markdown_file: str | Path,
    document_url: str | None = None,
    *,
    identity: str = "user",
) -> ApiResult[SyncPlan]:
    """只读生成单文档推送计划。"""
    prepared = _push_plan_internal(
        lark_cli,
        markdown_file,
        document_url,
        identity=identity,
    )
    return ApiResult(ok=True, status=200, data=prepared.plan)


def _apply_prepared_push(
    lark_cli: str,
    prepared: _PreparedPush,
    *,
    identity: str,
) -> None:
    content = prepared.content
    document_id = prepared.document_id
    if prepared.board_operations and prepared.text_unchanged:
        pass
    elif prepared.text_updates:
        for before, after in prepared.text_updates:
            _update(
                lark_cli,
                document_id,
                "str_replace",
                identity=identity,
                content=after,
                pattern=before,
                markdown=True,
            )
    elif any(op.block_id for op in prepared.board_operations):
        rewrite_keeping_boards(
            lark_cli,
            document_id,
            content,
            prepared.board_operations,
            prepared.document_xml,
            identity=identity,
        )
    else:
        if prepared.headings and prepared.document_xml:
            title = ET.fromstring('<root>' + prepared.document_xml + '</root>').find('title')
            lines = content.lstrip().splitlines(keepends=True)
            if title is not None and lines and lines[0].rstrip() == '# ' + ''.join(title.itertext()):
                content = ''.join(lines[1:]).lstrip('\r\n')
        result = overwrite_doc(
            lark_cli, prepared.document_url, content, identity=identity
        )
        document_id = str(result.get("document_id", "")) or prepared.document_id
    if prepared.board_operations:
        restore_and_bind(
            lark_cli,
            document_id,
            prepared.board_operations,
            identity=identity,
            atomic_write=_atomic_write,
        )
    if prepared.board_operations and (prepared.text_unchanged or prepared.text_updates):
        return
    if (prepared.images or prepared.callouts or prepared.headings) and not document_id:
        raise LarkCliError("块修复失败：飞书没有返回底层 document_id")
    for image in prepared.images:
        if image.board is not None:
            continue
        insert_local_image(
            lark_cli,
            document_id,
            image.path,
            image.marker,
            caption=image.alt,
            after=image.insert_after,
            identity=identity,
        )
        remove_image_marker(
            lark_cli,
            document_id,
            image.marker,
            identity=identity,
        )
    sequences_by_marker = {
        image.sequence_marker: image.resume_sequence
        for image in prepared.images
        if image.sequence_marker and image.resume_sequence is not None
    }
    if sequences_by_marker or prepared.callouts or prepared.headings:
        document_xml = fetch_document_xml(
            lark_cli,
            document_id,
            identity=identity,
        )
        try:
            sequence_replacements = ordered_list_replacements(
                document_xml,
                sequences_by_marker,
            )
            styled_callouts = callout_replacements(
                document_xml,
                prepared.callouts,
            )
        except ValueError as exc:
            raise LarkCliError(f"飞书块修复失败：{exc}") from exc
        for block_id, content in reversed(heading_number_replacements(document_xml, prepared.headings)):
            replace_block_xml(lark_cli, document_id, block_id, content, identity=identity)
        for replacement in reversed(sequence_replacements):
            replace_block_xml(
                lark_cli,
                document_id,
                replacement.block_id,
                replacement.content,
                identity=identity,
            )
        for replacement in reversed(styled_callouts):
            replace_block_xml(
                lark_cli,
                document_id,
                replacement.block_id,
                replacement.content,
                identity=identity,
            )


def _blocking_warning_error(plan: SyncPlan) -> str | None:
    blocked = [warning.message for warning in plan.warnings if warning.blocking]
    if not blocked:
        return None
    return "当前同步能力不能安全回写该文档：" + "；".join(blocked)


def apply_push_file(
    lark_cli: str,
    markdown_file: str | Path,
    plan_id: str,
    document_url: str | None = None,
    *,
    identity: str = "user",
) -> ApiResult[SyncApplyResult]:
    """重新校验并执行单文档 Markdown 推送计划。"""
    prepared = _push_plan_internal(
        lark_cli,
        markdown_file,
        document_url,
        identity=identity,
    )
    plan = prepared.plan
    if plan.plan_id != plan_id:
        return ApiResult(
            ok=False,
            status=409,
            error="同步计划已过期：本地文件或远端 revision 已变化，请重新预览",
        )
    if plan.conflicts:
        return ApiResult(ok=False, status=409, error="；".join(plan.conflicts))
    blocking_error = _blocking_warning_error(plan)
    if blocking_error:
        return ApiResult(ok=False, status=409, error=blocking_error)
    _apply_prepared_push(lark_cli, prepared, identity=identity)
    return ApiResult(
        ok=True,
        status=200,
        data=SyncApplyResult(
            plan_id=plan.plan_id,
            operation="push",
            changed=1,
            local_paths=(str(prepared.markdown_file),),
            document_urls=(prepared.document_url,),
            assets_changed=len(prepared.images) + len(prepared.board_operations),
            asset_paths=tuple(str(image.path) for image in prepared.images)
            + tuple(str(op.image.board.path) for op in prepared.board_operations),
            warnings=plan.warnings,
        ),
    )


def _normalize_table_separators(text: str) -> str:
    result: list[str] = []
    in_code_block = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~~")):
            in_code_block = not in_code_block
        if not in_code_block and re.fullmatch(
            r"\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|?\s*\n?",
            line,
        ):
            count = len(line.strip().strip("|").split("|"))
            ending = "\n" if line.endswith("\n") else ""
            line = "|" + "|".join("-" for _ in range(count)) + "|" + ending
        result.append(line)
    return "".join(result)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", _normalize_table_separators(text)).strip()


def compare_file(
    lark_cli: str,
    markdown_file: str | Path,
    document_url: str | None = None,
    *,
    identity: str = "user",
    document_urls: Mapping[Path, str] | None = None,
) -> ApiResult[DiffResultData]:
    """比较本地 Markdown 正文与远端 Markdown 导出。"""
    path = _absolute(markdown_file)
    if path.is_symlink() or not path.is_file():
        return ApiResult(ok=False, status=404, error=f"本地 Markdown 不存在：{path}")
    resolved_url = resolve_document_url(path, document_url)
    local_body = preprocess_markdown(
        path.read_text(encoding="utf-8"),
        source_path=path,
        document_urls=document_urls,
    )
    local = prepare_markdown_resources(local_body, path)
    remote = _fetch_snapshot(
        lark_cli,
        resolved_url,
        path,
        identity=identity,
        include_front_matter=False,
    )
    text_changed = _normalize_text(local.comparison_markdown) != _normalize_text(
        remote.comparison_markdown
    )
    local_hashes = tuple(image.digest for image in local.images)
    remote_hashes = tuple(
        asset.semantic_digest or asset.digest
        for asset in remote.assets
        if asset.role != "board_source"
    )
    assets_changed = local_hashes != remote_hashes
    changed = text_changed or assets_changed
    diff_lines = None
    if text_changed:
        diff_lines = list(
            difflib.unified_diff(
                remote.comparison_markdown.splitlines(keepends=True),
                local.comparison_markdown.splitlines(keepends=True),
                fromfile="remote (feishu)",
                tofile="local (.md)",
                lineterm="",
            )
        )
    return ApiResult(
        ok=True,
        status=200,
        data=DiffResultData(
            identical=not changed,
            changes=int(changed),
            diff_lines=diff_lines,
            detail=DiffDetail(
                text_changed=text_changed,
                assets_changed=assets_changed,
                size_remote=len(remote.comparison_markdown),
                size_local=len(local.comparison_markdown),
                remote_assets=len(remote.assets),
                local_assets=len(local.images),
            ),
            warnings=remote.warnings,
        ),
    )


def _sanitize_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return value or "untitled"


def _wiki_url(root_url: str, node_token: str) -> str:
    return build_document_url(
        "wiki",
        node_token,
        base_url=base_url_from_document(root_url),
    )


def _collect_wiki_nodes(
    lark_cli: str,
    root_url: str,
    *,
    identity: str,
    max_depth: int | None,
    page_limit: int,
) -> tuple[list[tuple[dict[str, Any], tuple[str, ...]]], list[SyncWarning]]:
    report(f"遍历 Wiki 文档树：{root_url}")
    root = get_wiki_node(lark_cli, root_url, identity=identity)
    space_id = str(root.get("space_id", ""))
    if not space_id:
        raise ValueError("根文档不是可遍历的 Wiki 节点，无法同步子文档")
    collected: list[tuple[dict[str, Any], tuple[str, ...]]] = [(root, ())]
    warnings: list[SyncWarning] = []

    def walk(parent: dict[str, Any], parents: tuple[str, ...], depth: int) -> None:
        if max_depth is not None and depth >= max_depth:
            return
        if not parent.get("has_child"):
            return
        report(f"读取子页面目录：{parent.get('title', parent.get('node_token', ''))}")
        children = list_wiki_children(
            lark_cli,
            space_id,
            str(parent["node_token"]),
            identity=identity,
            page_limit=page_limit,
        )
        names = [
            _sanitize_filename(str(item.get("title") or "untitled"))
            for item in children
        ]
        duplicates = {name for name in names if names.count(name) > 1}
        for child, base_name in zip(children, names, strict=True):
            token = str(child.get("node_token", ""))
            segment = (
                f"{base_name}-{token[-6:]}" if base_name in duplicates else base_name
            )
            child_parents = (*parents, segment)
            collected.append((child, child_parents))
            walk(child, child_parents, depth + 1)

    walk(root, (), 0)
    report(f"文档树遍历完成：{len(collected)} 个节点")
    return collected, warnings


def _markdown_candidates(root: Path) -> list[Path]:
    return sorted({*root.rglob("*.md"), *root.rglob("*.markdown")})


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _local_document_title(path: Path) -> str:
    metadata, body = split_front_matter(path.read_text(encoding="utf-8"))
    declared = metadata.get("title", "").strip()
    if declared:
        return declared
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", line)
        if match:
            return match.group(1).strip()
    return path.stem


def _choose_local_root_file(
    root: Path,
    candidates: list[Path],
    remote_title: str,
    preferred: Path | None,
) -> tuple[Path | None, list[str]]:
    if preferred is not None:
        return preferred, []
    top_level = [path for path in candidates if path.parent == root]
    title_key = _normalize_title(remote_title)
    title_matches = [
        path
        for path in top_level
        if _normalize_title(_local_document_title(path)) == title_key
        or _normalize_title(path.stem)
        == _normalize_title(_sanitize_filename(remote_title))
    ]
    if len(title_matches) == 1:
        return title_matches[0], []
    if len(title_matches) > 1:
        return None, [
            "无法确定 Wiki 根文档：多个顶层 Markdown 的标题或文件名与远端根标题相同"
        ]
    index_pages = [
        path for path in top_level if path.stem.casefold() in {"readme", "index"}
    ]
    if len(index_pages) == 1:
        return index_pages[0], []
    if len(top_level) == 1:
        return top_level[0], []
    return None, [
        "无法确定 Wiki 根文档：请使用 README.md、index.md，或让唯一的顶层文档标题"
        f"与远端根标题“{remote_title}”一致"
    ]


def _companion_title(parent: Path, segment: str) -> str:
    for suffix in (".md", ".markdown"):
        companion = parent / f"{segment}{suffix}"
        if companion.is_file() and not companion.is_symlink():
            return _local_document_title(companion)
    child_dir = parent / segment
    for name in ("README.md", "README.markdown", "index.md", "index.markdown"):
        index_page = child_dir / name
        if index_page.is_file() and not index_page.is_symlink():
            return _local_document_title(index_page)
    return segment


def _local_hierarchy_key(path: Path, base: Path) -> tuple[str, ...]:
    relative = path.relative_to(base)
    titles: list[str] = []
    parent = base
    for segment in relative.parent.parts:
        titles.append(_normalize_title(_companion_title(parent, segment)))
        parent /= segment
    if path.stem.casefold() not in {"readme", "index"}:
        titles.append(_normalize_title(_local_document_title(path)))
    return tuple(titles)


def _discover_hierarchy_documents(
    lark_cli: str,
    directory: str | Path,
    root_document_url: str,
    *,
    identity: str,
    max_depth: int | None,
    page_limit: int,
    preferred_root: Path | None = None,
) -> tuple[Path, list[_MappedDocument], list[SyncWarning], list[str]]:
    root = _absolute(directory)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"文档树目录不存在或不是普通目录：{root}")

    candidates = _markdown_candidates(root)
    warnings: list[SyncWarning] = []
    conflicts: list[str] = []
    safe_candidates: list[Path] = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            warnings.append(
                SyncWarning(
                    code="unsafe_tree_path",
                    message="跳过符号链接或非普通 Markdown 文件",
                    path=str(path),
                    blocking=True,
                )
            )
        else:
            safe_candidates.append(path)
    if not safe_candidates:
        raise FileNotFoundError(f"{root} 中没有 Markdown 文件")

    canonical_root_url = _canonical_document_url(root_document_url)
    nodes, remote_warnings = _collect_wiki_nodes(
        lark_cli,
        canonical_root_url,
        identity=identity,
        max_depth=max_depth,
        page_limit=page_limit,
    )
    warnings.extend(remote_warnings)
    root_node = nodes[0][0]
    if str(root_node.get("obj_type", "")) != "docx":
        conflicts.append("Wiki 根节点不是 docx，不能与本地 Markdown 建立映射")
    root_file, root_conflicts = _choose_local_root_file(
        root,
        safe_candidates,
        str(root_node.get("title") or "untitled"),
        preferred_root,
    )
    conflicts.extend(root_conflicts)
    if root_file is None:
        return root, [], warnings, conflicts

    companion_directory = root / root_file.stem
    if root_file.stem.casefold() in {"readme", "index"}:
        hierarchy_base = root
    elif companion_directory.is_dir() and not companion_directory.is_symlink():
        hierarchy_base = companion_directory
    else:
        hierarchy_base = root

    local_by_key: dict[tuple[str, ...], Path] = {}
    for path in safe_candidates:
        if path == root_file:
            continue
        try:
            key = _local_hierarchy_key(path, hierarchy_base)
        except ValueError:
            warnings.append(
                SyncWarning(
                    code="outside_tree_root",
                    message=(f"不在根文档对应目录 {hierarchy_base} 中，未参与层级映射"),
                    path=str(path),
                )
            )
            continue
        if not key:
            conflicts.append(f"{path} 与根文档使用了相同的层级位置")
        elif key in local_by_key:
            conflicts.append(f"{path} 与 {local_by_key[key]} 在同一层级使用了相同标题")
        else:
            local_by_key[key] = path

    remote_by_key: dict[tuple[str, ...], tuple[dict[str, Any], str]] = {}
    for index, (node, segments) in enumerate(nodes):
        if index == 0:
            continue
        obj_type = str(node.get("obj_type", ""))
        if obj_type != "docx":
            warnings.append(
                SyncWarning(
                    code="unsupported_wiki_object",
                    message=(
                        f"跳过 {obj_type or 'unknown'} 节点："
                        f"{node.get('title', node.get('node_token', ''))}"
                    ),
                )
            )
            continue
        key = tuple(_normalize_title(item) for item in segments[:-1]) + (
            _normalize_title(str(node.get("title") or "untitled")),
        )
        if key in remote_by_key:
            conflicts.append(
                "远端 Wiki 同一层级存在重名 docx 节点，无法只按目录和标题安全映射："
                f"{node.get('title', 'untitled')}"
            )
            continue
        remote_by_key[key] = (
            node,
            _wiki_url(canonical_root_url, str(node.get("node_token", ""))),
        )

    documents = [_MappedDocument(root_file, canonical_root_url)]
    for key, path in sorted(local_by_key.items(), key=lambda item: str(item[1])):
        remote = remote_by_key.pop(key, None)
        if remote is None:
            conflicts.append(f"{path} 在远端 Wiki 的对应层级中没有同标题 docx 节点")
            continue
        documents.append(_MappedDocument(path, remote[1]))
    for node, _ in remote_by_key.values():
        warnings.append(
            SyncWarning(
                code="remote_document_unmapped",
                message="远端 docx 节点没有对应的本地 Markdown，已跳过",
                path=str(node.get("title") or node.get("node_token", "")),
            )
        )
    return root, documents, warnings, conflicts


def _tree_pull_plan_internal(
    lark_cli: str,
    root_url: str,
    directory: str | Path,
    *,
    identity: str,
    max_depth: int | None,
    page_limit: int,
    include_front_matter: bool,
) -> tuple[SyncPlan, list[_TreeDocument]]:
    root_dir = _absolute(directory)
    nodes, warnings = _collect_wiki_nodes(
        lark_cli,
        root_url,
        identity=identity,
        max_depth=max_depth,
        page_limit=page_limit,
    )
    root_name = _sanitize_filename(str(nodes[0][0].get("title") or "untitled"))
    documents: list[_TreeDocument] = []
    actions: list[SyncAction] = []
    conflicts: list[str] = []
    entries: list[dict[str, Any]] = []

    for index, (node, segments) in enumerate(nodes):
        obj_type = str(node.get("obj_type", ""))
        node_token = str(node.get("node_token", ""))
        if obj_type != "docx":
            warnings.append(
                SyncWarning(
                    code="unsupported_wiki_object",
                    message=(
                        f"跳过 {obj_type or 'unknown'} 节点："
                        f"{node.get('title', node_token)}"
                    ),
                )
            )
            continue
        if index == 0:
            relative = Path(f"{root_name}.md")
            document_url = _canonical_document_url(root_url)
        else:
            parent_segments = segments[:-1]
            leaf = segments[-1]
            relative = Path(root_name, *parent_segments, f"{leaf}.md")
            document_url = _wiki_url(root_url, node_token)
        target = root_dir / relative
        report(f"页面 {index + 1}/{len(nodes)}：{node.get('title', node_token)}")
        snapshot = _fetch_snapshot(
            lark_cli,
            document_url,
            target,
            identity=identity,
            include_front_matter=include_front_matter,
        )
        documents.append(_TreeDocument(relative, snapshot))
        conflicts.extend(_target_conflicts(target))
        for asset in snapshot.assets:
            conflicts.extend(_target_conflicts(asset.path))
            conflicts.extend(_native_pull_conflicts(asset))
        warnings.extend(snapshot.warnings)
        current_hash = _file_hash(target)
        actions.append(
            SyncAction(
                action="write_local_markdown",
                local_path=str(target),
                document_url=snapshot.document_url,
                remote_revision=snapshot.revision_id,
                overwrites=current_hash is not None,
            )
        )
        actions.extend(
            SyncAction(
                action="write_local_board_source"
                if asset.role == "board_source"
                else "write_local_image",
                local_path=str(asset.path),
                document_url=snapshot.document_url,
                remote_revision=snapshot.revision_id,
                overwrites=_file_hash(asset.path) is not None,
            )
            for asset in snapshot.assets
        )
        entries.append(
            {
                "path": relative.as_posix(),
                "url": snapshot.document_url,
                "revision": snapshot.revision_id,
                "remote_hash": snapshot.raw_hash,
                "rendered_hash": _bytes_hash(snapshot.markdown.encode("utf-8")),
                "local_before_hash": current_hash,
                "assets": [
                    {
                        "path": str(asset.path),
                        "remote_hash": asset.digest,
                        "local_before_hash": _file_hash(asset.path),
                    }
                    for asset in snapshot.assets
                ],
            }
        )

    plan = SyncPlan(
        plan_id=_json_hash(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "operation": "tree_pull",
                "directory": str(root_dir),
                "root_document_url": _canonical_document_url(root_url),
                "max_depth": max_depth,
                "include_front_matter": include_front_matter,
                "entries": entries,
            }
        ),
        operation="tree_pull",
        target=str(root_dir),
        actions=tuple(actions),
        risks=(
            "将逐个原子替换已有 Markdown 和引用图片；不删除已不在远端树中的本地文件",
        ),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )
    return plan, documents


def download_tree(
    lark_cli: str, root_url: str, directory: str | Path, *, identity: str = "user",
    max_depth: int | None = None, page_limit: int = 10, include_front_matter: bool = True,
) -> ApiResult[SyncApplyResult]:
    """Read and save each page once; retain completed files if a later page fails."""
    root_dir = _absolute(directory)
    nodes, warnings = _collect_wiki_nodes(
        lark_cli, root_url, identity=identity, max_depth=max_depth, page_limit=page_limit)
    root_name = _sanitize_filename(str(nodes[0][0].get("title") or "untitled"))
    paths, urls, assets, page_plans = [], [], [], []

    def completed():
        return SyncApplyResult(
            plan_id=_json_hash(page_plans), operation="tree_pull", changed=len(paths),
            local_paths=tuple(paths), document_urls=tuple(urls),
            assets_changed=len(assets), asset_paths=tuple(assets), warnings=tuple(warnings))

    for index, (node, segments) in enumerate(nodes):
        token = str(node.get("node_token", ""))
        if node.get("obj_type") != "docx":
            warnings.append(SyncWarning(code="unsupported_wiki_object",
                message=f"跳过 {node.get('obj_type', 'unknown')} 节点：{node.get('title', token)}"))
            continue
        relative = (Path(f"{root_name}.md") if index == 0 else
                    Path(root_name, *segments[:-1], f"{segments[-1]}.md"))
        url = _canonical_document_url(root_url) if index == 0 else _wiki_url(root_url, token)
        report(f"下载并保存 {index + 1}/{len(nodes)}：{node.get('title', token)}")
        try:
            result = apply_pull_file(lark_cli, url, root_dir / relative,
                                    identity=identity, include_front_matter=include_front_matter)
        except (LarkCliError, OSError, ValueError, RuntimeError) as exc:
            result = ApiResult(ok=False, status=500, error=str(exc))
        if not result.ok:
            return ApiResult(ok=False, status=result.status, data=completed(),
                error=f"拉取 {url} 失败：{result.error}；已保存 {len(paths)} 篇，失败页可能有部分资源已写入")
        page = result.data
        paths.extend(page.local_paths)
        urls.extend(page.document_urls)
        assets.extend(page.asset_paths)
        warnings.extend(page.warnings)
        page_plans.append(page.plan_id)
    return ApiResult(ok=True, status=200, data=completed())


def plan_tree_pull(
    lark_cli: str,
    root_url: str,
    directory: str | Path,
    *,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    include_front_matter: bool = True,
) -> ApiResult[SyncPlan]:
    """只读生成 Wiki 文档树拉取计划。"""
    plan, _ = _tree_pull_plan_internal(
        lark_cli,
        root_url,
        directory,
        identity=identity,
        max_depth=max_depth,
        page_limit=page_limit,
        include_front_matter=include_front_matter,
    )
    return ApiResult(ok=True, status=200, data=plan)


def apply_tree_pull(
    lark_cli: str,
    root_url: str,
    directory: str | Path,
    plan_id: str,
    *,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    include_front_matter: bool = True,
) -> ApiResult[SyncApplyResult]:
    """重新校验并逐篇写入 Wiki 文档树。"""
    plan, documents = _tree_pull_plan_internal(
        lark_cli,
        root_url,
        directory,
        identity=identity,
        max_depth=max_depth,
        page_limit=page_limit,
        include_front_matter=include_front_matter,
    )
    if plan.plan_id != plan_id:
        return ApiResult(
            ok=False,
            status=409,
            error="文档树同步计划已过期；节点、本地文件或远端 revision 已变化",
        )
    if plan.conflicts:
        return ApiResult(ok=False, status=409, error="；".join(plan.conflicts))
    root_dir = _absolute(directory)
    for index, document in enumerate(documents, 1):
        report(f"写入 {index}/{len(documents)}：{document.relative_path}（{len(document.snapshot.assets)} 个资源）")
        for asset in document.snapshot.assets:
            _atomic_write(asset.path, asset.content)
        _atomic_write(
            root_dir / document.relative_path,
            document.snapshot.markdown.encode("utf-8"),
        )
    return ApiResult(
        ok=True,
        status=200,
        data=SyncApplyResult(
            plan_id=plan.plan_id,
            operation="tree_pull",
            changed=len(documents),
            local_paths=tuple(str(root_dir / item.relative_path) for item in documents),
            document_urls=tuple(item.snapshot.document_url for item in documents),
            assets_changed=sum(len(item.snapshot.assets) for item in documents),
            asset_paths=tuple(
                str(asset.path) for item in documents for asset in item.snapshot.assets
            ),
            warnings=plan.warnings,
        ),
    )


def _discover_mapped_documents(
    directory: str | Path,
) -> tuple[Path, list[_MappedDocument], list[SyncWarning], list[str]]:
    root = _absolute(directory)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"文档树目录不存在或不是普通目录：{root}")
    candidates = _markdown_candidates(root)
    documents: list[_MappedDocument] = []
    warnings: list[SyncWarning] = []
    conflicts: list[str] = []
    tokens: dict[str, Path] = {}

    for path in candidates:
        if path.is_symlink() or not path.is_file():
            warnings.append(
                SyncWarning(
                    code="unsafe_tree_path",
                    message="跳过符号链接或非普通 Markdown 文件",
                    path=str(path),
                    blocking=True,
                )
            )
            continue
        declared = _frontmatter_url(path)
        if not declared:
            warnings.append(
                SyncWarning(
                    code="missing_document_mapping",
                    message="没有 feishu_url/lark_url front matter，已跳过",
                    path=str(path),
                )
            )
            continue
        url = _canonical_document_url(declared)
        token = parse_doc_id(url).token
        if token in tokens:
            conflicts.append(f"{path} 与 {tokens[token]} 指向同一远端文档")
            continue
        tokens[token] = path
        documents.append(_MappedDocument(path, url))

    if not documents:
        raise FileNotFoundError(
            f"{root} 中没有带 feishu_url/lark_url front matter 的 Markdown"
        )
    return root, documents, warnings, conflicts


def _implicit_hierarchy_root(
    directory: str | Path,
) -> tuple[str, Path] | None:
    root = _absolute(directory)
    if root.is_symlink() or not root.is_dir():
        return None
    mapped: list[tuple[Path, str]] = []
    for path in _markdown_candidates(root):
        declared = _frontmatter_url(path)
        if declared:
            mapped.append((path, declared))
    if len(mapped) != 1:
        return None
    root_file, root_url = mapped[0]
    companion = root / root_file.stem
    if root_file.parent != root or not companion.is_dir() or companion.is_symlink():
        return None
    if not _markdown_candidates(companion):
        return None
    return root_url, root_file


def _tree_push_plan_internal(
    lark_cli: str,
    directory: str | Path,
    *,
    identity: str,
    root_document_url: str | None,
    max_depth: int | None,
    page_limit: int,
) -> tuple[SyncPlan, list[_PreparedPush]]:
    implicit = None if root_document_url else _implicit_hierarchy_root(directory)
    if root_document_url or implicit:
        hierarchy_url = root_document_url or implicit[0]
        root, documents, warnings, conflicts = _discover_hierarchy_documents(
            lark_cli,
            directory,
            hierarchy_url,
            identity=identity,
            max_depth=max_depth,
            page_limit=page_limit,
            preferred_root=None if implicit is None else implicit[1],
        )
    else:
        root, documents, warnings, conflicts = _discover_mapped_documents(directory)
    document_urls = {
        document.path.resolve(): document.document_url for document in documents
    }
    prepared_items: list[_PreparedPush] = []
    component_ids: list[str] = []
    actions: list[SyncAction] = []
    for document in documents:
        prepared = _push_plan_internal(
            lark_cli,
            document.path,
            document.document_url,
            identity=identity,
            document_urls=document_urls,
        )
        prepared_items.append(prepared)
        component_ids.append(prepared.plan.plan_id)
        actions.extend(prepared.plan.actions)
        warnings.extend(prepared.plan.warnings)
        conflicts.extend(f"{document.path}: {item}" for item in prepared.plan.conflicts)
    plan = SyncPlan(
        plan_id=_json_hash(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "operation": "tree_push",
                "root": str(root),
                "component_plan_ids": component_ids,
            }
        ),
        operation="tree_push",
        target=str(root),
        actions=tuple(actions),
        risks=("远端文档会逐篇覆盖；执行中断可能已有部分文档成功",),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )
    return plan, prepared_items


def plan_tree_push(
    lark_cli: str,
    directory: str | Path,
    *,
    identity: str = "user",
    root_document_url: str | None = None,
    max_depth: int | None = None,
    page_limit: int = 10,
) -> ApiResult[SyncPlan]:
    """根据 front matter 或根 Wiki URL 生成文档树推送计划。"""
    plan, _ = _tree_push_plan_internal(
        lark_cli,
        directory,
        identity=identity,
        root_document_url=root_document_url,
        max_depth=max_depth,
        page_limit=page_limit,
    )
    return ApiResult(ok=True, status=200, data=plan)


def apply_tree_push(
    lark_cli: str,
    directory: str | Path,
    plan_id: str,
    *,
    identity: str = "user",
    root_document_url: str | None = None,
    max_depth: int | None = None,
    page_limit: int = 10,
) -> ApiResult[SyncApplyResult]:
    """重新扫描 front matter 并执行文档树推送计划。"""
    plan, prepared_items = _tree_push_plan_internal(
        lark_cli,
        directory,
        identity=identity,
        root_document_url=root_document_url,
        max_depth=max_depth,
        page_limit=page_limit,
    )
    if plan.plan_id != plan_id:
        return ApiResult(ok=False, status=409, error="文档树推送计划已过期，请重新预览")
    if plan.conflicts:
        return ApiResult(ok=False, status=409, error="；".join(plan.conflicts))
    blocking_error = _blocking_warning_error(plan)
    if blocking_error:
        return ApiResult(ok=False, status=409, error=blocking_error)
    paths: list[str] = []
    urls: list[str] = []
    asset_paths: list[str] = []
    for prepared in prepared_items:
        try:
            _apply_prepared_push(lark_cli, prepared, identity=identity)
        except (LarkCliError, OSError) as exc:
            return ApiResult(
                ok=False,
                status=500,
                error=f"已推送 {len(paths)} 篇后失败：{exc}",
            )
        paths.append(str(prepared.markdown_file))
        urls.append(prepared.document_url)
        asset_paths.extend(str(image.path) for image in prepared.images)
        asset_paths.extend(str(op.image.board.path) for op in prepared.board_operations)
    return ApiResult(
        ok=True,
        status=200,
        data=SyncApplyResult(
            plan_id=plan.plan_id,
            operation="tree_push",
            changed=len(paths),
            local_paths=tuple(paths),
            document_urls=tuple(urls),
            assets_changed=len(asset_paths),
            asset_paths=tuple(asset_paths),
            warnings=plan.warnings,
        ),
    )


def tree_status(
    lark_cli: str,
    directory: str | Path,
    *,
    identity: str = "user",
    root_document_url: str | None = None,
    max_depth: int | None = None,
    page_limit: int = 10,
) -> ApiResult[TreeStatus]:
    """根据 front matter 或根 Wiki URL 比较文档树。"""
    implicit = None if root_document_url else _implicit_hierarchy_root(directory)
    if root_document_url or implicit:
        hierarchy_url = root_document_url or implicit[0]
        root, documents, warnings, conflicts = _discover_hierarchy_documents(
            lark_cli,
            directory,
            hierarchy_url,
            identity=identity,
            max_depth=max_depth,
            page_limit=page_limit,
            preferred_root=None if implicit is None else implicit[1],
        )
    else:
        root, documents, warnings, conflicts = _discover_mapped_documents(directory)
    if conflicts:
        return ApiResult(ok=False, status=409, error="；".join(conflicts))
    document_urls = {
        document.path.resolve(): document.document_url for document in documents
    }
    items: list[TreeStatusItem] = []
    for document in documents:
        result = compare_file(
            lark_cli,
            document.path,
            document.document_url,
            identity=identity,
            document_urls=document_urls,
        )
        if not result.ok or result.data is None:
            warnings.append(
                SyncWarning(
                    code="tree_status_failed",
                    message=result.error or "文档比较失败",
                    path=str(document.path),
                )
            )
            continue
        warnings.extend(result.data.warnings)
        items.append(
            TreeStatusItem(
                local_path=str(document.path),
                document_url=document.document_url,
                identical=result.data.identical,
                changes=result.data.changes,
            )
        )
    return ApiResult(
        ok=True,
        status=200,
        data=TreeStatus(root=str(root), items=tuple(items), warnings=tuple(warnings)),
    )
