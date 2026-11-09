"""在可浏览的 Markdown Alert 与飞书高亮块之间转换。"""

from __future__ import annotations

import copy
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass

_ALERT_START_RE = re.compile(
    r"^[ \t]{0,3}>[ \t]*\[!(?P<kind>NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]"
    r"[ \t]*(?:\r?\n)?$",
    re.IGNORECASE,
)
_QUOTED_LINE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?(?P<body>.*?)(?P<ending>\r?\n)?$")
_REMOTE_CALLOUT_RE = re.compile(
    r"^<callout(?P<attrs>[^>]*)>[ \t]*\r?\n(?P<body>.*?)"
    r"^</callout>[ \t]*(?:\r?\n|$)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_ATTRIBUTE_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_:-]*)\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
_UNSUPPORTED_BODY_RE = re.compile(
    r"^(?:```|~~~|\|.*\||!\[|<\s*(?:img|table|pre|grid|whiteboard|sheet)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CalloutStyle:
    """一种本地 Alert 对应的飞书高亮块样式。"""

    emoji: str
    background_color: str
    border_color: str


CALLOUT_STYLES: dict[str, CalloutStyle] = {
    "NOTE": CalloutStyle("📝", "light-blue", "blue"),
    "TIP": CalloutStyle("💡", "light-green", "green"),
    "IMPORTANT": CalloutStyle("❗", "light-purple", "purple"),
    "WARNING": CalloutStyle("📌", "light-yellow", "yellow"),
    "CAUTION": CalloutStyle("❌", "light-red", "red"),
}

_EMOJI_KINDS = {
    "📝": "NOTE",
    "ℹ️": "NOTE",
    "💡": "TIP",
    "✅": "TIP",
    "❗": "IMPORTANT",
    "‼️": "IMPORTANT",
    "⚠️": "WARNING",
    "📌": "WARNING",
    "❌": "CAUTION",
}


@dataclass(frozen=True, slots=True)
class LocalCallout:
    """一次推送中需要在飞书端修复样式的高亮块。"""

    kind: str
    marker: str


@dataclass(frozen=True, slots=True)
class PreparedCallouts:
    """转换后的上传正文、比较正文和高亮块列表。"""

    upload_markdown: str
    comparison_markdown: str
    callouts: tuple[LocalCallout, ...]


@dataclass(frozen=True, slots=True)
class MaterializedCallouts:
    """从飞书高亮块恢复的本地 Markdown。"""

    markdown: str
    count: int
    unknown_emojis: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalloutReplacement:
    """一次精确的飞书高亮块替换。"""

    block_id: str
    content: str
    kind: str


def _body_lines(lines: list[str]) -> list[str]:
    body: list[str] = []
    for line in lines:
        match = _QUOTED_LINE_RE.match(line)
        if match is None:
            raise ValueError("Markdown Alert 的每一行都必须以 > 开头")
        ending = match.group("ending") or ""
        body.append(f"{match.group('body')}{ending}")
    return body


def _validate_body(lines: list[str]) -> None:
    for line in lines:
        if _UNSUPPORTED_BODY_RE.match(line.lstrip()):
            raise ValueError(
                "飞书高亮块暂不支持图片、代码块、表格、Mermaid 或其他资源块"
            )


def _canonical_alert(kind: str, body: list[str]) -> str:
    rendered = [f"> [!{kind}]\n"]
    for line in body:
        value = line.rstrip("\r\n")
        rendered.append(">\n" if not value else f"> {value}\n")
    return "".join(rendered)


def prepare_callouts(markdown: str) -> PreparedCallouts:
    """把本地 Markdown Alert 转成飞书 Markdown 可识别的高亮块标签。"""
    lines = markdown.splitlines(keepends=True)
    upload: list[str] = []
    comparison: list[str] = []
    callouts: list[LocalCallout] = []
    in_code_block = False
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip()
        if stripped.startswith(("```", "~~~")):
            in_code_block = not in_code_block
            upload.append(lines[index])
            comparison.append(lines[index])
            index += 1
            continue
        match = None if in_code_block else _ALERT_START_RE.match(lines[index])
        if match is None:
            upload.append(lines[index])
            comparison.append(lines[index])
            index += 1
            continue

        end = index + 1
        quoted: list[str] = []
        while end < len(lines) and _QUOTED_LINE_RE.match(lines[end]):
            quoted.append(lines[end])
            end += 1
        body = _body_lines(quoted)
        if not any(line.strip() for line in body):
            raise ValueError("Markdown Alert 不能为空")
        _validate_body(body)
        kind = match.group("kind").upper()
        digest = hashlib.sha256(
            (kind + "\n" + "".join(body)).encode("utf-8")
        ).hexdigest()[:16]
        marker = f"LARKMD_SYNC_CALLOUT_{len(callouts) + 1:04d}_{digest}"
        callouts.append(LocalCallout(kind=kind, marker=marker))
        upload.append("<callout>\n")
        upload.extend(body)
        if body and not body[-1].endswith(("\n", "\r")):
            upload.append("\n")
        upload.append(f"\n{marker}\n</callout>\n")
        comparison.append(_canonical_alert(kind, body))
        index = end
    return PreparedCallouts(
        upload_markdown="".join(upload),
        comparison_markdown="".join(comparison),
        callouts=tuple(callouts),
    )


def _attributes(raw: str) -> dict[str, str]:
    return {
        match.group("name").lower(): html.unescape(match.group("value"))
        for match in _ATTRIBUTE_RE.finditer(raw)
    }


def _kind_for_emoji(emoji: str) -> tuple[str, bool]:
    if not emoji:
        return "NOTE", False
    kind = _EMOJI_KINDS.get(emoji)
    return (kind, False) if kind else ("NOTE", True)


def materialize_callouts(
    markdown: str,
    kinds_by_marker: Mapping[str, str] | None = None,
) -> MaterializedCallouts:
    """把飞书 Markdown 中的高亮块恢复为 GitHub Alert。"""
    count = 0
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        attrs = _attributes(match.group("attrs"))
        emoji = attrs.get("emoji", "")
        body_text = match.group("body")
        marker = next(
            (
                candidate
                for candidate in (kinds_by_marker or {})
                if candidate in body_text
            ),
            "",
        )
        if marker:
            kind = (kinds_by_marker or {})[marker]
            is_unknown = False
            body_text = re.sub(
                rf"^[ \t]*{re.escape(marker)}[ \t]*(?:\r?\n|$)",
                "",
                body_text,
                flags=re.MULTILINE,
            )
        else:
            kind, is_unknown = _kind_for_emoji(emoji)
        if is_unknown:
            unknown.append(emoji)
        body = body_text.strip("\r\n").splitlines(keepends=True)
        return _canonical_alert(kind, body)

    converted = _REMOTE_CALLOUT_RE.sub(replace, markdown)
    return MaterializedCallouts(
        markdown=converted,
        count=count,
        unknown_emojis=tuple(dict.fromkeys(unknown)),
    )


def _remove_marker(callout: ET.Element, marker: str) -> None:
    for parent in callout.iter():
        for child in list(parent):
            if "".join(child.itertext()).strip() == marker:
                parent.remove(child)
                return
    for node in callout.iter():
        if node.text and marker in node.text:
            node.text = node.text.replace(f" {marker}", "").replace(marker, "")
            return


def callout_replacements(
    document_xml: str,
    callouts: tuple[LocalCallout, ...],
) -> tuple[CalloutReplacement, ...]:
    """从完整块 XML 中生成带明确样式的高亮块替换。"""
    if not callouts:
        return ()
    try:
        root = ET.fromstring(f"<document>{document_xml}</document>")
    except ET.ParseError as exc:
        raise ValueError(f"飞书返回的完整文档 XML 无法解析：{exc}") from exc

    by_marker = {callout.marker: callout for callout in callouts}
    found: set[str] = set()
    replacements: list[CalloutReplacement] = []
    for remote in root.iter("callout"):
        text = "".join(remote.itertext())
        matches = [marker for marker in by_marker if marker in text]
        if not matches:
            continue
        if len(matches) != 1:
            raise ValueError("一个飞书高亮块中出现了多个临时标记")
        marker = matches[0]
        if marker in found:
            raise ValueError(f"飞书高亮块临时标记不唯一：{marker}")
        found.add(marker)
        block_id = remote.attrib.get("id", "")
        if not block_id:
            raise ValueError(f"飞书高亮块缺少 block ID：{marker}")

        local = by_marker[marker]
        style = CALLOUT_STYLES[local.kind]
        replacement = copy.deepcopy(remote)
        replacement.set("emoji", style.emoji)
        replacement.set("background-color", style.background_color)
        replacement.set("border-color", style.border_color)
        _remove_marker(replacement, marker)
        for node in replacement.iter():
            node.attrib.pop("id", None)
        replacements.append(
            CalloutReplacement(
                block_id=block_id,
                content=ET.tostring(replacement, encoding="unicode"),
                kind=local.kind,
            )
        )

    missing = set(by_marker) - found
    if missing:
        raise ValueError(
            "飞书文档中找不到高亮块临时标记：" + ", ".join(sorted(missing))
        )
    return tuple(replacements)
