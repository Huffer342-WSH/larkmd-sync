import pytest

from larkmd_sync.lark_cli import LarkCliError, _source_format, download_image_url


def test_remote_image_download_rejects_untrusted_hosts() -> None:
    with pytest.raises(LarkCliError, match="untrusted image URL"):
        download_image_url("https://example.com/image.png")


def test_remote_image_download_rejects_plain_http() -> None:
    with pytest.raises(LarkCliError, match="untrusted image URL"):
        download_image_url("http://internal-api-drive-stream.feishu.cn/image.png")


def test_whiteboard_source_recognizes_mermaid_after_directive() -> None:
    source = "%%{init: {'theme': 'neutral'}}%%\nmindmap\n    root((Config))"

    assert _source_format("", source) == "mermaid"


@pytest.mark.parametrize('after,anchor', [(False, 'prior'), (True, 'marker')])
def test_image_insertion_uses_exact_block_anchor(monkeypatch, tmp_path, after, anchor):
    import subprocess

    from larkmd_sync import lark_cli
    calls = []
    monkeypatch.setattr(lark_cli, 'fetch_document_xml', lambda *a, **k:
        '<title>T</title><p id="prior">before</p><p id="marker">IMAGE_MARKER</p>')
    def run(cli, args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{"ok":true,"data":{}}', '')
    monkeypatch.setattr(lark_cli, '_run', run)
    lark_cli.insert_local_image('lark-cli', 'doc', tmp_path / 'a & b.png',
                               'IMAGE_MARKER', caption='A "quote"', after=after)
    args, kwargs = calls[0]
    assert args[args.index('--block-id') + 1] == anchor
    assert '--selection-with-ellipsis' not in args
    import xml.etree.ElementTree as ET
    image = ET.fromstring(kwargs['stdin_data'])
    assert image.attrib['path'] == '@./a & b.png'
    assert image.attrib['caption'] == 'A "quote"'
    assert kwargs['cwd'] == tmp_path


def test_image_ambiguous_marker_fails_before_write(monkeypatch, tmp_path):
    from larkmd_sync import lark_cli
    monkeypatch.setattr(lark_cli, 'fetch_document_xml', lambda *a, **k:
        '<p id="a">MARK</p><p id="b">MARK</p>')
    monkeypatch.setattr(lark_cli, '_run', lambda *a, **k: pytest.fail('must not write'))
    with pytest.raises(LarkCliError, match='唯一'):
        lark_cli.insert_local_image('cli', 'doc', tmp_path / 'a.png', 'MARK')


def test_svg_export_retries_timeout_and_caches_by_remote_content(monkeypatch, tmp_path):
    import subprocess
    from pathlib import Path

    from larkmd_sync import lark_cli

    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    calls, waits = [], []
    monkeypatch.setattr(lark_cli.time, 'sleep', waits.append)

    def run(cli, args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, '',
                '{"ok":false,"error":{"message":"API call failed: server time out error"}}')
        (Path(kwargs['cwd']) / 'board.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>OK</text></svg>')
        return subprocess.CompletedProcess(args, 0, '{"ok":true}', '')

    monkeypatch.setattr(lark_cli, '_run', run)
    first = lark_cli.export_whiteboard_svg('cli', 'board', cache_key='doc:nodes1')
    assert waits == [1]
    assert len(calls) == 2
    assert lark_cli.export_whiteboard_svg('cli', 'board', cache_key='doc:nodes1') == first
    assert len(calls) == 2
    lark_cli.export_whiteboard_svg('cli', 'board', cache_key='doc:nodes2')
    lark_cli.export_whiteboard_svg('cli', 'board', identity='bot', cache_key='doc:nodes2')
    lark_cli.export_whiteboard_svg('cli', 'other-board', cache_key='doc:nodes2')
    assert len(calls) == 5

    for path in (tmp_path / 'cache').rglob('*.svg'):
        path.write_bytes(b'broken')
    lark_cli.export_whiteboard_svg('cli', 'board', cache_key='doc:nodes1')
    assert len(calls) == 6


@pytest.mark.parametrize('message,attempts', [('server time out error', 3), ('permission denied', 1)])
def test_svg_export_failure_reports_board_attempts_and_elapsed(monkeypatch, message, attempts):
    from larkmd_sync import lark_cli

    calls = []
    monkeypatch.setattr(lark_cli.time, 'sleep', lambda _: None)
    def run(*args, **kwargs):
        calls.append(1)
        raise LarkCliError(message, exit_code=4, stderr='diagnostic')
    monkeypatch.setattr(lark_cli, '_run', run)
    with pytest.raises(LarkCliError, match=f'board=broken-board, attempts={attempts}, elapsed=') as error:
        lark_cli.export_whiteboard_svg('cli', 'broken-board')
    assert len(calls) == attempts
    assert error.value.exit_code == 4
    assert error.value.stderr == 'diagnostic'


def test_svg_export_rejects_invalid_output_without_retry(monkeypatch):
    import subprocess
    from pathlib import Path

    from larkmd_sync import lark_cli

    def run(cli, args, **kwargs):
        (Path(kwargs['cwd']) / 'board.svg').write_text('<html/>')
        return subprocess.CompletedProcess(args, 0, '{"ok":true}', '')
    monkeypatch.setattr(lark_cli, '_run', run)
    with pytest.raises(LarkCliError, match='attempts=1.*not return an SVG'):
        lark_cli.export_whiteboard_svg('cli', 'board')


def test_svg_cache_expiration_and_unwritable_cache(monkeypatch, tmp_path):
    import os
    import subprocess
    from pathlib import Path

    from larkmd_sync import lark_cli

    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    calls = []
    def run(cli, args, **kwargs):
        calls.append(1)
        (Path(kwargs['cwd']) / 'board.svg').write_text('<svg/>')
        return subprocess.CompletedProcess(args, 0, '{"ok":true}', '')
    monkeypatch.setattr(lark_cli, '_run', run)
    lark_cli.export_whiteboard_svg('cli', 'board', cache_key='nodes')
    path = next(tmp_path.rglob('*.svg'))
    os.utime(path, (1, 1))
    lark_cli.export_whiteboard_svg('cli', 'board', cache_key='nodes')
    assert len(calls) == 2
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('block')
    monkeypatch.setenv('XDG_CACHE_HOME', str(blocker))
    assert lark_cli.export_whiteboard_svg('cli', 'board', cache_key='nodes').content == b'<svg/>'


@pytest.mark.parametrize('after,duplicate', [(True, False), (False, False), (True, True)])
def test_image_marker_at_list_item_end(monkeypatch, tmp_path, after, duplicate):
    import subprocess

    from larkmd_sync import lark_cli

    xml = ('<ol><li id="first" seq="1"><b>第一项</b> IMAGE_MARKER</li>'
           '<li id="second" seq="2">第二项</li></ol>')
    if duplicate:
        xml += '<p id="duplicate">IMAGE_MARKER</p>'
    monkeypatch.setattr(lark_cli, 'fetch_document_xml', lambda *a, **k: xml)
    calls = []

    def run(cli, args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"ok":true,"data":{}}', '')

    monkeypatch.setattr(lark_cli, '_run', run)
    if after and not duplicate:
        lark_cli.insert_local_image('cli', 'doc', tmp_path / 'a.png', 'IMAGE_MARKER', after=after)
        assert calls[0][calls[0].index('--block-id') + 1] == 'first'
    else:
        with pytest.raises(LarkCliError, match='唯一'):
            lark_cli.insert_local_image('cli', 'doc', tmp_path / 'a.png', 'IMAGE_MARKER', after=after)
        assert not calls


def test_new_board_uses_document_size_and_marker_index(monkeypatch):
    import json
    import subprocess

    from larkmd_sync import lark_cli
    calls = []

    def run(cli, args, **kwargs):
        calls.append((args, kwargs))
        data = ({'block': {'children': ['first', 'marker', 'last']}} if args[1] == 'GET'
                else {'children': [{'board': {'token': 'T'}}]})
        return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': data}), '')

    monkeypatch.setattr(lark_cli, '_run', run)
    assert lark_cli.create_sized_board('cli', 'D', 'marker', 820, 939) == 'T'
    body = json.loads(calls[1][1]['stdin_data'])
    assert body == {'index': 2, 'children': [
        {'block_type': 43, 'board': {'width': 820, 'height': 939, 'align': 2}}]}
