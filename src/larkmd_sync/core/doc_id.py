"""文档 URL、token 和 UUID 的统一解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from .urls import base_url_from_document, build_document_url


class DocIdType(Enum):
    TOKEN = "token"
    DOCX = "docx"
    WIKI = "wiki"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class DocId:
    """解析后的文档标识符。"""

    raw: str
    token: str
    url: str = ""
    type: DocIdType = DocIdType.TOKEN
    is_valid: bool = True


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,}$")


def parse_doc_id(input_str: str, *, base_url: str | None = None) -> DocId:
    """解析任意飞书或 Lark 租户的文档 URL，也支持纯 token。"""

    raw = input_str.strip()
    parsed = urlsplit(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"docx", "wiki"}:
            doc_type = DocIdType.WIKI if parts[0] == "wiki" else DocIdType.DOCX
            return DocId(raw=raw, token=parts[1], url=raw, type=doc_type)
        if len(parts) >= 3 and parts[:2] == ["space", "doc"]:
            return DocId(raw=raw, token=parts[2], url=raw, type=DocIdType.UNKNOWN)

    if _TOKEN_PATTERN.fullmatch(raw):
        root = base_url_from_document(raw, fallback=base_url)
        return DocId(
            raw=raw,
            token=raw,
            url=build_document_url("docx", raw, base_url=root),
            type=DocIdType.TOKEN,
        )

    return DocId(raw=raw, token=raw, type=DocIdType.UNKNOWN, is_valid=False)
