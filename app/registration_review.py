# Copyright (c) 2026 Damian Gulczynski.
"""Persist a bounded target-pair review without applying its provisional pose.

Review and adoption are separate from the existing network solver. Adoption
returns only explicitly approved Fit-to-Exclude edits; target observations,
coordinate constraints and measured point files are never changed here.
"""
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import json
import math
import uuid

import registration_workspace as rw
import robust_registration


_MISSING = object()
MAX_PAIRS = 200


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _canonical(body):
    if not isinstance(body, dict):
        raise ValueError('Supply the scans and target matches to review.')
    a, b = body.get('a'), body.get('b')
    if any(not isinstance(i, str) or not i or len(i) > 200 for i in (a, b)) or a == b:
        raise ValueError('Choose two different scans.')
    mode = body.get('mode', 'rigid')
    if mode not in ('rigid', 'vertical'):
        raise ValueError('Choose full 3D or keep vertical geometry.')
    confirmed = body.get('vertical_confirmed', False)
    if not isinstance(confirmed, bool):
        raise ValueError('Confirm the shared vertical using the checkbox.')
    if mode == 'vertical' and not confirmed:
        raise ValueError('Confirm that the scans share the same vertical (Z) direction, or choose Full 3D.')
    try:
        tolerance = float(body.get('tolerance', 5))
    except (TypeError, ValueError):
        raise ValueError('The review tolerance must be a positive number in millimetres.') from None
    if isinstance(body.get('tolerance'), bool) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('The review tolerance must be a positive number in millimetres.')
    matches = body.get('matches')
    if not isinstance(matches, list) or len(matches) > MAX_PAIRS:
        raise ValueError('Supply no more than 200 target matches.')
    clean, used_a, used_b = [], set(), set()
    for match in matches:
        if not isinstance(match, dict):
            raise ValueError('Every target match must contain two target names and a role.')
        na, nb = match.get('a'), match.get('b')
        role, keep = match.get('role', 'fit'), match.get('keep', '')
        if any(not isinstance(n, str) or len(n) > 60 for n in (na, nb)):
            raise ValueError('Choose existing target names of up to 60 characters.')
        if role not in ('fit', 'check', 'off') or keep not in ('', 'a', 'b'):
            raise ValueError('Choose Fit, Check or Exclude and a valid coordinate choice.')
        if keep and role != 'fit':
            raise ValueError('Only Fit targets can keep coordinates. Check and excluded targets are independent.')
        if role != 'off':
            if not na or not nb:
                raise ValueError('Choose both target names for every active match.')
            if na in used_a or nb in used_b:
                raise ValueError('Each target can appear only once in an active link.')
            used_a.add(na)
            used_b.add(nb)
        clean.append(dict(a=na, b=nb, role=role, keep=keep))
    return dict(a=a, b=b, matches=clean, mode=mode, tolerance=tolerance,
                vertical_confirmed=confirmed)


def _sync(s):
    import job_api
    job_api.sync_active(s)
    rw.persist(s)


def _file_identity(value, required=False):
    if not value:
        if required:
            raise ValueError('A source scan is no longer available. Reload the scan before reviewing its matches.')
        return None
    path = Path(value)
    try:
        info = path.stat()
        if not path.is_file():
            raise OSError('not a file')
    except OSError:
        if required:
            raise ValueError('A source scan file is no longer available. Reload the scan before reviewing its matches.') from None
        return dict(path=str(path.resolve()), missing=True)
    return dict(path=str(path.resolve()), bytes=info.st_size,
                modified_ns=info.st_mtime_ns, changed_ns=info.st_ctime_ns)


def source_stamp(s, a, b):
    """Check metadata and file identities without reading a large point cloud."""
    identities = []
    for ident in (a, b):
        item = rw.scan(s, ident)
        meta = item['meta']
        state = rw.state_for(item)
        identities.append(dict(
            id=ident, count=meta.get('count'),
            points=_file_identity(meta.get('file'), required=True),
            mask=_file_identity(meta.get('mask_file')),
            attributes=_file_identity(meta.get('attributes_file')),
            revision=state.get('revision'), rows=state.get('rows', []),
            origin=item.get('scan_origin'), orientation=item.get('scan_orientation'),
        ))
    return _digest(dict(workspace=str(Path(s.WORK).resolve()),
                        project=s.STATE.get('project_id'), scans=identities))


def prepare(s, body):
    request = _canonical(body)
    _sync(s)
    aa, bb = rw.scan(s, request['a']), rw.scan(s, request['b'])
    ta, tb = rw.targets(aa), rw.targets(bb)
    rows = []
    for index, match in enumerate(request['matches']):
        row = dict(id=f'pair:{index}', role=match['role'], keep=match['keep'],
                   protected=bool(match['keep']))
        if match['role'] != 'off':
            if match['a'] not in ta or match['b'] not in tb:
                raise ValueError('A matched target is missing or excluded. Save target edits and refresh the pairs.')
            row.update(source=copy.deepcopy(tb[match['b']]['source']),
                       control=copy.deepcopy(ta[match['a']]['source']))
        rows.append(row)
    stamp = source_stamp(s, request['a'], request['b'])
    return dict(id=uuid.uuid4().hex, request=request, rows=rows,
                source_stamp=stamp, request_fingerprint=_digest(request))


def _replace(s, values):
    """Commit review metadata using the workspace's atomic save boundary."""
    previous = {key: s.STATE.get(key, _MISSING) for key in values}
    for key, value in values.items():
        if value is _MISSING:
            s.STATE.pop(key, None)
        else:
            s.STATE[key] = value
    try:
        s.save()
    except BaseException:
        for key, value in previous.items():
            if value is _MISSING:
                s.STATE.pop(key, None)
            else:
                s.STATE[key] = value
        raise


def complete(s, prepared, progress):
    request = prepared['request']
    # Recognition confidence measures the printed pattern, not the correctness
    # of a correspondence. Never use it to rank named target matches.
    result = robust_registration.review(
        prepared['rows'], tolerance_mm=request['tolerance'], mode=request['mode'],
        quality=None, progress=progress, max_iterations=2000, seed=0)
    for row in result['rows']:
        index = int(row['id'].split(':', 1)[1])
        match = request['matches'][index]
        row.update(match_index=index, a=match['a'], b=match['b'])
    progress('Checking that the reviewed targets are still current…')
    _sync(s)
    if source_stamp(s, request['a'], request['b']) != prepared['source_stamp']:
        raise ValueError('Scans or target observations changed during review. Review the current pairs again.')
    review = dict(
        id=prepared['id'], a=request['a'], b=request['b'], mode=request['mode'],
        tolerance_mm=request['tolerance'], vertical_confirmed=request['vertical_confirmed'],
        input_matches=copy.deepcopy(request['matches']),
        source_stamp=prepared['source_stamp'],
        request_fingerprint=prepared['request_fingerprint'],
        created_at=datetime.now(timezone.utc).isoformat(), result=result,
    )
    # Verify finite JSON before replacing a previous snapshot, then check Stop
    # immediately before publishing the completed review.
    _digest(review)
    progress('Saving the completed target-match review…')
    _replace(s, {'scan_match_review': review})
    return review


def start(s, body):
    prepared = prepare(s, body)
    # An older geometry solution must not reappear through Review pending after
    # the matching inputs have entered a new diagnostic review.
    _replace(s, {'scan_match_review': _MISSING, 'scan_link_draft': _MISSING})
    import job_api
    s.start_job(lambda: complete(s, prepared, lambda msg: job_api.progress(s, msg)), kind='job')
    return prepared['id']


def public(s):
    review = s.STATE.get('scan_match_review')
    if not isinstance(review, dict):
        return None
    try:
        if source_stamp(s, review['a'], review['b']) != review['source_stamp']:
            return None
    except (ValueError, KeyError, TypeError, OSError):
        return None
    return copy.deepcopy(review)


def adopt(s, body):
    request = _canonical(body)
    _sync(s)
    review = public(s)
    if not review or body.get('id') != review.get('id'):
        raise ValueError('This review is no longer current. Review the target matches again.')
    if _digest(request) != review['request_fingerprint']:
        raise ValueError('The matching inputs changed. Review the current pairs before excluding suggestions.')
    result = review['result']
    if result.get('status') != 'suspects' or result.get('can_adopt') is not True:
        raise ValueError('This review has no safe exclusion proposal. Check the target pairs manually.')
    proposed = result.get('proposed_exclusions', [])
    if not isinstance(proposed, list) or not proposed or len(set(proposed)) != len(proposed):
        raise ValueError('There are no current exclusion suggestions to adopt.')
    reviewed = {row['id']: row for row in result.get('rows', [])}
    matches = copy.deepcopy(request['matches'])
    for ident in proposed:
        row = reviewed.get(ident)
        index = row.get('match_index') if row else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(matches):
            raise ValueError('An exclusion suggestion is invalid. Review the pairs again.')
        match = matches[index]
        if (ident != f'pair:{index}' or match['role'] != 'fit' or match['keep'] or
                row.get('protected') or row.get('suggested_role') != 'off' or
                row.get('a') != match['a'] or row.get('b') != match['b']):
            raise ValueError('A protected or changed match cannot be excluded. Review the pairs again.')
        match['role'] = 'off'
    review.update(adopted_matches=copy.deepcopy(matches),
                  adopted_at=datetime.now(timezone.utc).isoformat())
    _replace(s, {'scan_match_review': review, 'scan_link_draft': _MISSING})
    return dict(id=review['id'], matches=matches, saved=True, started=False)


def attach_provenance(s, body, draft):
    """Record an adopted diagnostic on the existing solver's new link only.

    The robust transform is deliberately not used to position scans or replace
    the joint solver's exact coordinate constraints.
    """
    review = public(s)
    if not review or not review.get('adopted_matches'):
        return
    try:
        request = _canonical({**body, 'vertical_confirmed': review['vertical_confirmed']})
    except ValueError:
        return
    if (request['a'] != review['a'] or request['b'] != review['b'] or
            request['mode'] != review['mode'] or request['tolerance'] != review['tolerance_mm'] or
            request['matches'] != review['adopted_matches']):
        return
    result = review['result']
    audit = dict(id=review['id'], method='Reviewed robust target-pair exclusions',
                 paper='Pankaj and Nidamanuri (2016), doi:10.5566/ias.1378',
                 scope='Pairwise diagnostic; final coordinates use the existing joint target adjustment',
                 source_stamp=review['source_stamp'],
                 request_fingerprint=review['request_fingerprint'],
                 tolerance_mm=review['tolerance_mm'], mode=review['mode'],
                 adopted_at=review['adopted_at'],
                 original_matches=copy.deepcopy(review['input_matches']),
                 adopted_matches=copy.deepcopy(review['adopted_matches']),
                 proposed_exclusions=copy.deepcopy(result['proposed_exclusions']),
                 rows=copy.deepcopy(result['rows']),
                 diagnostics=copy.deepcopy(result['diagnostics']),
                 warnings=copy.deepcopy(result['warnings']))
    draft['match_review'] = audit
    for edge in draft.get('network_edges', []):
        if edge.get('a') == request['a'] and edge.get('b') == request['b']:
            edge['match_review'] = copy.deepcopy(audit)
