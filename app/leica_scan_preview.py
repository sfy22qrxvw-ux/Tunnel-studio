"""Build a read-only display sample of a selected REGISTER 360 snapshot."""
from pathlib import Path
import json
import os
import subprocess
import threading

import numpy as np
from scipy.spatial.transform import Rotation


_MAX_SETUPS = 24
_WORKER = threading.Lock()


def _sample(paths):
    from jobdata import _worker_python
    from project_browser import MAX_POINTS

    worker = Path(__file__).with_name('hspca_worker.py')
    # Rapidly changing the selected database must not launch several native
    # decoders at once. A disconnected or slow scan drive has a hard timeout.
    with _WORKER:
        process = subprocess.run(
            [str(_worker_python()), str(worker), 'sample', str(MAX_POINTS)],
            input=json.dumps(paths), capture_output=True, timeout=12,
            encoding='utf-8', errors='replace',
            creationflags=0x08000000 if os.name=='nt' else 0,
        )
    if process.returncode or not process.stdout.startswith('RESULT\t'):
        raise ValueError('The Leica preview decoder could not read the setup files.')
    return json.loads(process.stdout.split('\t',1)[1])


def measured_preview(record, root, origin):
    from leica_project import inside

    scans = []
    for uri, position, quaternion in record['scan_sources']:
        try:
            path = inside(root, uri)
            if path.suffix.lower() != '.hspca' or not path.is_file():
                continue
            position = np.asarray(position, dtype=float)
            quaternion = np.asarray(quaternion, dtype=float)
            if not np.isfinite(position).all() or not np.isfinite(quaternion).all() or abs(np.linalg.norm(quaternion)-1) > .001:
                continue
            scans.append((str(path), position-origin, Rotation.from_quat(quaternion).as_matrix()))
        except (OSError, ValueError, TypeError, RuntimeError):
            continue
    if not record['scan_sources']:
        return None, 'This database has no supported scan references with saved positions.'
    if not scans:
        return None, 'Referenced setup files are missing or unreadable. Browse the complete project folder, including SetupsFolder.'
    if len(scans) > _MAX_SETUPS:
        scans = [scans[i] for i in np.linspace(0,len(scans)-1,_MAX_SETUPS,dtype=int)]
    try:
        results = _sample([scan[0] for scan in scans])
    except subprocess.TimeoutExpired:
        return None, 'Scan preview timed out. Make the setup files available locally, then refresh the folder.'
    except (OSError, ValueError):
        return None, 'The native scan preview could not be read.'

    samples = []
    for (_, offset, rotation), result in zip(scans, results):
        if result['status'] != 'sampled':
            continue
        rows = np.asarray(result['rows'], dtype=float)
        xyz = rows[:,:3] @ rotation.T + offset
        if np.isfinite(xyz).all():
            samples.append(np.column_stack((xyz, rows[:,3])))
    if not samples:
        if all(result['status']=='unavailable' for result in results):
            return None, 'Make the referenced setup files available locally, then refresh the folder.'
        return None, 'The referenced scans could not be sampled in this preview. Open the setup inspector to check the source files.'
    values = np.concatenate(samples)
    lo, hi = np.percentile(values[:,3], [5, 95])
    intensity = np.clip((values[:,3]-lo)/max(hi-lo,1),0,1) if hi>lo else np.full(len(values),.6)
    colours = np.array([.22,.45,.59]) + intensity[:,None]*np.array([.62,.49,.41])
    points = np.round(np.column_stack((values[:,:3], colours)), 5).tolist()
    return dict(points=points,setups=len(samples)), 'Original scan points before Leica cleaning · Intensity shading'
