from pathlib import Path

import pytest

from larkmd_sync import (
    SkillPackage,
    discover_builtin_skills,
    install_skill,
    skill_status,
    uninstall_skill,
    update_skill,
)

INSTALLER = "larkmd-sync"


def _skill():
    return discover_builtin_skills("larkmd_sync.skills")["larkmd-sync"]


def _versioned_skill(parent: Path, version: str, marker: str) -> SkillPackage:
    root = parent / "larkmd-sync"
    root.mkdir(parents=True)
    root.joinpath("SKILL.md").write_text(
        "\n".join(
            (
                "---",
                "name: larkmd-sync",
                "description: Versioned test skill.",
                "metadata:",
                f'  skill-version: "{version}"',
                "---",
                "",
                marker,
            )
        ),
        encoding="utf-8",
    )
    return SkillPackage(
        name="larkmd-sync",
        description="Versioned test skill.",
        version=version,
        root=root,
    )


def test_install_status_repeat_and_uninstall(tmp_path: Path) -> None:
    skill = _skill()
    installed = install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    assert installed["changed"] is True
    assert (
        install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")[
            "changed"
        ]
        is False
    )
    assert skill_status(skill, tmp_path, installer=INSTALLER)["modified"] is False
    assert uninstall_skill(skill, tmp_path, installer=INSTALLER)["changed"] is True


def test_local_modification_is_preserved_and_can_be_backed_up(
    tmp_path: Path,
) -> None:
    skill = _skill()
    install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    (tmp_path / skill.name / "SKILL.md").write_text("local", encoding="utf-8")

    with pytest.raises(FileExistsError):
        update_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    with pytest.raises(FileExistsError):
        uninstall_skill(skill, tmp_path, installer=INSTALLER)

    updated = update_skill(
        skill,
        tmp_path,
        installer=INSTALLER,
        cli_version="5.0.0",
        backup=True,
    )
    assert Path(str(updated["backup_path"])).joinpath("SKILL.md").read_text() == "local"


def test_unknown_target_is_not_overwritten(tmp_path: Path) -> None:
    skill = _skill()
    target = tmp_path / skill.name
    target.mkdir()
    (target / "unknown.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    assert (target / "unknown.txt").read_text(encoding="utf-8") == "keep"


def test_update_replaces_owned_version(tmp_path: Path) -> None:
    old = _versioned_skill(tmp_path / "sources" / "old", "1.0.0", "old")
    new = _versioned_skill(tmp_path / "sources" / "new", "2.0.0", "new")
    target = tmp_path / "installed"
    install_skill(old, target, installer=INSTALLER, cli_version="5.0.0")

    result = update_skill(new, target, installer=INSTALLER, cli_version="5.0.0")

    assert result["skill_version"] == "2.0.0"
    assert "new" in (target / new.name / "SKILL.md").read_text(encoding="utf-8")


def test_symlink_is_detected_and_external_target_survives(tmp_path: Path) -> None:
    skill = _skill()
    install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    external = tmp_path / "external.txt"
    external.write_text("keep", encoding="utf-8")
    (tmp_path / skill.name / "external-link").symlink_to(external)

    state = skill_status(skill, tmp_path, installer=INSTALLER)
    assert state["modified"] is True
    assert "符号链接" in str(state["error"])
    with pytest.raises(FileExistsError):
        uninstall_skill(skill, tmp_path, installer=INSTALLER)

    uninstall_skill(skill, tmp_path, installer=INSTALLER, force=True)
    assert external.read_text(encoding="utf-8") == "keep"


def test_failed_atomic_replace_restores_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill()
    install_skill(skill, tmp_path, installer=INSTALLER, cli_version="5.0.0")
    target = tmp_path / skill.name
    target.joinpath("SKILL.md").write_text("local", encoding="utf-8")
    original_rename = Path.rename

    def fail_staging_rename(source: Path, destination: Path) -> Path:
        if source.name.startswith(f".{skill.name}.staging-"):
            raise OSError("simulated replace failure")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_staging_rename)
    with pytest.raises(OSError, match="simulated replace failure"):
        update_skill(
            skill,
            tmp_path,
            installer=INSTALLER,
            cli_version="5.0.0",
            force=True,
        )

    assert target.joinpath("SKILL.md").read_text(encoding="utf-8") == "local"
    assert not list(tmp_path.glob(f".{skill.name}.staging-*"))
