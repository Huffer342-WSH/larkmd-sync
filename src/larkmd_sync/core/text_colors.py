"""CSS color spans in Markdown, mapped to Feishu text-color on upload."""

from __future__ import annotations

import copy
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# CSS names have CSS values, not the similarly named Feishu palette values.
_NAMES = {
    "red": "#ff0000",
    "orange": "#ffa500",
    "yellow": "#ffff00",
    "green": "#008000",
    "blue": "#0000ff",
    "purple": "#800080",
    "gray": "#808080",
    "grey": "#808080",
    "black": "#000000",
    "white": "#ffffff",
}
# Verified by exporting the seven native text colors from Feishu.
FEISHU_TEXT_PALETTE = (
    "#d83931",
    "#de7802",
    "#dc9b04",
    "#2ea121",
    "#245bdb",
    "#6425d0",
    "#8f959e",
)


def palette_color(color: str) -> str:
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    return min(
        FEISHU_TEXT_PALETTE,
        key=lambda c: sum(
            (v - int(c[i : i + 2], 16)) ** 2 for v, i in zip(rgb, (1, 3, 5))
        ),
    )


_ATTR = re.compile(r'([\w-]+)\s*=\s*([\'"])(.*?)\2', re.S)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "title"}


def normalize_color(value: str) -> str:
    value = value.strip().lower()
    if value in _NAMES:
        return _NAMES[value]
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", value
    )
    if match and all(int(v) <= 255 for v in match.groups()):
        return "#" + "".join(f"{int(v):02x}" for v in match.groups())
    raise ValueError(
        f"不支持的字体颜色：{value}；使用 #RGB、#RRGGBB、rgb(r,g,b) 或基础 CSS 色名"
    )


def color_spans(
    markdown: str, *, upload: bool = False, normalized: list[str] | None = None
) -> str:
    """Normalize color tags outside fenced/inline code, preserving nested styles."""

    def replace(match):
        attrs = {
            k.lower(): html.unescape(v) for k, _, v in _ATTR.findall(match.group())
        }
        if "style" not in attrs and "text-color" not in attrs:
            return match.group()
        if "text-color" in attrs:
            color = normalize_color(attrs["text-color"])
        else:
            declarations = [v.strip() for v in attrs["style"].split(";") if v.strip()]
            if len(declarations) != 1 or not re.match(
                r"^color\s*:", declarations[0], re.I
            ):
                raise ValueError("字体颜色 span 只支持一个 color 声明")
            color = normalize_color(declarations[0].split(":", 1)[1])
        target = palette_color(color)
        if target != color and normalized is not None:
            normalized.append(f"{color} → {target}")
        color = target
        if set(attrs) - {"style", "text-color"}:
            raise ValueError("字体颜色 span 含有暂不支持的属性")
        if upload:
            rgb = ",".join(str(int(color[i : i + 2], 16)) for i in (1, 3, 5))
            return f'<span text-color="rgb({rgb})">'
        return f'<span style="color:{color}">'

    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        found = _FENCE.match(line)
        if fence:
            output.append(line)
            if re.fullmatch(
                r"\s*" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",}\s*", line
            ):
                fence = None
            continue
        if found:
            fence = found[1]
            output.append(line)
            continue
        # Match same-length backtick delimiters, not the text inside them.
        cursor = 0
        for literal in re.finditer(
            r"(?P<ticks>`+)(?!`)(?P<body>.*?)(?<!`)(?P=ticks)(?!`)", line
        ):
            output.append(
                re.sub(
                    r"<span\b[^>]*>",
                    replace,
                    line[cursor : literal.start()],
                    flags=re.I,
                )
            )
            output.append(literal.group())
            cursor = literal.end()
        output.append(re.sub(r"<span\b[^>]*>", replace, line[cursor:], flags=re.I))
    return "".join(output)


@dataclass(frozen=True)
class RestoredColors:
    markdown: str
    warnings: tuple[str, ...] = ()
    count: int = 0


def _text(value: str | None, escaped: bool) -> str:
    value = value or ""
    if escaped:
        return re.sub(r"([\\`*_\[\]<>~])", r"\\\1", value)
    return value


def _inline(node: ET.Element, colored: bool) -> str:
    value = _text(node.text, colored)
    for child in node:
        if child.tag in {"ul", "ol"}:
            continue
        content = _inline(child, colored)
        if child.tag == "span":
            if colored and "text-color" in child.attrib:
                content = f'<span style="color:{normalize_color(child.attrib["text-color"])}">{content}</span>'
        elif child.tag in {"b", "strong"}:
            content = "**" + content + "**"
        elif child.tag in {"em", "i"}:
            content = "*" + content + "*"
        elif child.tag in {"del", "s"}:
            content = "~~" + content + "~~"
        elif child.tag == "a":
            content = "[" + content + "](" + child.attrib["href"] + ")"
        elif child.tag == "code":
            content = "`" + "".join(child.itertext()) + "`"
        elif child.tag == "br":
            content = "<br/>"
        elif child.tag == "cite":
            content = (
                ET.tostring(child, encoding="unicode").split("</cite>")[0] + "</cite>"
            )
        else:
            raise ValueError(f"暂不支持带颜色的 {child.tag} 行内结构")
        value += content + _text(child.tail, colored)
    return value


def restore_text_colors(markdown: str, document_xml: str) -> RestoredColors:
    """Restore colored runs in their containing text block, never by global text replacement.

    Match blocks in document order. Ambiguous or unsupported blocks are reported
    as blocking warnings, so color cannot silently move to an unrelated occurrence.
    """
    if "text-color=" not in document_xml:
        return RestoredColors(color_spans(markdown))
    root = ET.fromstring("<root>" + document_xml + "</root>")
    changes = []
    warnings = []
    table_nodes = {node for table in root.iter("table") for node in table.iter()}
    cursor = 0
    count = 0
    for block in root.iter():
        if block.tag not in _BLOCKS:
            continue
        if block in table_nodes:
            if block.tag in {"td", "th"} and any(
                "text-color" in node.attrib for node in block.iter()
            ):
                warnings.append("表格单元格字体颜色暂不能可靠恢复")
            continue
        # Nested list items get their own turn; don't count their colors twice.
        own = copy.deepcopy(block)
        for child in list(own):
            if child.tag in {"ul", "ol"}:
                own.remove(child)
        colors = [n for n in own.iter("span") if "text-color" in n.attrib]
        try:
            plain = _inline(own, False)
            if not plain.strip():
                continue
            # Exporters vary in escaping punctuation, but not in visible content.
            pattern = "".join(
                r"\\?" + re.escape(c) if c in r"\`*_[]<>~" else re.escape(c)
                for c in plain
            )
            candidates = list(re.finditer(pattern, markdown[cursor:]))
            # Require a text block start (heading/list/quote prefixes allowed).
            candidates = [
                m
                for m in candidates
                if re.fullmatch(
                    r"[ \t]*(?:(?:\d+[.)]|[-+*]|#{1,6}|>)[ \t]+)*",
                    markdown[
                        markdown.rfind("\n", 0, cursor + m.start()) + 1 : cursor
                        + m.start()
                    ],
                )
            ]
            if not candidates:
                if colors:
                    warnings.append("字体颜色未能定位到原 Markdown 文本块")
                continue
            # Repeated paragraphs can be mapped by prior blocks; an initial
            # ambiguous match is rejected rather than applying color to all.
            if colors and len(candidates) > 1:
                warnings.append("字体颜色对应多个相同文本块，未自动应用")
                continue
            match = candidates[0]
            start = cursor + match.start()
            end = cursor + match.end()
            cursor = end
            if colors:
                changes.append((start, end, _inline(own, True)))
                count += len(colors)
        except (ValueError, KeyError) as exc:
            if colors:
                warnings.append(str(exc))
    for start, end, value in reversed(changes):
        markdown = markdown[:start] + value + markdown[end:]
    return RestoredColors(color_spans(markdown), tuple(warnings), count)
