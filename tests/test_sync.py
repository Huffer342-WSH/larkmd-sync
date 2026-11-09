from pathlib import Path
from unittest.mock import patch

from larkmd_sync import compare_file, pull_file, push_file, push_tree, tree_status
from larkmd_sync.resources import DownloadedMedia, WhiteboardSource
from larkmd_sync.sync import apply_tree_pull, plan_tree_pull, resolve_document_url

URL = "https://tenant.feishu.cn/wiki/wikcnDocument123"
PNG = b"\x89PNG\r\n\x1a\n" + b"sync-image"


def _markdown(
    revision: int = 1,
    *,
    content: str = "# Test\n\nHello\n",
    reference_map: object = None,
    tips: str = "",
) -> dict:
    return {
        "content": content,
        "document_id": "docxDocument123",
        "revision_id": revision,
        "reference_map": {} if reference_map is None else reference_map,
        "tips": tips,
    }


def test_pull_apply_rejects_stale_remote_revision(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    revision = {"value": 1}

    def markdown_result(*args, **kwargs):
        return _markdown(revision["value"])

    with patch("larkmd_sync.sync.fetch_markdown", side_effect=markdown_result):
        planned = pull_file(target, URL, lark_cli="lark-cli")
        revision["value"] = 2
        applied = pull_file(
            target,
            URL,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert not applied.ok
    assert applied.status == 409
    assert not target.exists()


def test_pull_downloads_images_and_restores_mermaid(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    remote = _markdown(
        content=(
            '# Test\n\n<img token="img-token" name="diagram.png" '
            'caption="架构图"/>\n\n<whiteboard token="board-token"/>\n'
        )
    )
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=remote),
        patch(
            "larkmd_sync.sync.download_media",
            return_value=DownloadedMedia(PNG, ".png"),
        ),
        patch(
            "larkmd_sync.sync.export_whiteboard_source",
            return_value=WhiteboardSource("mermaid", "flowchart LR\n    A --> B"),
        ),
    ):
        planned = pull_file(target, URL, lark_cli="lark-cli")
        applied = pull_file(
            target,
            URL,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert [action.action for action in planned.data.actions] == [
        "write_local_markdown",
        "write_local_image",
    ]
    assert applied.ok
    assert applied.data.assets_changed == 1
    assert (tmp_path / ".assets" / "document" / "diagram.png").read_bytes() == PNG
    markdown = target.read_text(encoding="utf-8")
    assert "![架构图](./.assets/document/diagram.png)" in markdown
    assert "```mermaid\nflowchart LR\n    A --> B\n```" in markdown


def test_push_strips_front_matter_and_keeps_gfm_table(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    table = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    target.write_text(
        f'---\ntitle: "Test"\nfeishu_url: "{URL}"\n---\n\n# Test\n\n{table}',
        encoding="utf-8",
    )
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch(
            "larkmd_sync.sync.overwrite_doc",
            return_value={"document_id": "docxDocument123", "revision_id": 2},
        ) as overwrite,
    ):
        planned = push_file(target, lark_cli="lark-cli")
        applied = push_file(
            target,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert applied.ok
    pushed = overwrite.call_args.args[2]
    assert "feishu_url:" not in pushed
    assert table in pushed
    assert "<table" not in pushed


def test_push_uploads_local_image_and_creates_mermaid_board(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    image = tmp_path / "assets" / "diagram.png"
    image.parent.mkdir()
    image.write_bytes(PNG)
    target.write_text(
        f"---\nfeishu_url: {URL}\n---\n\n"
        "# Test\n\n![架构图](./assets/diagram.png)\n\n"
        "```mermaid\nflowchart TD\n    A --> B\n```\n",
        encoding="utf-8",
    )
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch(
            "larkmd_sync.sync.overwrite_doc",
            return_value={"document_id": "docxDocument123", "revision_id": 2},
        ) as overwrite,
        patch("larkmd_sync.sync.insert_local_image") as insert_image,
        patch("larkmd_sync.sync.remove_image_marker") as remove_marker,
    ):
        planned = push_file(target, lark_cli="lark-cli")
        applied = push_file(
            target,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert [action.action for action in planned.data.actions] == [
        "overwrite_remote_markdown",
        "insert_remote_image",
    ]
    pushed = overwrite.call_args.args[2]
    assert "![架构图]" not in pushed
    assert "LARKMD_SYNC_IMAGE_0001_" in pushed
    assert '<whiteboard type="mermaid">' in pushed
    assert applied.ok
    assert applied.data.assets_changed == 1
    assert insert_image.call_args.args[:3] == (
        "lark-cli",
        "docxDocument123",
        image,
    )
    marker = insert_image.call_args.args[3]
    assert insert_image.call_args.kwargs["caption"] == "架构图"
    assert not insert_image.call_args.kwargs["after"]
    assert remove_marker.call_args.args[:3] == (
        "lark-cli",
        "docxDocument123",
        marker,
    )


def test_push_restores_sequence_after_interrupted_list_image(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    image = tmp_path / "image.png"
    image.write_bytes(PNG)
    target.write_text(
        f"---\nfeishu_url: {URL}\n---\n\n"
        "1. 第一步\n\n![截图](./image.png)\n\n2. 第二步\n",
        encoding="utf-8",
    )
    marker_holder: dict[str, str] = {}

    def capture_overwrite(*args, **kwargs):
        marker_holder["marker"] = next(
            word
            for word in args[2].split()
            if word.startswith("LARKMD_SYNC_SEQUENCE_")
        )
        return {"document_id": "docxDocument123", "revision_id": 2}

    def full_xml(*args, **kwargs):
        marker = marker_holder["marker"]
        return (
            '<ol><li id="first" seq="1">第一步</li></ol>'
            '<img id="image" src="token"/>'
            f'<ol><li id="second" seq="1">第二步 {marker}</li></ol>'
        )

    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch(
            "larkmd_sync.sync.overwrite_doc",
            side_effect=capture_overwrite,
        ),
        patch("larkmd_sync.sync.insert_local_image") as insert_image,
        patch("larkmd_sync.sync.remove_image_marker"),
        patch("larkmd_sync.sync.fetch_document_xml", side_effect=full_xml),
        patch("larkmd_sync.sync.replace_block_xml") as replace_block,
    ):
        planned = push_file(target, lark_cli="lark-cli")
        applied = push_file(
            target,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert applied.ok
    assert insert_image.call_args.kwargs["after"]
    replace_block.assert_called_once_with(
        "lark-cli",
        "docxDocument123",
        "second",
        '<ol><li seq="2">第二步</li></ol>',
        identity="user",
    )


def test_push_styles_alert_as_exact_callout_block(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    target.write_text(
        f"---\nfeishu_url: {URL}\n---\n\n"
        "> [!WARNING]\n> **发布前检查**\n",
        encoding="utf-8",
    )
    marker_holder: dict[str, str] = {}

    def capture_overwrite(*args, **kwargs):
        marker_holder["marker"] = next(
            line
            for line in args[2].splitlines()
            if line.startswith("LARKMD_SYNC_CALLOUT_")
        )
        return {"document_id": "docxDocument123", "revision_id": 2}

    def full_xml(*args, **kwargs):
        marker = marker_holder["marker"]
        return (
            '<callout id="callout" emoji="💡">'
            '<p id="content"><b>发布前检查</b></p>'
            f'<p id="marker">{marker}</p>'
            "</callout>"
        )

    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch("larkmd_sync.sync.overwrite_doc", side_effect=capture_overwrite),
        patch("larkmd_sync.sync.fetch_document_xml", side_effect=full_xml) as fetch_xml,
        patch("larkmd_sync.sync.replace_block_xml") as replace_block,
    ):
        planned = push_file(target, lark_cli="lark-cli")
        applied = push_file(
            target,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert applied.ok
    fetch_xml.assert_called_once()
    replacement = replace_block.call_args.args[3]
    assert replace_block.call_args.args[2] == "callout"
    assert 'emoji="📌"' in replacement
    assert 'background-color="light-yellow"' in replacement
    assert 'border-color="yellow"' in replacement
    assert marker_holder["marker"] not in replacement
    assert "<b>发布前检查</b>" in replacement


def test_pull_converts_callout_and_warns_about_style_normalization(
    tmp_path: Path,
) -> None:
    target = tmp_path / "document.md"
    remote = _markdown(
        content='<callout emoji="❌">\n危险操作\n</callout>\n',
    )

    with patch("larkmd_sync.sync.fetch_markdown", return_value=remote):
        planned = pull_file(target, URL, lark_cli="lark-cli")
        applied = pull_file(
            target,
            URL,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert applied.ok
    assert "> [!CAUTION]\n> 危险操作" in target.read_text(encoding="utf-8")
    assert any(
        warning.code == "callout_style_normalized"
        for warning in planned.data.warnings
    )


def test_diff_compares_image_bytes_and_mermaid_source(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    image = tmp_path / "assets" / "diagram.png"
    image.parent.mkdir()
    image.write_bytes(PNG)
    target.write_text(
        f"---\nfeishu_url: {URL}\n---\n\n"
        "# Test\n\n![架构图](./assets/diagram.png)\n\n"
        "```mermaid\nflowchart LR\n    A --> B\n```\n",
        encoding="utf-8",
    )
    remote = _markdown(
        content=(
            '# Test\n\n<img token="img-token" name="diagram.png" '
            'caption="架构图"/>\n\n<whiteboard token="board-token"/>\n'
        )
    )
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=remote),
        patch(
            "larkmd_sync.sync.download_media",
            return_value=DownloadedMedia(PNG, ".png"),
        ),
        patch(
            "larkmd_sync.sync.export_whiteboard_source",
            return_value=WhiteboardSource("mermaid", "flowchart LR\n    A --> B"),
        ),
    ):
        result = compare_file(target, lark_cli="lark-cli")

    assert result.ok
    assert result.data.identical
    assert not result.data.detail.text_changed
    assert not result.data.detail.assets_changed
    assert result.data.detail.local_assets == 1
    assert result.data.detail.remote_assets == 1


def test_missing_front_matter_requires_runtime_url(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    target.write_text("# Local document\n", encoding="utf-8")

    result = push_file(target, lark_cli="lark-cli")

    assert not result.ok
    assert result.status == 422
    assert "--doc" in result.error


def test_explicit_url_must_match_front_matter(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    target.write_text(f"---\nfeishu_url: {URL}\n---\n\n# Test\n", encoding="utf-8")

    try:
        resolve_document_url(
            target,
            "https://tenant.feishu.cn/wiki/wikcnDifferent999",
        )
    except ValueError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("mismatched target must fail")


def test_remote_cloud_feature_is_reported_without_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    target.write_text(f"---\nfeishu_url: {URL}\n---\n\n# Test\n", encoding="utf-8")
    remote = _markdown(content='# Test\n\n<synced-source token="source123"></synced-source>\n')
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=remote),
        patch("larkmd_sync.sync.overwrite_doc") as overwrite,
    ):
        result = push_file(target, lark_cli="lark-cli")
        applied = push_file(
            target,
            plan_id=result.data.plan_id,
            lark_cli="lark-cli",
        )

    assert result.ok
    assert any(
        warning.code == "markdown_feature_degraded" and warning.blocking
        for warning in result.data.warnings
    )
    assert not applied.ok
    assert applied.status == 409
    overwrite.assert_not_called()
    assert not (tmp_path / ".larkmd").exists()


def test_push_apply_rejects_stale_plan_without_remote_write(tmp_path: Path) -> None:
    target = tmp_path / "document.md"
    target.write_text("# Local document\n", encoding="utf-8")
    revision = {"value": 1}

    def markdown_result(*args, **kwargs):
        return _markdown(revision["value"])

    with (
        patch("larkmd_sync.sync.fetch_markdown", side_effect=markdown_result),
        patch("larkmd_sync.sync.overwrite_doc") as overwrite,
    ):
        planned = push_file(target, URL, lark_cli="lark-cli")
        revision["value"] = 2
        applied = push_file(
            target,
            URL,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert not applied.ok
    assert applied.status == 409
    overwrite.assert_not_called()


def _wiki_nodes():
    root = {
        "space_id": "123456",
        "node_token": "wikcnRoot12345",
        "obj_token": "docxRoot12345",
        "obj_type": "docx",
        "title": "Root",
        "has_child": True,
    }
    child = {
        "space_id": "123456",
        "node_token": "wikcnChild123",
        "obj_token": "docxChild123",
        "obj_type": "docx",
        "title": "Child",
        "has_child": False,
    }
    sheet = {
        "space_id": "123456",
        "node_token": "wikcnSheet123",
        "obj_token": "shtcnSheet123",
        "obj_type": "sheet",
        "title": "Data",
        "has_child": False,
    }
    return root, child, sheet


def test_tree_pull_uses_each_markdown_front_matter_without_manifest(
    tmp_path: Path,
) -> None:
    root, child, sheet = _wiki_nodes()
    output = tmp_path / "wiki"
    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child, sheet]),
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
    ):
        planned = plan_tree_pull("lark-cli", URL, output)
        assert len(planned.data.actions) == 2
        applied = apply_tree_pull(
            "lark-cli",
            URL,
            output,
            planned.data.plan_id,
        )

    assert applied.ok
    files = sorted(path.relative_to(output) for path in output.rglob("*"))
    assert files == [Path("Root"), Path("Root/Child.md"), Path("Root.md")]
    assert "feishu_url:" in (output / "Root.md").read_text(encoding="utf-8")
    assert "feishu_url:" in (output / "Root" / "Child.md").read_text(encoding="utf-8")
    assert not (output / ".larkmd").exists()


def test_tree_push_and_status_scan_front_matter(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    first = root / "first.md"
    second = root / "nested" / "second.md"
    second.parent.mkdir()
    first.write_text(
        f"---\nfeishu_url: {URL}\n---\n\n# Test\n\nHello\n", encoding="utf-8"
    )
    second_url = "https://tenant.feishu.cn/wiki/wikcnSecond123"
    second.write_text(
        f"---\nfeishu_url: {second_url}\n---\n\n# Test\n\nHello\n",
        encoding="utf-8",
    )
    (root / "notes.md").write_text("# Local only\n", encoding="utf-8")
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch(
            "larkmd_sync.sync.overwrite_doc",
            return_value={"document_id": "doc", "revision_id": 2},
        ) as overwrite,
    ):
        planned = push_tree(root, lark_cli="lark-cli")
        applied = push_tree(
            root,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )
        status = tree_status(root, lark_cli="lark-cli")

    assert applied.ok
    assert overwrite.call_count == 2
    assert len(status.data.items) == 2
    assert all(item.identical for item in status.data.items)
    assert any(
        warning.code == "missing_document_mapping" for warning in planned.data.warnings
    )
    assert not (root / ".larkmd").exists()


def test_tree_push_maps_plain_markdown_from_root_url_and_rewrites_links(
    tmp_path: Path,
) -> None:
    root_node, child_node, _ = _wiki_nodes()
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "README.md").write_text(
        "# Local project\n\n[Child](./child.md)\n",
        encoding="utf-8",
    )
    (root / "child.md").write_text("# Child\n\nDetails\n", encoding="utf-8")
    child_url = "https://tenant.feishu.cn/wiki/wikcnChild123"

    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root_node),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child_node]),
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
        patch(
            "larkmd_sync.sync.overwrite_doc",
            return_value={"document_id": "doc", "revision_id": 2},
        ) as overwrite,
    ):
        planned = push_tree(root, URL, lark_cli="lark-cli")
        applied = push_tree(
            root,
            URL,
            plan_id=planned.data.plan_id,
            lark_cli="lark-cli",
        )

    assert planned.ok
    assert planned.data.conflicts == ()
    assert [action.document_url for action in planned.data.actions] == [
        URL,
        child_url,
    ]
    assert applied.ok
    assert applied.data.changed == 2
    assert child_url in overwrite.call_args_list[0].args[2]
    assert "tenant.feishu.cn" not in (root / "README.md").read_text(encoding="utf-8")
    assert "tenant.feishu.cn" not in (root / "child.md").read_text(encoding="utf-8")


def test_tree_push_uses_only_canonical_root_file_mapping(tmp_path: Path) -> None:
    root_node, child_node, _ = _wiki_nodes()
    root = tmp_path / "wiki"
    child_dir = root / "Root"
    child_dir.mkdir(parents=True)
    (root / "Root.md").write_text(
        f"---\nfeishu_url: {URL}\n---\n\n# Root\n",
        encoding="utf-8",
    )
    (child_dir / "Child.md").write_text("# Child\n", encoding="utf-8")

    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root_node),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child_node]),
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
    ):
        planned = push_tree(root, lark_cli="lark-cli")

    assert planned.ok
    assert planned.data.conflicts == ()
    assert len(planned.data.actions) == 2
    assert not any(
        warning.code == "missing_document_mapping"
        for warning in planned.data.warnings
    )


def test_tree_push_reports_plain_markdown_without_remote_title_match(
    tmp_path: Path,
) -> None:
    root_node, child_node, _ = _wiki_nodes()
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "README.md").write_text("# Root\n", encoding="utf-8")
    unknown = root / "unknown.md"
    unknown.write_text("# Unknown\n", encoding="utf-8")

    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root_node),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child_node]),
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
    ):
        planned = push_tree(root, URL, lark_cli="lark-cli")

    assert planned.ok
    assert any(str(unknown) in conflict for conflict in planned.data.conflicts)
    assert any(
        warning.code == "remote_document_unmapped"
        for warning in planned.data.warnings
    )


def test_tree_pull_can_omit_all_front_matter(tmp_path: Path) -> None:
    root_node, child_node, _ = _wiki_nodes()
    output = tmp_path / "wiki"
    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root_node),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child_node]),
        patch("larkmd_sync.sync.fetch_markdown", return_value=_markdown()),
    ):
        planned = plan_tree_pull(
            "lark-cli",
            URL,
            output,
            include_front_matter=False,
        )
        applied = apply_tree_pull(
            "lark-cli",
            URL,
            output,
            planned.data.plan_id,
            include_front_matter=False,
        )

    assert applied.ok
    assert "feishu_url:" not in (output / "Root.md").read_text(encoding="utf-8")
    assert "feishu_url:" not in (output / "Root" / "Child.md").read_text(
        encoding="utf-8"
    )


def test_pull_pipeline_corrects_bold_and_compares_corrected_snapshot(tmp_path: Path) -> None:
    target = tmp_path / 'corrected.md'
    remote = _markdown(content=(
        '# Test\n\n**一句话读懂：**AC/VLT\n\n'
        '<callout emoji="💡">\n**边界：**正文\n</callout>\n\n'
        '```python\n**代码：**literal\n```\n'
    ))
    with patch('larkmd_sync.sync.fetch_markdown', return_value=remote):
        planned = pull_file(target, URL, lark_cli='lark-cli')
        assert planned.ok and not target.exists()
        applied = pull_file(target, URL, plan_id=planned.data.plan_id, lark_cli='lark-cli')
        assert applied.ok
        text = target.read_text()
        assert text.startswith('---\ntitle: "Test"\nfeishu_url:')
        assert list(tmp_path.iterdir()) == [target]
        assert '**一句话读懂：** AC/VLT' in text
        assert '> **边界：** 正文' in text
        assert '**代码：**literal' in text
        compared = compare_file(target, lark_cli='lark-cli')
        assert compared.ok and compared.data.identical
        assert remote['content'].startswith('# Test\n\n**一句话读懂：**AC/VLT')


def test_heading_number_roundtrip_uses_native_heading_attribute(tmp_path: Path) -> None:
    import re
    target = tmp_path / 'headings.md'
    remote = _markdown(content='<title>Doc</title>\n# 章\n')
    remote['document_xml'] = '<title>Doc</title><h1 seq="auto" seq-marker="1.">章</h1>'
    uploaded = []
    def write(cli, url, content, **kwargs):
        uploaded.append(content)
        return {'document_id': 'docxDocument123'}
    def xml(*args, **kwargs):
        marker = re.search(r'LARKMD_SYNC_HEADING_\w+', uploaded[-1])[0]
        return '<title>Doc</title><h1 id="H">章 ' + marker + '</h1>'
    with patch('larkmd_sync.sync.fetch_markdown', return_value=remote), \
         patch('larkmd_sync.sync.overwrite_doc', side_effect=write), \
         patch('larkmd_sync.sync.fetch_document_xml', side_effect=xml), \
         patch('larkmd_sync.sync.replace_block_xml') as replace:
        plan = pull_file(target, URL, lark_cli='cli')
        assert pull_file(target, URL, plan_id=plan.data.plan_id, lark_cli='cli').ok
        assert '# 1. 章 <!-- larkmd-heading:' in target.read_text()
        plan = push_file(target, lark_cli='cli')
        assert plan.ok
        assert push_file(target, plan_id=plan.data.plan_id, lark_cli='cli').ok
        assert '# 1. 章' not in uploaded[-1]
        assert '# Doc' not in uploaded[-1]
        assert replace.call_args.args[3] == '<h1 seq="auto">章</h1>'
