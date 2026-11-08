# Copyright (c) 2026 Damian Gulczynski.
"""Compact disposable display buffers; analytical records remain float64 on disk.

TSDP v1: 24-byte header, UTF-8 metadata padded to eight bytes, six float32
values per displayed point (local XYZ/RGB), optional float64 deviations in mm.
The origin and all report statistics stay in the metadata at their original
precision. No point-cloud, comparison or export data is changed by this codec.
"""
from collections import OrderedDict
import json
from pathlib import Path
import struct
import threading

import numpy as np

HEADER = struct.Struct('<4s5I')
MAX_POINTS = 1_000_000
MAX_METADATA = 1024 * 1024
CACHE_BYTES = 32 * 1024 * 1024
_cache = OrderedDict()
_lock = threading.Lock()


def encode(preview):
    metadata = dict(preview)
    points = np.asarray(metadata.pop('packed', metadata.pop('points', None)), dtype='<f4')
    deviations = metadata.pop('deviations_mm', None)
    if points.ndim != 2 or points.shape[1] != 6 or len(points) > MAX_POINTS:
        raise ValueError('Invalid display point buffer.')
    if not np.isfinite(points).all():
        raise ValueError('Display coordinates and colours must be finite.')
    if metadata.get('sample_count', len(points)) != len(points):
        raise ValueError('Display sample count does not match the buffer.')
    metadata['sample_count'] = len(points)
    flags = int(deviations is not None)
    if deviations is not None:
        deviations = np.asarray(deviations, dtype='<f8')
        if deviations.shape != (len(points),) or not np.isfinite(deviations).all():
            raise ValueError('Display deviations must match the finite sample.')
    raw = json.dumps(metadata, allow_nan=False, separators=(',', ':')).encode('utf8')
    if len(raw) > MAX_METADATA:
        raise ValueError('Display metadata is too large.')
    padding = b'\0' * (-len(raw) % 8)
    return (HEADER.pack(b'TSDP', 1, len(points), len(raw), flags, 0) + raw + padding
            + points.tobytes(order='C')
            + (deviations.tobytes(order='C') if deviations is not None else b''))


def cached_result(result, build):
    """Reuse only identical saved results; stale-source checks belong to the API.

    File identity and the complete result descriptor invalidate this small
    memory cache. Closing/replacing a result never changes the source files.
    """
    path = Path(result['results_file'])
    info = path.stat()
    key = (str(path.resolve()), info.st_size, info.st_mtime_ns, info.st_ctime_ns,
           json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    value = encode(build())
    # Do not publish bytes if their backing result changed while being read.
    after = path.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != key[1:4]:
        raise ValueError('The comparison changed while loading. Show the current result again.')
    with _lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > 4 or sum(map(len, _cache.values())) > CACHE_BYTES:
            _cache.popitem(last=False)
    return value
