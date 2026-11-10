"""Plan and apply native board restoration without recreating existing boards."""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .core.board_assets import decide_board, json_bytes, nodes_hash, svg_display_size
from .lark_cli import (
    LarkCliError,
    _error,
    _parse_json_output,
    _run,
    create_sized_board,
    download_media,
    export_whiteboard_source,
    export_whiteboard_svg,
    fetch_document_xml,
)


@dataclass(frozen=True)
class BoardOperation:
    image: object
    action: str
    token: str = ""
    block_id: str = ""
    remote_hash: str = ""


def board_blocks(xml: str) -> dict[str, str]:
    if not xml:
        return {}
    root = ET.fromstring("<root>" + xml + "</root>")
    result = {}
    for board in root.iter("whiteboard"):
        if board not in list(root):
            raise ValueError(
                "嵌套在列表或表格中的原生画板尚不能安全定位，请保留远端结构"
            )
        result[board.attrib["token"]] = board.attrib["id"]
    return result


def plan_boards(images, snapshot):
    remote = {}
    for asset in snapshot.assets:
        if asset.role == "board_source":
            value = json.loads(asset.content)
            binding = value["bindings"][snapshot.document_id]
            remote[binding["token"]] = binding["baseline_hash"]
    # Mermaid boards are not native assets, and are outside this map.
    ids = board_blocks(snapshot.document_xml) if remote else {}
    operations = []
    used = set()
    local_paths = set()
    for image in images:
        if image.board is None:
            continue
        board = image.board
        if board.path in local_paths:
            raise ValueError("同一本地画板被重复引用；请复制 SVG 后分别引用")
        local_paths.add(board.path)
        binding = board.value.get("bindings", {}).get(snapshot.document_id, {})
        token = binding.get("token", "")
        if token and token not in remote:
            raise ValueError("映射的远端原生画板已删除或改成其他格式，请先解决冲突")
        if not token:
            matches = [
                t
                for t, digest in remote.items()
                if digest == board.digest and t not in used
            ]
            if len(matches) > 1:
                raise ValueError("目标存在多个相同原生画板，无法唯一建立映射")
            if matches:
                token = matches[0]
                binding = {"baseline_hash": board.digest}
        if token in used:
            raise ValueError("同一原生画板被重复引用；请显式复制源文件后再上传")
        if token:
            action = decide_board(
                board.digest, remote[token], binding.get("baseline_hash", "")
            )
            operations.append(
                BoardOperation(image, action, token, ids[token], remote[token])
            )
            used.add(token)
        else:
            if image.path.suffix != ".svg":
                raise ValueError("旧版 PNG/JPG 画板请先拉取为 SVG，再上传到新文档")
            operations.append(BoardOperation(image, "create"))
    if set(remote) - used:
        raise ValueError(
            "目标存在未映射的原生画板，禁止全文覆盖；请先拉取并保留画板引用"
        )
    return tuple(operations)


def _update(
    lark_cli,
    doc,
    command,
    *,
    identity,
    content=None,
    block_id=None,
    src_ids=None,
    pattern=None,
    markdown=False,
):
    args = [
        "docs",
        "+update",
        "--api-version",
        "v2",
        "--doc",
        doc,
        "--command",
        command,
        "--as",
        identity,
        "--format",
        "json",
    ]
    if content is not None:
        args += ["--content", "-", "--doc-format", "markdown" if markdown else "xml"]
    if block_id is not None:
        args += ["--block-id", block_id]
    if src_ids is not None:
        args += ["--src-block-ids", src_ids]
    if pattern is not None:
        args += ["--pattern", pattern]
    result = _run(lark_cli, args, stdin_data=content, timeout=180)
    value = _parse_json_output(result)
    if not value.get("ok"):
        raise _error(command, result, value)
    return value




def _marker_block(xml, marker):
    root = ET.fromstring("<root>" + xml + "</root>")
    matches = [n for n in root if marker in "".join(n.itertext())]
    if len(matches) != 1:
        raise LarkCliError("原生画板占位必须唯一匹配")
    return root, matches[0]


def rewrite_keeping_boards(lark_cli, doc, content, operations, old_xml, *, identity):
    """Append first, move retained boards, then remove only old non-board blocks."""
    kept = {op.block_id for op in operations if op.block_id}
    root = ET.fromstring("<root>" + old_xml + "</root>")
    def block_ids(node):
        if node.tag == "title":
            return []
        if "id" in node.attrib:
            return [node.attrib["id"]] if node.attrib["id"] not in kept else []
        if node.tag in {"ul", "ol"}:
            return [block_id for child in node for block_id in block_ids(child)]
        raise LarkCliError(f"无法定位待替换的文档块：{node.tag}")

    old_ids = [block_id for node in root for block_id in block_ids(node)]
    title = root.find("title")
    if title is not None:
        heading = "# " + "".join(title.itertext())
        lines = content.lstrip().splitlines(keepends=True)
        if lines and lines[0].rstrip("\r\n") == heading:
            content = "".join(lines[1:]).lstrip("\r\n")
    _update(lark_cli, doc, "append", identity=identity, content=content, markdown=True)
    for op in operations:
        if not op.block_id:
            continue
        xml = fetch_document_xml(lark_cli, doc, identity=identity)
        _, marker = _marker_block(xml, op.image.marker)
        _update(
            lark_cli,
            doc,
            "block_move_after",
            identity=identity,
            block_id=marker.attrib["id"],
            src_ids=op.block_id,
        )
        _update(lark_cli, doc, "block_delete", identity=identity, block_id=marker.attrib["id"])
    for block_id in reversed(old_ids):
        _update(lark_cli, doc, "block_delete", identity=identity, block_id=block_id)


def restore_and_bind(lark_cli, doc, operations, *, identity, atomic_write):
    for op in operations:
        token = op.token
        if op.action == "create":
            xml = fetch_document_xml(lark_cli, doc, identity=identity)
            _, marker = _marker_block(xml, op.image.marker)
            width, height = svg_display_size(op.image.path.read_text())
            token = create_sized_board(lark_cli, doc, marker.attrib["id"],
                                       width, height, identity=identity)
        try:
            _restore_board(lark_cli, doc, op, token, identity=identity, atomic_write=atomic_write)
        except (LarkCliError, OSError, ValueError) as exc:
            raise LarkCliError(
                f"画板同步未完成（文档 {doc}，画板 {token}）：{exc}。"
                "远端可能已经写入；请先拉取到新路径检查并恢复映射，不要直接重复创建。"
            ) from exc


def _restore_board(lark_cli, doc, op, token, *, identity, atomic_write):
    if op.action in {"create", "update"}:
        if op.image.path.suffix != ".svg":
            raise LarkCliError("画板编辑仅支持 SVG，请先拉取迁移旧预览")
        svg_content = op.image.path.read_text()
        key = hashlib.sha256((token + op.image.board.digest).encode()).hexdigest()
        args = [
            "whiteboard",
            "+update",
            "--whiteboard-token",
            token,
            "--input_format",
            "svg",
            "--source",
            "-",
            "--idempotent-token",
            key,
            "--as",
            identity,
            "--format",
            "json",
        ]
        if op.action == "update":
            args += ["--overwrite"]
        result = _run(
            lark_cli,
            args,
            stdin_data=svg_content,
            timeout=180,
        )
        value = _parse_json_output(result)
        if not value.get("ok"):
            raise _error("restore_native_board", result, value)
    expected = (
        op.remote_hash if op.action == "remote_changed" else op.image.board.digest
    )
    for attempt in range(6):
        try:
            source = export_whiteboard_source(lark_cli, token, identity=identity)
            if source is None or source.format != "native":
                raise LarkCliError("原生画板读回格式不符")
            nodes = json.loads(source.source)["nodes"]
            digest = nodes_hash(nodes)
            svg_written = op.action in {"create", "update"}
            if svg_written:
                requested = ET.fromstring(svg_content)
                texts = ["".join(n.itertext()).strip() for n in requested.iter()
                         if n.tag.split("}")[-1] == "text"]
                actual = " ".join(n.get("text", {}).get("text", "") for n in nodes)
                if not nodes or any(" ".join(t.split()) not in " ".join(actual.split()) for t in texts if t):
                    raise LarkCliError("SVG 写入后的文字回读不一致，未更新基准")
            if digest == expected or svg_written:
                break
            if attempt == 5:
                raise LarkCliError(
                    "原生画板恢复后节点、位置或连接关系不一致；未更新同步基准"
                )
        except LarkCliError as exc:
            if "not ready" not in str(exc).lower() or attempt == 5:
                raise
        time.sleep(1)
    if op.action == "create":
        xml = fetch_document_xml(lark_cli, doc, identity=identity)
        _, marker = _marker_block(xml, op.image.marker)
        _update(lark_cli, doc, "block_delete", identity=identity, block_id=marker.attrib["id"])
    new_svg = not op.image.board.value
    if new_svg:
        if op.image.board.path.exists() or op.image.board.path.is_symlink():
            raise LarkCliError("执行期间画板 JSON 已出现，未覆盖本地文件")
        current = {"schema_version": 1, "type": "feishu-whiteboard", "bindings": {}}
    else:
        current = json.loads(op.image.board.path.read_text())
        if current != op.image.board.value:
            raise LarkCliError("执行期间本地画板源文件发生变化，未覆盖本地修改；请重新核对")
    if (
        "sha256:" + hashlib.sha256(op.image.path.read_bytes()).hexdigest()
        != (op.image.board.digest if op.image.board.svg is not None else current["preview_hash"])
    ):
        raise LarkCliError("执行期间本地画板预览发生变化，未覆盖本地修改")
    value = {key: current[key] for key in ("type", "origin_url", "preview_hash") if key in current}
    value["bindings"] = dict(current.get("bindings", {}))
    if op.action in {"create", "update", "remote_changed"}:

        if op.action == "update":
            time.sleep(1)
        preview = (export_whiteboard_svg(lark_cli, token, identity=identity)
                   if op.image.path.suffix == ".svg" else download_media(
                       lark_cli, token, identity=identity, media_type="whiteboard"))
        # Keep the reference path stable. Feishu preview format must agree.
        if (op.image.path.suffix == ".png") != (
            preview.content.startswith(b"\x89PNG")
        ):
            raise LarkCliError("画板预览格式发生变化，请先重新拉取")
        if ("sha256:" + hashlib.sha256(op.image.path.read_bytes()).hexdigest()
                != (op.image.board.digest if op.image.board.svg is not None else current["preview_hash"])):
            raise LarkCliError("导出期间本地 SVG 已修改，未覆盖本地内容")
        atomic_write(op.image.path, preview.content)
        value["preview_hash"] = (
            "sha256:" + hashlib.sha256(preview.content).hexdigest()
        )
    value.pop("nodes", None)
    value["schema_version"] = 2
    value["baseline_hash"] = digest
    value.setdefault("bindings", {})[doc] = {
        "token": token,
        "baseline_hash": digest,
    }
    atomic_write(op.image.board.path, json_bytes(value))
