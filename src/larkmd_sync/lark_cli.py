"""调用官方 lark-cli 的最小 Markdown 和 Wiki 接口。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.sax.saxutils import quoteattr

from .core.text_colors import restore_text_colors
from .resources import (
    DownloadedMedia,
    WhiteboardSource,
    detect_mermaid_diagram_type,
)


class LarkCliError(Exception):
    """lark-cli 调用失败。"""

    def __init__(self, message: str, exit_code: int = -1, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def _run(
    lark_cli: str,
    args: list[str],
    *,
    stdin_data: str | None = None,
    timeout: int = 120,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [lark_cli, *args]
    try:
        return subprocess.run(
            command,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise LarkCliError(f"lark-cli not found at: {lark_cli}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError(
            f"lark-cli timed out after {timeout}s: {' '.join(command)}"
        ) from exc


def _parse_json_output(result: subprocess.CompletedProcess[str]) -> dict:
    for source in (result.stdout, result.stderr):
        text = (source or "").strip()
        start = text.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _error(operation: str, result: subprocess.CompletedProcess[str], value: dict):
    error = value.get("error", {})
    message = error.get("message", str(error)) if isinstance(error, dict) else error
    return LarkCliError(
        f"{operation} failed: {message}",
        exit_code=result.returncode,
        stderr=result.stderr,
    )


def fetch_markdown(
    lark_cli: str,
    doc: str,
    *,
    identity: str = "user",
    detail: str = "simple",
) -> dict:
    """以 Markdown 格式读取文档正文和 revision。"""
    result = _run(
        lark_cli,
        [
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--doc-format",
            "markdown",
            "--detail",
            detail,
            "--as",
            identity,
            "--format",
            "json",
        ],
    )
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("fetch_markdown", result, value)
    document = value.get("data", {}).get("document", {})
    document_xml = fetch_document_xml(lark_cli, doc, identity=identity,
                                      revision_id=document.get("revision_id", -1))
    colors = restore_text_colors(str(document.get("content", "")), document_xml)
    return {
        "content": colors.markdown,
        "document_xml": document_xml,
        "color_warnings": colors.warnings,
        "document_id": document.get("document_id", ""),
        "revision_id": document.get("revision_id", 0),
        "reference_map": document.get("reference_map", {}),
        "tips": document.get("tips", ""),
    }


def fetch_document_xml(
    lark_cli: str,
    doc: str,
    *,
    identity: str = "user",
    revision_id: int = -1,
) -> str:
    """Read full-detail XML for an internal, exact block repair."""
    result = _run(
        lark_cli,
        [
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--revision-id",
            str(revision_id),
            "--doc-format",
            "xml",
            "--detail",
            "full",
            "--as",
            identity,
            "--format",
            "json",
        ],
    )
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("fetch_document_xml", result, value)
    document = value.get("data", {}).get("document", {})
    return str(document.get("content", ""))


def overwrite_doc(
    lark_cli: str,
    doc: str,
    content: str,
    *,
    identity: str = "user",
) -> dict:
    """以 Markdown 格式整体替换文档正文。"""
    result = _run(
        lark_cli,
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--as",
            identity,
            "--content",
            "-",
        ],
        stdin_data=content,
    )
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("overwrite_doc", result, value)
    document = value.get("data", {}).get("document", {})
    return {
        "document_id": document.get("document_id", ""),
        "revision_id": document.get("revision_id", 0),
    }


def download_media(
    lark_cli: str,
    token: str,
    *,
    identity: str = "user",
    media_type: str = "media",
) -> DownloadedMedia:
    """把一张飞书文档图片下载到内存，不在工作区维护临时文件。"""
    with tempfile.TemporaryDirectory(prefix="larkmd-sync-media-") as directory:
        root = Path(directory)
        result = _run(
            lark_cli,
            [
                "docs",
                "+media-download",
                "--type",
                media_type,
                "--token",
                token,
                "--output",
                "./asset",
                "--as",
                identity,
                "--format",
                "json",
            ],
            timeout=180,
            cwd=root,
        )
        value = _parse_json_output(result)
        if not value.get("ok"):
            raise _error("download_media", result, value)
        files = [path for path in root.iterdir() if path.is_file()]
        if len(files) != 1:
            raise LarkCliError(
                "download_media failed: lark-cli did not create exactly one file"
            )
        path = files[0]
        return DownloadedMedia(content=path.read_bytes(), suffix=path.suffix.lower())


def download_image_url(url: str) -> DownloadedMedia:
    """下载飞书 Markdown 导出的短期图片 URL，并限制域名与大小。"""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    trusted_domains = ("feishu.cn", "larksuite.com", "larkoffice.com")
    if parsed.scheme != "https" or not any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in trusted_domains
    ):
        raise LarkCliError(f"refuse to download untrusted image URL: {hostname}")
    request = Request(url, headers={"User-Agent": "larkmd-sync/5"})
    limit = 25 * 1024 * 1024
    try:
        with urlopen(request, timeout=60) as response:
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > limit:
                raise LarkCliError("remote image exceeds the 25 MiB safety limit")
            content = response.read(limit + 1)
            content_type = response.headers.get_content_type()
    except LarkCliError:
        raise
    except Exception as exc:
        raise LarkCliError(f"download_image_url failed: {exc}") from exc
    if len(content) > limit:
        raise LarkCliError("remote image exceeds the 25 MiB safety limit")
    suffix = mimetypes.guess_extension(content_type) or Path(parsed.path).suffix
    return DownloadedMedia(content=content, suffix=suffix.lower())


def _walk_values(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def _source_format(value: str, source: str) -> str:
    lowered = value.lower()
    if "mermaid" in lowered:
        return "mermaid"
    if "plantuml" in lowered or source.lstrip().startswith("@startuml"):
        return "plantuml"
    if detect_mermaid_diagram_type(source) is not None:
        return "mermaid"
    first = source.lstrip().splitlines()[0].strip() if source.strip() else ""
    if first and (
        first.startswith(
            (
                "graph ",
                "flowchart ",
                "sequenceDiagram",
                "classDiagram",
                "stateDiagram",
                "erDiagram",
                "journey",
                "gantt",
                "pie",
                "mindmap",
                "timeline",
                "quadrantChart",
                "xychart",
                "sankey",
                "gitGraph",
                "C4",
            )
        )
        or first in {"graph", "flowchart"}
    ):
        return "mermaid"
    return lowered


def _whiteboard_source(value: dict[str, Any], stdout: str) -> WhiteboardSource | None:
    candidates: list[tuple[str, str]] = []
    for mapping in _walk_values(value.get("data", value)):
        source = next(
            (
                str(mapping[key])
                for key in ("source", "content", "code")
                if isinstance(mapping.get(key), str) and mapping[key].strip()
            ),
            "",
        )
        if not source:
            continue
        declared = next(
            (
                str(mapping[key])
                for key in ("source_type", "input_format", "format", "language", "type")
                if isinstance(mapping.get(key), str)
            ),
            "",
        )
        candidates.append((_source_format(declared, source), source))
    if candidates:
        format_name, source = max(candidates, key=lambda item: len(item[1]))
        return WhiteboardSource(format=format_name, source=source.rstrip())
    raw = stdout.strip()
    if raw and not raw.startswith("{"):
        return WhiteboardSource(format=_source_format("", raw), source=raw)
    return None


def export_whiteboard_source(
    lark_cli: str,
    token: str,
    *,
    identity: str = "user",
) -> WhiteboardSource | None:
    """导出仅包含一个 Mermaid/PlantUML 图的画板源码。"""
    result = _run(
        lark_cli,
        [
            "whiteboard",
            "+export",
            "--whiteboard-token",
            token,
            "--output-type",
            "source",
            "--as",
            identity,
            "--format",
            "json",
        ],
        timeout=180,
    )
    value = _parse_json_output(result)
    if result.returncode != 0 or value.get("ok") is False:
        raise _error("export_whiteboard_source", result, value)
    source = _whiteboard_source(value, result.stdout)
    if source is not None and source.format.lower() == "mermaid":
        return source
    result = _run(lark_cli, ["whiteboard", "+export", "--whiteboard-token", token,
                            "--output-type", "raw", "--as", identity, "--format", "json"], timeout=180)
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("export_whiteboard_raw", result, value)
    nodes = value.get("data", {}).get("nodes")
    if not isinstance(nodes, list):
        return None
    return WhiteboardSource(format="native", source=json.dumps({"nodes": nodes}, ensure_ascii=False))


def insert_local_image(
    lark_cli: str,
    document_id: str,
    image: Path,
    marker: str,
    *,
    caption: str = "",
    after: bool = False,
    identity: str = "user",
) -> dict:
    """在唯一占位内容之前或之后插入本地图片。"""
    root = ET.fromstring("<root>" + fetch_document_xml(
        lark_cli, document_id, identity=identity
    ) + "</root>")
    # Ordered-list image markers are appended to the preceding list item.
    blocks = []
    for node in root:
        if node.tag == "title":
            continue
        if node.tag in {"ol", "ul"}:
            blocks.extend(list(node))
        else:
            blocks.append(node)
    matches = []
    for index, node in enumerate(blocks):
        text = "".join(node.itertext())
        if (node.tag == "p" and text == marker) or (
            after and node.tag == "li" and text.rstrip().endswith(" " + marker)
        ):
            matches.append(index)
    if len(matches) != 1 or "".join(root.itertext()).count(marker) != 1:
        raise LarkCliError("图片占位必须唯一匹配一个完整段落或预期的列表项末尾")
    index = matches[0]
    anchor_node = blocks[index] if after else (blocks[index - 1] if index else None)
    anchor = anchor_node.attrib.get("id", "") if anchor_node is not None else "0"
    if not anchor:
        raise LarkCliError("图片插入位置缺少 block ID")
    content = f'<img path={quoteattr("@./" + image.name)}'
    if caption:
        content += f' caption={quoteattr(caption)}'
    content += "/>"
    args = ["docs", "+update", "--doc", document_id,
            "--command", "block_insert_after", "--block-id", anchor,
            "--content", "-", "--as", identity, "--format", "json"]
    result = _run(lark_cli, args, stdin_data=content, timeout=300, cwd=image.parent)
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("insert_local_image", result, value)
    data = value.get("data", {})
    return data if isinstance(data, dict) else {}


def remove_image_marker(
    lark_cli: str,
    document_id: str,
    marker: str,
    *,
    identity: str = "user",
) -> None:
    """删除图片插入完成后的唯一占位文本。"""
    result = _run(
        lark_cli,
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            document_id,
            "--command",
            "str_replace",
            "--doc-format",
            "markdown",
            "--pattern",
            marker,
            "--content",
            "",
            "--as",
            identity,
            "--format",
            "json",
        ],
    )
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("remove_image_marker", result, value)


def replace_block_xml(
    lark_cli: str,
    document_id: str,
    block_id: str,
    content: str,
    *,
    identity: str = "user",
) -> None:
    """Replace one block with exact XML content."""
    result = _run(
        lark_cli,
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            document_id,
            "--command",
            "block_replace",
            "--block-id",
            block_id,
            "--doc-format",
            "xml",
            "--content",
            "-",
            "--as",
            identity,
            "--format",
            "json",
        ],
        stdin_data=content,
    )
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error("replace_block_xml", result, value)


def get_wiki_node(
    lark_cli: str,
    node: str,
    *,
    identity: str = "user",
) -> dict:
    """读取 Wiki 节点元数据。"""
    result = _run(
        lark_cli,
        [
            "wiki",
            "+node-get",
            "--node-token",
            node,
            "--as",
            identity,
            "--format",
            "json",
        ],
    )
    value = _parse_json_output(result)
    if value.get("ok") is False:
        raise _error("get_wiki_node", result, value)
    data = value.get("data", value)
    if isinstance(data, dict) and isinstance(data.get("node"), dict):
        data = data["node"]
    if not isinstance(data, dict) or not data.get("node_token"):
        raise LarkCliError("get_wiki_node failed: lark-cli returned no node metadata")
    return data


def list_wiki_children(
    lark_cli: str,
    space_id: str,
    parent_node_token: str,
    *,
    identity: str = "user",
    page_limit: int = 10,
) -> list[dict]:
    """读取一个 Wiki 节点的全部直接子节点。"""
    result = _run(
        lark_cli,
        [
            "wiki",
            "+node-list",
            "--space-id",
            space_id,
            "--parent-node-token",
            parent_node_token,
            "--page-all",
            "--page-limit",
            str(page_limit),
            "--as",
            identity,
            "--format",
            "json",
        ],
    )
    value = _parse_json_output(result)
    if value.get("ok") is False:
        raise _error("list_wiki_children", result, value)
    data = value.get("data", value)
    nodes = data.get("nodes", []) if isinstance(data, dict) else []
    if not isinstance(nodes, list):
        raise LarkCliError("list_wiki_children failed: invalid nodes payload")
    if isinstance(data, dict) and data.get("has_more"):
        raise LarkCliError(
            "list_wiki_children stopped before all pages were read; increase page_limit"
        )
    return [item for item in nodes if isinstance(item, dict)]


def materialize_addon_mermaid(lark_cli: str, document: dict, *, identity: str = "user") -> str:
    """Recover known Mermaid add-ons omitted from Markdown exports.

    Match full XML block IDs in document order; never guess when counts differ.
    Unknown components remain untouched so push stays blocked.
    """
    markdown = str(document.get("content", ""))
    placeholder = re.compile(r'<readonly-block\b[^>]*type="isv"[^>]*(?:/>|>\s*</readonly-block>)')
    placeholders = list(placeholder.finditer(markdown))
    if not placeholders:
        return markdown
    xml = document.get("document_xml") or fetch_document_xml(lark_cli, document["document_id"], identity=identity)
    tags = re.findall(r'<readonly-block\b[^>]*type="isv"[^>]*(?:/>|>\s*</readonly-block>)', xml)
    if len(tags) != len(placeholders):
        return markdown
    replacements = []
    for tag, match in zip(tags, placeholders, strict=True):
        block_id = re.search(r'\bid="([A-Za-z0-9]+)"', tag)
        if not block_id:
            replacements.append(match.group())
            continue
        path = f"/open-apis/docx/v1/documents/{document['document_id']}/blocks/{block_id[1]}"
        result = _run(lark_cli, ["api", "GET", path, "--as", identity, "--format", "json"])
        value = _parse_json_output(result)
        if not value.get("ok"):
            raise _error("fetch_addon", result, value)
        addon = value.get("data", {}).get("block", {}).get("add_ons", {})
        # This is the Mermaid component verified against its native record.
        if addon.get("component_type_id") != "blk_631fefbbae02400430b8f9f4":
            replacements.append(match.group())
            continue
        try:
            source = json.loads(addon["record"])["data"]
            if not isinstance(source, str):
                raise ValueError("Mermaid record data is not a string")
            # Older component records contain a second JSON-string encoding.
            if "\\n" in source and "\n" not in source:
                source = json.loads('"' + source + '"')
            source = source.replace("\u00a0", " ").strip()
            if _source_format("", source) != "mermaid" or "```" in source:
                raise ValueError("Unrecognized Mermaid source")
        except (ValueError, KeyError, TypeError):
            replacements.append(match.group())
            continue
        replacements.append("```mermaid\n" + source + "\n```")
    iterator = iter(replacements)
    return placeholder.sub(lambda _: next(iterator), markdown)


def _validate_svg(content: bytes) -> None:
    if ET.fromstring(content).tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("export did not return an SVG document")


def export_whiteboard_svg(
    lark_cli: str, token: str, *, identity: str = "user", cache_key: str = ""
) -> DownloadedMedia:
    """Retry transient export timeouts; cache only against freshly read remote nodes."""
    cache_file = None
    if cache_key:
        scope = json.dumps([str(Path(lark_cli).resolve()), identity, token, cache_key])
        key = hashlib.sha256(scope.encode()).hexdigest()
        cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        cache_file = cache_root / "larkmd-sync" / "svg" / (key + ".svg")
        try:
            if time.time() - cache_file.stat().st_mtime < 86400:
                content = cache_file.read_bytes()
                _validate_svg(content)
                return DownloadedMedia(content, ".svg")
        except (OSError, ValueError, ET.ParseError):
            pass

    started = time.monotonic()
    for attempt in range(1, 4):
        try:
            with tempfile.TemporaryDirectory(prefix="larkmd-sync-svg-") as directory:
                result = _run(lark_cli, [
                    "whiteboard", "+export", "--whiteboard-token", token,
                    "--output-type", "svg", "--output", "board.svg", "--as", identity,
                    "--format", "json",
                ], cwd=directory, timeout=180)
                value = _parse_json_output(result)
                if not value.get("ok"):
                    raise _error("export_whiteboard_svg", result, value)
                content = (Path(directory) / "board.svg").read_bytes()
                _validate_svg(content)
        except (LarkCliError, OSError, ValueError, ET.ParseError) as exc:
            transient = isinstance(exc, LarkCliError) and any(
                word in str(exc).lower() for word in ("timeout", "time out", "timed out")
            )
            if transient and attempt < 3:
                time.sleep(attempt)
                continue
            elapsed = time.monotonic() - started
            raise LarkCliError(
                f"SVG export failed: board={token}, attempts={attempt}, elapsed={elapsed:.1f}s: {exc}",
                exit_code=getattr(exc, "exit_code", -1),
                stderr=getattr(exc, "stderr", ""),
            ) from exc
        break

    if cache_file is not None:
        temporary = None
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(dir=cache_file.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            temporary.replace(cache_file)
        except OSError:
            pass  # Cache availability must not turn a successful export into a failure.
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
    return DownloadedMedia(content, ".svg")
