from pathlib import Path
from unittest.mock import patch

from larkmd_sync import ApiResult, push_file


def test_push_defaults_to_plan_only(tmp_path: Path) -> None:
    markdown = tmp_path / "test.md"
    markdown.write_text("# Test\n", encoding="utf-8")
    expected = ApiResult(ok=True, status=200, data=None)
    with (
        patch("larkmd_sync.api.plan_push_file", return_value=expected) as inner,
        patch("larkmd_sync.api.apply_push_file") as apply,
    ):
        push_file(markdown, "doc", lark_cli="lark-cli")

    apply.assert_not_called()

    assert inner.call_args.args[:3] == ("lark-cli", markdown, "doc")


def test_missing_cli_returns_api_error(capsys) -> None:
    with patch('larkmd_sync.api.find_lark_cli', side_effect=RuntimeError('missing CLI')):
        result = push_file('example.md')
    assert not result.ok and result.status == 500
    assert result.error == 'missing CLI'
    assert capsys.readouterr() == ('', '')
