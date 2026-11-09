"""Check the CLI on PATH against this installed Skill; standard library only."""

import json
import re
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    result = {
        "ok": False,
        "expected_version": None,
        "actual_version": None,
        "cli_path": None,
        "error": None,
    }
    try:
        text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
            encoding="utf-8"
        )
        front = text.split("---", 2)
        if len(front) != 3 or front[0].strip():
            raise ValueError("Skill front matter 无效")
        versions = re.findall(
            r'^  skill-version: "([^"\n]+)"\s*$', front[1], re.MULTILINE
        )
        if len(versions) != 1:
            raise ValueError("无法读取唯一的 Skill 版本")
        result["expected_version"] = versions[0]
        cli = shutil.which("larkmd-sync")
        result["cli_path"] = cli
        if not cli:
            raise ValueError("PATH 中未安装 larkmd-sync")
        proc = subprocess.run(
            [cli, "--version"], capture_output=True, text=True, timeout=10
        )
        if proc.returncode:
            raise ValueError(f"CLI 版本查询失败，退出码 {proc.returncode}")
        match = re.fullmatch(r"larkmd-sync ([^\s]+)", proc.stdout.strip())
        if not match:
            raise ValueError("CLI 版本输出无法识别")
        result["actual_version"] = match[1]
        if match[1] != versions[0]:
            raise ValueError("CLI 与 Skill 版本不一致，请安装配套版本后重新检查")
        result["ok"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
