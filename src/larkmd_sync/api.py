"""larkmd-sync 的稳定公共 Python API。"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from pathlib import Path

from .config import find_lark_cli
from .lark_cli import LarkCliError
from .result import ApiResult
from .sync import (
    apply_pull_file,
    apply_push_file,
    apply_tree_pull,
    apply_tree_push,
    plan_pull_file,
    plan_push_file,
    plan_tree_pull,
    plan_tree_push,
)
from .sync import compare_file as compare_local_file
from .sync import tree_status as read_tree_status

Diagnostic = Callable[[str], None]


def _run_quietly(
    call: Callable[[], ApiResult], diagnostic: Diagnostic | None
) -> ApiResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = call()
    except FileNotFoundError as exc:
        result = ApiResult(ok=False, status=404, error=str(exc))
    except ValueError as exc:
        result = ApiResult(ok=False, status=422, error=str(exc))
    except (LarkCliError, RuntimeError, OSError) as exc:
        result = ApiResult(ok=False, status=500, error=str(exc))
    if diagnostic is not None:
        for line in (stdout.getvalue() + stderr.getvalue()).splitlines():
            if line.strip():
                diagnostic(line)
    return result


def pull_file(
    markdown_file: str | Path,
    document_url: str,
    *,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    include_front_matter: bool = True,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """预览或执行单篇文档拉取。"""
    return _run_quietly(
        lambda: (
            apply_pull_file(
                lark_cli or find_lark_cli(),
                document_url,
                markdown_file,
                plan_id,
                identity=identity,
                include_front_matter=include_front_matter,
            )
            if plan_id
            else plan_pull_file(
                lark_cli or find_lark_cli(),
                document_url,
                markdown_file,
                identity=identity,
                include_front_matter=include_front_matter,
            )
        ),
        diagnostic,
    )


def push_file(
    markdown_file: str | Path,
    document_url: str | None = None,
    *,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """预览或执行单篇 Markdown 推送。"""
    return _run_quietly(
        lambda: (
            apply_push_file(
                lark_cli or find_lark_cli(),
                markdown_file,
                plan_id,
                document_url,
                identity=identity,
            )
            if plan_id
            else plan_push_file(
                lark_cli or find_lark_cli(),
                markdown_file,
                document_url,
                identity=identity,
            )
        ),
        diagnostic,
    )


def compare_file(
    markdown_file: str | Path,
    document_url: str | None = None,
    *,
    lark_cli: str | None = None,
    identity: str = "user",
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """比较单篇本地和远端 Markdown。"""
    return _run_quietly(
        lambda: compare_local_file(
            lark_cli or find_lark_cli(),
            markdown_file,
            document_url,
            identity=identity,
        ),
        diagnostic,
    )


def pull_tree(
    root_document_url: str,
    directory: str | Path,
    *,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    include_front_matter: bool = True,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """预览或执行 Wiki 根文档及其子文档拉取。"""
    return _run_quietly(
        lambda: (
            apply_tree_pull(
                lark_cli or find_lark_cli(),
                root_document_url,
                directory,
                plan_id,
                identity=identity,
                max_depth=max_depth,
                page_limit=page_limit,
                include_front_matter=include_front_matter,
            )
            if plan_id
            else plan_tree_pull(
                lark_cli or find_lark_cli(),
                root_document_url,
                directory,
                identity=identity,
                max_depth=max_depth,
                page_limit=page_limit,
                include_front_matter=include_front_matter,
            )
        ),
        diagnostic,
    )


def push_tree(
    directory: str | Path,
    root_document_url: str | None = None,
    *,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """根据 front matter 或根 Wiki URL 预览或执行文档树推送。"""
    return _run_quietly(
        lambda: (
            apply_tree_push(
                lark_cli or find_lark_cli(),
                directory,
                plan_id,
                identity=identity,
                root_document_url=root_document_url,
                max_depth=max_depth,
                page_limit=page_limit,
            )
            if plan_id
            else plan_tree_push(
                lark_cli or find_lark_cli(),
                directory,
                identity=identity,
                root_document_url=root_document_url,
                max_depth=max_depth,
                page_limit=page_limit,
            )
        ),
        diagnostic,
    )


def tree_status(
    directory: str | Path,
    root_document_url: str | None = None,
    *,
    lark_cli: str | None = None,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """根据 front matter 或根 Wiki URL 比较文档树。"""
    return _run_quietly(
        lambda: read_tree_status(
            lark_cli or find_lark_cli(),
            directory,
            identity=identity,
            root_document_url=root_document_url,
            max_depth=max_depth,
            page_limit=page_limit,
        ),
        diagnostic,
    )
