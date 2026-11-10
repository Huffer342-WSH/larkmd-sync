---
name: larkmd-sync
description: 使用 CLI 在本地 Markdown 与飞书/Lark 文档之间拉取、比较和推送，支持图片、画板及 Wiki 文档树。适用于维护本地与云端副本；仅阅读云文档、管理 Wiki 节点或权限、操作 Sheet/Base 时不使用。
metadata:
  skill-version: "1.0.1"
  cli-min-version: "1.0.1"
---

# larkmd-sync

用 CLI 完成同步，不自行编写上传、下载或格式转换脚本。先确定本地路径、目标 URL 和方向；同步图片与画板时，素材也属于操作范围。

## 开始前

```bash
python3 <本Skill目录>/scripts/check_cli.py
lark-cli auth status
```

先确认预检退出码为 0 且 `ok: true`，再检查认证。预检失败时报告原因，安装或更新配套 CLI 后重试；不要继续同步。默认使用 user 身份。

完整选项按需查看 `larkmd-sync --help`、`larkmd-sync push --help`、`larkmd-sync tree push --help` 或 `larkmd-sync skill --help`。业务命令使用 `--json`，读取 stdout JSON，stderr 用于诊断。

## 常用命令

将模板中的路径、URL 和计划 ID 替换为实际值。

```bash
# 单篇：下载、比较、上传
larkmd-sync --json pull ./文档.md --doc "$URL"
larkmd-sync --json diff ./文档.md --doc "$URL"
larkmd-sync --json push ./文档.md --doc "$URL"

# Wiki 文档树：下载、比较、上传
larkmd-sync --json tree pull ./文档目录 --doc "$ROOT_URL"
larkmd-sync --json tree status ./文档目录 --doc "$ROOT_URL"
larkmd-sync --json tree push ./文档目录 --doc "$ROOT_URL"
```

文件头已有 `feishu_url` 或 `lark_url` 时，单篇比较和上传可省略 `--doc`。树上传按目录层级和标题匹配已有页面，不创建 Wiki 节点；匹配不清楚时先查看 `tree status`。

## 执行与预览

`pull`、`push`、`tree pull`、`tree push` 默认直接同步。用户要求执行时直接调用；仅要求查看计划、范围不确定或需要审阅覆盖内容时添加 `--dry-run`：

```bash
larkmd-sync --json push ./文档.md --doc "$URL" --dry-run
```

检查返回的 `ok`、`warnings`，以及计划中的 `actions`、`risks` 和 `conflicts`。执行模式遇到冲突或推送阻断警告会失败，不要删除元数据或改参数来强制覆盖。拉取可带阻断警告保存供检查，不能据此宣称可完整回写。

如需执行预览时的固定计划，原命令加 `--apply PLAN_ID`；不要同时加 `--dry-run`。计划过期后重新检查再执行。失败可能已经部分写入，先核对实际状态，不盲目重试。成功后用 `diff` 或 `tree status` 检查；样式和画板还需读回或目视确认。

## 编辑时保留什么

- 图片用独立一行的相对路径引用，素材放在 Markdown 同目录或子目录。
- 新画板可以直接引用 `图名.board.svg`；首次成功上传自动生成 JSON。已有画板的 SVG 和 JSON 必须配对保留，不修改或删除同步元数据来绕过冲突。
- 自动编号标题保留显示序号和行末注释；编辑标题正文即可。
- Mermaid 用代码块，高亮块用 `> [!NOTE]` 等 Markdown Alert。

遇到格式限制、警告或需要了解 JSON 字段时，按需阅读 [使用补充](references/CLI.md)。
