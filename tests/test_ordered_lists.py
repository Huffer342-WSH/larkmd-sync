import pytest

from larkmd_sync.core.ordered_lists import ordered_list_replacements


def test_builds_exact_sequence_replacement_and_preserves_inline_markup() -> None:
    marker = "LARKMD_SYNC_SEQUENCE_0001_deadbeef"
    document = (
        '<ol><li id="first" seq="1">第一步</li></ol>'
        '<img id="image" src="token"/>'
        f'<ol><li id="second" seq="1" seq-marker="1. ">第二步 '
        f'<code id="code">value</code> {marker}</li></ol>'
    )

    replacements = ordered_list_replacements(document, {marker: 2})

    assert len(replacements) == 1
    replacement = replacements[0]
    assert replacement.block_id == "second"
    assert replacement.sequence == 2
    assert replacement.content == '<ol><li seq="2">第二步 <code>value</code></li></ol>'
    assert marker not in replacement.content
    assert "seq-marker" not in replacement.content
    assert " id=" not in replacement.content


def test_rejects_missing_sequence_marker() -> None:
    with pytest.raises(ValueError, match="找不到"):
        ordered_list_replacements(
            '<ol><li id="second">第二步</li></ol>',
            {"LARKMD_SYNC_SEQUENCE_missing": 2},
        )


def test_rejects_duplicate_sequence_marker() -> None:
    marker = "LARKMD_SYNC_SEQUENCE_duplicate"
    document = (
        f'<ol><li id="second">第二步 {marker}</li></ol>'
        f'<ol><li id="third">第三步 {marker}</li></ol>'
    )

    with pytest.raises(ValueError, match="不唯一"):
        ordered_list_replacements(document, {marker: 2})
