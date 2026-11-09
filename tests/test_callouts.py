import pytest

from larkmd_sync.core.callouts import (
    callout_replacements,
    materialize_callouts,
    prepare_callouts,
)


def test_prepare_alert_for_upload_and_comparison() -> None:
    prepared = prepare_callouts(
        "> [!WARNING]\n"
        "> **写入前检查**，并保留 `code`。\n"
        ">\n"
        "> - 第一项\n"
        "> - 第二项\n"
    )

    assert prepared.comparison_markdown == (
        "> [!WARNING]\n"
        "> **写入前检查**，并保留 `code`。\n"
        ">\n"
        "> - 第一项\n"
        "> - 第二项\n"
    )
    assert prepared.upload_markdown.startswith("<callout>\n")
    assert prepared.callouts[0].marker in prepared.upload_markdown
    assert prepared.callouts[0].kind == "WARNING"


def test_materialize_remote_callout_as_alert() -> None:
    result = materialize_callouts(
        '<callout emoji="⚠️">\n提示 **重要内容**。\n- 一\n- 二\n</callout>\n'
    )

    assert result.markdown == (
        "> [!WARNING]\n> 提示 **重要内容**。\n> - 一\n> - 二\n"
    )
    assert result.count == 1
    assert result.unknown_emojis == ()


def test_materialize_unknown_emoji_as_note_with_warning_data() -> None:
    result = materialize_callouts('<callout emoji="🚀">\n内容\n</callout>\n')

    assert result.markdown == "> [!NOTE]\n> 内容\n"
    assert result.unknown_emojis == ("🚀",)


@pytest.mark.parametrize(
    "body",
    (
        "> ![图片](./image.png)\n",
        "> ```python\n> print('x')\n> ```\n",
        "> | A | B |\n> | - | - |\n",
    ),
)
def test_reject_unsupported_content_inside_alert(body: str) -> None:
    with pytest.raises(ValueError, match="暂不支持"):
        prepare_callouts("> [!NOTE]\n" + body)


def test_callout_replacement_preserves_children_and_removes_marker() -> None:
    prepared = prepare_callouts("> [!TIP]\n> 内容\n")
    marker = prepared.callouts[0].marker
    document_xml = (
        '<callout id="callout-id" emoji="💡" background-color="orange">'
        '<p id="paragraph"><b>内容</b></p>'
        f'<p id="marker">{marker}</p>'
        '<ul id="list"><li id="item">列表</li></ul>'
        "</callout>"
    )

    replacements = callout_replacements(document_xml, prepared.callouts)

    assert len(replacements) == 1
    replacement = replacements[0]
    assert replacement.block_id == "callout-id"
    assert replacement.kind == "TIP"
    assert 'emoji="💡"' in replacement.content
    assert 'background-color="light-green"' in replacement.content
    assert 'border-color="green"' in replacement.content
    assert "<b>内容</b>" in replacement.content
    assert "<ul><li>列表</li></ul>" in replacement.content
    assert marker not in replacement.content
    assert " id=" not in replacement.content


def test_callout_replacement_requires_every_marker() -> None:
    prepared = prepare_callouts("> [!CAUTION]\n> 内容\n")

    with pytest.raises(ValueError, match="找不到高亮块临时标记"):
        callout_replacements('<p id="paragraph">内容</p>', prepared.callouts)
