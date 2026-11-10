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


def _synchronize(plan, apply, *, dry_run: bool, plan_id: str | None) -> ApiResult:
    if dry_run and plan_id is not None:
        raise ValueError("dry_run 与 plan_id 不能同时使用")
    if plan_id is not None:
        return apply(plan_id)
    preview = plan()
    if dry_run or not preview.ok:
        return preview
    proposed = preview.data
    problems = list(proposed.conflicts)
    if proposed.operation.endswith("push"):
        problems.extend(w.message for w in proposed.warnings if w.blocking)
    if problems:
        return ApiResult(ok=False, status=409, data=proposed, error="；".join(problems))
    return apply(proposed.plan_id)


def pull_file(
    markdown_file: str | Path,
    document_url: str,
    *,
    dry_run: bool = False,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    include_front_matter: bool = True,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """拉取文档；dry_run=True 仅预览，plan_id 可执行指定计划。"""
    return _run_quietly(
        lambda: _synchronize(
            lambda: plan_pull_file(
                lark_cli or find_lark_cli(),
                document_url,
                markdown_file,
                identity=identity,
                include_front_matter=include_front_matter,
            ),
            lambda reviewed_id: apply_pull_file(
                lark_cli or find_lark_cli(),
                document_url,
                markdown_file,
                reviewed_id,
                identity=identity,
                include_front_matter=include_front_matter,
            ),
            dry_run=dry_run,
            plan_id=plan_id,
        ),
        diagnostic,
    )


def push_file(
    markdown_file: str | Path,
    document_url: str | None = None,
    *,
    dry_run: bool = False,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """推送 Markdown；dry_run=True 仅预览，plan_id 可执行指定计划。"""
    return _run_quietly(
        lambda: _synchronize(
            lambda: plan_push_file(
                lark_cli or find_lark_cli(),
                markdown_file,
                document_url,
                identity=identity,
            ),
            lambda reviewed_id: apply_push_file(
                lark_cli or find_lark_cli(),
                markdown_file,
                reviewed_id,
                document_url,
                identity=identity,
            ),
            dry_run=dry_run,
            plan_id=plan_id,
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
    dry_run: bool = False,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    include_front_matter: bool = True,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """拉取 Wiki 文档树；dry_run=True 仅预览。"""
    return _run_quietly(
        lambda: _synchronize(
            lambda: plan_tree_pull(
                lark_cli or find_lark_cli(),
                root_document_url,
                directory,
                identity=identity,
                max_depth=max_depth,
                page_limit=page_limit,
                include_front_matter=include_front_matter,
            ),
            lambda reviewed_id: apply_tree_pull(
                lark_cli or find_lark_cli(),
                root_document_url,
                directory,
                reviewed_id,
                identity=identity,
                max_depth=max_depth,
                page_limit=page_limit,
                include_front_matter=include_front_matter,
            ),
            dry_run=dry_run,
            plan_id=plan_id,
        ),
        diagnostic,
    )


def push_tree(
    directory: str | Path,
    root_document_url: str | None = None,
    *,
    dry_run: bool = False,
    plan_id: str | None = None,
    lark_cli: str | None = None,
    identity: str = "user",
    max_depth: int | None = None,
    page_limit: int = 10,
    diagnostic: Diagnostic | None = None,
) -> ApiResult:
    """根据 front matter 或根 Wiki URL 推送文档树；dry_run=True 仅预览。"""
    return _run_quietly(
        lambda: _synchronize(
            lambda: plan_tree_push(
                lark_cli or find_lark_cli(),
                directory,
                identity=identity,
                root_document_url=root_document_url,
                max_depth=max_depth,
                page_limit=page_limit,
            ),
            lambda reviewed_id: apply_tree_push(
                lark_cli or find_lark_cli(),
                directory,
                reviewed_id,
                identity=identity,
                root_document_url=root_document_url,
                max_depth=max_depth,
                page_limit=page_limit,
            ),
            dry_run=dry_run,
            plan_id=plan_id,
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
