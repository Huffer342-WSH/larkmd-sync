import pytest

from larkmd_sync.core.text_colors import (
    color_spans,
    normalize_color,
    restore_text_colors,
)
from larkmd_sync.resources import prepare_markdown_resources


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#D83931", "#d83931"),
        ("#f00", "#ff0000"),
        ("rgb(216, 57,49)", "#d83931"),
        ("red", "#ff0000"),
    ],
)
def test_css_normalization(source, expected):
    assert normalize_color(source) == expected


@pytest.mark.parametrize(
    "value",
    [
        "url(https://example.com)",
        "rgba(1,2,3,0.5)",
        "rgb(256,0,0)",
        "var(--color)",
        "red;display:none",
    ],
)
def test_invalid_color_rejected(value):
    with pytest.raises(ValueError):
        normalize_color(value)


def test_code_is_literal_and_strong_link_survive(tmp_path):
    span = '<span style="color:#d83931">**红字** [链接](https://example.com)</span>'
    text = span + "\n`" + span + "`\n```html\n" + span + "\n```\n"
    p = prepare_markdown_resources(text, tmp_path / "x.md")
    assert p.upload_markdown.count('text-color="rgb(216,57,49)"') == 1
    assert p.upload_markdown.count(span) == 2
    assert p.comparison_markdown == text


def test_colored_list_restoration_and_roundtrip(tmp_path):
    xml = '<ol><li>正常</li><li><span text-color="rgb(216,57,49)">等system_service完成</span></li></ol>'
    out = restore_text_colors("1. 正常\n2. 等system_service完成\n", xml)
    assert out.count == 1 and not out.warnings
    assert (
        '2. <span style="color:#d83931">等system\\_service完成</span>' in out.markdown
    )
    p = prepare_markdown_resources(out.markdown, tmp_path / "x.md")
    assert '<span text-color="rgb(216,57,49)">' in p.upload_markdown


def test_partial_color_nested_rich_text():
    xml = '<p>前面<b><span text-color="rgb(216,57,49)">重点</span></b>后面</p>'
    out = restore_text_colors("前面**重点**后面\n", xml)
    assert out.markdown == '前面**<span style="color:#d83931">重点</span>**后面\n'
    assert not out.warnings


def test_repeated_text_not_globally_colored():
    xml = '<p><span text-color="red">重复</span></p><p>重复</p>'
    out = restore_text_colors("重复\n重复\n", xml)
    assert out.warnings and "style=" not in out.markdown


def test_unsupported_color_does_not_silently_disappear():
    out = restore_text_colors("红字\n", '<p><span text-color="unknown">红字</span></p>')
    assert out.warnings


def test_invalid_extra_css_rejected():
    with pytest.raises(ValueError):
        color_spans('<span style="color:red;display:none">x</span>')


def test_css_palette_change_is_reported_and_comparison_is_stable(tmp_path):
    prepared = prepare_markdown_resources(
        '<span style="color:red">红字</span>', tmp_path / "x.md"
    )
    assert prepared.color_normalizations == ("#ff0000 → #d83931",)
    assert prepared.comparison_markdown == '<span style="color:#d83931">红字</span>'
    assert "rgb(216,57,49)" in prepared.upload_markdown


def test_table_text_cannot_advance_cursor_into_mermaid():
    xml = ('<table><tr><td><p>正文</p></td></tr></table>'
           '<p><span text-color="red">红字</span></p>')
    markdown = '| 正文 |\n| --- |\n\n红字\n\n```mermaid\nmindmap\n  正文\n```\n'
    restored = restore_text_colors(markdown, xml)
    assert not restored.warnings
    assert '<span style="color:#d83931">红字</span>' in restored.markdown
    assert '```mermaid\nmindmap\n  正文\n```' in restored.markdown


def test_table_color_remains_blocking():
    xml = '<table><tr><td><p><span text-color="red">红字</span></p></td></tr></table>'
    assert restore_text_colors('| 红字 |\n| --- |\n', xml).warnings
