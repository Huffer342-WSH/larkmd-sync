"""把通用 Skill 生命周期接入 argparse CLI。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .skillkit import (
    SkillPackage,
    install_skill,
    resolve_skill_root,
    skill_status,
    uninstall_skill,
    update_skill,
    validate_skill,
)


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", choices=("auto", "codex", "claude"), default="auto")
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--dest", "--path", type=Path)
    parser.add_argument("--global", dest="global_scope", action="store_true")


def _add_change_args(parser: argparse.ArgumentParser, *, backup: bool = True) -> None:
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    if backup:
        parser.add_argument("--backup", action="store_true")


def add_skill_commands(commands: Any) -> None:
    """注册稳定的 `skill` 子命令组。"""
    skill = commands.add_parser("skill", aliases=["skills"])
    actions = skill.add_subparsers(dest="skill_command", required=True)
    actions.add_parser("list", aliases=["ls"])
    show = actions.add_parser("show")
    show.add_argument("name", nargs="?")
    validate = actions.add_parser("validate")
    validate.add_argument("name", nargs="?")
    doctor = actions.add_parser("doctor")
    doctor.add_argument("name", nargs="?")

    for name, aliases in (
        ("install", []),
        ("status", []),
        ("update", ["upgrade"]),
        ("uninstall", ["remove"]),
    ):
        action = actions.add_parser(name, aliases=aliases)
        action.add_argument("name", nargs="?")
        _add_target_args(action)
        if name in ("install", "update", "uninstall"):
            _add_change_args(action, backup=name != "uninstall")


def _select_skill(
    catalog: Mapping[str, SkillPackage],
    name: str | None,
    default_skill: str,
) -> SkillPackage:
    selected = name or default_skill
    try:
        return catalog[selected]
    except KeyError as exc:
        raise ValueError(f"未知 Skill：{selected}") from exc


def _project_root(value: Path | None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("项目级安装需要 --project 或位于 Git 仓库中")
    return Path(completed.stdout.strip()).resolve()


def _target(args: argparse.Namespace) -> Path:
    if args.dest is not None:
        return args.dest.expanduser().resolve()
    scope = "user" if args.global_scope else args.scope
    agent = args.agent
    if agent == "auto":
        detected = [name for name in ("codex", "claude") if shutil.which(name)]
        if len(detected) != 1:
            raise ValueError("无法唯一确定 Agent；请显式提供 --agent")
        agent = detected[0]
    project = _project_root(args.project) if scope == "project" else None
    return resolve_skill_root(agent, scope, project=project)


def run_skill_command(
    args: argparse.Namespace,
    *,
    catalog: Mapping[str, SkillPackage],
    default_skill: str,
    installer: str,
    cli_version: str,
    command_name: str,
) -> object:
    """执行已经注册的 Skill 子命令并返回可序列化结果。"""
    if args.skill_command in ("list", "ls"):
        return [
            {
                "name": item.name,
                "version": item.version,
                "description": item.description,
            }
            for item in catalog.values()
        ]
    skill = _select_skill(
        catalog,
        getattr(args, "name", None),
        default_skill,
    )
    if args.skill_command == "show":
        return {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "skill_md": skill.root.joinpath("SKILL.md").read_text(encoding="utf-8"),
        }
    if args.skill_command == "validate":
        validate_skill(skill.root)
        return {"name": skill.name, "valid": True}
    if args.skill_command == "doctor":
        validate_skill(skill.root)
        return {
            "cli_version": cli_version,
            "executable": shutil.which(command_name),
            "skill": skill.name,
            "valid": True,
        }

    target = _target(args)
    if args.skill_command == "status":
        return skill_status(skill, target, installer=installer)
    if args.skill_command == "install":
        return install_skill(
            skill,
            target,
            installer=installer,
            cli_version=cli_version,
            force=args.force,
            backup=args.backup,
            dry_run=args.dry_run,
        )
    if args.skill_command in ("update", "upgrade"):
        return update_skill(
            skill,
            target,
            installer=installer,
            cli_version=cli_version,
            force=args.force,
            backup=args.backup,
            dry_run=args.dry_run,
        )
    return uninstall_skill(
        skill,
        target,
        installer=installer,
        force=args.force,
        dry_run=args.dry_run,
    )
