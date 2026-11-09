import pytest

from larkmd_sync.core.markdown_correction import correct_markdown


@pytest.mark.parametrize('source,expected', [
    ('**一句话读懂：**AC/VLT', '**一句话读懂：** AC/VLT'),
    ('> **阅读边界：**正文', '> **阅读边界：** 正文'),
    ('前文**（重点）**后文', '前文 **（重点）** 后文'),
    ('**正常加粗**正文', '**正常加粗**正文'),
    ('**重点：**，正文', '**重点：**，正文'),
    ('**Label:**text', '**Label:** text'),
    ('**标题：** 已有空格', '**标题：** 已有空格'),
    (r'\**文字：\**正文', r'\**文字：\**正文'),
    ('`**代码：**text` **标签：**正文', '`**代码：**text` **标签：** 正文'),
    ('```python\n**代码：**text\n```\n**标签：**正文', '```python\n**代码：**text\n```\n**标签：** 正文'),
    ('> ```\n> **代码：**text\n> ```\n> **标签：**正文', '> ```\n> **代码：**text\n> ```\n> **标签：** 正文'),
    ('    **代码：**text\n', '    **代码：**text\n'),
    ('***嵌套：***text', '***嵌套：***text'),
    ('** 标签： **text', '** 标签： **text'),
])
def test_bold_boundaries(source, expected):
    assert correct_markdown(source) == expected
    assert correct_markdown(expected) == expected
