"""Readable RTC360 setup labels without inventing scan numbers.

Bare numeric scanner names are presented as setup labels. A meaningful source
folder name is preferred when available. Original names and stable setup IDs
are retained; display labels do not identify poses or registration links.
"""
from pathlib import PureWindowsPath
import re


def display_name(original, folder='', setup_id=''):
    name = str(original or '').strip()
    source = PureWindowsPath(str(folder or '').replace('/', '\\')).name
    if name and not re.fullmatch(r'[+-]?\d+', name):
        return name
    if re.fullmatch(r'(?:R\d+|(?:setup|scan)[ _-]?\d+)', source, re.IGNORECASE):
        return source
    if name:
        return 'Setup ' + name
    return 'RTC360 setup ' + (str(setup_id)[:8] or source or 'unnamed')


def normalize(state):
    """Upgrade only unedited automatic labels in already imported projects."""
    replacements = {}
    for item in state.get('job_items', []):
        if not isinstance(item, dict):continue
        meta = item.get('meta') or {}
        if not isinstance(meta, dict):continue
        if item.get('kind') != 'scan' or meta.get('source_format') != 'rtc360_raw_experimental':
            continue
        raw = meta.get('raw_import') or {}
        if not isinstance(raw, dict):continue
        acquisition = raw.get('acquisition_metadata') or {}
        if not isinstance(acquisition, dict):acquisition = {}
        original = raw.get('source_setup_name', acquisition.get('Name'))
        if original is None or item.get('rtc360_name_custom'):
            continue
        old = item.get('name')
        if old != original:
            continue
        label = display_name(original, meta.get('source_path') or meta.get('source_name'), raw.get('setup_id'))
        if label == old:
            continue
        item['name'] = label
        replacements[meta.get('file')] = (old, label)
        # Change matching automatic aliases only, preserving custom labels and
        # source acquisition metadata in every snapshot.
        def update(value):
            if not isinstance(value, dict):return
            if value.get('name') == old:value['name'] = label
        update(meta);update(item.get('original_meta'))
        for key in ('cloud', 'transformed'):
            update((item.get('registration_state') or {}).get(key))
    for meta in (state.get('cloud'), state.get('surface_source'),
                 (state.get('registration') or {}).get('cloud'),
                 (state.get('registration') or {}).get('transformed')):
        if isinstance(meta, dict) and meta.get('file') in replacements:
            old, label = replacements[meta['file']]
            if meta.get('name') == old:meta['name'] = label
    return state
