"""``larkmd-sync`` command adapter; business behavior lives in ``api.py``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .api import compare_file, pull_file, pull_tree, push_file, push_tree, tree_status
from .result import ApiResult
from .skill_cli import add_skill_commands, run_skill_command
from .skillkit import discover_builtin_skills

SCHEMA_VERSION = "1"
RESOURCE_PACKAGE = "larkmd_sync.skills"
DEFAULT_SKILL = "larkmd-sync"
INSTALLER_ID = "larkmd-sync"
COMMAND_NAME = "larkmd-sync"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _result_warnings(result: ApiResult) -> list[Any]:
    data = result.data
    warnings = getattr(data, "warnings", ()) if data is not None else ()
    return _jsonable(warnings)


def _payload(result: ApiResult) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": result.ok,
        "data": _jsonable(result.data),
        "warnings": _result_warnings(result),
    }
    if not result.ok:
        value["error"] = {"status": result.status, "message": result.error}
    return value


def _emit(value: object, *, json_output: bool) -> None:
    envelope = (
        value
        if isinstance(value, dict) and "schema_version" in value
        else {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "data": _jsonable(value),
            "warnings": [],
        }
    )
    if json_output:
        print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    else:
        data = envelope.get("data") if isinstance(envelope, dict) else envelope
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _exit_code(result: ApiResult) -> int:
    if result.ok:
        return 0
    if result.status in {400, 422}:
        return 2
    if result.status in {404, 409}:
        return 3
    return 4


def _add_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--as",
        dest="identity",
        choices=("user", "bot"),
        default="user",
    )


def _add_apply_and_identity(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成计划，不执行同步")
    mode.add_argument("--apply", metavar="PLAN_ID", help="执行指定计划；不传时自动检查并同步")
    _add_identity(parser)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable, non-interactive CLI parser."""
    parser = _ArgumentParser(
        prog=COMMAND_NAME,
        description="同步本地 Markdown 与飞书/Lark 文档",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("--json", action="store_true", help="stdout 只输出稳定 JSON")
    parser.add_argument("--lark-cli", help="lark-cli 可执行文件路径")
    commands = parser.add_subparsers(dest="command", required=True)

    pull_parser = commands.add_parser("pull", help="预览或拉取单篇文档")
    pull_parser.add_argument("md_file", type=Path)
    pull_parser.add_argument("--doc", required=True, help="飞书/Lark URL 或 token")
    pull_parser.add_argument("--no-front-matter", action="store_true")
    _add_apply_and_identity(pull_parser)

    push_parser = commands.add_parser("push", help="预览或推送单篇 Markdown")
    push_parser.add_argument("md_file", type=Path, help="本地 Markdown")
    push_parser.add_argument(
        "--doc",
        help="front matter 缺失时在本次调用中指定飞书/Lark URL",
    )
    _add_apply_and_identity(push_parser)

    diff_parser = commands.add_parser("diff", help="比较单篇本地与远端文档")
    diff_parser.add_argument("md_file", type=Path, help="本地 Markdown")
    diff_parser.add_argument(
        "--doc",
        help="front matter 缺失时在本次调用中指定飞书/Lark URL",
    )
    _add_identity(diff_parser)

    tree = commands.add_parser("tree", help="同步 Wiki 根文档及其子文档")
    tree_commands = tree.add_subparsers(dest="tree_command", required=True)

    tree_pull = tree_commands.add_parser("pull", help="预览或拉取文档树")
    tree_pull.add_argument("directory", type=Path)
    tree_pull.add_argument("--doc", required=True, help="Wiki 根文档 URL")
    tree_pull.add_argument("--max-depth", type=int)
    tree_pull.add_argument("--page-limit", type=int, default=10)
    tree_pull.add_argument("--no-front-matter", action="store_true")
    _add_apply_and_identity(tree_pull)

    tree_push = tree_commands.add_parser(
        "push", help="预览或推送 Wiki 文档树"
    )
    tree_push.add_argument("directory", type=Path)
    tree_push.add_argument(
        "--doc",
        help="根 Wiki URL；子文档按目录层级和标题匹配，无需各自保存 URL",
    )
    tree_push.add_argument("--max-depth", type=int)
    tree_push.add_argument("--page-limit", type=int, default=10)
    _add_apply_and_identity(tree_push)

    tree_status_parser = tree_commands.add_parser(
        "status", help="比较 Wiki 文档树"
    )
    tree_status_parser.add_argument("directory", type=Path)
    tree_status_parser.add_argument(
        "--doc",
        help="根 Wiki URL；子文档按目录层级和标题匹配，无需各自保存 URL",
    )
    tree_status_parser.add_argument("--max-depth", type=int)
    tree_status_parser.add_argument("--page-limit", type=int, default=10)
    _add_identity(tree_status_parser)

    add_skill_commands(commands)
    return parser


def _diagnostic(message: str) -> None:
    print(message, file=sys.stderr)


def _normalize_global_args(argv: list[str]) -> list[str]:
    """Allow machine flags before or after nested subcommands."""
    global_args: list[str] = []
    command_args: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--json" or value.startswith("--lark-cli="):
            global_args.append(value)
        elif value == "--lark-cli" and index + 1 < len(argv):
            global_args.extend((value, argv[index + 1]))
            index += 1
        else:
            command_args.append(value)
        index += 1
    return global_args + command_args


def _run(args: argparse.Namespace) -> ApiResult | object:
    if args.command == "pull":
        return pull_file(
            args.md_file,
            args.doc,
            plan_id=args.apply,
            dry_run=args.dry_run,
            lark_cli=args.lark_cli,
            identity=args.identity,
            include_front_matter=not args.no_front_matter,
            diagnostic=_diagnostic,
        )
    if args.command == "push":
        return push_file(
            args.md_file,
            args.doc,
            plan_id=args.apply,
            dry_run=args.dry_run,
            lark_cli=args.lark_cli,
            identity=args.identity,
            diagnostic=_diagnostic,
        )
    if args.command == "diff":
        return compare_file(
            args.md_file,
            args.doc,
            lark_cli=args.lark_cli,
            identity=args.identity,
            diagnostic=_diagnostic,
        )
    if args.command == "tree":
        if args.tree_command == "pull":
            return pull_tree(
                args.doc,
                args.directory,
                plan_id=args.apply,
                dry_run=args.dry_run,
                lark_cli=args.lark_cli,
                identity=args.identity,
                max_depth=args.max_depth,
                page_limit=args.page_limit,
                include_front_matter=not args.no_front_matter,
                diagnostic=_diagnostic,
            )
        if args.tree_command == "push":
            return push_tree(
                args.directory,
                args.doc,
                plan_id=args.apply,
                dry_run=args.dry_run,
                lark_cli=args.lark_cli,
                identity=args.identity,
                max_depth=args.max_depth,
                page_limit=args.page_limit,
                diagnostic=_diagnostic,
            )
        return tree_status(
            args.directory,
            args.doc,
            lark_cli=args.lark_cli,
            identity=args.identity,
            max_depth=args.max_depth,
            page_limit=args.page_limit,
            diagnostic=_diagnostic,
        )

    catalog = discover_builtin_skills(RESOURCE_PACKAGE)
    return run_skill_command(
        args,
        catalog=catalog,
        default_skill=DEFAULT_SKILL,
        installer=INSTALLER_ID,
        cli_version=__version__,
        command_name=COMMAND_NAME,
    )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, call the public API, and emit a stable envelope."""
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(_normalize_global_args(raw_args))
    except ValueError as exc:
        result = ApiResult(ok=False, status=422, error=str(exc))
        if "--json" in raw_args:
            _emit(_payload(result), json_output=True)
        else:
            parser.print_usage(sys.stderr)
            _diagnostic(str(exc))
        return _exit_code(result)
    try:
        result = _run(args)
    except ValueError as exc:
        result = ApiResult(ok=False, status=422, error=str(exc))
    except (FileExistsError, FileNotFoundError, PermissionError) as exc:
        result = ApiResult(ok=False, status=409, error=str(exc))
    except OSError as exc:
        result = ApiResult(ok=False, status=500, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must stay structured
        result = ApiResult(ok=False, status=500, error=str(exc))

    if isinstance(result, ApiResult):
        _emit(_payload(result), json_output=args.json)
        if not result.ok and not args.json:
            print(result.error, file=sys.stderr)
        return _exit_code(result)
    _emit(result, json_output=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
