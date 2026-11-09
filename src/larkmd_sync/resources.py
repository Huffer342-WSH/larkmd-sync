"""在标准 Markdown 与飞书图片、Mermaid 画板之间转换。"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .core.board_assets import BoardAsset, json_bytes, load_board, nodes_hash
from .core.callouts import LocalCallout, materialize_callouts, prepare_callouts
from .core.text_colors import color_spans
from .core.urls import FILE_TYPE_PATHS, build_document_url

_IMAGE_RE = re.compile(
    r"!\[(?P<alt>(?:\\.|[^\]\\])*)\]"
    r"\(\s*(?P<destination><[^>\n]+>|(?:\\.|[^\s)])+)"
    r"(?:\s+(?P<title>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'))?\s*\)"
)
_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)")
_REMOTE_IMAGE_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE)
_REMOTE_WHITEBOARD_RE = re.compile(
    r"<whiteboard\b[^>]*(?:/>|>\s*</whiteboard>)",
    re.IGNORECASE,
)
_REMOTE_TABLE_RE = re.compile(r"<table\b.*?</table>\r?\n?", re.IGNORECASE | re.DOTALL)
_REMOTE_CHECKBOX_RE = re.compile(
    r"<checkbox\b[^>]*\bdone=[\"'](?P<done>true|false)[\"'][^>]*(?:/>|>\s*</checkbox>)",
    re.IGNORECASE,
)
_ATTRIBUTE_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_:-]*)\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
_UNSAFE_FILENAME_RE = re.compile(r"[\x00-\x1f\\/:*?\"<>|\s]+")
_SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
SUPPORTED_MERMAID_DIAGRAM_TYPES = (
    "flowchart",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "erDiagram",
    "gantt",
    "pie",
    "mindmap",
)
_MERMAID_DECLARATION_RE = re.compile(
    r"^(?P<type>flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|"
    r"erDiagram|gantt|pie|mindmap)(?=\s|$)"
)


@dataclass(frozen=True, slots=True)
class LocalImage:
    """一张准备上传的本地 Markdown 图片。"""

    path: Path
    alt: str
    marker: str
    digest: str
    insert_after: bool = False
    sequence_marker: str = ""
    resume_sequence: int | None = None
    board: BoardAsset | None = None


@dataclass(frozen=True, slots=True)
class PreparedMarkdown:
    """本地 Markdown 的上传正文和语义比较正文。"""

    upload_markdown: str
    comparison_markdown: str
    images: tuple[LocalImage, ...]
    callouts: tuple[LocalCallout, ...]
    mermaid_count: int
    mermaid_types: tuple[str, ...]
    color_normalizations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    """从飞书读取到内存中的一张图片。"""

    content: bytes
    suffix: str = ""


@dataclass(frozen=True, slots=True)
class WhiteboardSource:
    """画板中可恢复的源码。"""

    format: str
    source: str
    converted: bool = False


@dataclass(frozen=True, slots=True)
class LocalAsset:
    """拉取时将写入本地的资源。"""

    path: Path
    content: bytes
    digest: str
    role: str = "image"
    semantic_digest: str = ""


@dataclass(frozen=True, slots=True)
class ResourceWarning:
    """资源转换产生的结构化警告。"""

    code: str
    message: str
    blocking: bool = False


@dataclass(frozen=True, slots=True)
class MaterializedMarkdown:
    """已把远端资源转换为本地可浏览形式的 Markdown。"""

    markdown: str
    comparison_markdown: str
    assets: tuple[LocalAsset, ...]
    warnings: tuple[ResourceWarning, ...]
    handled_reference_types: frozenset[str]


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    return "\n" if line.endswith("\n") else ""


def _closing_fence(line: str, fence: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(fence) and not stripped[len(fence) :].strip()


def _canonical_mermaid(source: str, ending: str = "\n") -> str:
    return f"```mermaid{ending}{source.rstrip()}{ending}```{ending}"


def _upload_mermaid(source: str, ending: str = "\n") -> str:
    if "</whiteboard>" in source.lower():
        raise ValueError("Mermaid 源码不能包含 </whiteboard> 标签")
    return (
        f'<whiteboard type="mermaid">{ending}'
        f"{source.rstrip()}{ending}</whiteboard>{ending}"
    )


def detect_mermaid_diagram_type(source: str) -> str | None:
    """识别已验证支持的 Mermaid 图表声明。"""
    for line in source.splitlines():
        declaration = line.strip()
        if not declaration or declaration.startswith("%%"):
            continue
        match = _MERMAID_DECLARATION_RE.match(declaration)
        if match is None:
            return None
        diagram_type = match.group("type")
        return "stateDiagram" if diagram_type == "stateDiagram-v2" else diagram_type
    return None


def _local_image_path(
    destination: str,
    source_path: Path,
    asset_root: Path,
) -> Path | None:
    raw = destination[1:-1] if destination.startswith("<") else destination
    parsed = urlsplit(raw)
    if parsed.scheme in {"http", "https"}:
        return None
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"图片只支持本地相对路径或 HTTP(S) URL：{destination}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"本地图片路径不能包含 query 或 fragment：{destination}")
    decoded = unquote(parsed.path)
    if not decoded or Path(decoded).is_absolute():
        raise ValueError(f"本地图片必须使用相对路径：{destination}")
    candidate = source_path.parent / decoded
    if candidate.is_symlink():
        raise ValueError(f"拒绝上传符号链接图片：{candidate}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"本地图片不存在：{candidate}") from exc
    try:
        resolved.relative_to(asset_root)
    except ValueError as exc:
        raise ValueError(
            f"本地图片必须位于 Markdown 同目录或子目录：{candidate}"
        ) from exc
    if not resolved.is_file():
        raise ValueError(f"本地图片不是普通文件：{candidate}")
    if resolved.suffix.lower() not in _SUPPORTED_IMAGE_SUFFIXES and not resolved.name.endswith(".board.svg"):
        raise ValueError(
            "本地图片只支持 PNG、JPEG、GIF、WebP 或 BMP：" f"{candidate}"
        )
    return resolved


def prepare_markdown_resources(
    markdown: str,
    source_path: str | Path,
) -> PreparedMarkdown:
    """解析本地图片和 Mermaid，并生成飞书上传正文。"""
    source = Path(source_path).resolve()
    asset_root = source.parent.resolve()
    upload: list[str] = []
    comparison: list[str] = []
    images: list[LocalImage] = []
    mermaid_count = 0
    mermaid_types: list[str] = []
    color_normalizations: list[str] = []
    markdown = color_spans(markdown, normalized=color_normalizations)
    prepared_callouts = prepare_callouts(markdown)
    lines = prepared_callouts.upload_markdown.splitlines(keepends=True)
    sequence_markers_by_line: dict[int, list[str]] = {}
    index = 0

    while index < len(lines):
        line = lines[index]
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            info = fence_match.group("info").strip().split(maxsplit=1)
            end = index + 1
            while end < len(lines) and not _closing_fence(lines[end], fence):
                end += 1
            if end >= len(lines):
                if info and info[0].lower() == "mermaid":
                    raise ValueError("Mermaid fenced code block 未闭合")
                upload.extend(lines[index:])
                comparison.extend(lines[index:])
                break
            block = lines[index : end + 1]
            if info and info[0].lower() == "mermaid":
                source_code = "".join(block[1:-1]).rstrip("\r\n")
                if not source_code.strip():
                    raise ValueError("Mermaid fenced code block 不能为空")
                ending = _line_ending(line) or "\n"
                upload.append(_upload_mermaid(source_code, ending))
                comparison.append(_canonical_mermaid(source_code, ending))
                mermaid_count += 1
                diagram_type = detect_mermaid_diagram_type(source_code)
                if diagram_type is not None:
                    mermaid_types.append(diagram_type)
            else:
                upload.extend(block)
                comparison.extend(block)
            index = end + 1
            continue

        matches = list(_IMAGE_RE.finditer(line))
        local_matches: list[tuple[re.Match[str], Path]] = []
        for match in matches:
            image_path = _local_image_path(
                match.group("destination"),
                source,
                asset_root,
            )
            if image_path is not None:
                local_matches.append((match, image_path))
        if local_matches:
            if len(local_matches) != 1 or line.strip() != local_matches[0][0].group(0):
                raise ValueError("本地图片必须单独占一行，不能作为行内图片或列表子项")
            match, image_path = local_matches[0]
            indent = line[: match.start()]
            insert_after = False
            sequence_marker = ""
            resume_sequence = None
            if indent:
                while upload and not upload[-1].strip():
                    upload.pop()
                if upload and re.match(r"^[ \t]{0,3}\d+[.)][ \t]+", upload[-1]):
                    previous = upload[-1]
                    previous_ending = _line_ending(previous)
                    previous_body = (
                        previous[: -len(previous_ending)]
                        if previous_ending
                        else previous
                    )
                    insert_after = True
            content = image_path.read_bytes()
            board = load_board(image_path)
            digest = board.digest if board else _digest(content)
            image_number = len(images) + 1
            marker = (
                f"LARKMD_SYNC_IMAGE_{image_number:04d}_"
                f"{digest.removeprefix('sha256:')[:16]}"
            )
            if insert_after:
                expected_indent = indent[:-3]
                for candidate_index in range(index + 1, len(lines)):
                    candidate = lines[candidate_index]
                    if not candidate.strip():
                        continue
                    ordered = re.match(
                        r"^(?P<indent>[ \t]{0,3})(?P<number>\d+)[.)][ \t]+",
                        candidate,
                    )
                    if (
                        ordered
                        and ordered.group("indent") == expected_indent
                        and candidate_index not in sequence_markers_by_line
                    ):
                        resume_sequence = int(ordered.group("number"))
                        sequence_marker = (
                            f"LARKMD_SYNC_SEQUENCE_{image_number:04d}_"
                            f"{digest.removeprefix('sha256:')[:16]}"
                        )
                        sequence_markers_by_line.setdefault(
                            candidate_index, []
                        ).append(sequence_marker)
                    break
            images.append(
                LocalImage(
                    path=image_path,
                    alt=match.group("alt").replace("\\]", "]"),
                    marker=marker,
                    digest=digest,
                    insert_after=insert_after,
                    sequence_marker=sequence_marker,
                    resume_sequence=resume_sequence,
                    board=board,
                )
            )
            ending = _line_ending(line)
            if insert_after:
                upload[-1] = f"{previous_body} {marker}{previous_ending}"
            else:
                upload.append(f"{indent}{marker}{ending}")
            comparison.append(
                f"{indent}LARKMD_SYNC_IMAGE_{image_number:04d}{ending}"
            )
        else:
            upload_line = line
            for sequence_marker in sequence_markers_by_line.get(index, ()):
                ending = _line_ending(upload_line)
                body = upload_line[: -len(ending)] if ending else upload_line
                upload_line = f"{body} {sequence_marker}{ending}"
            upload.append(upload_line)
            comparison.append(line)
        index += 1

    comparison_markdown = materialize_callouts(
        "".join(comparison),
        {callout.marker: callout.kind for callout in prepared_callouts.callouts},
    ).markdown
    return PreparedMarkdown(
        upload_markdown=color_spans(restore_user_mentions("".join(upload)), upload=True),
        comparison_markdown=comparison_markdown,
        images=tuple(images),
        callouts=prepared_callouts.callouts,
        mermaid_count=mermaid_count,
        mermaid_types=tuple(mermaid_types),
        color_normalizations=tuple(dict.fromkeys(color_normalizations)),
    )


def _attributes(tag: str) -> dict[str, str]:
    return {
        match.group("name").lower(): html.unescape(match.group("value"))
        for match in _ATTRIBUTE_RE.finditer(tag)
    }


def _image_suffix(content: bytes, declared: str) -> str:
    suffix = declared.lower()
    if suffix in _SUPPORTED_IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith((b"\xff\xd8\xff",)):
        return ".jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if content.startswith(b"BM"):
        return ".bmp"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return ""


def _safe_filename(name: str, suffix: str, index: int) -> str:
    raw_name = Path(name).name if name else ""
    stem = _UNSAFE_FILENAME_RE.sub("_", Path(raw_name).stem).strip("._")
    stem = stem.encode("utf-8")[:160].decode("utf-8", errors="ignore")
    declared_suffix = Path(raw_name).suffix.lower()
    if declared_suffix == ".jpeg":
        declared_suffix = ".jpg"
    actual_suffix = suffix or (
        declared_suffix if declared_suffix in _SUPPORTED_IMAGE_SUFFIXES else ""
    )
    return f"{stem or f'image-{index}'}{actual_suffix or '.img'}"


def _unique_filename(name: str, used: set[str]) -> str:
    candidate = name
    stem = Path(name).stem
    suffix = Path(name).suffix
    number = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{number}{suffix}"
        number += 1
    used.add(candidate.casefold())
    return candidate


def _markdown_alt(value: str) -> str:
    return value.replace("\\", "\\\\").replace("]", "\\]")


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", r"\|").replace("\n", "<br>")


class _CheckboxTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._current_row = []
        elif lowered in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
        elif lowered == "checkbox" and self._current_cell is not None:
            done = dict(attrs).get("done", "false").lower() == "true"
            self._current_cell.append("[x]" if done else "[ ]")
        elif lowered == "br" and self._current_cell is not None:
            self._current_cell.append("<br>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_row is not None:
            text = "".join(self._current_cell or []).strip()
            self._current_row.append(text)
            self._current_cell = None
        elif lowered == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _remote_checkbox_table_to_markdown(match: re.Match[str]) -> str:
    raw = match.group(0)
    if not _REMOTE_CHECKBOX_RE.search(raw):
        return raw
    parser = _CheckboxTableParser()
    parser.feed(raw)
    rows = [row for row in parser.rows if row]
    if not rows:
        return raw
    width = max(len(row) for row in rows)
    padded = [[*row, *("" for _ in range(width - len(row)))] for row in rows]
    rendered = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in padded[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    rendered.extend(
        "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
        for row in padded[1:]
    )
    return "\n".join(rendered) + "\n"


def materialize_remote_tables(markdown: str) -> str:
    """把含 checkbox 的飞书 HTML 表格恢复为可回写的 GFM 表格。"""
    return _REMOTE_TABLE_RE.sub(_remote_checkbox_table_to_markdown, markdown)


def restore_user_mentions(markdown: str) -> str:
    def replace(match):
        return '<cite type="user" user-id="' + match[2] + '"></cite>'
    pattern = r"\[@((?:\\.|[^\]])*)\]\(https://applink\.feishu\.cn/client/profile/open\?openId=(ou_[A-Za-z0-9]+)\)"
    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        found = _FENCE_RE.match(line)
        if fence:
            output.append(line)
            if _closing_fence(line, fence):
                fence = None
        elif found:
            fence = found.group("fence")
            output.append(line)
        else:
            parts = re.split(r"(`+[^`]*`+)", line)
            for i in range(0, len(parts), 2):
                parts[i] = re.sub(pattern, replace, parts[i])
            output.append("".join(parts))
    return "".join(output)


def materialize_remote_links(markdown: str, base_url: str) -> str:
    """Turn document mentions into ordinary readable links without dropping targets."""
    def replace(match: re.Match[str]) -> str:
        attrs = _attributes(match.group())
        if attrs.get("type") == "user" and re.fullmatch(r"ou_[A-Za-z0-9]+", attrs.get("user-id", "")):
            name = _markdown_alt(attrs.get("user-name") or "用户").replace("[", r"\[")
            return f"[@{name}](https://applink.feishu.cn/client/profile/open?openId={attrs['user-id']})"
        if attrs.get("type") != "doc":
            return match.group()
        kind = attrs.get("file-type", "")
        token = attrs.get("doc-id", "")
        if kind not in FILE_TYPE_PATHS or not re.fullmatch(r"[A-Za-z0-9]+", token):
            return match.group()
        label = _markdown_alt(attrs.get("title") or token).replace("[", r"\[")
        return f"[{label}]({build_document_url(kind, token, base_url=base_url)})"

    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        found = _FENCE_RE.match(line)
        if fence:
            output.append(line)
            if _closing_fence(line, fence):
                fence = None
            continue
        if found:
            fence = found.group("fence")
            output.append(line)
            continue
        # Inline code is literal, just like fenced examples.
        parts = re.split(r"(`+[^`]*`+)", line)
        for i in range(0, len(parts), 2):
            parts[i] = re.sub(r"<cite\b[^>]*(?:/>|>\s*</cite>)", replace, parts[i])
            parts[i] = re.sub(r"<title>(.*?)</title>", lambda m: "# " + html.unescape(m[1]), parts[i])
        output.append("".join(parts))
    return "".join(output)


def feishu_media_token(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    trusted = any(host == d or host.endswith("." + d) for d in ("feishu.cn", "larksuite.com", "larkoffice.com"))
    match = re.fullmatch(r"/file/([A-Za-z0-9]+)", parsed.path)
    return match[1] if parsed.scheme == "https" and trusted and match else None


def materialize_remote_resources(
    markdown: str,
    target_path: str | Path,
    *,
    download_media: Callable[[str], DownloadedMedia],
    download_url: Callable[[str], DownloadedMedia] | None = None,
    export_whiteboard: Callable[[str], WhiteboardSource | None],
    download_whiteboard: Callable[[str], DownloadedMedia] | None = None,
    download_whiteboard_svg: Callable[[str, str], DownloadedMedia] | None = None,
    document_id: str = "",
    document_url: str = "",
) -> MaterializedMarkdown:
    """把远端图片和 Mermaid 画板恢复成本地 Markdown 与资源文件。"""
    target = Path(target_path).resolve()
    assets_dir = target.parent / ".assets" / target.stem
    assets_relative = assets_dir.relative_to(target.parent).as_posix()
    markdown = materialize_remote_tables(markdown)
    assets: list[LocalAsset] = []
    warnings: list[ResourceWarning] = []
    handled: set[str] = set()
    used_names: set[str] = set()
    rendered: list[str] = []
    comparison: list[str] = []
    lines = markdown.splitlines(keepends=True)
    in_code_block = False

    def store_image(
        media: DownloadedMedia,
        *,
        name: str,
        alt: str,
        ending: str,
    ) -> tuple[str, str] | None:
        image_number = sum(a.role != "board_source" for a in assets) + 1
        suffix = _image_suffix(media.content, media.suffix)
        if not suffix:
            return None
        filename = _unique_filename(
            _safe_filename(name, suffix, image_number),
            used_names,
        )
        asset_path = assets_dir / filename
        assets.append(
            LocalAsset(
                path=asset_path,
                content=media.content,
                digest=_digest(media.content),
            )
        )
        relative = f"./{assets_relative}/{filename}"
        return (
            f"![{_markdown_alt(alt or Path(filename).stem)}]({relative}){ending}",
            f"LARKMD_SYNC_IMAGE_{image_number:04d}{ending}",
        )

    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            in_code_block = not in_code_block
            rendered.append(line)
            comparison.append(line)
            continue
        if in_code_block:
            rendered.append(line)
            comparison.append(line)
            continue

        stripped = line.strip()
        markdown_image = _IMAGE_RE.fullmatch(stripped)
        if markdown_image:
            destination = markdown_image.group("destination")
            raw_destination = (
                destination[1:-1] if destination.startswith("<") else destination
            )
            parsed = urlsplit(raw_destination)
            if parsed.scheme in {"http", "https"}:
                if download_url is None and feishu_media_token(raw_destination) is None:
                    warnings.append(
                        ResourceWarning(
                            code="image_download_unavailable",
                            message="没有配置远端 Markdown 图片下载器",
                            blocking=True,
                        )
                    )
                    rendered.append(line)
                    comparison.append(line)
                    continue
                try:
                    token = feishu_media_token(raw_destination)
                    media = download_media(token) if token else download_url(raw_destination)
                except Exception as exc:  # noqa: BLE001 - warning boundary
                    warnings.append(
                        ResourceWarning(
                            code="image_download_failed",
                            message=f"远端图片下载失败：{exc}",
                            blocking=True,
                        )
                    )
                    rendered.append(line)
                    comparison.append(line)
                    continue
                ending = _line_ending(line) or "\n"
                stored = store_image(
                    media,
                    name=markdown_image.group("alt"),
                    alt=markdown_image.group("alt"),
                    ending=ending,
                )
                if stored is None:
                    warnings.append(
                        ResourceWarning(
                            code="unsupported_remote_image",
                            message="远端 Markdown 图片格式无法识别",
                            blocking=True,
                        )
                    )
                    rendered.append(line)
                    comparison.append(line)
                    continue
                local_markdown, semantic_markdown = stored
                rendered.append(local_markdown)
                comparison.append(semantic_markdown)
                handled.update({"img", "image"})
                continue

        image_match = _REMOTE_IMAGE_RE.fullmatch(stripped)
        if image_match:
            attrs = _attributes(stripped)
            token = attrs.get("token") or attrs.get("src")
            if not token:
                warnings.append(
                    ResourceWarning(
                        code="image_missing_token",
                        message="远端图片没有可下载的 media token，已保留原始标签",
                        blocking=True,
                    )
                )
                rendered.append(line)
                comparison.append(line)
                continue
            try:
                media = download_media(token)
            except Exception as exc:  # noqa: BLE001 - resource error becomes warning
                warnings.append(
                    ResourceWarning(
                        code="image_download_failed",
                        message=f"远端图片 {token} 下载失败：{exc}",
                        blocking=True,
                    )
                )
                rendered.append(line)
                comparison.append(line)
                continue
            ending = _line_ending(line) or "\n"
            stored = store_image(
                media,
                name=attrs.get("name", ""),
                alt=attrs.get("caption") or attrs.get("alt", ""),
                ending=ending,
            )
            if stored is None:
                warnings.append(
                    ResourceWarning(
                        code="unsupported_remote_image",
                        message=f"远端图片 {token} 的格式无法识别，已保留原始标签",
                        blocking=True,
                    )
                )
                rendered.append(line)
                comparison.append(line)
                continue
            local_markdown, semantic_markdown = stored
            rendered.append(local_markdown)
            comparison.append(semantic_markdown)
            handled.update({"img", "image"})
            continue

        board_match = _REMOTE_WHITEBOARD_RE.fullmatch(stripped)
        if board_match:
            attrs = _attributes(stripped)
            token = attrs.get("token") or attrs.get("board-token")
            if not token:
                warnings.append(
                    ResourceWarning(
                        code="whiteboard_missing_token",
                        message="远端画板没有 token，已保留原始标签",
                        blocking=True,
                    )
                )
                rendered.append(line)
                comparison.append(line)
                continue
            try:
                board = export_whiteboard(token)
            except Exception as exc:  # noqa: BLE001 - resource error becomes warning
                board = None
                export_failed = True
                warnings.append(
                    ResourceWarning(
                        code="whiteboard_export_failed",
                        message=f"远端画板 {token} 导出失败：{exc}",
                        blocking=True,
                    )
                )
            else:
                export_failed = False
            if board is not None and board.format == "native":
                if download_whiteboard is None:
                    raise ValueError("原生画板需要预览下载器")
                data = json.loads(board.source)
                nodes = sorted(data["nodes"], key=lambda node: node["id"])
                digest = nodes_hash(nodes)
                preview = (download_whiteboard_svg(token, _digest(json_bytes(nodes)))
                           if download_whiteboard_svg else download_whiteboard(token))
                suffix = ".svg" if download_whiteboard_svg else _image_suffix(preview.content, preview.suffix)
                if not suffix:
                    raise ValueError("原生画板预览不是支持的图片格式")
                number = sum(a.role == "board_source" for a in assets) + 1
                preview_path = assets_dir / f"画板-{number}.board{suffix}"
                source_path = preview_path.with_suffix(".json")
                bindings = {}
                if source_path.is_file() and not source_path.is_symlink():
                    old = json.loads(source_path.read_text())
                    bindings.update(old.get("bindings", {}))
                bindings[document_id] = {"token": token, "baseline_hash": digest}
                source = {
                    "schema_version": 1, "type": "feishu-whiteboard", "nodes": nodes,
                    "baseline_hash": digest, "bindings": bindings,
                    "origin_url": document_url, "preview_hash": _digest(preview.content),
                }
                image_number = sum(a.role != "board_source" for a in assets) + 1
                assets.append(LocalAsset(preview_path, preview.content, _digest(preview.content), "board_preview", digest))
                encoded = json_bytes(source)
                assets.append(LocalAsset(source_path, encoded, _digest(encoded), "board_source"))
                ending = _line_ending(line) or "\n"
                rendered.append(f"![原生画板](./{assets_relative}/{preview_path.name}){ending}")
                comparison.append(f"LARKMD_SYNC_IMAGE_{image_number:04d}{ending}")
                handled.add("whiteboard")
                continue
            if board is None or board.format.lower() != "mermaid":
                if not export_failed:
                    warnings.append(
                        ResourceWarning(
                            code="unsupported_whiteboard_source",
                            message=(
                                f"远端画板 {token} 不是可恢复的 Mermaid 画板，"
                                "已保留原始标签"
                            ),
                            blocking=True,
                        )
                    )
                if download_whiteboard is not None:
                    try:
                        preview = download_whiteboard(token)
                        stored = store_image(preview, name="画板预览", alt="画板预览（原生编辑结构未恢复）", ending=_line_ending(line) or "\n")
                        if stored is not None:
                            rendered.append(stored[0])
                            comparison.append(stored[1])
                            continue
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(ResourceWarning(code="whiteboard_preview_failed", message=f"画板预览下载失败：{exc}", blocking=True))
                rendered.append(line)
                comparison.append(line)
                continue
            ending = _line_ending(line) or "\n"
            if board.converted:
                warnings.append(ResourceWarning(code="whiteboard_layout_normalized", message="原生画板已转换为 Mermaid，保留节点、连线和分组；坐标、字体及折线路径由 Mermaid 重新布局", blocking=False))
            canonical = _canonical_mermaid(board.source, ending)
            rendered.append(canonical)
            comparison.append(canonical)
            handled.add("whiteboard")
            continue

        rendered.append(line)
        comparison.append(line)

    return MaterializedMarkdown(
        markdown="".join(rendered),
        comparison_markdown="".join(comparison),
        assets=tuple(assets),
        warnings=tuple(warnings),
        handled_reference_types=frozenset(handled),
    )
