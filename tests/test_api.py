from pathlib import Path
from unittest.mock import patch

import pytest

from larkmd_sync import ApiResult, push_file


def test_push_dry_run_does_not_apply(tmp_path: Path) -> None:
    from larkmd_sync.models import SyncPlan

    expected = ApiResult(ok=True, status=200, data=SyncPlan(
        plan_id="reviewed", operation="push", target="doc", actions=()))
    with (
        patch("larkmd_sync.api.plan_push_file", return_value=expected),
        patch("larkmd_sync.api.apply_push_file") as apply,
    ):
        assert push_file(tmp_path / "test.md", "doc", lark_cli="cli", dry_run=True) is expected
    apply.assert_not_called()


def test_missing_cli_returns_api_error(capsys) -> None:
    with patch('larkmd_sync.api.find_lark_cli', side_effect=RuntimeError('missing CLI')):
        result = push_file('example.md')
    assert not result.ok and result.status == 500
    assert result.error == 'missing CLI'
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize("name,plan_name,apply_name,args,operation", [
    ("push_file", "plan_push_file", "apply_push_file", ("local.md", "doc"), "push"),
    ("push_tree", "plan_tree_push", "apply_tree_push", ("local", "doc"), "tree_push"),
])
def test_sync_defaults_to_apply_reviewed_plan(name, plan_name, apply_name, args, operation):
    from larkmd_sync import api
    from larkmd_sync.models import SyncPlan

    preview = ApiResult(ok=True, status=200, data=SyncPlan(
        plan_id="reviewed", operation=operation, target="doc", actions=()))
    done = ApiResult(ok=True, status=200)
    with (patch.object(api, plan_name, return_value=preview),
          patch.object(api, apply_name, return_value=done) as apply):
        assert getattr(api, name)(*args, lark_cli="cli") is done
    assert "reviewed" in apply.call_args.args
    assert apply.call_count == 1


@pytest.mark.parametrize("conflict,blocked", [(True, False), (False, True)])
def test_automatic_push_stops_before_apply(conflict, blocked):
    from larkmd_sync.models import SyncPlan, SyncWarning

    plan = SyncPlan(plan_id="P", operation="push", target="doc", actions=(),
                    conflicts=("conflict",) if conflict else (),
                    warnings=(SyncWarning(code="unsupported", message="blocked", blocking=True),) if blocked else ())
    with (patch("larkmd_sync.api.plan_push_file", return_value=ApiResult(ok=True, status=200, data=plan)),
          patch("larkmd_sync.api.apply_push_file") as apply):
        result = push_file("local.md", "doc", lark_cli="cli")
    assert not result.ok and result.status == 409 and result.data is plan
    apply.assert_not_called()


def test_dry_run_and_reviewed_plan_are_mutually_exclusive():
    result = push_file("local.md", "doc", lark_cli="cli", dry_run=True, plan_id="P")
    assert not result.ok and result.status == 422


def test_default_pull_actually_writes_local_file(tmp_path):
    from larkmd_sync import pull_file

    target = tmp_path / "local.md"
    remote = {"content": "# Remote\n\nHello\n", "document_id": "D", "revision_id": 1}
    with patch("larkmd_sync.sync.fetch_markdown", return_value=remote) as fetch:
        result = pull_file(target, "https://tenant.feishu.cn/docx/D", lark_cli="cli")
    assert result.ok and result.data.changed == 1
    assert "Hello" in target.read_text()
    assert fetch.call_count == 1
