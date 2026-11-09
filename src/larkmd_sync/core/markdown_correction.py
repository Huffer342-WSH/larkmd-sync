"""Correct exported Markdown before resource materialization; currently bold only."""

import re
import unicodedata

_BOLD = re.compile(r"(?<![\\*])\*\*(?!\*)([^\n]+?)(?<![\\*])\*\*(?!\*)")
_CODE = re.compile(r"(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)")
_FENCE = re.compile(r"^[ \t]{0,3}(?:>[ \t]*)*(`{3,}|~{3,})")


def _punctuation(char: str) -> bool:
    return unicodedata.category(char)[0] in "PS"


def correct_markdown(markdown: str) -> str:
    """Add spaces only where punctuation prevents ** from opening or closing."""
    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if fence:
            output.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                if not line[marker.end():].strip():
                    fence = None
            continue
        if marker:
            fence = marker[1]
            output.append(line)
            continue
        if line.startswith(("    ", "\t")):
            output.append(line)
            continue

        code = [m.span() for m in _CODE.finditer(line)]
        def replace(match: re.Match[str]) -> str:
            start, end = match.span()
            if any(start < b and end > a for a, b in code):
                return match[0]
            body = match[1]
            # Leave whitespace-delimited or nested emphasis to the original exporter.
            if body[0].isspace() or body[-1].isspace() or "*" in body:
                return match[0]
            before = line[start - 1] if start else " "
            after = line[end] if end < len(line) else " "
            prefix = " " if _punctuation(body[0]) and not (before.isspace() or _punctuation(before)) else ""
            suffix = " " if _punctuation(body[-1]) and not (after.isspace() or _punctuation(after)) else ""
            return prefix + match[0] + suffix

        output.append(_BOLD.sub(replace, line))
    return "".join(output)
