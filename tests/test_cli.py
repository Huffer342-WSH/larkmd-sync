import json
from pathlib import Path
from unittest.mock import patch

from larkmd_sync.cli import main
from larkmd_sync.models import SyncAction, SyncApplyResult, SyncPlan, SyncWarning
from larkmd_sync.result import ApiResult


def _plan(markdown: Path) -> SyncPlan:
    return SyncPlan(
        plan_id="sha256:plan",
        operation="push",
        target="https://tenant.feishu.cn/wiki/document",
        actions=(
            SyncAction(
                action="overwrite_remote_document",
                local_path=str(markdown),
                document_url="https://tenant.feishu.cn/wiki/document",
                remote_revision=7,
                overwrites=True,
            ),
        ),
        warnings=(
            SyncWarning(code="test_warning", message="review this", path=str(markdown)),
        ),
    )


def test_json_stdout_contains_only_plan_envelope(tmp_path: Path, capsys) -> None:
    markdown = tmp_path / "test.md"
    markdown.write_text("# Test\n", encoding="utf-8")
    expected = ApiResult(ok=True, status=200, data=_plan(markdown))
    with patch("larkmd_sync.cli.push_file", return_value=expected) as public_api:
        assert (
            main(
                [
                    "push",
                    str(markdown),
                    "--doc",
                    "https://tenant.feishu.cn/wiki/document",
                    "--json",
                    "--no-input",
                ]
            )
            == 0
        )

    streams = capsys.readouterr()
    value = json.loads(streams.out)
    assert value["schema_version"] == "1"
    assert value["data"]["plan_id"] == "sha256:plan"
    assert value["warnings"][0]["code"] == "test_warning"
    assert streams.err == ""
    assert public_api.call_args.kwargs["plan_id"] is None


def test_push_apply_passes_reviewed_plan_id(tmp_path: Path, capsys) -> None:
    markdown = tmp_path / "test.md"
    markdown.write_text("# Test\n", encoding="utf-8")
    expected = ApiResult(
        ok=True,
        status=200,
        data=SyncApplyResult(
            plan_id="sha256:plan",
            operation="push",
            changed=1,
        ),
    )
    with patch("larkmd_sync.cli.push_file", return_value=expected) as public_api:
        assert main(["--json", "push", str(markdown), "--apply", "sha256:plan"]) == 0

    json.loads(capsys.readouterr().out)
    assert public_api.call_args.kwargs["plan_id"] == "sha256:plan"


def test_skill_install_requires_explicit_dry_run_for_preview(
    tmp_path: Path,
    capsys,
) -> None:
    assert (
        main(
            [
                "skill",
                "install",
                "--dest",
                str(tmp_path),
                "--dry-run",
                "--json",
                "--no-input",
            ]
        )
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value["data"]["dry_run"] is True
    assert not (tmp_path / "larkmd-sync").exists()


def test_json_validation_failure_stays_on_stdout(tmp_path: Path, capsys) -> None:
    markdown = tmp_path / "test.md"
    markdown.write_text("# Test\n", encoding="utf-8")
    assert main(["--json", "push", str(markdown)]) == 2

    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value["ok"] is False
    assert value["error"]["status"] == 422
    assert captured.err == ""




def test_invalid_nested_arguments_return_json(capsys) -> None:
    assert main(['--json', '--no-input', 'tree', 'pull']) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['schema_version'] == '1'
    assert payload['ok'] is False
    assert payload['error']['status'] == 422
    assert captured.err == ''


def test_skill_list_alias_is_read_only(capsys) -> None:
    assert main(['--json', 'skill', 'ls']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['data'][0]['name'] == 'larkmd-sync'
