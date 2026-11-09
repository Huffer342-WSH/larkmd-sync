"""Conservative native flowchart to Mermaid conversion; no geometry sidecars."""

from __future__ import annotations

import re
from typing import Any


def _label(value: str) -> str:
    # Mermaid numeric entities prevent labels from introducing syntax or HTML.
    return "".join(
        f"#{ord(c)};" if c in '&<>"|`[]{}' else c for c in value.strip()
    ).replace("\n", "<br/>")


def native_board_to_mermaid(nodes: list[dict[str, Any]]) -> str:
    """Preserve supported nodes, containment, edge directions and captions.

    Reject unsupported nodes or unbound edges rather than silently omit them.
    Position/font information is deliberately normalized by Mermaid layout.
    """
    if not nodes:
        raise ValueError("画板没有节点")
    shapes = {}
    edges = []
    for node in nodes:
        if node.get("type") == "connector":
            edges.append(node)
        elif node.get("type") == "composite_shape" and node.get(
            "composite_shape", {}
        ).get("type") in {"rect", "round_rect", "diamond", "ellipse"}:
            shapes[node["id"]] = node
        else:
            raise ValueError(f"不支持的画板节点类型：{node.get('type')}")
    if len({n["id"] for n in nodes}) != len(nodes):
        raise ValueError("画板节点 ID 重复")
    ordered = sorted(
        shapes, key=lambda k: (shapes[k].get("y", 0), shapes[k].get("x", 0), k)
    )
    ids = {key: f"n{i}" for i, key in enumerate(ordered)}

    def contains(a, b):
        return (
            a["x"] <= b["x"]
            and a["y"] <= b["y"]
            and a["x"] + a["width"] >= b["x"] + b["width"]
            and a["y"] + a["height"] >= b["y"] + b["height"]
            and a["width"] * a["height"] > b["width"] * b["height"]
        )

    parents = {}
    for key, node in shapes.items():
        possible = [
            k
            for k, n in shapes.items()
            if n["composite_shape"]["type"] == "rect" and contains(n, node)
        ]
        parents[key] = (
            min(possible, key=lambda k: shapes[k]["width"] * shapes[k]["height"])
            if possible
            else None
        )
    containers = set(parents.values()) - {None}
    lines = ["flowchart TB"]

    def emit(parent, depth):
        for key in ordered:
            if parents[key] != parent:
                continue
            node = shapes[key]
            label = _label(node.get("text", {}).get("text", ""))
            indent = "    " * depth
            if key in containers:
                lines.append(f'{indent}subgraph {ids[key]}["{label}"]')
                emit(key, depth + 1)
                lines.append(indent + "end")
            else:
                kind = node["composite_shape"]["type"]
                left, right = {
                    "rect": ("[", "]"),
                    "round_rect": ("(", ")"),
                    "diamond": ("{", "}"),
                    "ellipse": ("([", "])"),
                }[kind]
                lines.append(f'{indent}{ids[key]}{left}"{label}"{right}')

    emit(None, 1)
    for edge in sorted(edges, key=lambda n: n["id"]):
        c = edge["connector"]
        ends = []
        arrows = []
        for side in ("start", "end"):
            endpoint = c.get(side, {})
            key = endpoint.get("attached_object", c.get(side + "_object", {})).get("id")
            arrow = endpoint.get("arrow_style", "none")
            if key not in ids or arrow not in {"none", "line_arrow", "triangle_arrow"}:
                raise ValueError("画板连线缺少绑定端点或使用不支持的箭头")
            ends.append(ids[key])
            arrows.append(arrow != "none")
        if arrows == [True, False]:
            ends.reverse()
            arrows.reverse()
        dashed = edge.get("style", {}).get("border_style") == "dashed"
        operator = (
            ("<-.->" if dashed else "<-->")
            if all(arrows)
            else (
                ("-.->" if dashed else "-->")
                if arrows[1]
                else ("-.-" if dashed else "---")
            )
        )
        caption = "<br/>".join(
            _label(v.get("text", ""))
            for v in c.get("captions", {}).get("data", [])
            if v.get("text")
        )
        label = f'|"{caption}"|' if caption else ""
        lines.append(f"    {ends[0]} {operator}{label} {ends[1]}")
    for key in ordered:
        style = shapes[key].get("style", {})
        attrs = []
        for field, name in [("fill_color", "fill"), ("border_color", "stroke")]:
            value = style.get(field, "")
            if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                attrs.append(f"{name}:{value}")
        if style.get("border_style") == "dashed":
            attrs.append("stroke-dasharray: 5 5")
        if attrs:
            lines.append(f"    style {ids[key]} {','.join(attrs)}")
    return "\n".join(lines)
