"""Recover visible heading numbers from Feishu's full document metadata."""

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .text_colors import _inline


def _normalized(text: str) -> str:
    return re.sub(r"\\([\\`*_\[\]<>~.()!+-])", r"\1", text).strip()


def restore_heading_numbers(markdown: str, document_xml: str) -> tuple[str, tuple[str, ...]]:
    if not document_xml:
        return markdown, ()
    root = ET.fromstring('<root>' + document_xml + '</root>')
    headings = [e for e in root.iter() if re.fullmatch(r'h[1-9]', e.tag)]
    if not any(e.get('seq-marker') or e.get('seq') for e in headings):
        return markdown, ()

    lines = markdown.splitlines(keepends=True)
    candidates = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r'^ {0,3}(?:> *)*(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        match = re.match(r'^( {0,3}#{1,6})[ \t]+(.*?)(\r?\n)?$', line)
        if match:
            candidates.append((index, match))

    title = root.find('title')
    if len(candidates) == len(headings) + 1 and title is not None:
        if _normalized(candidates[0][1][2]) == _normalized(_inline(title, False)):
            candidates.pop(0)
    if len(candidates) != len(headings):
        return markdown, ('标题数量与原始文档不一致，未自动补入序号',)

    changes = []
    for element, (index, match) in zip(headings, candidates):
        number = (element.get('seq-marker') or '').strip()
        if element.get('seq') and not number:
            return markdown, ('带编号的标题缺少 seq-marker，未推算序号',)
        try:
            variants = {_normalized(_inline(element, mode)) for mode in (False, True)}
        except (ValueError, KeyError):
            return markdown, ('标题含不支持的行内结构，未自动补入序号',)
        body = re.sub(r' <!-- larkmd-heading: .*? -->$', '', match[2])
        plain = _normalized(body)
        already_numbered = bool(number) and any(plain == number + ' ' + text for text in variants)
        if len(match[1].strip()) != int(element.tag[1:]) or (plain not in variants and not already_numbered):
            return markdown, ('标题顺序或内容与原始文档不匹配，未自动补入序号',)
        if number:
            seq = element.get('seq', 'auto')
            if seq != 'auto':
                return markdown, ('暂不支持该标题编号设置：' + seq,)
            metadata = json.dumps({'seq': seq, 'number': number}, ensure_ascii=False, separators=(',', ':'))
            if '--' in metadata:
                return markdown, ('标题编号包含不支持的字符',)
            visible = body if already_numbered else number + ' ' + body
            changes.append((index, match[1] + ' ' + visible + ' <!-- larkmd-heading: ' + metadata + ' -->' + (match[3] or '')))

    for index, line in changes:
        lines[index] = line
    return ''.join(lines), ()


@dataclass(frozen=True)
class HeadingNumber:
    marker: str
    level: int
    seq: str


def prepare_heading_numbers(markdown: str) -> tuple[str, tuple[HeadingNumber, ...]]:
    """Remove display-only numbers before upload, retaining native numbering intent."""
    output = []
    headings = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(?:> *)*(`{3,}|~{3,})', line)
        if fence:
            output.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            output.append(line)
            continue
        if '<!-- larkmd-heading:' not in line:
            output.append(line)
            continue
        match = re.fullmatch(r'( {0,3}#{1,6}) +(.+?) <!-- larkmd-heading: (.+) -->(\r?\n)?', line)
        if not match:
            raise ValueError('标题编号元数据必须位于对应标题行末尾')
        metadata = json.loads(match[3])
        if not isinstance(metadata, dict) or set(metadata) != {'seq', 'number'} or metadata['seq'] != 'auto':
            raise ValueError('不支持的标题编号元数据')
        number = metadata['number']
        if not isinstance(number, str) or not number or not match[2].startswith(number + ' '):
            raise ValueError('标题显示序号与元数据不一致；请保留原显示序号，飞书会自动重新编号')
        body = match[2][len(number) + 1:]
        if not body.strip():
            raise ValueError('编号标题不能为空')
        digest = hashlib.sha256(line.encode()).hexdigest()[:16]
        temporary = f'LARKMD_SYNC_HEADING_{len(headings):04d}_{digest}'
        headings.append(HeadingNumber(temporary, len(match[1].strip()), metadata['seq']))
        output.append(match[1] + ' ' + body + ' ' + temporary + (match[4] or ''))
    return ''.join(output), tuple(headings)


def heading_number_replacements(document_xml: str, headings: tuple[HeadingNumber, ...]) -> list[tuple[str, str]]:
    root = ET.fromstring('<root>' + document_xml + '</root>')
    replacements = []
    for heading in headings:
        matches = [e for e in root.iter('h' + str(heading.level)) if heading.marker in ''.join(e.itertext())]
        if len(matches) != 1 or not matches[0].get('id'):
            raise ValueError('无法唯一定位编号标题：' + heading.marker)
        block = copy.deepcopy(matches[0])
        block_id = block.attrib.pop('id')
        block.attrib.pop('seq-marker', None)
        block.set('seq', heading.seq)
        for e in block.iter():
            e.attrib.pop('id', None)
            if e.text:
                e.text = e.text.replace(' ' + heading.marker, '').replace(heading.marker, '')
            if e.tail:
                e.tail = e.tail.replace(' ' + heading.marker, '').replace(heading.marker, '')
        replacements.append((block_id, ET.tostring(block, encoding='unicode')))
    return replacements
