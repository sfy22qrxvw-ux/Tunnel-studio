"""Inspect RTC360 positioning metadata without applying or inferring a pose.

Files are read with a four-MiB limit. XML is represented as a bounded tag tree,
JSON is parsed strictly, and binary or unknown content is described by a hash
and a short hex preview. No referenced paths are followed. This read-only
inspector is not a registration solver or a claim of survey accuracy.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

LIMIT = 4 * 1024 * 1024
PREVIEW = 4096
NOTE = 'Inspection only. No pose was applied or inferred.'


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Non-finite JSON number: ' + value)


def strict_json(text):
    value = json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 100000:
            raise ValueError('JSON tree exceeds inspection limits.')
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError('JSON number is not finite.')
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    return value


class _Tree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.count = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.count += 1
        if self.depth > 64 or self.count > 50000:
            raise ValueError('XML tree exceeds inspection limits.')
        return super().start(tag, attrs)

    def end(self, tag):
        value = super().end(tag)
        self.depth -= 1
        return value

    def doctype(self, name, pubid, system):
        raise ValueError('DOCTYPE and ENTITY declarations are not supported.')


def _tree(element):
    result = dict(tag=element.tag, attributes=dict(element.attrib),
                  children=[_tree(child) for child in element])
    if element.text and element.text.strip():
        result['text'] = element.text.strip()
    if element.tail and element.tail.strip():
        result['tail'] = element.tail.strip()
    return result


def read_limited(path, limit=LIMIT):
    """Read a regular file without consuming more than limit + one bytes."""
    target = Path(path)
    before = target.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Expected a regular metadata file.')
    if before.st_size > limit:
        raise ValueError('Metadata file exceeds the 4 MiB inspection limit.')
    with target.open('rb') as stream:
        data = stream.read(limit + 1)
    after = target.stat()
    if len(data) > limit:
        raise ValueError('Metadata file exceeds the 4 MiB inspection limit.')
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('Metadata file changed during inspection.')
    return data


def _decode(data):
    # Check UTF-32 before UTF-16: their little-endian BOMs share a prefix.
    if data.startswith((b'\xff\xfe\0\0', b'\0\0\xfe\xff')):
        return data.decode('utf-32')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    if data[:4] in (b'\0\0\0<', b'<\0\0\0'):
        return data.decode('utf-32-be' if data[0] == 0 else 'utf-32-le')
    if data[:4] in (b'\0<\0?', b'<\0?\0'):
        return data.decode('utf-16-be' if data[0] == 0 else 'utf-16-le')
    return data.decode('utf-8-sig')


def _probe(path):
    try:
        if path is None or not str(path).strip():
            return dict(format='invalid', warnings=['No metadata path was supplied.'])
        data = read_limited(path)
    except FileNotFoundError:
        return {'format': 'missing'}
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        return dict(format='unavailable', warnings=[str(exc)])
    base = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    if not data:
        return dict(base, format='empty')
    fallback = dict(base, format='binary', hex_preview=data[:PREVIEW].hex())
    try:
        text = _decode(data)
    except (UnicodeError, ValueError):
        return fallback
    stripped = text.lstrip()
    if stripped.startswith('<'):
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, flags=re.I):
            return dict(base, format='rejected_xml', warnings=['DOCTYPE and ENTITY declarations are not supported.'])
        try:
            root = ET.fromstring(text, parser=ET.XMLParser(target=_Tree()))
            return dict(base, format='xml', tree=_tree(root))
        except (ET.ParseError, ValueError, RecursionError, LookupError) as exc:
            return dict(fallback, format='invalid_xml', warnings=[str(exc)])
    try:
        return dict(base, format='json', data=strict_json(text))
    except (ValueError, RecursionError, TypeError, OverflowError):
        if all(ord(ch) >= 32 or ch in '\t\r\n' for ch in text):
            return dict(fallback, format='text', text_preview=text[:PREVIEW],
                        warnings=['Content is not recognised XML or JSON.'])
        return fallback


def probe_setup_positions(path):
    """Inspect the supplied SetupPositions file; unknown content is reported."""
    return _probe(path)


def probe_project(path):
    """Inspect project.rtc360, including empty project marker files."""
    return _probe(path)


def probe(folder, output_path=None):
    """Inspect exactly this folder; optionally create a report inside it.

    Existing files are never overwritten. Relative output paths are resolved
    inside folder. Output outside folder (including through symlinks) is refused.
    """
    result = dict(folder=str(folder), setup_positions={'format': 'missing'},
                  project={'format': 'missing'}, warnings=[], note=NOTE)
    try:
        if folder is None or not str(folder).strip():
            raise ValueError('A metadata folder is required.')
        root = Path(folder).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Expected a metadata folder.')
        result.update(folder=str(root), setup_positions=_probe(root / 'SetupPositions'),
                      project=_probe(root / 'project.rtc360'))
        for key in ('setup_positions', 'project'):
            result['warnings'].extend(key + ': ' + warning for warning in result[key].get('warnings', []))
        if output_path is not None:
            output = Path(output_path)
            output = (output if output.is_absolute() else root / output).resolve()
            if not output.is_relative_to(root) or output == root or output.name in ('SetupPositions', 'project.rtc360', 'VGFibGVPZkNvbnRlbnQ='):
                raise ValueError('Choose a new report filename inside the inspected folder.')
            with output.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, indent=2, ensure_ascii=True, allow_nan=False)
                stream.write('\n')
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        result['warnings'].append(str(exc))
    return result
