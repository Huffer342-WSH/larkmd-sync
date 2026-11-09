"""推送 Markdown 前移除本地元数据并转换本地文档链接。"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

_FRONT_MATTER_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[([^\]\n]+)\]\(\s*(<[^>\n]+>|[^\s)]+)"
    r"(\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_CHECKBOX_CELL_RE = re.compile(r"^\[(?P<mark>[ xX])\]$")
_ORDERED_LIST_RE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<number>\d+)(?P<marker>[.)])(?P<space>[ \t]+)"
)
_STANDALONE_MEDIA_RE = re.compile(
    r"^\s*(?:!\[[^\n]*\]\([^\n]+\)|<img\b[^>]*?/?>|"
    r"LARKMD_SYNC_IMAGE_\d+)\s*$",
    re.IGNORECASE,
)


def split_front_matter(markdown: str) -> tuple[dict[str, str], str]:
    """读取顶层标量 front matter，并返回剩余 Markdown 正文。"""
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, markdown

    end = None
    for index, line in enumerate(lines[1:201], start=1):
        if line.strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        return {}, markdown

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        match = _FRONT_MATTER_KEY_RE.match(line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        metadata[match.group(1)] = value
    if not metadata:
        return {}, markdown
    return metadata, "".join(lines[end + 1 :]).lstrip("\r\n")


def _target_document_url(
    source_path: Path,
    destination: str,
    document_urls: Mapping[Path, str] | None = None,
) -> str | None:
    raw_destination = destination[1:-1] if destination.startswith("<") else destination
    parsed = urlsplit(raw_destination)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None

    decoded_path = unquote(parsed.path)
    if not decoded_path.lower().endswith((".md", ".markdown")):
        return None
    target = Path(decoded_path)
    if not target.is_absolute():
        target = source_path.parent / target
    target = target.resolve(strict=False)
    if document_urls:
        mapped_url = document_urls.get(target)
        if mapped_url:
            return mapped_url
    try:
        content = target.resolve(strict=True).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    metadata, _ = split_front_matter(content)
    value = metadata.get("feishu_url") or metadata.get("lark_url")
    if value and value.startswith(("https://", "http://")):
        return value
    return None


def rewrite_local_markdown_links(
    markdown: str,
    source_path: str | Path,
    *,
    document_urls: Mapping[Path, str] | None = None,
) -> str:
    """把指向本地 Markdown 的普通链接改为目标文件声明的云文档 URL。"""
    source = Path(source_path).resolve()
    result: list[str] = []
    in_code_block = False

    def replace_link(match: re.Match[str]) -> str:
        target_url = _target_document_url(
            source,
            match.group(2),
            document_urls,
        )
        if not target_url:
            return match.group(0)
        return f"[{match.group(1)}]({target_url}{match.group(3) or ''})"

    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~~")):
            in_code_block = not in_code_block
            result.append(line)
            continue
        result.append(
            line if in_code_block else _MARKDOWN_LINK_RE.sub(replace_link, line)
        )
    return "".join(result)


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", value)]


def _is_checkbox_table(lines: list[str]) -> bool:
    return any(
        _CHECKBOX_CELL_RE.match(cell)
        for line in lines
        for cell in _split_table_row(line)
    )


def _table_cell_xml(cell: str) -> str:
    match = _CHECKBOX_CELL_RE.match(cell)
    if match:
        done = "true" if match.group("mark").lower() == "x" else "false"
        return f'<checkbox done="{done}"></checkbox>'
    return html.escape(cell.replace(r"\|", "|"), quote=False)


def _checkbox_table_to_feishu_xml(lines: list[str]) -> str:
    header = _split_table_row(lines[0])
    rows = [_split_table_row(line) for line in lines[2:]]
    width = max([len(header), *(len(row) for row in rows)] or [0])
    all_rows = [header, *rows]
    rendered_rows: list[str] = []
    for row in all_rows:
        padded = [*row, *("" for _ in range(width - len(row)))]
        cells = "".join(
            f'<td vertical-align="top">{_table_cell_xml(cell)}</td>'
            for cell in padded
        )
        rendered_rows.append(f"<tr>{cells}</tr>")
    colgroup = "<colgroup>" + "".join("<col/>" for _ in range(width)) + "</colgroup>"
    return f"<table>{colgroup}<tbody>{''.join(rendered_rows)}</tbody></table>\n"


def rewrite_interrupted_ordered_lists(markdown: str) -> str:
    """把跨图片的有序列表改成飞书可连续导入的 Markdown。"""
    lines = markdown.splitlines(keepends=True)
    output: list[str] = []
    next_number_by_indent: dict[str, int] = {}
    in_code_block = False

    def followed_by_ordered_item(index: int, indent: str) -> bool:
        for candidate in lines[index + 1 :]:
            if not candidate.strip() or _STANDALONE_MEDIA_RE.match(candidate):
                continue
            match = _ORDERED_LIST_RE.match(candidate)
            return match is not None and match.group("indent") == indent
        return False

    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~~")):
            in_code_block = not in_code_block
            next_number_by_indent.clear()
            output.append(line)
            continue
        if in_code_block:
            output.append(line)
            continue

        match = _ORDERED_LIST_RE.match(line)
        if match:
            indent = match.group("indent")
            for nested_indent in tuple(next_number_by_indent):
                if len(nested_indent) > len(indent):
                    del next_number_by_indent[nested_indent]
            original_number = int(match.group("number"))
            number = next_number_by_indent.get(indent, original_number)
            next_number_by_indent[indent] = number + 1
            output.append(
                f'{indent}{number}{match.group("marker")}{match.group("space")}'
                f"{line[match.end():]}"
            )
            continue

        if not line.strip():
            output.append(line)
            continue

        if _STANDALONE_MEDIA_RE.match(line):
            containing_indent = next(
                (
                    indent
                    for indent in sorted(
                        next_number_by_indent,
                        key=len,
                        reverse=True,
                    )
                    if followed_by_ordered_item(index, indent)
                ),
                None,
            )
            if containing_indent is not None:
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                media = line.strip()
                output.append(f"{containing_indent}   {media}{ending}")
            else:
                output.append(line)
            continue

        next_number_by_indent.clear()
        output.append(line)

    return "".join(output)


def rewrite_checkbox_tables(markdown: str) -> str:
    """把包含复选框单元格的 GFM 表格转换为飞书可交互 checkbox 表格。"""
    lines = markdown.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 >= len(lines) or not _TABLE_SEPARATOR_RE.match(lines[index + 1]):
            output.append(lines[index])
            index += 1
            continue
        end = index + 2
        while end < len(lines) and "|" in lines[end].strip():
            end += 1
        block = [line.rstrip("\r\n") for line in lines[index:end]]
        if _is_checkbox_table(block):
            output.append(_checkbox_table_to_feishu_xml(block))
        else:
            output.extend(lines[index:end])
        index = end
    return "".join(output)


def preprocess_markdown(
    markdown: str,
    *,
    source_path: str | Path | None = None,
    document_urls: Mapping[Path, str] | None = None,
) -> str:
    """生成发送给飞书 Markdown 接口的正文。"""
    _, body = split_front_matter(markdown)
    body = rewrite_interrupted_ordered_lists(body)
    body = rewrite_checkbox_tables(body)
    if source_path is not None:
        body = rewrite_local_markdown_links(
            body,
            source_path,
            document_urls=document_urls,
        )
    return body
