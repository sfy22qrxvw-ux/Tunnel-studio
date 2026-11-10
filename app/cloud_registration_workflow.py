"""Reviewable cloud refinement of the existing manual registration session.

Stream retained XYZ into bounded samples, solve in a nearby frame, and keep
the candidate outside project state until explicit acceptance. Source stamps,
linked-group membership and the fixed anchor are checked again on acceptance.
This is local mathematical alignment, not survey control or global matching.
"""
import copy
import time
from pathlib import Path
import uuid

import numpy as np

import cloud_registration as icp
import job_api
import pointio
import registration_pose as pose
import registration_workspace as rw
import visual_registration as visual


def session(s, token):
    value = getattr(s, '_visual_registration', None)
    if not value or value['token'] != token:
        raise ValueError('This alignment session is no longer current. Reload the pair.')
    ids = value['fixed_ids'] + value['moving_ids']
    if value['context'] != id(s.STATE) or value['stamp'] != visual.fingerprint(s, ids):
        raise ValueError('The scans, positions or links changed. Reload the pair.')
    return value


def rigid(rotation, translation):
    try:
        R, t = np.asarray(rotation, dtype=float), np.asarray(translation, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Enter a finite rigid transform.') from exc
    if (R.shape != (3, 3) or t.shape != (3,) or not np.isfinite(R).all()
            or not np.isfinite(t).all() or not np.allclose(R.T @ R, np.eye(3), atol=1e-8, rtol=0)
            or abs(np.linalg.det(R) - 1) > 1e-8):
        raise ValueError('Registration requires a proper rotation and translation, with fixed scale.')
    return R, t


def assets(s, ids):
    """Check geometry and deletion-mask revisions, independent of attributes."""
    rows = []
    for ident in ids:
        meta = rw.scan(s, ident)['meta']
        count = meta.get('count')
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError('A scan has an invalid point count.')
        if meta.get('units', 'metres') not in ('metres', 'meters', 'm'):
            raise ValueError('Cloud registration requires coordinates in metres.')
        rigid(*pose.get(meta))
        for key, width in (('file', 24), ('mask_file', 1)):
            name = meta.get(key)
            if not name and key == 'mask_file':
                continue
            if not isinstance(name, (str, Path)) or not str(name).strip():
                raise ValueError('A scan has no XYZ cache path.')
            path = Path(name).resolve()
            status = path.stat()
            if not path.is_file() or status.st_size != count * width:
                raise ValueError('The ' + key + ' cache does not match its recorded point count.')
            rows.append((ident, key, str(path), status.st_dev, status.st_ino,
                         status.st_size, status.st_mtime_ns, status.st_ctime_ns))
    return rows


def _chunks(s, ids, origin, cancel):
    for ident in ids:
        icp._check(cancel)
        meta = copy.deepcopy(rw.scan(s, ident)['meta'])
        R, t = pose.get(meta)
        # Missing optional intensity/colour data must not prevent XYZ matching.
        meta['attributes_file'] = None
        with_chunks = pointio.chunks(meta)
        try:
            for points, _ in with_chunks:
                icp._check(cancel)
                yield points @ R.T + (t - origin), None
        finally:
            with_chunks.close()


def start(s, body):
    current = session(s, body.get('token'))
    if body.get('correct_tilt') is not True:
        raise ValueError('Enable Correct tilt: cloud-to-cloud refinement adjusts all six rigid axes.')
    R, t = rigid(body.get('rotation'), body.get('translation'))
    quality = body.get('quality', 'balanced')
    if not isinstance(quality, str) or quality not in icp.QUALITY:
        raise ValueError('Choose Fast, Balanced or High sampling.')
    iterations, tolerance, rng = icp._options(body.get('max_iterations', 50), body.get('tolerance_mm', 5.0), body.get('seed'))
    ids = current['fixed_ids'] + current['moving_ids']
    before = assets(s, ids)
    fixed_files = {Path(rw.scan(s, i)['meta']['file']).resolve() for i in current['fixed_ids']}
    if any(Path(rw.scan(s, i)['meta']['file']).resolve() in fixed_files for i in current['moving_ids']):
        raise ValueError('Fixed and moving groups share an XYZ cache. Choose independent scans.')
    ident = uuid.uuid4().hex
    candidate = dict(id=ident, state='running', applied=False)
    current['refinement'] = candidate
    history = current.setdefault('refinements', {})
    history[ident] = candidate
    while len(history) > 32:
        history.pop(next(iter(history)))

    def run():
        progress = lambda message: job_api.progress(s, message)
        cancel = lambda: job_api.CANCEL.is_set() or s.JOB_CANCEL_FILE.exists()
        try:
            origin = np.asarray(current['origin'], float)
            moving_cap, fixed_cap = icp.QUALITY[quality]
            progress('Reading retained points for cloud-to-cloud registration…')
            read_started=time.perf_counter()
            reference, ref_info = icp._sample_chunks(_chunks(s, current['fixed_ids'], origin, cancel), fixed_cap, rng, progress, cancel, 'fixed group')
            moving, moving_info = icp._sample_chunks(_chunks(s, current['moving_ids'], origin, cancel), moving_cap, rng, progress, cancel, 'moving group')
            solve_started=time.perf_counter()
            if min(len(moving), len(reference)) < icp.MIN_OVERLAP:
                result = icp._base_result('insufficient overlap')
            else:
                moving = moving @ R.T + t
                progress('Estimating reference normals once…')
                normals, tree = icp._normals(reference, 12, progress, cancel)
                result = icp._run_icp(moving, reference, normals, tree, iterations, tolerance, rng, progress, cancel)
            if body.get('diagnostics') is True:
                candidate['timings']={'cloud_read_ms':(solve_started-read_started)*1000,
                                      'solve_ms':(time.perf_counter()-solve_started)*1000}
            icp._check(cancel)
            session(s, current['token'])
            if before != assets(s, ids):
                raise ValueError('The source points or masks changed. Reload the pair.')
            delta_R, delta_t = rigid(result['rotation'], result['translation'])
            local_R, local_t = delta_R @ R, delta_R @ t + delta_t
            # Include the user's manual draft in the final world-space delta.
            result = icp._frame_result(result, origin)
            world_t = origin + local_t - local_R @ origin
            matrix = np.eye(4)
            matrix[:3, :3], matrix[:3, 3] = local_R, world_t
            result.update(rotation=local_R.tolist(), translation=world_t.tolist(), matrix=matrix.tolist(),
                          source_centroid=origin.tolist(), control_centroid=(origin + local_t).tolist(),
                          transform_frame='current moving-group world coordinates to fixed-group world coordinates',
                          quality=quality, moving_sampling=moving_info, reference_sampling=ref_info,
                          anchor=current['fixed'], anchor_name=rw.scan(s, current['fixed'])['name'], cloud_to_cloud=True)
            # Target tables expect target IDs and 3D residuals. Keep these plane
            # diagnostics distinct; sample indices are never raw point IDs.
            result['sample_residuals'] = result.pop('residuals', [])
            result['residuals'] = []
            result['warnings'].append('Local refinement only: repetitive surfaces can produce an incorrect alignment with small residuals. Review plan and elevation before saving.')
            if moving_info['nonfinite_points_skipped'] or ref_info['nonfinite_points_skipped']:
                result['warnings'].append('Non-finite XYZ records were skipped.')
            candidate.update(state='ready' if result['converged'] else 'refused', result=result,
                             rotation=local_R.tolist(), translation=local_t.tolist(), assets=before)
            progress('Refinement ready for review. No scan positions have been saved.' if result['converged'] else 'Refinement was not accepted: ' + result.get('reason', 'not converged') + '.')
        except icp._Cancelled:
            candidate.update(state='cancelled', reason='Cancelled; no scan positions were changed.')
        except BaseException as exc:
            candidate.update(state='cancelled' if cancel() else 'failed', reason=str(exc))
            raise

    s.start_job(run, kind='job', native_progress=True)
    return dict(started=True, refinement_id=ident)


def status(s, body):
    current = session(s, body.get('token'))
    value = current.get('refinement')
    if not value or value['id'] != body.get('refinement_id'):
        raise ValueError('This refinement result is no longer current.')
    return copy.deepcopy({k: v for k, v in value.items() if k != 'assets'})


def accepted_solution(s, current, body, R, t):
    """Use the reviewed server result, never client-supplied residuals."""
    candidate = current.get('refinements', {}).get(body.get('refinement_id'))
    if (not candidate or candidate['id'] != body.get('refinement_id')
            or candidate['state'] != 'ready' or not candidate['result']['converged']):
        raise ValueError('Run cloud-to-cloud refinement again before saving this result.')
    if not (np.allclose(R, candidate['rotation'], atol=1e-12, rtol=0)
            and np.allclose(t, candidate['translation'], atol=1e-12, rtol=0)):
        raise ValueError('The alignment changed after refinement. Refine again or save it as manual alignment.')
    if candidate['assets'] != assets(s, current['fixed_ids'] + current['moving_ids']):
        raise ValueError('The source points or masks changed after refinement. Reload the pair.')
    return copy.deepcopy(candidate['result'])
