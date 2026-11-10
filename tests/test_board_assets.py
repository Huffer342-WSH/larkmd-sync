import copy
import json
from types import SimpleNamespace

import pytest

from larkmd_sync.board_sync import plan_boards
from larkmd_sync.core.board_assets import (
    decide_board,
    load_board,
    nodes_hash,
)
from larkmd_sync.resources import (
    DownloadedMedia,
    WhiteboardSource,
    materialize_remote_resources,
    prepare_markdown_resources,
)

PNG = b"\x89PNG\r\n\x1a\npreview"
NODES = [
    {
        "id": "shape",
        "type": "composite_shape",
        "x": 10.0,
        "y": 20.0,
        "width": 100,
        "height": 80,
        "text": {"text": "shape"},
    },
    {
        "id": "edge",
        "type": "connector",
        "x": 110,
        "y": 20,
        "width": 30,
        "height": 0,
        "connector": {"start": {"attached_object": {"id": "shape"}}},
    },
]


def write_asset(tmp_path):
    out = materialize_remote_resources(
        '<whiteboard token="T"></whiteboard>\n',
        tmp_path / "doc.md",
        download_media=lambda _: None,
        export_whiteboard=lambda _: WhiteboardSource(
            "native", json.dumps({"nodes": NODES})
        ),
        download_whiteboard=lambda _: DownloadedMedia(PNG, ".png"),
        document_id="D",
        document_url="https://tenant.feishu.cn/docx/D",
    )
    for a in out.assets:
        a.path.parent.mkdir(parents=True, exist_ok=True)
        a.path.write_bytes(a.content)
    (tmp_path / "doc.md").write_text(out.markdown)
    return out, prepare_markdown_resources(out.markdown, tmp_path / "doc.md")


def test_native_download_and_upload_do_not_convert_to_mermaid(tmp_path):
    out, prepared = write_asset(tmp_path)
    assert len(out.assets) == 2 and out.assets[1].role == "board_source"
    assert "```mermaid" not in out.markdown
    assert len(prepared.images) == 1
    assert "nodes" not in prepared.images[0].board.value
    assert prepared.images[0].board.value["schema_version"] == 2
    assert prepared.comparison_markdown == out.comparison_markdown
    assert prepared.images[0].digest == nodes_hash(NODES)


def test_node_id_remapping_preserves_text_and_connections():
    other = copy.deepcopy(NODES)
    other[0]["id"] = "new-shape"
    other[1]["id"] = "new-edge"
    other[1]["connector"]["start"]["attached_object"]["id"] = "new-shape"
    assert nodes_hash(other) == nodes_hash(NODES)
    other[0]["text"]["text"] = "new-shape"
    assert nodes_hash(other) != nodes_hash(NODES)


@pytest.mark.parametrize(
    ("local", "remote", "base", "expected"),
    [
        ("a", "a", "b", "keep"),
        ("a", "b", "a", "remote_changed"),
        ("b", "a", "a", "update"),
    ],
)
def test_three_way_board_decisions(local, remote, base, expected):
    assert decide_board(local, remote, base) == expected


def test_both_sides_changed_are_conflict():
    with pytest.raises(ValueError, match="均有修改"):
        decide_board("a", "b", "c")


def test_edited_preview_and_missing_source_rejected(tmp_path):
    _, prepared = write_asset(tmp_path)
    image = prepared.images[0]
    image.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="预览已修改"):
        load_board(image.path)
    image.board.path.unlink()
    with pytest.raises(ValueError, match="缺少"):
        load_board(image.path)


def test_plan_keeps_existing_board_and_rejects_unmapped_board(tmp_path):
    out, prepared = write_asset(tmp_path)
    snapshot = SimpleNamespace(
        assets=out.assets,
        document_id="D",
        document_xml='<title id="D">T</title><whiteboard id="B" token="T"></whiteboard>',
    )
    operations = plan_boards(prepared.images, snapshot)
    assert operations[0].action == "keep" and operations[0].block_id == "B"
    with pytest.raises(ValueError, match="未映射"):
        plan_boards([], snapshot)
    snapshot.document_id = "New"
    snapshot.assets = ()
    snapshot.document_xml = ""
    with pytest.raises(ValueError, match="先拉取为 SVG"):
        plan_boards(prepared.images, snapshot)


def test_native_return_order_does_not_expire_plan(tmp_path):
    def materialize(nodes):
        return materialize_remote_resources(
            '<whiteboard token="T"></whiteboard>\n',
            tmp_path / "x.md",
            download_media=lambda _: None,
            export_whiteboard=lambda _: WhiteboardSource(
                "native", json.dumps({"nodes": nodes})
            ),
            download_whiteboard=lambda _: DownloadedMedia(PNG, ".png"),
            document_id="D",
        )

    a = materialize(NODES)
    b = materialize(list(reversed(NODES)))
    assert [v.digest for v in a.assets] == [v.digest for v in b.assets]


def test_preserving_rewrite_never_deletes_board(monkeypatch):
    from larkmd_sync import board_sync

    calls = []
    monkeypatch.setattr(
        board_sync, "_update", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setattr(
        board_sync,
        "fetch_document_xml",
        lambda *args, **kwargs: (
            '<title id="D">T</title><whiteboard token="T" id="B"/><p id="P">MARKER</p>'
        ),
    )
    op = SimpleNamespace(block_id="B", image=SimpleNamespace(marker="MARKER"))
    board_sync.rewrite_keeping_boards(
        "lark",
        "D",
        "new",
        [op],
        '<title id="D">T</title><p id="old">old</p><whiteboard id="B" token="T"/>',
        identity="user",
    )
    assert calls[0][0][2] == "append"
    deletes = [
        kwargs["block_id"] for args, kwargs in calls if args[2] == "block_delete"
    ]
    assert "B" not in deletes and "old" in deletes
    moves = [kwargs for args, kwargs in calls if args[2] == "block_move_after"]
    assert moves[0]["src_ids"] == "B"


def test_pull_rejects_unsynced_native_source(tmp_path):
    from larkmd_sync.core.board_assets import json_bytes
    from larkmd_sync.sync import _native_pull_conflicts

    out, prepared = write_asset(tmp_path)
    source = prepared.images[0].board.path
    value = json.loads(source.read_text())
    value["schema_version"] = 1
    value["nodes"] = copy.deepcopy(NODES)
    value["nodes"][0]["x"] += 10
    source.write_bytes(json_bytes(value))
    assert _native_pull_conflicts(out.assets[1])


def test_transient_board_readiness_is_retried_without_rewriting(monkeypatch, tmp_path):
    from larkmd_sync import board_sync

    out, prepared = write_asset(tmp_path)
    calls = []
    writes = []

    def export(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise board_sync.LarkCliError("This whiteboard is not ready yet")
        return WhiteboardSource("native", json.dumps({"nodes": NODES}))

    monkeypatch.setattr(board_sync, "export_whiteboard_source", export)
    monkeypatch.setattr(board_sync.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        board_sync,
        "_run",
        lambda *args, **kwargs: pytest.fail("keep must not write remote nodes"),
    )
    op = board_sync.BoardOperation(
        prepared.images[0], "keep", "T", "B", nodes_hash(NODES)
    )
    board_sync.restore_and_bind(
        "lark",
        "D",
        [op],
        identity="user",
        atomic_write=lambda p, c: writes.append((p, c)),
    )
    assert len(calls) == 2 and len(writes) == 1


def test_preserving_rewrite_deletes_list_items_not_virtual_container(monkeypatch):
    from larkmd_sync import board_sync
    calls = []
    monkeypatch.setattr(board_sync, "_update", lambda *a, **k: calls.append((a,k)))
    board_sync.rewrite_keeping_boards("cli", "doc", "new", [],
        '<title id="D">T</title><ul><li id="L1">one</li><li id="L2">two</li></ul>',
        identity="user")
    assert [k["block_id"] for a,k in calls if a[2] == "block_delete"] == ["L2,L1"]


def test_preserving_rewrite_does_not_append_document_title(monkeypatch):
    from larkmd_sync import board_sync
    calls = []
    monkeypatch.setattr(board_sync, "_update", lambda *a, **k: calls.append((a,k)))
    board_sync.rewrite_keeping_boards("cli", "doc", "# Title\n\n## Body\n", [],
        '<title id="D">Title</title><p id="P">old</p>', identity="user")
    assert calls[0][1]["content"] == "## Body\n"


def svg_asset(tmp_path):
    path = tmp_path / 'diagram.board.svg'
    content = '<svg xmlns="http://www.w3.org/2000/svg"><text>old</text></svg>'
    import hashlib
    path.write_text(content)
    path.with_suffix('.json').write_text(json.dumps({
        'schema_version': 1, 'type': 'feishu-whiteboard', 'nodes': NODES,
        'baseline_hash': nodes_hash(NODES),
        'preview_hash': 'sha256:' + hashlib.sha256(content.encode()).hexdigest(),
    }))
    return path


def test_svg_is_editable_while_unchanged_uses_native_baseline(tmp_path):
    path = svg_asset(tmp_path)
    assert load_board(path).digest == nodes_hash(NODES)
    assert load_board(path).svg is None
    path.write_text(path.read_text().replace('old', 'new'))
    board = load_board(path)
    assert board.svg and 'new' in board.svg
    assert board.digest != nodes_hash(NODES)
    assert decide_board(board.digest, nodes_hash(NODES), nodes_hash(NODES)) == 'update'
    with pytest.raises(ValueError, match='均有修改'):
        decide_board(board.digest, 'changed-remotely', nodes_hash(NODES))


def test_svg_rejects_external_resources_and_competing_node_edit(tmp_path):
    path = svg_asset(tmp_path)
    path.write_text('<svg><image href="https://example.com/image"/></svg>')
    with pytest.raises(ValueError, match='外部引用'):
        load_board(path)
    path.write_text('<svg><text>changed</text></svg>')
    source = path.with_suffix('.json')
    data = json.loads(source.read_text())
    data['nodes'][0]['text']['text'] = 'other edit'
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='同时修改'):
        load_board(path)


def test_malformed_svg_reports_validation_error(tmp_path):
    path = svg_asset(tmp_path)
    path.write_text('<svg>')
    with pytest.raises(ValueError, match='SVG 格式'):
        load_board(path)


def test_svg_downloader_receives_fresh_node_fingerprint(tmp_path):
    calls = []
    nodes = copy.deepcopy(NODES)
    def svg(token, digest):
        calls.append((token, digest))
        return DownloadedMedia(b'<svg/>', '.svg')
    def materialize():
        return materialize_remote_resources(
            '<whiteboard token="T"></whiteboard>\n', tmp_path / 'doc.md',
            download_media=lambda _: None,
            export_whiteboard=lambda _: WhiteboardSource('native', json.dumps({'nodes': nodes})),
            download_whiteboard=lambda _: pytest.fail('must use SVG'),
            download_whiteboard_svg=svg, document_id='D',
        )
    materialize()
    materialize()
    assert calls[0] == calls[1]
    nodes[0]['x'] += 0.00001
    materialize()
    assert calls[2][0] == 'T'
    assert calls[2][1] != calls[0][1]


@pytest.mark.parametrize('failure', [None, 'readback', 'write', 'local_edit', 'json_appeared'])
def test_first_svg_creates_binding_only_after_verified_upload(tmp_path, monkeypatch, failure):
    from larkmd_sync import board_sync, push_file

    svg = tmp_path / 'new.board.svg'
    original = '<svg xmlns="http://www.w3.org/2000/svg"><text>shape</text></svg>'
    svg.write_text(original)
    md = tmp_path / 'new.md'
    md.write_text('![new](new.board.svg)\n')
    remote = {'content': '# Remote\n', 'document_id': 'D', 'revision_id': 1}
    monkeypatch.setattr('larkmd_sync.sync.fetch_markdown', lambda *a, **k: remote)
    plan = push_file(md, 'https://tenant.feishu.cn/docx/D', lark_cli='unused', dry_run=True)
    assert plan.ok and not plan.data.conflicts
    assert {'create_native_board', 'write_local_board_source'} <= {a.action for a in plan.data.actions}
    assert not svg.with_suffix('.json').exists()

    prepared = prepare_markdown_resources(md.read_text(), md)
    image = prepared.images[0]
    marker_xml = f'<p id="M">{image.marker}</p><whiteboard id="B" token="T"/>'
    monkeypatch.setattr(board_sync, 'fetch_document_xml', lambda *a, **k: marker_xml)
    monkeypatch.setattr(board_sync, 'create_sized_board', lambda *a, **k: 'T')
    monkeypatch.setattr(board_sync, '_update', lambda *a, **k: None)
    uploads = []

    def upload(cli, args, **kwargs):
        uploads.append((args, kwargs))
        return SimpleNamespace(stdout='{"ok":true}', stderr='', returncode=0)

    def readback(*args, **kwargs):
        if failure == 'readback':
            return WhiteboardSource('native', '{"nodes":[]}')
        if failure == 'local_edit':
            svg.write_text('<svg><text>local edit</text></svg>')
        if failure == 'json_appeared':
            svg.with_suffix('.json').write_text('user data')
        return WhiteboardSource('native', json.dumps({'nodes': NODES}))

    def write(path, content):
        if failure == 'write':
            raise OSError('disk full')
        path.write_bytes(content)

    monkeypatch.setattr(board_sync, '_run', upload)
    monkeypatch.setattr(board_sync, 'export_whiteboard_source', readback)
    monkeypatch.setattr(board_sync, 'export_whiteboard_svg', lambda *a, **k: DownloadedMedia(original.replace('shape', 'shape ').encode(), '.svg'))
    monkeypatch.setattr('larkmd_sync.sync.overwrite_doc', lambda *a, **k: {'document_id': 'D'})
    monkeypatch.setattr('larkmd_sync.sync._atomic_write', write)
    applied = push_file(md, 'https://tenant.feishu.cn/docx/D',
                        lark_cli='unused', plan_id=plan.data.plan_id)
    assert len(uploads) == 1
    assert uploads[0][0][uploads[0][0].index('--input_format') + 1] == 'svg'
    assert uploads[0][1]['stdin_data'] == original
    if failure:
        assert not applied.ok
        assert '画板 T' in applied.error and '先拉取' in applied.error
        if failure == 'json_appeared':
            assert svg.with_suffix('.json').read_text() == 'user data'
        else:
            assert not svg.with_suffix('.json').exists()
        # An orphan remote board must block a retry instead of creating a duplicate.
        asset = SimpleNamespace(role='board_source', content=json.dumps({
            'nodes': NODES, 'bindings': {'D': {'token': 'T', 'baseline_hash': nodes_hash(NODES)}}}))
        snapshot = SimpleNamespace(assets=[asset], document_id='D', document_xml=marker_xml)
        with pytest.raises(ValueError, match='未映射'):
            plan_boards(prepared.images, snapshot)
    else:
        assert applied.ok
        assert svg.read_text() == original.replace("shape", "shape ")
        assert "nodes" not in json.loads(svg.with_suffix(".json").read_text())
        board = load_board(svg)
        assert board.svg is None and board.value['bindings']['D']['token'] == 'T'
        asset = SimpleNamespace(role='board_source', content=svg.with_suffix('.json').read_bytes())
        snapshot = SimpleNamespace(assets=[asset], document_id='D', document_xml=marker_xml)
        repeated = prepare_markdown_resources(md.read_text(), md)
        assert plan_boards(repeated.images, snapshot)[0].action == 'keep'


def test_first_svg_validates_and_rejects_duplicate_and_unsafe_sidecar(tmp_path):
    svg = tmp_path / 'new.board.svg'
    svg.write_text('<svg><script>bad()</script></svg>')
    with pytest.raises(ValueError, match='脚本'):
        load_board(svg)
    svg.write_text('<svg/>')
    prepared = prepare_markdown_resources('![a](new.board.svg)\n\n![b](new.board.svg)\n', tmp_path / 'doc.md')
    with pytest.raises(ValueError, match='重复引用'):
        plan_boards(prepared.images, SimpleNamespace(assets=[], document_id='D'))
    svg.with_suffix('.json').symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='安全'):
        load_board(svg)


def test_first_svg_plan_expires_if_sidecar_appears(tmp_path, monkeypatch):
    from larkmd_sync import push_file

    svg = tmp_path / 'new.board.svg'
    svg.write_text('<svg><text>shape</text></svg>')
    md = tmp_path / 'doc.md'
    md.write_text('![new](new.board.svg)\n')
    monkeypatch.setattr('larkmd_sync.sync.fetch_markdown', lambda *a, **k: {
        'content': '# Remote\n', 'document_id': 'D', 'revision_id': 1})
    monkeypatch.setattr('larkmd_sync.sync.overwrite_doc',
                        lambda *a, **k: pytest.fail('stale plan must not write remote'))
    plan = push_file(md, 'https://tenant.feishu.cn/docx/D', lark_cli='unused', dry_run=True)
    assert plan.ok
    import hashlib

    svg.with_suffix('.json').write_text(json.dumps({
        'schema_version': 1, 'type': 'feishu-whiteboard', 'nodes': NODES,
        'baseline_hash': nodes_hash(NODES), 'bindings': {},
        'preview_hash': 'sha256:' + hashlib.sha256(svg.read_bytes()).hexdigest(),
    }))
    result = push_file(md, 'https://tenant.feishu.cn/docx/D', lark_cli='unused',
                       plan_id=plan.data.plan_id)
    assert not result.ok and result.status == 409


@pytest.mark.parametrize('svg,size', [
    ('<svg viewBox="0 0 1240 1420"/>', (820, 939)),
    ('<svg width="720px" height="200px"/>', (820, 228)),
    ('<svg viewBox="0,0,100,10000"/>', (820, 1640)),
    ('<svg/>', (820, 460)),
])
def test_svg_document_size(svg, size):
    from larkmd_sync.core.board_assets import svg_display_size
    assert svg_display_size(svg) == size


def test_legacy_node_edits_block_even_without_svg_edit(tmp_path):
    path = svg_asset(tmp_path)
    metadata = path.with_suffix('.json')
    value = json.loads(metadata.read_text())
    value['nodes'][0]['x'] += 1
    metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='未同步修改'):
        load_board(path)


def test_legacy_keep_migrates_without_retaining_nodes(tmp_path, monkeypatch):
    from larkmd_sync import board_sync
    path = svg_asset(tmp_path)
    prepared = prepare_markdown_resources('![x](diagram.board.svg)\n', tmp_path / 'doc.md')
    monkeypatch.setattr(board_sync, 'export_whiteboard_source',
                        lambda *a, **k: WhiteboardSource('native', json.dumps({'nodes': NODES})))
    monkeypatch.setattr(board_sync, '_run', lambda *a, **k: pytest.fail('keep must not upload'))
    board_sync.restore_and_bind('cli', 'D', [board_sync.BoardOperation(
        prepared.images[0], 'keep', 'T', 'B', nodes_hash(NODES))], identity='user',
        atomic_write=lambda p, b: p.write_bytes(b))
    value = json.loads(path.with_suffix('.json').read_text())
    assert value['schema_version'] == 2 and 'nodes' not in value
    assert load_board(path).digest == nodes_hash(NODES)


def test_remote_change_refreshes_svg_and_metadata(tmp_path, monkeypatch):
    from larkmd_sync import board_sync
    path = svg_asset(tmp_path)
    local = prepare_markdown_resources('![x](diagram.board.svg)\n', tmp_path / 'doc.md')
    changed = copy.deepcopy(NODES)
    changed[0]['text']['text'] = 'remote'
    remote_svg = b'<svg viewBox="0 0 100 100"><text>remote</text></svg>'
    monkeypatch.setattr(board_sync, 'export_whiteboard_source',
                        lambda *a, **k: WhiteboardSource('native', json.dumps({'nodes': changed})))
    monkeypatch.setattr(board_sync, 'export_whiteboard_svg',
                        lambda *a, **k: DownloadedMedia(remote_svg, '.svg'))
    monkeypatch.setattr(board_sync, '_run', lambda *a, **k: pytest.fail('must not upload'))
    board_sync.restore_and_bind('cli', 'D', [board_sync.BoardOperation(
        local.images[0], 'remote_changed', 'T', 'B', nodes_hash(changed))], identity='user',
        atomic_write=lambda p, b: p.write_bytes(b))
    assert path.read_bytes() == remote_svg
    assert load_board(path).digest == nodes_hash(changed)
    assert 'nodes' not in json.loads(path.with_suffix('.json').read_text())


@pytest.mark.parametrize('remaining,expected_calls', [([], ['A,B']), (['B'], ['A,B', 'B'])])
def test_delete_failure_rechecks_remote_before_retry(monkeypatch, remaining, expected_calls):
    from larkmd_sync import board_sync
    calls = []

    def update(*a, **k):
        calls.append(k['block_id'])
        if len(calls) == 1:
            raise board_sync.LarkCliError('Something went wrong')

    monkeypatch.setattr(board_sync, '_update', update)
    monkeypatch.setattr(board_sync, 'fetch_document_xml', lambda *a, **k:
        ''.join(f'<p id="{i}">old</p>' for i in remaining) + '<p id="NEW">new</p><whiteboard id="KEEP" token="T"/>')
    monkeypatch.setattr(board_sync.time, 'sleep', lambda _: None)
    board_sync._delete_blocks('cli', 'D', ['A', 'B'], identity='user')
    assert calls == expected_calls


def test_delete_validation_failure_is_not_retried(monkeypatch):
    from larkmd_sync import board_sync
    calls = []

    def update(*a, **k):
        calls.append(k['block_id'])
        raise board_sync.LarkCliError('field validation failed')

    monkeypatch.setattr(board_sync, '_update', update)
    monkeypatch.setattr(board_sync, 'fetch_document_xml', lambda *a, **k: '<p id="A">old</p>')
    with pytest.raises(board_sync.LarkCliError, match='仍存在的块：A'):
        board_sync._delete_blocks('cli', 'D', ['A'], identity='user')
    assert calls == ['A']


def test_delete_transient_failure_has_bounded_retries(monkeypatch):
    from larkmd_sync import board_sync
    calls = []

    def update(*a, **k):
        calls.append(k['block_id'])
        raise board_sync.LarkCliError('server internal error')

    monkeypatch.setattr(board_sync, '_update', update)
    monkeypatch.setattr(board_sync, 'fetch_document_xml', lambda *a, **k: '<p id="A">old</p>')
    monkeypatch.setattr(board_sync.time, 'sleep', lambda _: None)
    with pytest.raises(board_sync.LarkCliError, match='仍存在的块：A'):
        board_sync._delete_blocks('cli', 'D', ['A'], identity='user')
    assert calls == ['A'] * 3
