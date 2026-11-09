---
name: larkmd-sync
description: 在本地 Markdown 与飞书/Lark 文档之间同步正文、本地图片、Mermaid 画板和高亮块，支持单文档与 Wiki 子文档的显式拉取、比较和推送。用户要保留可直接浏览的 Markdown、只用根 Wiki URL 映射无 URL 子文档或同步文档树时使用；只读/直接编辑云文档、管理 Wiki 节点、直接编辑原生画板、附件、电子表格或多维表格时不使用。
metadata:
  skill-version: "1.0.0"
  cli-min-version: "1.0.0"
---

# larkmd-sync

使用 `larkmd-sync` 同步本地 Markdown 和飞书/Lark 文档。用户只维护 Markdown；文档结构只保存一个 `.md` 文件，图片保留为 Markdown 引用的普通本地文件。工具可能在内存中读取块 XML 来修复飞书格式，但不要保存 XML 或文档树清单。原生画板必须保留工具生成的 `.board.json` 和预览，源文件内的映射和基准用于安全恢复。

## 调用前

1. 在每次同步任务开始前，运行 `python3 <本 Skill 目录>/scripts/check_cli.py`（使用当前读取的 Skill 所在目录，不依赖工作目录）。脚本检查 PATH 中的 CLI 是否可执行、版本是否与本 Skill 完全一致。只有退出码为 `0` 且 JSON 中 `ok: true` 才能继续。缺少脚本、未安装 CLI、查询失败或版本不一致时，立即停止同步任务并报告检查结果；不得继续认证、plan 或 apply，也不得绕过检查。安装或更新配套版本后必须重新检查。
2. 运行 `lark-cli auth status`，确认需要的 `user` 或 `bot` 身份已登录。默认使用 `user`。
3. Agent 调用始终添加 `--json --no-input`，只解析 stdout 中的 schema `1` JSON；stderr 只作诊断。

## 选择命令

- 单篇拉取：`pull FILE --doc URL`。
- 单篇推送：`push FILE`；文件没有 `feishu_url`/`lark_url` 时追加 `--doc URL`。
- 单篇比较：`diff FILE`，URL 规则与推送相同。
- Wiki 根文档及子文档拉取：`tree pull DIR --doc ROOT_URL`；本地不保存 URL 时添加 `--no-front-matter`。
- 每篇文件有 URL 时比较或推送：`tree status DIR`、`tree push DIR`。
- 只有根 Wiki URL 时比较或推送：`tree status DIR --doc ROOT_URL`、`tree push DIR --doc ROOT_URL`。工具按目录层级和页面标题匹配子 `docx` 节点。
- Skill 安装、更新或卸载：使用 `skill` 命令组。

需要具体参数、结果字段或退出码时读取 [CLI 参考](references/CLI.md)。

## 执行同步

CLI 仍使用 plan/apply 防止状态变化，但 Agent 默认在同一轮连续执行：

1. 用户明确要求拉取、上传、推送或同步时，先运行不带 `--apply` 的命令。
2. 检查 `actions`、`risks`、`conflicts` 和 `warnings`。没有 conflict 时，拉取可以带阻断警告落地检查；推送必须没有 `blocking: true` warning。满足条件后立即使用返回的 `plan_id` 执行 apply，不再请求用户确认。
3. 计划过期时重新生成计划并重试一次；再次过期或执行失败时停止并报告实际状态。

只有用户要求查看、比较、预览或仅生成方案时才停在只读阶段。目标文档无法确定、计划存在 conflict 时必须停止；推送遇到 `blocking: true` warning 也必须停止，不得 apply。普通非阻断 warning 不需要暂停同步，在最终结果中说明即可。

`warnings` 中出现 `blocking: true` 表示远端含普通 Markdown 无法可靠表达的引用或云端结构。允许拉取到本地检查，但不要执行 `push` 或 `tree push`；改用对应的飞书文档、画板、Base 或 Sheet 工具。

本地图片使用 `![说明](./相对路径.png)`，必须单独占一行，位于 Markdown 同目录或子目录。支持 PNG、JPEG、GIF、WebP 和 BMP。拉取时图片写入同级 `.assets/<文档名>/`；计划中的 `write_local_image`、`insert_remote_image` 和结果中的 `asset_paths` 都必须纳入授权检查。

图片打断有序列表时，本地继续使用普通的 `1.`、图片、`2.`。推送会在图片插入后恢复飞书列表项的明确序号；不要手工改成 XML，也不要把 block ID 写入 Markdown。

本地 Mermaid 使用 fenced `mermaid`。推送时创建飞书 Mermaid 画板，拉取时恢复源码。保证双向支持 `flowchart`、`sequenceDiagram`、`classDiagram`、`stateDiagram`/`stateDiagram-v2`、`erDiagram`、`gantt`、`pie` 和 `mindmap`；其他 Mermaid 声明只透传，不保证飞书能够无损回导。原生画板默认下载为 `.board.json` 和 `.board.svg`，不自动改绘为 Mermaid。新文档恢复原生节点，已有文档保留原生画板；本地和飞书同时修改会报告冲突。已识别的 Mermaid 小组件会提取原始源码。不支持的画板尝试下载预览，但仍阻止回写，预览图不等同于恢复可编辑画板。

飞书高亮块在本地使用 `> [!NOTE]` 形式的 Markdown Alert。支持 `NOTE`、`TIP`、`IMPORTANT`、`WARNING` 和 `CAUTION`；后续内容的每一行都以 `>` 开头。推送会设置固定图标和颜色，拉取会按图标恢复类型。任意自定义颜色无法保存在 Markdown 中，回写时会归一化；检查 `callout_style_normalized` warning。高亮块内不要放图片、代码块、表格、Mermaid 或其他资源块。

`tree push/status` 不传 `--doc` 时递归扫描每个 Markdown 的 front matter。传根 URL 时，根页使用 `README.md`、`index.md`、与远端根标题匹配的页面或唯一顶层 Markdown；子页按目录层级和一级标题匹配同层远端 `docx` 节点，没有一级标题时使用文件名。本地页面找不到对应节点或同层标题重复会产生冲突。`Root.md` 加 `Root/Child.md` 的布局允许只在 `Root.md` 保存根 URL。多篇推送不是事务，中途失败时核对返回的已成功数量。

## 使用范围

本 Skill 不用于只读取或直接编辑云文档，也不用于 Wiki 节点创建/移动、权限管理、电子表格、多维表格、非 Mermaid 画板、同步块或普通文件附件操作。这些任务使用对应的官方飞书能力。

文档引用转换为保留租户域名和目标的 Markdown 链接；人员引用以可点击链接显示并在上传时恢复 @ 人。不得将未解析引用改成只有标题的文字，也不得删除不可恢复的占位后声称可以完整回写。

字体颜色使用标准行内 HTML：`<span style="color:#d83931">红字</span>`。支持十六进制、整数 RGB 和基础 CSS 色名；非色板颜色映射到飞书七色色板，并报告 `text_color_palette_normalized`。下载从完整 XML 恢复字体颜色；有歧义或不支持的样式不得静默删除。完整 XML 只在内存处理，不生成 sidecar。部分渲染器会过滤 CSS，不能将本地未显示颜色直接判为颜色已丢失。

原生画板 SVG 是本地编辑源，必须与同名 `.board.json` 一起保留。优先编辑 SVG，不要同时修改 JSON nodes，也不要伪造同步基准。旧 JPG/PNG 预览只读。仅修改普通文本时优先局部更新；较大正文变化保留原生画板块并重建其余正文，不承诺所有块级评论都保留。嵌套原生画板、缺失映射和双端冲突必须停下处理，不得通过删除画板引用绕过。

## SVG 画板编辑（5.7.0）

原生画板拉取为 `.board.svg` 与 `.board.json`，Markdown 引用 SVG。编辑图形优先修改 SVG；不要同时修改 SVG 和 JSON nodes，也不要修改同步基准。推送已修改 SVG 会在原token上重建节点，回读后刷新SVG与原生数据；未修改的图保留原节点。SVG只保证视觉编辑，不保证原分组或连接绑定语义无损。旧JPG/JSON引用仍兼容，下次拉取迁移为SVG。SVG必须自包含，不支持脚本、事件或外部引用。

SVG 导出超时最多尝试三次；最终错误包含 token、次数和耗时。成功 SVG 按最新节点内容缓存 24 小时，预检与执行仍重新检查远端节点和冲突。缓存不会解除专有引用的回写阻断，也不会自动降级原生画板。

拉取时会在必要位置补空格，修复标点紧接正文导致的 Markdown 加粗定界符歧义；不修改远端文档。

Markdown 纠正由同步层的独立模块统一执行，目前仅处理加粗边界；不自动修复其他格式，也不因纠正本地输出而改写云文档。

拉取根据 seq-marker 恢复标题序号，并在行末用 larkmd-heading 注释保留自动编号设置。编辑时保留注释和显示序号；推送去掉显示序号后恢复飞书 seq="auto"。序号与元数据不一致时拒绝推送，不将手工数字误当自动编号。
