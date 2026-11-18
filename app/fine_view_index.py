"""Optional conservative bounds for 1,024 original rows within coarse view blocks."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile
import numpy as np

STEP = 1024


def path_for(key):
    source = Path(key[0])
    fingerprint = sha256(repr(key).encode()).hexdigest()
    return source.parent / 'display_cache' / ('fine-view-' + fingerprint + '.npz')


@lru_cache(maxsize=2)
def _load(key, path, modified):
    count = key[1]
    shape = ((count + STEP - 1) // STEP, 2, 3)
    with np.load(path, allow_pickle=False) as index:
        if not np.array_equal(index['signature'], [*key[1:5], STEP]):
            return None
        boxes = index['bounds']
    return boxes if boxes.dtype == np.float64 and boxes.shape == shape else None


def candidates(key, view, frame, coarse):
    """Cull sub-blocks only. Exact world-space filters still decide every point."""
    if key[-1] % STEP:
        return None
    path = path_for(key)
    try:
        stat = path.stat()
        maximum = ((key[1] + STEP - 1) // STEP) * 48 + 65536
        if stat.st_size > maximum:
            return None
        boxes = _load(key, path, stat.st_mtime_ns)
    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
        return None
    if boxes is None:
        return None
    target, axes, scale, aspect = frame
    axes = axes[:2]
    abs_axes = np.abs(axes)
    limit = np.array([scale, scale / aspect]) * 1.15 + 1e-7
    count = key[1]

    def ranges():
        for offset in range(0, len(coarse), 512):
            rows = np.asarray(coarse[offset:offset + 512], dtype=np.int64)
            starts = rows[:, 0, None] + np.arange(0, key[-1], STEP)
            starts = starts[starts < rows[:, 1, None]]
            bounds = boxes[starts // STEP]
            finite = np.isfinite(bounds).all(axis=(1, 2))
            valid = np.ones(len(starts), dtype=bool)
            delta = bounds[finite] - target
            centre = (delta[:, 0] + delta[:, 1]) * .5
            half = (delta[:, 1] - delta[:, 0]) * .5
            valid[finite] = (np.abs(centre @ axes.T) - half @ abs_axes.T <= limit).all(axis=1)
            for start in starts[valid]:
                yield int(start), min(int(start) + STEP, count)
    return ranges()


