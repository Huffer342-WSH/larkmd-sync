from pathlib import Path

import pytest

from larkmd_sync.resources import (
    DownloadedMedia,
    WhiteboardSource,
    detect_mermaid_diagram_type,
    materialize_remote_resources,
    prepare_markdown_resources,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
MERMAID_CASES = (
    ("flowchart", "flowchart LR\n    A --> B"),
    ("sequenceDiagram", "sequenceDiagram\n    Alice->>Bob: Hello"),
    ("classDiagram", "classDiagram\n    class User"),
    ("stateDiagram", "stateDiagram\n    [*] --> Ready"),
    ("erDiagram", "erDiagram\n    USER ||--o{ ORDER : places"),
    (
        "gantt",
        "gantt\n    dateFormat YYYY-MM-DD\n    task :2026-08-30, 1d",
    ),
    ("pie", 'pie title Usage\n    "Used" : 70\n    "Free" : 30'),
    ("mindmap", "mindmap\n    root((Config))\n        Reader\n        Writer"),
)


@pytest.mark.parametrize(("diagram_type", "source"), MERMAID_CASES)
def test_supported_mermaid_diagrams_are_prepared_as_whiteboards(
    tmp_path: Path,
    diagram_type: str,
    source: str,
) -> None:
    markdown = f"```mermaid\n{source}\n```\n"

    prepared = prepare_markdown_resources(markdown, tmp_path / "document.md")

    assert detect_mermaid_diagram_type(source) == diagram_type
    assert prepared.mermaid_types == (diagram_type,)
    assert prepared.mermaid_count == 1
    assert prepared.upload_markdown == (
        f'<whiteboard type="mermaid">\n{source}\n</whiteboard>\n'
    )


def test_mermaid_detection_skips_directives_and_accepts_state_v2() -> None:
    source = (
        "%%{init: {'theme': 'neutral'}}%%\n"
        "%% 状态机\n"
        "stateDiagram-v2\n"
        "    [*] --> Ready"
    )

    assert detect_mermaid_diagram_type(source) == "stateDiagram"


def test_unlisted_mermaid_diagram_is_forwarded_without_guarantee(tmp_path: Path) -> None:
    source = "journey\n    title Work\n    section Day\n      Code: 5"

    prepared = prepare_markdown_resources(
        f"```mermaid\n{source}\n```\n",
        tmp_path / "document.md",
    )

    assert prepared.mermaid_types == ()
    assert source in prepared.upload_markdown


@pytest.mark.parametrize(
    "line",
    (
        "文字 ![图片](./image.png)\n",
        "- ![图片](./image.png)\n",
    ),
)
def test_local_image_must_be_a_standalone_block(tmp_path: Path, line: str) -> None:
    (tmp_path / "image.png").write_bytes(PNG)

    with pytest.raises(ValueError, match="单独占一行"):
        prepare_markdown_resources(line, tmp_path / "document.md")


def test_local_image_marker_preserves_list_indentation(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(PNG)

    prepared = prepare_markdown_resources(
        "1. 步骤\n\n   ![结果](./image.png)\n",
        tmp_path / "document.md",
    )

    assert "1. 步骤 LARKMD_SYNC_IMAGE_0001_" in prepared.upload_markdown
    assert prepared.images[0].insert_after
    assert "\n   LARKMD_SYNC_IMAGE_0001\n" in prepared.comparison_markdown


def test_interrupted_list_records_resume_sequence(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(PNG)

    prepared = prepare_markdown_resources(
        "1. 第一步\n\n   ![结果](./image.png)\n\n2. 第二步\n",
        tmp_path / "document.md",
    )

    image = prepared.images[0]
    assert image.insert_after
    assert image.resume_sequence == 2
    assert image.sequence_marker.startswith("LARKMD_SYNC_SEQUENCE_0001_")
    assert f"2. 第二步 {image.sequence_marker}\n" in prepared.upload_markdown
    assert image.sequence_marker not in prepared.comparison_markdown


def test_multiple_images_before_same_item_share_one_sequence_repair(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.png").write_bytes(PNG)
    (tmp_path / "second.png").write_bytes(PNG + b"second")

    prepared = prepare_markdown_resources(
        "1. 第一步\n\n"
        "   ![图一](./first.png)\n\n"
        "   ![图二](./second.png)\n\n"
        "2. 第二步\n",
        tmp_path / "document.md",
    )

    repairs = [image for image in prepared.images if image.sequence_marker]
    assert len(repairs) == 1
    assert repairs[0].resume_sequence == 2
    assert prepared.upload_markdown.count("LARKMD_SYNC_SEQUENCE_") == 1


def test_local_image_cannot_escape_markdown_directory(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "outside.png").write_bytes(PNG)

    with pytest.raises(ValueError, match="同目录或子目录"):
        prepare_markdown_resources(
            "![图片](../outside.png)\n",
            docs / "document.md",
        )


def test_remote_image_and_mermaid_are_materialized(tmp_path: Path) -> None:
    markdown = (
        '# Test\n\n<img token="img-token" name="架构 图.png" '
        'caption="架构图"/>\n\n<whiteboard token="board-token"/>\n'
    )

    materialized = materialize_remote_resources(
        markdown,
        tmp_path / "document.md",
        download_media=lambda token: DownloadedMedia(PNG, ".png"),
        export_whiteboard=lambda token: WhiteboardSource(
            "mermaid",
            "flowchart TD\n    A --> B",
        ),
    )

    assert "![架构图](./.assets/document/架构_图.png)" in materialized.markdown
    assert "```mermaid\nflowchart TD\n    A --> B\n```" in materialized.markdown
    assert materialized.comparison_markdown.count("LARKMD_SYNC_IMAGE_0001") == 1
    assert len(materialized.assets) == 1
    assert materialized.assets[0].path == (
        tmp_path / ".assets" / "document" / "架构_图.png"
    )
    assert materialized.assets[0].content == PNG
    assert not materialized.warnings
    assert materialized.handled_reference_types == {"img", "image", "whiteboard"}


def test_remote_checkbox_table_is_materialized_to_gfm_table(tmp_path: Path) -> None:
    materialized = materialize_remote_resources(
        '<table><colgroup><col/><col/></colgroup><tbody>'
        '<tr><td vertical-align="top">测试signoff</td>'
        '<td vertical-align="top"><checkbox done="false"></checkbox></td></tr>'
        '<tr><td>回归</td><td><checkbox done="true"></checkbox></td></tr>'
        '</tbody></table>\n',
        tmp_path / "document.md",
        download_media=lambda token: DownloadedMedia(PNG, ".png"),
        export_whiteboard=lambda token: None,
    )

    assert materialized.markdown == (
        "| 测试signoff | [ ] |\n"
        "| --- | --- |\n"
        "| 回归 | [x] |\n"
    )
    assert materialized.comparison_markdown == materialized.markdown
    assert not materialized.warnings


def test_markdown_image_url_is_downloaded_to_local_asset(tmp_path: Path) -> None:
    materialized = materialize_remote_resources(
        "![测试图片](https://internal-api-drive-stream.feishu.cn/image)\n",
        tmp_path / "document.md",
        download_media=lambda token: DownloadedMedia(PNG, ".png"),
        download_url=lambda url: DownloadedMedia(PNG, ".png"),
        export_whiteboard=lambda token: None,
    )

    assert materialized.markdown == (
        "![测试图片](./.assets/document/测试图片.png)\n"
    )
    assert materialized.assets[0].content == PNG
    assert not materialized.warnings


def test_non_mermaid_whiteboard_remains_blocking(tmp_path: Path) -> None:
    materialized = materialize_remote_resources(
        '<whiteboard token="board-token"/>\n',
        tmp_path / "document.md",
        download_media=lambda token: DownloadedMedia(PNG, ".png"),
        export_whiteboard=lambda token: WhiteboardSource("svg", "<svg/>")
    )

    assert "<whiteboard" in materialized.markdown
    assert materialized.warnings[0].code == "unsupported_whiteboard_source"
    assert materialized.warnings[0].blocking
