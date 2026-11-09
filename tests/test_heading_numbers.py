from larkmd_sync.core.heading_numbers import (
    heading_number_replacements,
    prepare_heading_numbers,
    restore_heading_numbers,
)


def marked(text, number):
    return text + ' <!-- larkmd-heading: {"seq":"auto","number":"' + number + '"} -->'


def test_numbered_headings_restore_exact_markers_and_preserve_inline_format():
    xml = '<title>Doc</title><h1 seq="auto" seq-marker="3."><b>章</b></h1><h2 seq="auto" seq-marker="3.2">节</h2><h1>未编号</h1>'
    md = '<title>Doc</title>\n# **章**\n## 节\n# 未编号\n'
    expected = '<title>Doc</title>\n' + marked('# 3. **章**', '3.') + '\n' + marked('## 3.2 节', '3.2') + '\n# 未编号\n'
    assert restore_heading_numbers(md, xml) == (expected, ())
    assert restore_heading_numbers(expected, xml) == (expected, ())


def test_code_and_title_do_not_consume_heading_positions():
    xml = '<title>Doc</title><h1 seq-marker="1.">章</h1>'
    md = '# Doc\n```md\n# literal\n```\n# 章\n'
    assert restore_heading_numbers(md, xml) == (md.replace('# 章', marked('# 1. 章', '1.')), ())


def test_duplicate_headings_are_matched_in_order():
    xml = '<h1 seq-marker="1.">重复</h1><h1 seq-marker="2.">重复</h1>'
    assert restore_heading_numbers('# 重复\n# 重复\n', xml) == (marked('# 1. 重复', '1.') + '\n' + marked('# 2. 重复', '2.') + '\n', ())


def test_unmatched_headings_fail_closed():
    for xml in ['<h1 seq="auto">章</h1>', '<h2 seq-marker="1.1">章</h2>', '<h1 seq-marker="1.">其他</h1>', '<h1 seq-marker="1.">章</h1><h2>节</h2>']:
        result, warnings = restore_heading_numbers('# 章\n', xml)
        assert result == '# 章\n'
        assert warnings


def test_unnumbered_headings_unchanged():
    assert restore_heading_numbers('# 2. 手工编号\n', '<h1>2. 手工编号</h1>') == ('# 2. 手工编号\n', ())



def test_upload_removes_only_synthetic_number_and_restores_native_style():
    xml = '<h1 seq="auto" seq-marker="1.">2026 规划</h1>'
    local, warnings = restore_heading_numbers('# 2026 规划\n', xml)
    assert not warnings
    uploaded, headings = prepare_heading_numbers(local)
    assert uploaded.startswith('# 2026 规划 LARKMD_SYNC_HEADING_')
    replacement = heading_number_replacements(
        '<h1 id="H"><b>2026 规划</b> ' + headings[0].marker + '</h1>', headings)
    assert replacement == [('H', '<h1 seq="auto"><b>2026 规划</b></h1>')]


def test_invalid_heading_metadata_stops_upload():
    import pytest
    for value in [marked('# 2. 改了序号', '1.'), '# 标题 <!-- larkmd-heading: {} -->']:
        with pytest.raises(ValueError):
            prepare_heading_numbers(value)


def test_metadata_in_code_is_not_used():
    md = '```md\n' + marked('# 1. 示例', '1.') + '\n```\n'
    assert prepare_heading_numbers(md) == (md, ())
