"""Native whiteboard assets: editable source, preview and per-document baselines."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def canonical_nodes(nodes: list[dict]) -> list[dict]:
    """Ignore server-assigned IDs/order and subpixel float serialization only."""
    ordered = sorted(
        nodes,
        key=lambda n: (
            n.get("type", ""),
            round(n.get("x", 0), 3),
            round(n.get("y", 0), 3),
            round(n.get("width", 0), 3),
            round(n.get("height", 0), 3),
            n.get("text", {}).get("text", ""),
            n.get("z_index", 0),
        ),
    )
    ids = {n["id"]: f"node-{i}" for i, n in enumerate(ordered)}

    def visit(v, key=""):
        if isinstance(v, dict):
            return {k: visit(w, k) for k, w in v.items()}
        if isinstance(v, list):
            return [visit(w, key) for w in v]
        if isinstance(v, str) and (
            key in {"id", "parent_id", "node_id", "children_ids", "node_ids"}
        ):
            return ids.get(v, v)
        if isinstance(v, float):
            return round(v, 3)
        return v

    return visit(ordered)


def nodes_hash(nodes: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(json_bytes(canonical_nodes(nodes))).hexdigest()


@dataclass(frozen=True)
class BoardAsset:
    path: Path
    value: dict[str, Any]
    digest: str
    svg: str | None = None

    @property
    def nodes(self):
        return self.value["nodes"]


def load_board(preview: Path) -> BoardAsset | None:
    if ".board." not in preview.name:
        return None
    source = preview.with_suffix(".json")
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"原生画板缺少安全的源文件：{source}")
    value = json.loads(source.read_text())
    if value.get("schema_version") != 1 or value.get("type") != "feishu-whiteboard":
        raise ValueError(f"不支持的原生画板文件：{source}")
    nodes = value.get("nodes")
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(
            not isinstance(n, dict) or not isinstance(n.get("id"), str) for n in nodes
        )
    ):
        raise ValueError("原生画板 nodes 必须为带有 ID 的非空节点数组")
    if len({n["id"] for n in nodes}) != len(nodes):
        raise ValueError("原生画板节点 ID 重复")
    preview_hash = "sha256:" + hashlib.sha256(preview.read_bytes()).hexdigest()
    svg = None
    if preview.suffix == ".svg":
        content = preview.read_text()
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ValueError(f"SVG 格式不合法：{exc}") from exc
        if root.tag.split("}")[-1] != "svg":
            raise ValueError("画板源文件必须是 SVG")
        for node in root.iter():
            if node.tag.split("}")[-1] in {"script", "foreignObject"}:
                raise ValueError("SVG 不支持脚本或外部 HTML")
            for key, text in node.attrib.items():
                if key.lower().startswith("on") or (
                    key.split("}")[-1] == "href" and not text.startswith(("#", "data:image/"))
                ):
                    raise ValueError("SVG 不支持事件或外部引用")
        if value.get("preview_hash") != preview_hash:
            if nodes_hash(nodes) != value.get("baseline_hash"):
                raise ValueError("不能同时修改 SVG 和原生 nodes")
            svg = content
    elif value.get("preview_hash") != preview_hash:
        raise ValueError(
            "原生画板预览已修改；请修改 .board.json 源数据，不能将预览改动当作画板改动"
        )
    return BoardAsset(source, value, preview_hash if svg is not None else nodes_hash(nodes), svg)


def decide_board(local_hash: str, remote_hash: str, baseline: str) -> str:
    if local_hash == remote_hash:
        return "keep"
    if not baseline:
        raise ValueError("原生画板缺少同步基准，请先拉取建立映射")
    if local_hash == baseline:
        return "remote_changed"
    if remote_hash == baseline:
        return "update"
    raise ValueError("原生画板在本地和飞书均有修改，需先解决冲突")
