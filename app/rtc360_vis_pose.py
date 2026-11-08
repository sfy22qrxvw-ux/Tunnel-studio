"""Read observed RTC360 SetupPositions and apply an initial virtual pose.

The supported XML schema was inspected in the supplied 231023-SC-D1 job:
Positions / SetupPosition attributes SetupId and Pose, where Pose contains
typed Linear (Matrix3x3) and Translation (Vector3) JSON values. Adjacent matrix
rows are separated before strict JSON decoding. Setup IDs and OwnerJob IDs
must agree with the source metadata. Project-space translations are metres.
The coordinate convention is also described in siyka-au/leica-rtc360-parser,
RTC360_FORMAT.md; it is independently interpreted, not a Leica SDK contract.

Saved scanner positions may include on-scanner alignment as well as VIS. They
are initial alignment, not survey control or a replacement for target checks.
Stored XYZ already has recorded tilt applied: the virtual rotation is
R_saved @ inverse(R_tilt), so tilt is never applied twice. Existing target or
manual registration is preserved. Reapplying an owned initial pose is a no-op;
updating it composes a relative correction, never a second absolute pose.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re

import numpy as np

INDEX = 'VGFibGVPZkNvbnRlbnQ='
METHOD = 'RTC360 saved setup pose / VIS initial alignment'
NOTICE = ('VIS odometry / saved scanner pose applied as initial alignment; '
          'scanner odometry can drift and is not survey-grade control. '
          'Verify against survey targets before measurement use.')


def capabilities() -> dict:
    return dict(name='Apply RTC360 VIS / saved setup positions', requires_source='SetupPositions and matching setup/job XML',
                method=METHOD, limits=dict(max_setups=200, requires_rtc360_raw_import=True),
                supported_format='Positions/SetupPosition with typed Linear and Translation JSON')


def _failure(reason, **extra):
    return dict(applied=False, pose_available=False, reason=str(reason), warnings=[str(reason)], method=METHOD, **extra)


def _typed(value, kind, shape):
    from rtc360_vis_probe import strict_json
    if not isinstance(value, list) or len(value) != 2 or value[0] != kind or not isinstance(value[1], str):
        raise ValueError('Unsupported typed ' + kind + ' value.')
    text = re.sub(r'\]\s*\[', '],[', value[1]) if kind == 'Matrix3x3' else value[1]
    decoded = strict_json(text)
    array = np.asarray(decoded)
    if array.shape != shape or array.dtype.kind not in 'fiu':
        raise ValueError('Invalid ' + kind + ' shape or numeric type.')
    array = np.asarray(array, dtype=float)
    if not np.isfinite(array).all():
        raise ValueError('The pose contains non-finite coordinates.')
    return array


def _proper(R, warnings):
    determinant = float(np.linalg.det(R))
    error = float(np.max(np.abs(R.T @ R - np.eye(3))))
    if determinant <= 0 or abs(determinant - 1) > 1e-4 or error > 1e-4:
        raise ValueError('The recorded matrix is not a proper rotation; scale, shear and reflections are refused.')
    if error > 1e-10 or abs(determinant - 1) > 1e-10:
        U, _, Vt = np.linalg.svd(R)
        R = U @ Vt
        if np.linalg.det(R) <= 0:
            raise ValueError('Rotation projection would introduce a reflection.')
        warnings.append('Small matrix rounding errors were projected to the nearest proper rotation.')
    return R


def _transform(value, warnings):
    if not isinstance(value, dict) or set(value) != {'Linear', 'Translation'}:
        raise ValueError('Unsupported pose fields. Expected Linear and Translation.')
    R = _proper(_typed(value['Linear'], 'Matrix3x3', (3, 3)), warnings)
    return R, _typed(value['Translation'], 'Vector3', (3,))


def decode_positions(report):
    """Decode only the observed schema; unknown layouts are refused."""
    from rtc360_vis_probe import strict_json
    root = report.get('tree') if isinstance(report, dict) else None
    if not isinstance(report, dict) or report.get('format') != 'xml' or not isinstance(root, dict) or root.get('tag') != 'Positions':
        raise ValueError('SetupPositions is missing or has an unsupported format.')
    children = root.get('children', [])
    if len(children) > 200:
        raise ValueError('SetupPositions exceeds the 200-setup limit.')
    result = {}
    for child in children:
        attributes = child.get('attributes', {})
        ident = attributes.get('SetupId')
        if child.get('tag') != 'SetupPosition' or child.get('children') or not isinstance(ident, str) or not ident or ident in result:
            raise ValueError('Unknown or duplicate SetupPosition record.')
        warnings = []
        R, t = _transform(strict_json(attributes.get('Pose', '')), warnings)
        result[ident] = dict(R=R.tolist(), t=t.tolist(), warnings=warnings)
    return result


def read_pose(setup_folder):
    """Read a setup's saved absolute scanner pose without changing anything."""
    try:
        from rtc360_vis_probe import _probe, probe_setup_positions
        if setup_folder is None or not str(setup_folder).strip():
            return _failure('A scanner setup folder is required.')
        folder = Path(setup_folder).resolve(strict=True)
        setup_report = _probe(folder / INDEX)
        setup = setup_report.get('tree', {})
        if setup.get('tag') != 'Setup':
            return _failure('Choose the original individual scanner setup folder, containing its Setup XML.')
        ident = setup.get('attributes', {}).get('Id')
        owners = [c.get('attributes', {}).get('Id') for c in setup.get('children', []) if c.get('tag') == 'OwnerJob']
        if not isinstance(ident, str) or len(owners) != 1 or not owners[0]:
            return _failure('Setup ID or OwnerJob ID is missing or ambiguous.')
        owner = owners[0]
        # Only the direct parent is the observed job location. Never search
        # neighbouring jobs or select a pose by record order / display name.
        job_folder = folder.parent
        job_report = _probe(job_folder / INDEX)
        job = job_report.get('tree', {})
        if job.get('tag') != 'Job' or job.get('attributes', {}).get('Id') != owner:
            return _failure('The parent job XML does not match this setup OwnerJob ID.')
        source = job_folder / 'SetupPositions'
        report = probe_setup_positions(source)
        poses = decode_positions(report)
        if not poses:
            return _failure('SetupPositions contains no pose records.', source=str(source))
        if ident not in poses:
            return _failure('No saved pose matches this setup ID.', source=str(source))
        value = poses[ident]
        return dict(applied=False, pose_available=True, method=METHOD, source=str(source),
                    source_sha256=report['sha256'], setup_sha256=setup_report['sha256'], job_sha256=job_report['sha256'],
                    setup_id=ident, job_id=owner, job_name=job.get('attributes', {}).get('Name', owner),
                    positions_id=report['tree'].get('attributes', {}).get('Id'),
                    source_frame='scanner', units='metres', **value)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, np.linalg.LinAlgError) as exc:
        return _failure(exc)


def apply_pose(meta, setup_folder, progress=lambda m: None, cancel=lambda: False):
    """Apply only to a compatible raw cache; return a refusal without mutation."""
    if not isinstance(meta, dict):
        return _failure('Scan metadata must be a dictionary.')
    if not callable(progress) or not callable(cancel):
        return _failure('Progress and cancellation callbacks must be callable.')
    try:
        if cancel():
            return _failure('Cancelled.')
        progress('Reading RTC360 saved setup position…')
        value = read_pose(setup_folder)
        if not value.get('pose_available'):
            return value
        import registration_pose
        raw = meta.get('raw_import')
        if (meta.get('source_format') != 'rtc360_raw_experimental' or not isinstance(raw, dict)
                or raw.get('setup_id') != value['setup_id'] or meta.get('units') != 'metres'):
            return _failure('This saved pose does not match a supported RTC360 raw cache.')
        warnings = list(value['warnings'])
        tilt, tilt_t = _transform(raw.get('recorded_tilt'), warnings)
        if np.max(np.abs(tilt_t)) > 1e-9:
            return _failure('The imported tilt includes an unsupported translation.')
        # Cache points already equal R_tilt @ p_scanner.
        R, t = np.asarray(value['R']) @ tilt.T, np.asarray(value['t'])
        old_R, old_t = registration_pose.get(meta)
        previous = meta.get('rtc360_initial_pose')
        owned = (isinstance(previous, dict) and previous.get('job_id') == value['job_id']
                 and previous.get('setup_id') == value['setup_id']
                 and np.asarray(previous.get('rotation')).shape == (3, 3)
                 and np.asarray(previous.get('translation')).shape == (3,)
                 and np.allclose(old_R, previous['rotation'], atol=1e-10, rtol=0)
                 and np.allclose(old_t, previous['translation'], atol=1e-10, rtol=0))
        has_prior = any(key in meta for key in ('registration_rotation', 'registration_translation')) or bool(meta.get('virtual_registration'))
        if (has_prior or not registration_pose.is_identity(meta)) and not owned:
            return _failure('Existing manual, target or cloud registration is preserved; this is an initial-pose operation.', protected=True)
        if previous and not owned:
            return _failure('The scan has changed since its initial pose; current registration is preserved.', protected=True)
        if owned and previous.get('source_sha256') == value['source_sha256'] and np.allclose(R, old_R, atol=1e-10, rtol=0) and np.allclose(t, old_t, atol=1e-10, rtol=0):
            return dict(value, reason='This initial pose is already applied.', already_applied=True)
        warnings.append(NOTICE)
        if cancel():
            return _failure('Cancelled.')
        saved = copy.deepcopy(meta)
        try:
            if owned:
                delta = R @ old_R.T
                registration_pose.compose(meta, delta, t - delta @ old_t)
            else:
                registration_pose.set_pose(meta, R, t)
            marker = {k: copy.deepcopy(value[k]) for k in ('setup_id', 'job_id', 'source', 'source_sha256', 'setup_sha256', 'job_sha256')}
            meta.update(rtc360_initial_pose=dict(marker, rotation=R.tolist(), translation=t.tolist()),
                        position_status='RTC360 saved pose / VIS initial alignment · not survey control',
                        coordinate_frame='RTC360 job ' + value['job_id'] + ' · saved scanner positions (metres)')
            old_warnings = meta.get('warnings', [])
            if not isinstance(old_warnings, list):
                old_warnings = [str(old_warnings)]
            meta['warnings'] = list(dict.fromkeys([str(w) for w in old_warnings] + warnings))
        except BaseException:
            meta.clear();meta.update(saved)
            raise
        return dict(value, applied=True, rotation=R.tolist(), translation=t.tolist(),
                    scanner_rotation=value['R'], warnings=warnings)
    except (ImportError, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, np.linalg.LinAlgError) as exc:
        return _failure(exc)


def inspect_item(item):
    """Report the source metadata of an imported scan; no application state writes."""
    from rtc360_vis_probe import probe, probe_project
    try:
        meta = item['meta']
        folder = meta.get('source_path')
        if not folder and meta.get('source_file'):
            folder = str(Path(meta['source_file']).parent)
        if not folder:
            return _failure('This scan does not record an original scanner setup folder.')
        result = read_pose(folder)
        root = Path(folder).resolve().parent
        return dict(result, inspection=probe(root), project_marker=probe_project(root.parent / 'project.rtc360'))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return _failure(exc)
