import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'install.sh'


@pytest.mark.parametrize('release,success', [
    ('https://github.com/Huffer342-WSH/larkmd-sync/releases/tag/v1.2.3', True),
    ('https://example.com/releases/tag/v1.2.3', False),
    ('https://github.com/Huffer342-WSH/larkmd-sync/releases/tag/vbad', False),
])
def test_install_resolves_only_valid_release(tmp_path, release, success):
    log = tmp_path / 'args'
    for name, body in {
        'curl': 'printf "%s" "$TEST_RELEASE"',
        'uv': 'printf "%s\\n" "$@" > "$TEST_LOG"',
    }.items():
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n' + body + '\n')
        tool.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:/usr/bin:/bin',
               TEST_RELEASE=release, TEST_LOG=str(log))
    result = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == success
    if success:
        assert log.read_text().splitlines() == [
            'tool', 'install', '--upgrade', '--python', '>=3.10',
            'https://github.com/Huffer342-WSH/larkmd-sync/releases/download/v1.2.3/larkmd_sync-1.2.3-py3-none-any.whl',
        ]
    else:
        assert not log.exists()
