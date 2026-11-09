#!/usr/bin/env python3
"""确认 wheel 和 sdist 都包含完整内置 Skill。"""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1] / "src/larkmd_sync/skills/larkmd-sync"
REQUIRED = {
    "larkmd_sync/skills/larkmd-sync/" + path.relative_to(SKILL_ROOT).as_posix()
    for path in SKILL_ROOT.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
}


def _missing_in_wheel(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    return sorted(REQUIRED - names)


def _missing_in_sdist(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    return sorted(
        required
        for required in REQUIRED
        if not any(name.endswith(f"/{required}") for name in names)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()

    checked: set[str] = set()
    for artifact in args.artifacts:
        if artifact.suffix == ".whl":
            missing = _missing_in_wheel(artifact)
            kind = "wheel"
        elif artifact.name.endswith(".tar.gz"):
            missing = _missing_in_sdist(artifact)
            kind = "sdist"
        else:
            raise SystemExit(f"不支持的构建产物：{artifact}")
        if missing:
            raise SystemExit(f"{kind} 缺少内置 Skill 文件：{', '.join(missing)}")
        checked.add(kind)
        print(f"{kind} 包含 {len(REQUIRED)} 个必需 Skill 文件：{artifact.name}")
    if not {"wheel", "sdist"}.issubset(checked):
        raise SystemExit("必须同时检查 wheel 和 sdist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
