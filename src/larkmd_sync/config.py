"""Configuration: locate lark-cli binary."""

import os
import shutil
import subprocess
from pathlib import Path


def find_lark_cli() -> str:
    """Find the lark-cli binary.

    Checks in order:
    1. LARK_CLI_PATH env var
    2. PATH (via shutil.which)
    """
    env_path = os.environ.get("LARK_CLI_PATH", "")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)

    found = shutil.which("lark-cli")
    if found:
        return found

    raise RuntimeError(
        "lark-cli not found. Install it with: npm install -g @larksuite/cli\n"
        "Or set LARK_CLI_PATH env var."
    )


def get_lark_cli_version(lark_cli: str) -> str:
    """Get lark-cli version string."""
    try:
        result = subprocess.run(
            [lark_cli, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or result.stderr.strip()
    except Exception:
        return "unknown"
