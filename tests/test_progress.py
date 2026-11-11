import subprocess
from unittest.mock import patch

from larkmd_sync import pull_file
from larkmd_sync.lark_cli import _run
from larkmd_sync.progress import sink


def test_api_delivers_progress_before_work_finishes_and_resets_sink(tmp_path, capsys):
    messages = []

    def fetch(*args, **kwargs):
        assert any('读取文档' in m for m in messages)
        return {'content': '# Example\n', 'document_id': 'D', 'revision_id': 1}

    with patch('larkmd_sync.sync.fetch_markdown', side_effect=fetch):
        result = pull_file(tmp_path / 'x.md', 'https://tenant.feishu.cn/docx/D',
                           dry_run=True, lark_cli='unused', diagnostic=messages.append)
    assert result.ok and sink.get() is None
    assert capsys.readouterr() == ('', '')


def test_subprocess_progress_omits_payload_and_finishes_on_error(monkeypatch):
    messages = []
    token = sink.set(messages.append)

    def fail(*args, **kwargs):
        assert messages and '开始' in messages[0]
        raise FileNotFoundError('not installed')

    monkeypatch.setattr(subprocess, 'run', fail)
    try:
        from larkmd_sync.lark_cli import LarkCliError
        try:
            _run('missing', ['docs', '+fetch', '--doc', 'D', '--content', 'PRIVATE'])
        except LarkCliError:
            pass
        else:
            raise AssertionError('missing executable must fail')
    finally:
        sink.reset(token)
    assert '结束' in messages[-1]
    assert all('PRIVATE' not in message for message in messages)
