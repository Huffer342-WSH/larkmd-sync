import tempfile
import unittest
from pathlib import Path

from larkmd_sync.core.preprocess import (
    rewrite_checkbox_tables,
    rewrite_interrupted_ordered_lists,
    rewrite_local_markdown_links,
    split_front_matter,
)


class FrontMatterTest(unittest.TestCase):
    def test_leading_horizontal_rule_is_not_front_matter(self) -> None:
        markdown = "---\nplain text without a closing delimiter\n"
        self.assertEqual(split_front_matter(markdown), ({}, markdown))


class LocalLinkTest(unittest.TestCase):
    def test_rewrite_links_from_target_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            target = root / "target file.md"
            source.write_text("", encoding="utf-8")
            target.write_text(
                "---\ntitle: Target\nfeishu_url: https://example.feishu.cn/wiki/target\n---\n",
                encoding="utf-8",
            )
            markdown = (
                '[目标](<target file.md> "说明")\n'
                "![图片](<target file.md>)\n"
                "[外部](https://example.com)\n"
                "```markdown\n[代码](<target file.md>)\n```\n"
            )

            converted = rewrite_local_markdown_links(markdown, source)

            self.assertIn(
                '[目标](https://example.feishu.cn/wiki/target "说明")', converted
            )
            self.assertIn("![图片](<target file.md>)", converted)
            self.assertIn("[外部](https://example.com)", converted)
            self.assertIn("[代码](<target file.md>)", converted)

    def test_rewrite_interrupted_ordered_lists_across_images(self) -> None:
        markdown = (
            "**测试步骤：**\n\n"
            "1. 第一步\n\n"
            "![图1](./a.png)\n\n"
            "1. 第二步\n\n"
            "![图2](./b.png)\n\n"
            "1. 第三步\n\n"
            "**实际结果：**完成\n"
        )

        converted = rewrite_interrupted_ordered_lists(markdown)

        self.assertIn("1. 第一步", converted)
        self.assertIn("2. 第二步", converted)
        self.assertIn("3. 第三步", converted)
        self.assertIn("   ![图1](./a.png)", converted)
        self.assertIn("   ![图2](./b.png)", converted)

    def test_rewrite_interrupted_ordered_lists_across_comparison_markers(
        self,
    ) -> None:
        markdown = (
            "1. 第一步\n\n"
            "LARKMD_SYNC_IMAGE_0001\n\n"
            "1. 第二步\n\n"
            "LARKMD_SYNC_IMAGE_0002\n\n"
            "1. 第三步\n"
        )

        converted = rewrite_interrupted_ordered_lists(markdown)

        self.assertIn("1. 第一步", converted)
        self.assertIn("2. 第二步", converted)
        self.assertIn("3. 第三步", converted)
        self.assertIn("   LARKMD_SYNC_IMAGE_0001", converted)
        self.assertIn("   LARKMD_SYNC_IMAGE_0002", converted)

    def test_rewrite_interrupted_ordered_lists_keeps_trailing_image_top_level(self) -> None:
        markdown = "1. 唯一步骤\n\n![结果](./result.png)\n\n**结果：**通过\n"

        converted = rewrite_interrupted_ordered_lists(markdown)

        self.assertEqual(converted, markdown)

    def test_rewrite_interrupted_ordered_lists_resets_on_paragraph(self) -> None:
        markdown = "1. 第一项\n\n普通段落\n\n1. 新列表\n"

        converted = rewrite_interrupted_ordered_lists(markdown)

        self.assertEqual(converted, markdown)

    def test_rewrite_checkbox_table_to_feishu_table(self) -> None:
        markdown = "| 项目 | 状态 |\n| --- | --- |\n| 测试signoff | [ ] |\n| 回归 | [x] |\n"

        converted = rewrite_checkbox_tables(markdown)

        self.assertIn("<table>", converted)
        self.assertIn("测试signoff", converted)
        self.assertIn('<checkbox done="false"></checkbox>', converted)
        self.assertIn('<checkbox done="true"></checkbox>', converted)


if __name__ == "__main__":
    unittest.main()
