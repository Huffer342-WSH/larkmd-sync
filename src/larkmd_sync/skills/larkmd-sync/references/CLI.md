# CLI 参考

## 通用约定

```bash
larkmd-sync --json --no-input <command> ...
```

stdout 只包含一个 JSON 对象：

```json
{
  "schema_version": "1",
  "ok": true,
  "data": {},
  "warnings": []
}
```

失败时包含 `error.status` 和 `error.message`。退出码：`0` 成功，`2` 参数或输入校验失败，`3` 文件、映射或计划冲突，`4` 外部依赖、网络或运行失败。

完整 URL 保留租户域名。只传 token 时可以通过 `LARK_BASE_URL` 指定租户。身份参数为 `--as user|bot`，默认 `user`。

## Plan/apply

会修改本地或远端的命令先返回计划：

```bash
larkmd-sync --json --no-input push ./document.md --doc "$URL"
larkmd-sync --json --no-input push ./document.md --doc "$URL" \
  --apply 'sha256:...'
```

apply 会重新读取本地文件和远端 revision。任何一侧变化都会使计划过期。计划不会写入本地文件。

## 单文档

```bash
# 拉取；默认在 Markdown 内写 title 和 feishu_url front matter
larkmd-sync --json --no-input pull ./document.md --doc "$URL"
larkmd-sync --json --no-input pull ./document.md --doc "$URL" \
  --apply 'sha256:...'

# 不写 front matter；后续命令需要继续传 --doc
larkmd-sync --json --no-input pull ./document.md --doc "$URL" \
  --no-front-matter

# 有 URL front matter 时可以省略 --doc
larkmd-sync --json --no-input diff ./document.md
larkmd-sync --json --no-input push ./document.md
```

推送时会移除 front matter，并把指向其他本地 Markdown 的链接转换成目标文件声明的飞书 URL。GFM 表格保持 Markdown，不提供 XML 模式或表格宽度参数。

单独成块的本地相对路径图片会产生 `insert_remote_image` 计划动作。支持 PNG、JPEG、GIF、WebP 和 BMP；图片必须位于 Markdown 同目录或子目录。拉取时图片产生 `write_local_image` 动作并写入 `.assets/<文档名>/`。apply 结果中的 `assets_changed` 和 `asset_paths` 报告实际资源写入。

图片打断有序列表时，飞书媒体接口会重置后续列表编号。工具在图片上传后自动恢复 Markdown 中明确写出的序号，例如 `1.`、图片、`2.` 会继续显示为 1、2。该修复只在内存中读取块 XML，不增加本地文件。

fenced `mermaid` 在推送时创建飞书画板，拉取时恢复为 fenced block。保证双向支持 `flowchart`、`sequenceDiagram`、`classDiagram`、`stateDiagram`/`stateDiagram-v2`、`erDiagram`、`gantt`、`pie` 和 `mindmap`。其他 Mermaid 声明会透传，但不保证飞书能够无损回导。`diff.detail.assets_changed` 单独报告图片字节差异，正文和图片都一致时才返回 `identical: true`。

`> [!NOTE]`、`TIP`、`IMPORTANT`、`WARNING` 和 `CAUTION` 会转换为飞书高亮块。推送在正文覆盖后读取一次块 XML，通过临时标记设置对应图标和固定颜色；临时标记和 block ID 不写入本地文件。拉取按图标恢复 Alert，并返回 `callout_style_normalized` warning，说明飞书任意自定义颜色会在下次回写时归一化。高亮块内暂不允许图片、代码块、表格、Mermaid 或其他资源块。

## 文档树

```bash
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL" \
  --apply 'sha256:...'

# 拉取普通 Markdown，不在任何文件中保存 URL
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL" \
  --no-front-matter
larkmd-sync --json --no-input tree pull ./wiki-docs --doc "$ROOT_URL" \
  --no-front-matter --apply 'sha256:...'

# 每篇 Markdown 已通过 front matter 保存 URL
larkmd-sync --json --no-input tree status ./wiki-docs
larkmd-sync --json --no-input tree push ./wiki-docs
larkmd-sync --json --no-input tree push ./wiki-docs --apply 'sha256:...'

# 只有根 Wiki URL；子文档按目录层级和页面标题匹配
larkmd-sync --json --no-input tree status ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree push ./wiki-docs --doc "$ROOT_URL"
larkmd-sync --json --no-input tree push ./wiki-docs --doc "$ROOT_URL" \
  --apply 'sha256:...'
```

`tree pull/status/push` 支持 `--max-depth N` 和 `--page-limit N`。`tree pull` 默认让每篇文件通过自己的 front matter 保存 URL；`--no-front-matter` 只写普通 Markdown。两种方式都不生成清单。

`tree status/push --doc ROOT_URL` 先遍历远端 Wiki。根页使用 `README.md`、`index.md`、与远端根标题匹配的页面或唯一顶层 Markdown；子页按目录层级和一级标题匹配同层 `docx` 节点，没有一级标题时使用文件名。树内相对 Markdown 链接用同一份映射转换成远端 URL。本地页面找不到同层同标题节点或任一侧同层重名时返回冲突，远端未匹配页面和非 `docx` 节点作为 warning 跳过。

不传 `--doc` 时继续递归读取每篇文件的 URL。`Root.md` 加 `Root/Child.md` 的布局还允许只在 `Root.md` 保存根 URL，工具会自动按层级和标题映射子文件。

Sheet、Base 等非 `docx` 子节点会在拉取计划中列为跳过项。拉取不会删除远端已经移除的本地文件。

## Markdown 限制

工具不保存 XML、reference map 或文档树清单。原生画板保留 `.board.json` 源数据和同步基准，旁边的 `.board.svg` 是本地编辑源与预览，旧 `.board.jpg/png` 仅兼容读取。图片文件本身不是 sidecar，而是本地 Markdown 的正常资源。内部块 XML 只用于修复已经支持的飞书属性，不用于恢复 Markdown 无法表达的结构。`blocking: true` 表示远端包含无法恢复的引用、无法恢复的原生画板或其他专有结构：拉取和比较可以继续，推送必须停止并改用对应的飞书工具。

## Skill 生命周期

```bash
larkmd-sync --json --no-input skill list
larkmd-sync --json --no-input skill validate larkmd-sync
larkmd-sync --json --no-input skill install larkmd-sync \
  --agent codex --scope user --dry-run
larkmd-sync --json --no-input skill install larkmd-sync \
  --agent codex --scope user
larkmd-sync --json --no-input skill status larkmd-sync \
  --agent codex --scope user
```

未知来源或被本地修改的 Skill 默认不覆盖。需要保留现有内容时使用 `--backup`；只有确认可以丢弃时才使用 `--force`。

原生画板计划包含 `write_local_board_source`、`create_native_board`、`keep_native_board`、`update_native_board`、`remote_changed_native_board` 和 `update_local_board_binding` 等动作。已有原生画板的正文更新可能为 `update_text_blocks` 或 `rewrite_text_keep_native_boards`；后者保留画板 token，但会重建其他正文块。
