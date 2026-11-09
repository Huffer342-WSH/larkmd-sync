"""Restore explicit sequence numbers for lists interrupted by remote media blocks."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrderedListReplacement:
    """One ordered-list block that must be replaced after media insertion."""

    block_id: str
    content: str
    sequence: int


def _remove_marker(element: ET.Element, marker: str) -> None:
    for node in element.iter():
        if node.text:
            node.text = node.text.replace(f" {marker}", "").replace(marker, "")
        if node.tail:
            node.tail = node.tail.replace(f" {marker}", "").replace(marker, "")


def ordered_list_replacements(
    document_xml: str,
    sequences_by_marker: Mapping[str, int],
) -> tuple[OrderedListReplacement, ...]:
    """Build exact list-item replacements from a full-detail document snapshot."""
    if not sequences_by_marker:
        return ()
    try:
        root = ET.fromstring(f"<document>{document_xml}</document>")
    except ET.ParseError as exc:
        raise ValueError(f"飞书返回的完整文档 XML 无法解析：{exc}") from exc

    replacements: list[OrderedListReplacement] = []
    found_markers: set[str] = set()
    for list_item in root.iter("li"):
        text = "".join(list_item.itertext())
        matched = [marker for marker in sequences_by_marker if marker in text]
        if not matched:
            continue
        if len(matched) != 1:
            raise ValueError("一个有序列表项中出现了多个续号标记")
        marker = matched[0]
        if marker in found_markers:
            raise ValueError(f"有序列表续号标记不唯一：{marker}")
        found_markers.add(marker)

        block_id = list_item.attrib.get("id", "")
        if not block_id:
            raise ValueError(f"有序列表续号目标缺少 block ID：{marker}")
        item = copy.deepcopy(list_item)
        sequence = sequences_by_marker[marker]
        item.set("seq", str(sequence))
        item.attrib.pop("seq-marker", None)
        _remove_marker(item, marker)
        for node in item.iter():
            node.attrib.pop("id", None)
        container = ET.Element("ol")
        container.append(item)
        replacements.append(
            OrderedListReplacement(
                block_id=block_id,
                content=ET.tostring(container, encoding="unicode"),
                sequence=sequence,
            )
        )

    missing = set(sequences_by_marker) - found_markers
    if missing:
        raise ValueError(
            "飞书文档中找不到有序列表续号标记：" + ", ".join(sorted(missing))
        )
    return tuple(replacements)
