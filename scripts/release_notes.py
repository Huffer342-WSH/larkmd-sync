#!/usr/bin/env python3
"""从当前标签与最近的祖先版本标签之间的提交生成发布说明。"""

import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def release_notes(tag: str) -> str:
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('标签必须为 vMAJOR.MINOR.PATCH')
    commit = git('rev-parse', '--verify', f'{tag}^{{commit}}')
    ancestors = git('rev-list', commit).splitlines()[1:]
    versions = {}
    for candidate in git('tag', '--merged', commit).splitlines():
        if candidate != tag and re.fullmatch(r'v\d+\.\d+\.\d+', candidate):
            versions.setdefault(git('rev-parse', f'{candidate}^{{commit}}'), candidate)
    previous = next((versions[item] for item in ancestors if item in versions), None)
    revision = f'{previous}..{commit}' if previous else commit
    commits = git('log', '--reverse', '--format=- %s (`%h`)', revision)
    scope = f'{previous} → {tag}' if previous else f'{tag} 首次发布'
    return f'## 变更记录\n\n{scope}\n\n{commits or "无新增提交。"}\n'


if __name__ == '__main__':
    print(release_notes(sys.argv[1]), end='')
