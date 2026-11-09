import json
import os
import subprocess
import sys

import pytest

from larkmd_sync import __version__, discover_builtin_skills, install_skill


@pytest.mark.parametrize(
    "mode", ["match", "older", "newer", "missing", "invalid", "failed"]
)
def test_installed_skill_cli_gate(tmp_path, mode):
    skill = discover_builtin_skills("larkmd_sync.skills")["larkmd-sync"]
    assert skill.version == __version__
    install_skill(
        skill, tmp_path / "skills", installer="larkmd-sync", cli_version=__version__
    )
    script = tmp_path / "skills/larkmd-sync/scripts/check_cli.py"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if mode != "missing":
        cli = bin_dir / "larkmd-sync"
        output = {
            "match": f"larkmd-sync {__version__}",
            "older": "larkmd-sync 0.0.0",
            "newer": "larkmd-sync 999.0.0",
            "invalid": "garbage",
            "failed": "",
        }[mode]
        cli.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit {1 if mode == 'failed' else 0}\n"
        )
        cli.chmod(0o755)
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(bin_dir)},
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)
    assert proc.returncode == (0 if mode == "match" else 1)
    assert result["ok"] == (mode == "match")
    assert result["expected_version"] == __version__
