# CLI 使用补充

参数以当前 CLI 的 `--help` 为准。本页只补充结果解释和编辑约束，不需要每次同步前全部阅读。

## 结果与失败处理

机器输出为单个 JSON：`schema_version: "1"`、`ok`、`data`、`warnings`；失败时包含 `error.status` 和 `error.message`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 命令成功；计划仍需检查冲突和警告 |
| 2 | 参数或输入校验失败 |
| 3 | 文件、映射或计划冲突 |
| 4 | 外部依赖、网络或运行失败 |

同步命令默认执行，`--dry-run` 只返回计划。计划的 `data.plan_id` 可用于 `--apply PLAN_ID` 执行；执行结果的 `changed`、`asset_paths` 表示已处理内容；比较结果的 `data.identical` 表示工具判断正文和资源是否一致。非阻断警告可继续执行，但应说明颜色或样式归一化等影响。

远端写入可能部分完成。失败后核对页面，不把失败当作未修改；原生画板创建后失败时，先拉取到新路径检查并恢复映射。双端冲突、远端画板被删除或不支持的内容不能通过删除本地元数据绕过。

## 文件与格式

- 拉取默认保存文档映射到 YAML front matter；`--no-front-matter` 可关闭，后续单篇操作需继续传 `--doc`。显式 URL 不应与文件映射冲突。
- 本地 Markdown 相对链接仅在目标有文档映射时转为云端链接。Wiki 树可以只提供根 URL，按同层唯一标题匹配；远端缺少页面时需先通过 Wiki 工具创建。
- 普通图片支持 PNG、JPEG、GIF、WebP、BMP。普通 `.svg` 不作为画板，创建可编辑画板使用 `.board.svg`。SVG 导入不保证所有图形属性或连线关系无损。
- Mermaid 支持 flowchart、sequenceDiagram、classDiagram、stateDiagram/stateDiagram-v2、erDiagram、gantt、pie、mindmap；其他类型不保证往返。
- 高亮块支持 NOTE、TIP、IMPORTANT、WARNING、CAUTION，使用固定样式；块内不放图片、代码块、表格或画板。
- 字体颜色写成 `<span style="color:#d83931">红字</span>`；颜色会归一到飞书色板。本地渲染器可能过滤样式；表格内颜色暂不支持可靠回读。
- 同步不是云文档完整备份，不保证保留全部评论、排版或专有组件。遇到无法恢复的附件、同步块、Sheet/Base 等，应改用对应工具。

## Skill 安装与更新

Python CLI 与 Agent Skill 分别安装。用户要求安装到共享用户目录时：

```bash
larkmd-sync --json skill install --dest "$HOME/.agents/skills" --dry-run
larkmd-sync --json skill install --dest "$HOME/.agents/skills"
```

安装目录为 `~/.agents/skills/larkmd-sync/`；`--agent codex --scope user` 当前也指向此位置。Claude 专用目录使用 `--agent claude --scope user`。

升级 CLI 后，用 `skill update` 替换上述 `skill install`，先预览再执行。已有本地修改时先检查差异，不默认使用 `--force`。其他操作查看 `larkmd-sync skill --help` 及子命令帮助。

画板只编辑 SVG，JSON 是同步元数据。上传成功后本地 SVG 会更新为云端最终导出的版本，保留原稿请另存。新建画板按 SVG 比例设置显示区域；已有画板比例变化时，需在飞书中调整画板块显示大小。旧版 JSON nodes 有未同步修改时不会自动丢弃。
