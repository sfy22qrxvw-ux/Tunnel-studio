# Copyright (c) 2026 Damian Gulczynski.
"""Atomic target removal with a bounded undo history."""
import copy
import uuid


def remove_targets(registration, candidate_ids, row_ids=()):
    if not isinstance(candidate_ids, (list, tuple)) or not isinstance(row_ids, (list, tuple)):
        raise ValueError('Target selection must be a list of IDs.')
    if not all(isinstance(x,str) for x in [*candidate_ids,*row_ids]):
        raise ValueError('Invalid target IDs.')
    ids, rows = set(candidate_ids), set(row_ids)
    if not ids and not rows:
        raise ValueError('Select at least one target to delete.')
    if not all(isinstance(x, str) for x in ids | rows):
        raise ValueError('Invalid target IDs.')
    candidates = registration.get('candidates', [])
    pairs = registration.get('rows', [])
    if not ids <= {c['id'] for c in candidates} or not rows <= {r['id'] for r in pairs}:
        raise ValueError('Targets changed. Refresh the Targets window and select them again.')
    snapshot = {k: copy.deepcopy(registration.get(k)) for k in ('candidates','rows','solution','transformed','detection')}
    history = registration.setdefault('target_undo', [])
    history.append(snapshot)
    del history[:-5]
    registration['candidates'] = [c for c in candidates if c['id'] not in ids]
    registration['rows'] = [r for r in pairs if r['id'] not in ids | rows and r.get('candidate_id') not in ids]
    registration.update(solution=None, transformed=None, revision=uuid.uuid4().hex)
    return len(candidates)-len(registration['candidates']), len(pairs)-len(registration['rows'])


def undo_target_removal(registration):
    history = registration.get('target_undo', [])
    if not history:
        raise ValueError('There is no target deletion to undo.')
    previous = history.pop()
    registration.update(previous, revision=uuid.uuid4().hex)
