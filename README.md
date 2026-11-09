# larkmd-sync

> [!WARNING]
> 本项目完全通过 Vibe Coding 开发，不保证可靠性。使用前请自行评估风险，并备份重要文档。

`larkmd-sync` 面向需要同时维护本地 Markdown 和飞书/Lark 文档的 Agent。用户只需读写 Markdown；工具不提供 XML 同步接口，不生成文档树清单；原生画板使用 `.board.svg` 编辑图形，并用 `.board.json` 保存原生节点与同步基准。少数飞书格式修复会在内存中临时读取块 XML，例如恢复被图片打断的有序列表编号，以及设置高亮块的图标和颜色。

项目提供同一套业务实现的三个入口：

- 公共 Python API；
- 输出稳定 JSON 的 `larkmd-sync` CLI；
- 随 Python 包发布、需要显式安装的 Agent Skill。

同步格式与编辑限制见 [Markdown 格式](docs/Markdown格式.md)，命令和错误码见 [CLI 参考](src/larkmd_sync/skills/larkmd-sync/references/CLI.md)。

## 适用范围

- 拉取、比较或推送单篇 Markdown。
- 递归拉取 Wiki 根文档及其 `docx` 子文档。
- 通过每篇 Markdown 的 front matter 比较或推送文档树。
- 只提供根 Wiki URL，按目录层级和页面标题比较或推送不含 URL 的文档树。
- 双向同步本地图片与飞书图片。
- 双向同步 fenced Mermaid 与飞书 Mermaid 画板。
- 双向同步五类 Markdown Alert 与飞书高亮块。

原生画板默认保存 SVG 和原生节点，上传时恢复或保留原画板，不再自动转为 Mermaid。已识别的 Mermaid 小组件会恢复源码。文档引用转换为保留目标的 Markdown 链接，人员引用在上传时恢复 @ 人。飞书 `/file/` 图片通过认证素材接口下载。

本工具不处理无法转换的原生画板、同步块、Base、Sheet、普通文件附件或其他云端专有结构。发现无法恢复的引用或专有标签时，计划会输出 `blocking: true` 警告。不要使用本工具回写这类文档，应改用对应的飞书工具。

只读取或直接编辑云文档、创建或移动 Wiki 节点、管理权限、操作电子表格或多维表格也不属于本工具。

## 安装

推荐在 Linux/macOS 上通过安装脚本获取最新正式 Release，无需克隆仓库，也不需要 jq：

```bash
curl -fsSL https://raw.githubusercontent.com/Huffer342-WSH/larkmd-sync/main/install.sh | sh
```

脚本检查 uv，缺少时提示安装并退出，不自动安装 uv。按照 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装后重新打开终端，再运行上面的命令：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

脚本从 GitHub 最新正式 Release 下载对应 wheel，由 uv 安装或升级 CLI，并选择 Python 3.10 及以上环境。再次运行即可更新；不会自动安装 Agent Skill 或登录飞书。若找不到命令，执行 `uv tool update-shell` 后重新打开终端。

也可以指定版本安装，或在仓库根目录用 `uv tool install .` 从源码安装：

```bash
uv tool install https://github.com/Huffer342-WSH/larkmd-sync/releases/download/v1.0.0/larkmd_sync-1.0.0-py3-none-any.whl
```

使用同步功能前，需要 Node.js/npm，以及已安装、登录的官方飞书 CLI：

```bash
npm install -g @larksuite/cli
lark-cli auth login
lark-cli auth status
larkmd-sync --version
```

Python 包安装不会修改 Agent 配置。Skill 需要单独预览和安装：

```bash
larkmd-sync --json --no-input skill install larkmd-sync \
  --agent codex --scope user --dry-run
larkmd-sync --json --no-input skill install larkmd-sync \
  --agent codex --scope user
```

完整 URL 会保留自身租户域名。只传 token 时可通过 `LARK_BASE_URL` 指定租户：

```bash
export LARK_BASE_URL=https://tenant.feishu.cn
```

## CLI

Agent 调用统一添加 `--json --no-input`。stdout 只输出一个 schema `1` JSON，诊断信息写入 stderr。

### 单文档

```bash
# 生成拉取计划，再用返回的 data.plan_id 执行
larkmd-sync --json --no-input pull ./docs/design.md --doc "$URL"
larkmd-sync --json --no-input pull ./docs/design.md --doc "$URL" \
  --apply 'sha256:...'

# 有 front matter 时无需重复传 URL
larkmd-sync --json --no-input diff ./docs/design.md
larkmd-sync --json --no-input push ./docs/design.md
larkmd-sync --json --no-input push ./docs/design.md \
  --apply 'sha256:...'
```

使用 `pull --no-front-matter` 可以得到不带映射的普通 Markdown；后续 `diff` 和 `push` 必须通过 `--doc URL` 指定目标。如果显式 URL 与已有 front matter 不一致，工具会拒绝执行。

计划中的 `write_local_image` 或 `insert_remote_image` 动作会列出每张图片的实际路径。apply 结果通过 `assets_changed` 和 `asset_paths` 报告资源写入；`diff.detail` 分别报告正文和图片字节是否变化。

### Wiki 文档树

```bash
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL" \
  --apply 'sha256:...'

# 本地文件不保存 URL
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL" \
  --no-front-matter
larkmd-sync --json --no-input tree status ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree push ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree push ./wiki-docs --doc "$ROOT_URL" \
  --apply 'sha256:...'
```

`tree pull` 默认为每篇文档写入自己的 front matter；添加 `--no-front-matter` 后只写普通 Markdown。两种模式都不生成 `tree.json`。

`tree status/push --doc ROOT_URL` 先遍历远端 Wiki，再建立映射：

- 根目录中的 `README.md`、`index.md`、标题与远端根标题相同的文件，或唯一的顶层 Markdown 作为根页；
- 其余文件按所在目录和一级标题匹配同层远端 `docx` 节点，一级标题缺失时使用文件名；
- 相对 Markdown 链接使用同一份映射转换成远端链接，本地文件不会被改写；
- 本地页面在远端找不到同层同标题节点、同层标题重复或远端存在重名节点时，计划报告冲突，不会进入 apply；
- 远端没有本地页面的节点只报告 warning 并跳过，Sheet、Base 等非 `docx` 节点也不会伪装成 Markdown。

不传 `--doc` 时，原有 front matter 模式保持不变。若目录使用 `Root.md` 加 `Root/Child.md` 的标准树形布局，也可以只在 `Root.md` 保存根 URL，子文件无需保存 URL。

Sheet、Base 等非 `docx` 子节点会在拉取计划中列为跳过项。工具不会删除已经不在远端树中的本地文件。多篇远端覆盖不是事务，执行中断可能已有部分文档成功。

## Python API

CLI 只处理参数、JSON 和退出码。公共 API 默认不写 stdout/stderr，预期失败返回 `ApiResult`：

```python
from larkmd_sync import pull_file, push_file

preview = pull_file(
    "./docs/design.md",
    "https://tenant.feishu.cn/wiki/token",
)
if preview.ok and not preview.data.conflicts:
    applied = pull_file(
        "./docs/design.md",
        "https://tenant.feishu.cn/wiki/token",
        plan_id=preview.data.plan_id,
    )

push_preview = push_file("./docs/design.md")
```

公开入口为 `pull_file`、`push_file`、`compare_file`、`pull_tree`、`push_tree`、`tree_status`，以及结果模型和 Skill 生命周期 API。`push_tree` 和 `tree_status` 的第二个参数可以传根 Wiki URL，用于无 URL 文档树。

## 开发

开发从 [AGENTS.md](AGENTS.md) 开始；架构和设计专题见 [文档索引](docs/README.md)。

## 标签发布

将包、Python API、锁文件和内置 Skill 版本保持一致，然后创建并推送标签：

```bash
git tag -a v1.0.0 -m 'Release 1.0.0'
git push origin v1.0.0
```

[发布流程](.github/workflows/release.yml) 在推送 `v*` 标签时触发，只接受 `vMAJOR.MINOR.PATCH`。CI 检查版本一致性、锁文件、Ruff 和离线测试，并构建 wheel、sdist；检查 Skill 资源完整性后，在独立 Python 3.10 环境安装 wheel 并验证 Skill 安装。

全部通过后创建 GitHub Release，附带 wheel、sdist 和 SHA256SUMS。Release Notes 从最近的祖先版本标签到当前标签收集提交标题与短 commit ID；首次发布列出全部历史提交。因此提交标题应描述具体改动。此流程不访问真实飞书服务，也不发布到 PyPI。

Release 先创建为草稿，上传完成后公开。若公开步骤失败，检查已有草稿后手动发布；重复运行不会覆盖已有 Release。仓库需允许 GitHub Actions 使用 `contents: write` 权限，无需配置额外发布令牌。
