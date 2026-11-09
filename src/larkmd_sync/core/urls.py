"""飞书/Lark 文档 URL 的租户无关处理。"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_LARK_BASE_URL = "https://www.feishu.cn"

FILE_TYPE_PATHS = {
    "wiki": "/wiki/",
    "docx": "/docx/",
    "doc": "/docx/",
    "sheets": "/sheets/",
    "bitable": "/base/",
    "base": "/base/",
    "dustbin": "/wiki/",
    "slides": "/slides/",
    "mindnotes": "/mindnotes/",
}


def normalize_base_url(value: str) -> str:
    """校验并规范化租户根地址。"""

    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"无效的飞书/Lark 根地址：{value}")
    return f"{parsed.scheme}://{parsed.netloc}"


def configured_base_url() -> str:
    """返回配置的租户地址；默认使用公共飞书入口。"""

    return normalize_base_url(os.environ.get("LARK_BASE_URL", DEFAULT_LARK_BASE_URL))


def base_url_from_document(document: str, *, fallback: str | None = None) -> str:
    """优先从完整文档 URL 提取租户地址。"""

    parsed = urlsplit(document.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return normalize_base_url(fallback) if fallback else configured_base_url()


def build_document_url(
    file_type: str,
    token: str,
    *,
    base_url: str | None = None,
) -> str:
    """使用指定或配置的租户地址构建文档链接。"""

    root = normalize_base_url(base_url) if base_url else configured_base_url()
    return f"{root}{FILE_TYPE_PATHS.get(file_type, '/wiki/')}{token}"
