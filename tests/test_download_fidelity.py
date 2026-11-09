import pytest

from larkmd_sync.core.native_board import native_board_to_mermaid
from larkmd_sync.resources import (
    DownloadedMedia,
    WhiteboardSource,
    materialize_remote_links,
    materialize_remote_resources,
    prepare_markdown_resources,
)
from larkmd_sync.sync import _remote_warnings


def test_mentions_title_entities_and_literal_code():
    cite = '<cite doc-id="ABC" file-type="wiki" title="[XA] A&amp;B" type="doc"></cite>'
    text = (
        "<title>标题</title>\n" + cite + "\n`" + cite + "`\n```xml\n" + cite + "\n```\n"
    )
    converted = materialize_remote_links(text, "https://tenant.feishu.cn")
    assert converted.startswith(
        "# 标题\n[\\[XA\\] A&B](https://tenant.feishu.cn/wiki/ABC)"
    )
    assert converted.count(cite) == 2


def test_user_mentions_keep_identity_on_upload(tmp_path):
    text = '<cite type="user" user-id="ou_abc" user-name="张三"></cite>\n'
    md = materialize_remote_links(text, "https://tenant.feishu.cn")
    assert "[@张三]" in md
    out = prepare_markdown_resources(md, tmp_path / "doc.md")
    assert '<cite type="user" user-id="ou_abc"></cite>' in out.upload_markdown
    assert out.comparison_markdown == md


def test_authenticated_image_download_and_long_filename(tmp_path):
    calls = []

    def media(token):
        calls.append(token)
        return DownloadedMedia(b"\x89PNG\r\n\x1a\nimage", ".png")

    out = materialize_remote_resources(
        "![" + "图片说明" * 100 + "](https://feishu.cn/file/TOKEN)\n",
        tmp_path / "doc.md",
        download_media=media,
        export_whiteboard=lambda _: None,
        download_url=lambda _: pytest.fail("must not fetch login page"),
    )
    assert calls == ["TOKEN"]
    assert not out.warnings
    assert len(out.assets[0].path.name.encode()) < 255
    assert not out.assets[0].path.exists()  # plan must not write


def shape(key, text, x=0, y=0, width=20, height=20):
    return dict(
        id=key,
        type="composite_shape",
        composite_shape={"type": "rect"},
        text={"text": text},
        x=x,
        y=y,
        width=width,
        height=height,
    )


def edge(start, end):
    return {
        "id": "edge",
        "type": "connector",
        "connector": {
            "start": {"attached_object": {"id": start}, "arrow_style": "line_arrow"},
            "end": {"attached_object": {"id": end}, "arrow_style": "none"},
            "captions": {"data": [{"text": "send"}]},
        },
    }


def test_native_board_groups_reverse_arrows_and_rejects_missing_nodes():
    nodes = [
        shape("group", "group", -10, -10, 100, 100),
        shape("a", "A"),
        shape("b", "B", 40),
        edge("a", "b"),
    ]
    md = native_board_to_mermaid(nodes)
    assert 'subgraph n0["group"]' in md
    assert 'n2 -->|"send"| n1' in md
    with pytest.raises(ValueError, match="端点"):
        native_board_to_mermaid(nodes[:-1] + [edge("missing", "a")])
    with pytest.raises(ValueError, match="类型"):
        native_board_to_mermaid(nodes + [{"id": "image", "type": "image"}])


def test_unsupported_export_is_blocking_and_preview_is_not_restoration(tmp_path):
    assert _remote_warnings(
        {}, tmp_path / "doc.md", markdown='<readonly-block type="isv"></readonly-block>'
    )[0].blocking
    out = materialize_remote_resources(
        '<whiteboard token="B"></whiteboard>\n',
        tmp_path / "doc.md",
        download_media=lambda _: None,
        export_whiteboard=lambda _: None,
        download_whiteboard=lambda _: DownloadedMedia(b"\x89PNG\r\n\x1a\nimage"),
    )
    assert out.assets and any(w.blocking for w in out.warnings)


def test_native_conversion_is_explicit_but_not_blocking(tmp_path):
    out = materialize_remote_resources(
        '<whiteboard token="B"></whiteboard>\n',
        tmp_path / "doc.md",
        download_media=lambda _: None,
        export_whiteboard=lambda _: WhiteboardSource(
            "mermaid", "flowchart LR\nA --> B", converted=True
        ),
    )
    assert out.markdown.startswith("```mermaid\n")
    assert out.warnings[0].code == "whiteboard_layout_normalized"
    assert not out.warnings[0].blocking


def test_addon_mermaid_and_unknown_addon(monkeypatch):
    import json
    import subprocess

    from larkmd_sync import lark_cli

    monkeypatch.setattr(lark_cli, 'fetch_document_xml', lambda *a, **kw: '<readonly-block id="B1" type="isv"></readonly-block>')
    source = 'flowchart TB\n A --> B'
    record = json.dumps({'data': json.dumps(source)[1:-1]})
    payload = {'ok':True, 'data':{'block':{'add_ons':{
        'component_type_id':'blk_631fefbbae02400430b8f9f4', 'record':record}}}}
    monkeypatch.setattr(lark_cli, '_run', lambda *a, **kw: subprocess.CompletedProcess([],0,json.dumps(payload),''))
    document = {'document_id':'D1','content':'<readonly-block type="isv"></readonly-block>'}
    assert lark_cli.materialize_addon_mermaid('lark',document) == '```mermaid\n' + source + '\n```'
    payload['data']['block']['add_ons']['component_type_id'] = 'unknown'
    assert lark_cli.materialize_addon_mermaid('lark',document) == document['content']
