import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from larkmd_sync import compare_file


class DiffPreprocessTest(unittest.TestCase):
    @patch("larkmd_sync.sync.fetch_markdown")
    def test_local_metadata_and_link_mapping_do_not_create_diff(
        self, fetch_markdown
    ) -> None:
        fetch_markdown.return_value = {
            "content": "# Source\n\n[Target](https://example.feishu.cn/wiki/target)\n",
            "document_id": "source",
            "revision_id": 1,
            "reference_map": {},
            "tips": "",
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.md"
            target.write_text(
                "---\nfeishu_url: https://example.feishu.cn/wiki/target\n---\n",
                encoding="utf-8",
            )
            source = root / "source.md"
            source.write_text(
                "---\ntitle: Source\nfeishu_url: "
                "https://example.feishu.cn/wiki/source\n---\n\n"
                "# Source\n\n[Target](./target.md)\n",
                encoding="utf-8",
            )

            result = compare_file(source, lark_cli="lark-cli")

        self.assertTrue(result.ok)
        self.assertTrue(result.data.identical)
        self.assertEqual(result.data.changes, 0)

    @patch("larkmd_sync.sync.fetch_markdown")
    def test_feishu_table_separator_rendering_is_ignored(self, fetch_markdown) -> None:
        fetch_markdown.return_value = {
            "content": "| A | B |\n|-|-|\n| 1 | 2 |\n",
            "document_id": "source",
            "revision_id": 1,
            "reference_map": {},
            "tips": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.md"
            source.write_text(
                "| A | B |\n| :--- | ---: |\n| 1 | 2 |\n", encoding="utf-8"
            )
            result = compare_file(
                source,
                "https://example.feishu.cn/wiki/source123",
                lark_cli="lark-cli",
            )

        self.assertTrue(result.data.identical)


if __name__ == "__main__":
    unittest.main()
