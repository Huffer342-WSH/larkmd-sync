#!/bin/sh
# Install the latest published wheel without cloning the repository.
set -eu

if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<'MESSAGE'
未找到 uv。请先安装 uv，再重新运行本脚本：
  curl -LsSf https://astral.sh/uv/install.sh | sh
安装后重新打开终端，或按 uv 输出的提示更新 PATH。
MESSAGE
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo '未找到 curl，请先通过系统包管理器安装 curl。' >&2
    exit 1
fi

repo='https://github.com/Huffer342-WSH/larkmd-sync'
if ! release_url=$(curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
    -o /dev/null -w '%{url_effective}' "$repo/releases/latest"); then
    echo '无法获取最新 Release，请检查网络以及仓库是否已发布正式版本。' >&2
    exit 1
fi
case "$release_url" in
    "$repo/releases/tag/v"*) version=${release_url##*/v} ;;
    *) echo "无法识别 Release 地址：$release_url" >&2; exit 1 ;;
esac
if ! printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "不支持的版本号：$version" >&2
    exit 1
fi

wheel_url="$repo/releases/download/v$version/larkmd_sync-$version-py3-none-any.whl"
printf '安装 larkmd-sync %s\n' "$version"
uv tool install --upgrade --python '>=3.10' "$wheel_url"
cat <<'MESSAGE'
安装完成。若找不到 larkmd-sync 命令，请运行 uv tool update-shell 后重新打开终端。
使用前请安装并登录官方 lark-cli；Agent Skill 需要另行显式安装：
  larkmd-sync skill install --agent codex --scope user --dry-run
  larkmd-sync skill install --agent codex --scope user
MESSAGE
