import json
from unittest.mock import patch
from urllib.parse import unquote

import pytest

from larkmd_sync import compare_file, pull_file, pull_tree, push_file
from larkmd_sync.core.preprocess import (
    localize_document_links,
    rewrite_local_markdown_links,
)
from larkmd_sync.sync import apply_tree_pull, plan_tree_pull

ROOT_URL = "https://tenant.feishu.cn/wiki/rootToken"
CHILD_URL = "https://tenant.feishu.cn/wiki/childToken"
DOCX_URL = "https://tenant.feishu.cn/docx/childDocument"


def remote(content):
    return {"content": content, "document_id": "rootDocument", "revision_id": 1}


def test_local_links_round_trip_original_url_even_without_target_file(tmp_path):
    target = tmp_path / "子目录" / "目标 (新).md"
    url = CHILD_URL + "?from=wiki--menu#section"
    original = f'[目标]({url} "说明") 与 [另一个]({CHILD_URL})\n'
    local = localize_document_links(
        original, tmp_path / "source.md", {CHILD_URL: target}
    )

    assert "子目录/目标 (新).md#section" in unquote(local)
    assert "<!-- larkmd-link:" in local
    assert "wiki--menu" not in local  # Valid HTML comments cannot contain --.
    assert rewrite_local_markdown_links(local, tmp_path / "source.md") == original


def test_link_edits_override_saved_target_and_bad_metadata_is_rejected(tmp_path):
    source = tmp_path / "source.md"
    local = localize_document_links(
        f"[旧标题]({CHILD_URL})", source, {CHILD_URL: tmp_path / "old.md"}
    )
    edited = local.replace("[旧标题](old.md)", "[新标题](new.md#new)")
    assert (
        rewrite_local_markdown_links(
            edited,
            source,
            document_urls={
                tmp_path / "new.md": ROOT_URL,
            },
        )
        == f"[新标题]({ROOT_URL}#new)"
    )
    assert rewrite_local_markdown_links(
        local.replace("[旧标题]", "[新标题]"), source
    ).startswith(f"[新标题]({CHILD_URL})")
    with pytest.raises(ValueError, match="注释损坏"):
        rewrite_local_markdown_links(local.replace('"url":', '"broken":'), source)


def test_code_images_comments_unmapped_and_ambiguous_targets_are_literal(tmp_path):
    link = f"[目标]({CHILD_URL})"
    source = (
        f"`{link}`\n````md\n{link}\n```\n{link}\n````\n"
        f"~~~md\n{link}\n~~~\n    {link}\n"
        f"![图]({CHILD_URL})\n\\{link}\n<!--\n{link}\n-->\n"
        "[外部](https://other.feishu.cn/wiki/childToken)\n"
        "[未下载](https://tenant.feishu.cn/wiki/otherToken)\n"
    )
    paths = {CHILD_URL: tmp_path / "target.md"}
    assert localize_document_links(source, tmp_path / "source.md", paths) == source
    assert rewrite_local_markdown_links(source, tmp_path / "source.md") == source
    paths[CHILD_URL + "?alias=1"] = tmp_path / "duplicate.md"
    assert localize_document_links(link, tmp_path / "source.md", paths) == link


@pytest.mark.parametrize("explicit_plan", [False, True])
def test_tree_localizes_forward_and_parent_links_without_front_matter(
    tmp_path, explicit_plan
):
    root = {
        "space_id": "space",
        "node_token": "rootToken",
        "obj_type": "docx",
        "obj_token": "rootDocument",
        "title": "Root",
        "has_child": True,
    }
    child = {
        "node_token": "childToken",
        "obj_type": "docx",
        "obj_token": "childDocument",
        "title": "Child",
        "has_child": False,
    }
    root_text = (
        f"# Root\n\n[子页]({CHILD_URL}?source=wiki#part)\n[docx别名]({DOCX_URL})\n"
    )
    child_text = f"# Child\n\n[父页]({ROOT_URL})\n"

    def fetch(cli, url, **kwargs):
        if url == CHILD_URL:
            # The default path must still save pages before fetching the next one.
            if not explicit_plan:
                assert (tmp_path / "Root.md").is_file()
            return remote(child_text)
        return remote(root_text)

    with (
        patch("larkmd_sync.sync.get_wiki_node", return_value=root),
        patch("larkmd_sync.sync.list_wiki_children", return_value=[child]),
        patch("larkmd_sync.sync.fetch_markdown", side_effect=fetch) as reader,
    ):
        if explicit_plan:
            plan = plan_tree_pull(
                "unused", ROOT_URL, tmp_path, include_front_matter=False
            )
            assert not (tmp_path / "Root.md").exists()
            result = apply_tree_pull(
                "unused",
                ROOT_URL,
                tmp_path,
                plan.data.plan_id,
                include_front_matter=False,
            )
        else:
            result = pull_tree(
                ROOT_URL, tmp_path, lark_cli="unused", include_front_matter=False
            )
            assert reader.call_count == 2
    assert result.ok
    root_file = tmp_path / "Root.md"
    child_file = tmp_path / "Root" / "Child.md"
    assert "[子页](Root/Child.md#part) <!-- larkmd-link:" in root_file.read_text()
    assert "[父页](../Root.md) <!-- larkmd-link:" in child_file.read_text()
    assert rewrite_local_markdown_links(root_file.read_text(), root_file) == root_text
    assert (
        rewrite_local_markdown_links(child_file.read_text(), child_file) == child_text
    )

    # Single-page compare and push use the annotation, not the target's front matter.
    with (
        patch("larkmd_sync.sync.fetch_markdown", return_value=remote(root_text)),
        patch("larkmd_sync.sync.overwrite_doc", return_value={}) as writer,
    ):
        assert compare_file(root_file, ROOT_URL, lark_cli="unused").data.identical
        assert push_file(root_file, ROOT_URL, lark_cli="unused").ok
    assert writer.call_args.args[2] == root_text


def test_single_pull_localizes_mapped_siblings_and_plan_tracks_mapping(tmp_path):
    sibling = tmp_path / "target.md"
    sibling.write_text(f"---\nfeishu_url: {CHILD_URL}\n---\n")
    source = tmp_path / "source.md"
    with patch(
        "larkmd_sync.sync.fetch_markdown", return_value=remote(f"[子页]({CHILD_URL})\n")
    ):
        planned = pull_file(source, ROOT_URL, lark_cli="unused", dry_run=True)
        sibling.rename(tmp_path / "renamed.md")
        stale = pull_file(
            source, ROOT_URL, lark_cli="unused", plan_id=planned.data.plan_id
        )
        assert not stale.ok and stale.status == 409
        assert not source.exists()
        assert pull_file(source, ROOT_URL, lark_cli="unused").ok
    content = source.read_text()
    assert "[子页](renamed.md)" in content
    annotation = content.split("<!-- larkmd-link: ")[1].split(" -->")[0]
    assert json.loads(annotation)["url"] == CHILD_URL
