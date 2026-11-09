import runpy
import subprocess
from pathlib import Path


def test_release_notes_use_ancestor_tag_and_keep_commit_subjects(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts/release_notes.py'
    notes = runpy.run_path(str(script))['release_notes']
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True)

    git('init', '-b', 'main')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('commit', '--allow-empty', '-m', 'feat: 初始版本')
    git('tag', 'v1.0.0')
    first = notes('v1.0.0')
    assert '首次发布' in first and 'feat: 初始版本' in first
    git('checkout', '-b', 'unrelated')
    git('commit', '--allow-empty', '-m', 'other branch')
    git('tag', 'v9.0.0')
    git('checkout', 'main')
    git('commit', '--allow-empty', '-m', 'fix: 保留 "引用" 和 $变量')
    git('tag', '-a', 'v1.0.1', '-m', 'release')
    second = notes('v1.0.1')
    assert 'v1.0.0 → v1.0.1' in second
    assert 'fix: 保留 "引用" 和 $变量' in second
    assert '初始版本' not in second and 'other branch' not in second
