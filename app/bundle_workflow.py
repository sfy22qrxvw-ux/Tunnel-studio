"""Stream, review and atomically accept a global scan bundle adjustment.

Existing target links, cloud links and explicitly proposed loop pairs are used
together. Immutable XYZ caches supply bounded samples. Drafts stay outside the
project until Apply, which uses the existing virtual-pose transaction and Undo.
Cloud overlap and low residuals do not establish survey accuracy or correct
physical correspondence in repetitive tunnel geometry.
"""
import copy
import uuid
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

import bundle_adjustment as bundle
import cloud_registration as icp
import cloud_registration_workflow as cloud
import job_api
import registration_network as network
import registration_pose as pose
import registration_workspace as rw
import visual_registration as visual

QUALITY = {'fast': (500000, 15000, 1000), 'balanced': (1500000, 30000, 2000),
           'high': (3000000, 60000, 4000)}
PAIRWISE_METHOD = 'Validated pairwise rigid adjustment after full bundle nonconvergence'
FALLBACK_REVIEW_WARNING = ('Full bundle adjustment did not converge. A pairwise adjustment is proposed instead. '
                           'Review residuals before applying or measurement use.')
FALLBACK_APPLIED_WARNING = ('Full bundle adjustment did not converge. A pairwise adjustment was applied instead. '
                            'Review residuals before measurement use.')


def _cancel(s):
    return job_api.CANCEL.is_set() or s.JOB_CANCEL_FILE.exists()


def _options(body):
    quality = body.get('quality', 'balanced')
    if not isinstance(quality, str) or quality not in QUALITY:
        raise ValueError('Choose Fast, Balanced or High bundle sampling.')
    values = {}
    for key, default, maximum in (('tolerance_mm', 5., 1000.), ('capture_mm', 250., 10000.)):
        value = body.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 < value <= maximum:
            raise ValueError('Enter a positive ' + key.replace('_mm', '').replace('_', ' ') + ' in millimetres.')
        values[key] = float(value)
    iterations = body.get('max_iterations', 20)
    if isinstance(iterations, bool) or not isinstance(iterations, int) or not 2 <= iterations <= 50:
        raise ValueError('Choose 2 to 50 bundle iterations.')
    return dict(quality=quality, max_iterations=iterations, **values)


def inspect(s, body):
    rw.persist(s)
    anchor = body.get('anchor')
    rw.scan(s, anchor)
    ids = rw.component(s, anchor)
    links = [e for e in s.STATE.get('scan_links', []) if e['a'] in ids and e['b'] in ids]
    existing = {frozenset((e['a'], e['b'])) for e in links}
    bounds = {i: pose.world_bounds(rw.scan(s, i)['meta']) for i in ids}
    candidates = []
    # Sweep one spatial axis; candidates are suggestions only, never accepted
    # loop constraints. Cap the displayed suggestion list, not bundle size.
    ordered = sorted(ids, key=lambda i: bounds[i][0][0])
    for at, a in enumerate(ordered):
        lo, hi = bounds[a]
        for b in ordered[at+1:]:
            other_lo, other_hi = bounds[b]
            if other_lo[0] > hi[0]:break
            if frozenset((a, b)) in existing:continue
            extent = np.minimum(hi, other_hi) - np.maximum(lo, other_lo)
            if np.all(extent >= 0):
                candidates.append(dict(a=a, b=b, score=float(np.prod(np.maximum(extent, .01)))))
    candidates.sort(key=lambda v: v['score'], reverse=True)
    return dict(scans=[dict(id=i, name=rw.scan(s, i)['name']) for i in sorted(ids)],
                links=[dict(id=e['id'], a=e['a'], b=e['b'], kind=e.get('kind', 'target_match'),
                            loop_closure=bool(e.get('loop_closure'))) for e in links],
                cycle_count=max(0, len(links)-len(ids)+1), suggestions=candidates[:50],
                suggestions_total=len(candidates),
                note='Bounding-box overlap is only a suggestion. Added loop pairs must pass geometric validation.')


def _target_data(s, edges, origin):
    parent, coordinates, observations = {}, {}, []
    fit_keys, check_keys = set(), set()
    def root(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]];key = parent[key]
        return key
    for edge in edges:
        aa, bb = rw.scan(s, edge['a']), rw.scan(s, edge['b'])
        ta, tb = rw.targets(aa), rw.targets(bb)
        matches = rw.matched_targets(aa, bb, edge.get('matches', []))
        edge['matches'] = matches
        edge['has_targets'] = False
        for match in matches:
            if match['role'] == 'off':continue
            pa = np.asarray(ta[match['a']]['source'], float) - origin
            pb = np.asarray(tb[match['b']]['source'], float) - origin
            observations.append(dict(id=match['a']+' ↔ '+match['b'], edge_id=edge['id'],
                                     a=edge['a'], b=edge['b'], role=match['role'], control=pa, source=pb))
            keys = {(edge['a'], match['a']), (edge['b'], match['b'])}
            (fit_keys if match['role'] == 'fit' else check_keys).update(keys)
            if match['role'] != 'fit':continue
            edge['has_targets'] = True
            ka, kb = (edge['a'], match['a']), (edge['b'], match['b'])
            coordinates[ka], coordinates[kb] = pa, pb
            parent[root(kb)] = root(ka)
    if fit_keys & check_keys:
        raise ValueError('A target observation is used as both Fit and Check on different links. Give it one consistent role so check targets remain independent.')
    groups = {}
    for key in coordinates:groups.setdefault(root(key), []).append((key[0], coordinates[key]))
    landmarks = list(groups.values())
    for group in landmarks:
        if len({i for i, _ in group}) != len(group):
            raise ValueError('Target matches merge different targets from the same setup. Correct those names or links before bundle adjustment.')
    return observations, landmarks


def _seeds(edges, observations, layout, tolerance):
    poses = {i: (np.eye(3), np.zeros(3)) for i in layout.seed_ids}
    solved = {}
    for edge in edges:
        rows = [{**r, 'source': np.asarray(r['source']).tolist(), 'control': np.asarray(r['control']).tolist()}
                for r in observations if r['edge_id'] == edge['id']]
        if sum(r['role'] == 'fit' for r in rows) < (2 if edge['mode'] == 'vertical' else 3):continue
        try:
            out = network._edge_solution(edge, rows, tolerance)
            solved[edge['id']] = np.asarray(out['rotation']), np.asarray(out['translation'])
        except ValueError:
            # Individually weak links can still constrain a joint bundle.
            continue
    for _ in layout.centres:
        changed = False
        for edge in edges:
            if edge['id'] not in solved:continue
            a, b = edge['a'], edge['b'];R, t = solved[edge['id']]
            if a in poses and b not in poses:
                A, ta = poses[a];poses[b] = A@R, A@t+ta;changed = True
            elif b in poses and a not in poses:
                B, tb = poses[b];poses[a] = B@R.T, tb-B@R.T@t;changed = True
        if not changed:break
    return poses


def _pairwise_fallback(nodes, edges, landmarks, observations, layout, options,
                       failure, cloud_rows, progress, cancel):
    """Fit current-coordinate links independently, then validate the whole forest.

    Historical link deltas must not be applied a second time. Every edge is
    solved again from the current target coordinates or bounded cloud samples.
    A deterministic spanning forest propagates these relative rigid motions;
    non-tree links are checks, not an implicit claim of joint loop closure.
    """
    tolerance = options['tolerance_mm']
    ordered = sorted(edges, key=lambda e: (str(e['id']), e['a'], e['b']))
    solved, fit_kinds = {}, {}
    for index, edge in enumerate(ordered, 1):
        bundle.check(cancel)
        progress(f'Pairwise fallback: validating link {index}/{len(ordered)}')
        rows = [{**r, 'source': np.asarray(r['source']).tolist(),
                 'control': np.asarray(r['control']).tolist()}
                for r in observations if r['edge_id'] == edge['id']]
        enough_targets = sum(r['role'] == 'fit' for r in rows) >= (2 if edge['mode'] == 'vertical' else 3)
        target_error = None
        if enough_targets:
            try:
                fit = network._edge_solution(edge, rows, tolerance)
                if fit['fit']['rms_mm'] > tolerance:
                    raise ValueError('target fit residual exceeds the review tolerance')
                fit_kinds[edge['id']] = 'targets'
            except ValueError as exc:
                target_error = exc
        if not enough_targets or target_error is not None:
            a, b = nodes[edge['a']], nodes[edge['b']]
            if not edge.get('use_cloud') or a.get('tree') is None or b.get('tree') is None:
                detail = str(target_error) if target_error is not None else 'too few independent target observations or surface points'
                raise ValueError('Pairwise fallback failed on link ' + edge['id'] + ': ' + detail)
            fit = icp._run_icp(b['points'], a['points'], a['normals'], a['tree'],
                               50, tolerance / 1000, np.random.default_rng(0), progress, cancel)
            if (not fit.get('converged') or fit.get('inlier_count', 0) < bundle.MIN_OVERLAP
                    or fit.get('normal_matrix_rank', 0) < 6):
                raise ValueError('Pairwise fallback failed on link ' + edge['id'] + ': '
                                 + fit.get('reason', 'insufficient independently constrained overlap'))
            fit_kinds[edge['id']] = 'point-to-plane ICP'
        R, t = np.asarray(fit['rotation'], float), np.asarray(fit['translation'], float)
        cloud.rigid(R, t)
        solved[edge['id']] = R, t

    # A scan containing held coordinates may be fixed more strongly here than
    # in the joint solve. Identity at every such root preserves all references.
    roots = set(layout.seed_ids) | set(layout.fixed)
    if not roots:
        raise ValueError('Pairwise fallback needs a fixed reference setup.')
    poses = {ident: (np.eye(3), np.zeros(3)) for ident in roots}
    tree_edges = []
    while len(poses) < len(nodes):
        bundle.check(cancel)
        changed = False
        for edge in ordered:
            a, b = edge['a'], edge['b']
            R, t = solved[edge['id']]
            if a in poses and b not in poses:
                A, ta = poses[a]
                poses[b] = A @ R, A @ t + ta
            elif b in poses and a not in poses:
                B, tb = poses[b]
                poses[a] = B @ R.T, tb - B @ R.T @ t
            else:
                continue
            tree_edges.append(edge['id'])
            changed = True
        if not changed:
            raise ValueError('Pairwise fallback cannot connect every scan to a held reference.')

    # Reject motions the existing vertical/reference parameterisation cannot
    # represent exactly. Silently projecting them would change solved links.
    parameters = layout.seed(poses)
    centred = layout.transforms(parameters)
    for ident, (R, t) in poses.items():
        Q, u = centred[ident]
        centre = layout.centres[ident]
        actual_t = centre + u - Q @ centre
        if (not np.allclose(Q, R, rtol=0, atol=1e-7)
                or not np.allclose(actual_t, t, rtol=0, atol=1e-7)):
            raise ValueError('Pairwise fallback conflicts with a held coordinate or keep-vertical constraint. No positions were applied.')

    # Check every edge, including loops not used to construct the forest.
    # Each individual solution may fit well while the composed loop does not.
    before_values, after_values, reports = [], [], {}
    identity = {ident: (np.eye(3), np.zeros(3)) for ident in nodes}
    for edge in ordered:
        bundle.check(cancel)
        target_errors = []
        for row in observations:
            if row['edge_id'] != edge['id'] or row['role'] != 'fit':
                continue
            p = bundle.transformed(np.asarray(row['control'])[None], edge['a'], centred, layout.centres)[0]
            q = bundle.transformed(np.asarray(row['source'])[None], edge['b'], centred, layout.centres)[0]
            target_errors.append(float(np.linalg.norm(q - p)))
        target_stats = bundle.statistics(target_errors)
        if target_errors and target_stats['rms_mm'] > tolerance:
            raise ValueError('Pairwise fallback conflicts with target observations on link ' + edge['id'] + '. Review this link or loop.')
        if not edge.get('use_cloud'):
            continue
        match = bundle.cloud_matches(edge, nodes, centred, layout.centres,
                                     options['capture_mm'] / 1000, cloud_rows, cancel)
        if match is None:
            if fit_kinds[edge['id']] == 'targets':
                continue
            raise ValueError('Pairwise fallback has insufficient distinct overlap on link ' + edge['id'] + '.')
        values = bundle.plane_residual(match, centred, layout.centres)
        use = np.abs(values) <= 3 * tolerance / 1000
        retained = int(np.count_nonzero(use))
        if retained < bundle.MIN_OVERLAP:
            raise ValueError('Pairwise fallback has insufficient consistent overlap on link ' + edge['id'] + '.')
        after = bundle.statistics(values[use])
        if after['rms_mm'] > tolerance:
            raise ValueError('Pairwise fallback conflicts with surface observations on link ' + edge['id'] + '. Review this link or loop.')
        old = bundle.plane_residual(match, identity, layout.centres)[use]
        before_values.append(old);after_values.append(values[use])
        reports[edge['id']] = dict(before=bundle.statistics(old), after=after,
            inlier_count=retained, sampled_correspondences=len(values),
            rejected_correspondences=int(len(values) - retained),
            distinct_overlap=match['support'], overlap_fraction=match['overlap_fraction'],
            residual_type='point-to-plane', pairwise_method=fit_kinds[edge['id']],
            spanning_tree_edge=edge['id'] in tree_edges)
    bundle.check(cancel)
    targets = [np.mean([bundle.transformed(np.asarray(p)[None], ident, centred, layout.centres)[0]
                        for ident, p in group], axis=0) for group in landmarks]
    warnings = [FALLBACK_REVIEW_WARNING,
                'Pairwise fits were propagated through a deterministic spanning forest; all remaining links were checked. This is not a converged joint bundle or a joint loop-closure adjustment.',
                'Existing scan coordinates and scale are unchanged. Residuals are not survey accuracy or probability.']
    if set(layout.seed_ids) - set(layout.fixed):
        warnings.append('Setups containing held coordinates were kept entirely fixed during pairwise fallback.')
    return dict(method=PAIRWISE_METHOD, converged=False, full_bundle_converged=False,
                pairwise_converged=True, fallback_used=True, fallback_reason=str(failure), applied=False,
                transforms=centred, landmarks=np.asarray(targets).reshape(-1, 3),
                iterations=failure.iterations, evaluations=failure.evaluations,
                condition_estimate=failure.condition_estimate, scan_count=len(nodes),
                common_targets=len(landmarks), target_observations=sum(map(len, landmarks)),
                cloud_reports=reports, warnings=warnings, scale=1., centres=layout.centres,
                spanning_tree_edges=tree_edges,
                cloud_fit=bundle.statistics(np.concatenate(after_values) if after_values else []),
                cloud_before=bundle.statistics(np.concatenate(before_values) if before_values else []))


def build(s, body, progress=lambda message: None, cancel=lambda: False, proposed=None, references=None):
    options = _options(body)
    rw.persist(s)
    anchor = body.get('anchor');rw.scan(s, anchor)
    ids = rw.component(s, anchor)
    if proposed:
        ids |= rw.component(s, proposed['a']) | rw.component(s, proposed['b'])
    if len(ids) < 2:
        raise ValueError('Register at least two setups first, then adjust their connected bundle.')
    before_stamp = rw.stamp(s, ids)
    full_stamp = visual.fingerprint(s, ids)
    topology = network.topology(s, ids)
    context = id(s.STATE)
    edges = [copy.deepcopy(e) for e in s.STATE.get('scan_links', []) if e['a'] in ids and e['b'] in ids]
    if proposed:edges = [e for e in edges if e['id'] != proposed['id']] + [copy.deepcopy(proposed)]
    loops = body.get('loops', [])
    if not isinstance(loops, list):raise ValueError('Loop pairs must be a list.')
    pairs = {frozenset((e['a'], e['b'])) for e in edges}
    if len(pairs) != len(edges):raise ValueError('Remove duplicate links before adjusting this bundle.')
    for pair in loops:
        if not isinstance(pair, dict):raise ValueError('Choose two setups for each loop.')
        a, b = pair.get('a'), pair.get('b')
        if a == b or a not in ids or b not in ids:
            raise ValueError('A loop must connect two different setups in this bundle.')
        key = frozenset((a, b))
        if key in pairs:raise ValueError('That pair already has a link or loop candidate.')
        pairs.add(key)
        edges.append(dict(id=uuid.uuid4().hex, a=a, b=b, kind='cloud_to_cloud',
                          mode='rigid', matches=[], loop_closure=True, proposed_loop=True))
    origin = np.mean(pose.world_bounds(rw.scan(s, anchor)['meta']), axis=0)
    observations, landmarks = _target_data(s, edges, origin)
    use_cloud = body.get('use_cloud', True)
    if not isinstance(use_cloud, bool):raise ValueError('Choose whether to refine cloud surfaces.')
    for edge in edges:
        edge['mode'] = network.edge_mode(edge)
        edge['use_cloud'] = use_cloud or not edge['has_targets']
    cloud_ids = {i for e in edges if e['use_cloud'] for i in (e['a'], e['b'])}
    before_assets = cloud.assets(s, sorted(cloud_ids))
    files = [str(Path(rw.scan(s, i)['meta']['file']).resolve()) for i in cloud_ids]
    if len(set(files)) != len(files):raise ValueError('The bundle contains duplicate references to one XYZ cache. Select independent setups.')
    total_cap, per_scan, cloud_rows = QUALITY[options['quality']]
    cap = min(per_scan, total_cap // max(1, len(cloud_ids)))
    if cloud_ids and cap < 1000:
        raise ValueError('The selected sampling budget cannot give this many setups useful surface support. Choose a higher quality or a smaller group.')
    rng = np.random.default_rng(0)
    nodes, sampling = {}, {}
    for at, ident in enumerate(sorted(ids)):
        bundle.check(cancel)
        item = rw.scan(s, ident)
        progress(f'Reading setup {at+1}/{len(ids)}: {item["name"]}')
        points = np.empty((0, 3))
        normals, tree = None, None
        if ident in cloud_ids:
            points, info = icp._sample_chunks(cloud._chunks(s, [ident], origin, cancel), cap, rng, progress, cancel, item['name'])
            sampling[ident] = info
            if len(points) >= bundle.MIN_OVERLAP:
                normals, tree = icp._normals(points, 12, progress, cancel)
        nodes[ident] = dict(points=points, normals=normals, tree=tree)
    centres = {}
    for ident in ids:
        points = [p for group in landmarks for i, p in group if i == ident]
        if not points:points = nodes[ident]['points']
        centres[ident] = np.mean(points, axis=0) if len(points) else np.mean(pose.world_bounds(rw.scan(s, ident)['meta']), axis=0)-origin
    from registration_references import references as validate_references
    kept = validate_references(s, ids, references, edges, body.get('mode', 'rigid')) if references is not None else []
    local_refs = [{**r, 'coordinates': (np.asarray(r['coordinates'])-origin).tolist()} for r in kept]
    if references is not None and len({e['mode'] for e in edges}) > 1:
        raise ValueError('Choose a fixed setup for a bundle containing both 2D and 3D links.')
    layout = network._ReferenceMotions(ids, centres, local_refs, body.get('mode', 'rigid')) if references is not None else network._MixedMotions(ids, edges, anchor, centres)
    initial = _seeds(edges, observations, layout, options['tolerance_mm']) if not loops else None
    for edge in edges:
        if not edge.get('proposed_loop'):continue
        progress('Validating loop: '+rw.scan(s, edge['a'])['name']+' ↔ '+rw.scan(s, edge['b'])['name'])
        aa, bb = nodes[edge['a']], nodes[edge['b']]
        if aa['tree'] is None or bb['tree'] is None:
            raise ValueError('The proposed loop has too few retained surface points.')
        fit = icp._run_icp(bb['points'], aa['points'], aa['normals'], aa['tree'],
                           50, options['tolerance_mm']/1000, np.random.default_rng(0), progress, cancel)
        if not fit['converged']:
            raise ValueError('Loop validation failed: '+fit.get('reason', 'weak geometry')+'. Review the starting alignment.')
        edge['initial_guide'] = np.asarray(fit['rotation']), np.asarray(fit['translation'])
        edge['loop_validation'] = {k: fit[k] for k in ('method', 'rms_mm', 'median_mm', 'max_mm', 'inlier_count', 'converged', 'warnings')}
    try:
        out = bundle.optimise(nodes, edges, landmarks, layout, initial=initial,
                              tolerance_mm=options['tolerance_mm'], capture_mm=options['capture_mm'],
                              max_iterations=options['max_iterations'], cloud_rows=cloud_rows,
                              progress=progress, cancel=cancel)
    except bundle.NonConvergence as failure:
        bundle.check(cancel)
        progress('Full bundle did not converge. Checking an independent pairwise fallback…')
        out = _pairwise_fallback(nodes, edges, landmarks, observations, layout,
                                 options, failure, cloud_rows, progress, cancel)
    bundle.check(cancel)
    if context != id(s.STATE) or full_stamp != visual.fingerprint(s, ids) or before_assets != cloud.assets(s, sorted(cloud_ids)):
        raise ValueError('Scans, links or source files changed while adjusting. Run the bundle again.')
    transformed = out['transforms'];reports, after, before = {}, [], []
    for row in observations:
        a, b = row['a'], row['b'];p, q = row['control'], row['source']
        newp = bundle.transformed(p[None], a, transformed, centres)[0]
        newq = bundle.transformed(q[None], b, transformed, centres)[0]
        error, old = float(np.linalg.norm(newq-newp)*1000), float(np.linalg.norm(q-p)*1000)
        item = {**row, 'control': (newp+origin).tolist(), 'source': (q+origin).tolist(),
                'transformed': (newq+origin).tolist(), 'error_mm': error, 'before_mm': old,
                'residual_mm': ((newq-newp)*1000).tolist(), 'within_limit': error <= options['tolerance_mm']}
        after.append(item);before.append({**item, 'error_mm': old})
    for ref in kept:
        p = np.asarray(ref['coordinates']) - origin
        ref['movement_mm'] = float(np.linalg.norm(bundle.transformed(p[None], ref['scan_id'], transformed, centres)[0]-p)*1000)
        if ref['movement_mm'] > 1e-4:raise ValueError('A held coordinate moved. The bundle was refused.')
    target_fit, target_before = network.stats(after), network.stats(before)
    warnings = out['warnings']
    if not network.stats(after, 'check')['count']:warnings.append('No independent check targets are present.')
    if any(not r['within_limit'] for r in after):warnings.append('Some target residuals exceed the review tolerance.')
    if any(e['mode'] == 'vertical' for e in edges):warnings.append('Earlier keep-vertical links retain their recorded up-direction constraint.')
    warnings.append('View crops and hidden regions do not limit matching; all retained source points are eligible for sampling.')
    for edge in edges:
        edge.pop('initial_guide', None)
        edge.pop('proposed_loop', None)
        rows = [r for r in after if r['edge_id'] == edge['id']]
        old = [r for r in before if r['edge_id'] == edge['id']]
        surface = out['cloud_reports'].get(edge['id'])
        fit = network.stats(rows)
        reports[edge['id']] = dict(method=out['method'], fit=fit if fit['count'] else (surface or {}).get('after', bundle.statistics([])),
                                  check=network.stats(rows, 'check'), before=network.stats(old) if fit['count'] else (surface or {}).get('before', bundle.statistics([])),
                                  target_fit=fit, cloud=surface, residuals=rows, mode=edge['mode'],
                                  warnings=warnings, tolerance_mm=options['tolerance_mm'], bundle=True,
                                  loop_closure=bool(edge.get('loop_closure')), coordinate_references=kept,
                                  fallback_used=out.get('fallback_used', False),
                                  full_bundle_converged=out.get('full_bundle_converged', out['converged']),
                                  pairwise_converged=out.get('pairwise_converged', False))
    summary = dict(method=out['method'], bundle=True, fit=target_fit if target_fit['count'] else out['cloud_fit'],
                   before=target_before if target_fit['count'] else out['cloud_before'],
                   target_fit=target_fit, cloud_fit=out['cloud_fit'], cloud_before=out['cloud_before'],
                   check=network.stats(after, 'check'), residuals=after, warnings=warnings,
                   anchor=anchor if references is None else None, anchor_name=rw.scan(s, anchor)['name'] if references is None else None,
                   coordinate_references=kept, tolerance_mm=options['tolerance_mm'], scale=1.,
                   converged=out['converged'], full_bundle_converged=out.get('full_bundle_converged', out['converged']),
                   fallback_used=out.get('fallback_used', False), fallback_reason=out.get('fallback_reason'),
                   pairwise_converged=out.get('pairwise_converged', False),
                   scan_count=len(ids), link_count=len(edges), cycle_count=max(0, len(edges)-len(ids)+1),
                   common_targets=out['common_targets'], target_observations=out['target_observations'],
                   iterations=out['iterations'], evaluations=out['evaluations'], condition_estimate=out['condition_estimate'],
                   mode=body.get('mode', 'rigid'), constraint='Fixed reference with preserved existing vertical-link constraints',
                   quality=options['quality'], sampling=sampling,
                   edge_reports=[dict(id=e['id'], a=rw.scan(s, e['a'])['name'], b=rw.scan(s, e['b'])['name'],
                                      before_mm=reports[e['id']]['before']['rms_mm'], after_mm=reports[e['id']]['fit']['rms_mm'],
                                      cloud=reports[e['id']]['cloud'], target_fit=reports[e['id']]['target_fit'],
                                      loop_closure=bool(e.get('loop_closure'))) for e in edges])
    transforms = {i: dict(rotation=R.tolist(), translation=(origin+centres[i]+t-R@(origin+centres[i])).tolist())
                  for i, (R, t) in transformed.items() if i in layout.moving}
    for value in transforms.values():cloud.rigid(value['rotation'], value['translation'])
    a, b = (proposed['a'], proposed['b']) if proposed else (anchor, next(i for i in sorted(ids) if i != anchor))
    draft = dict(id=uuid.uuid4().hex, a=a, b=b, solution=summary, mode=body.get('mode', 'rigid'),
                 moving=layout.moving, fixed=layout.fixed, stamp=before_stamp, network=True, bundle=True,
                 kind='bundle_adjustment', network_topology=topology, network_edges=edges,
                 network_reports=reports, transforms=transforms, coordinate_references=kept,
                 bundle_stamp=full_stamp, bundle_assets=before_assets, bundle_cloud_ids=sorted(cloud_ids),
                 matches=proposed.get('matches', []) if proposed else [])
    preview = []
    per_scan = max(1, 40000 // len(ids))
    for ident in sorted(ids):
        points = nodes[ident]['points']
        if not len(points):points = np.asarray([p for group in landmarks for i, p in group if i == ident]).reshape(-1, 3)
        if len(points) > per_scan:points = points[np.linspace(0, len(points)-1, per_scan, dtype=int)]
        R, t = transformed[ident]
        preview.append(dict(id=ident, name=rw.scan(s, ident)['name'], fixed=ident in layout.fixed,
                            before=points.tolist(), after=bundle.transformed(points, ident, transformed, centres).tolist(),
                            shift_mm=float(np.linalg.norm(t)*1000), rotation_deg=float(np.linalg.norm(Rotation.from_matrix(R).as_rotvec())*180/np.pi)))
    return draft, preview


def record_applied(s, draft):
    """Record fallback truth inside the existing acceptance/save transaction."""
    if not draft.get('solution', {}).get('fallback_used'):
        return
    summaries = [draft['solution'], *draft.get('network_reports', {}).values()]
    last = s.STATE.get('last_registration_result')
    if last and last.get('id') == draft['id']:
        summaries.append(last['solution'])
    for edge in s.STATE.get('scan_links', []):
        if edge.get('network_adjustment_id') == draft['id']:
            edge['status'] = 'pairwise fallback adjusted'
            summaries.append(edge['solution'])
    for summary in summaries:
        warnings = [w for w in summary.get('warnings', []) if w != FALLBACK_REVIEW_WARNING]
        if FALLBACK_APPLIED_WARNING not in warnings:
            warnings.append(FALLBACK_APPLIED_WARNING)
        summary.update(warnings=warnings, fallback_used=True, full_bundle_converged=False,
                       pairwise_converged=True, converged=False)
    network_result = s.STATE.get('last_network_adjustment')
    if network_result and network_result.get('id') == draft['id']:
        network_result.update(method=PAIRWISE_METHOD, fallback_used=True,
                              full_bundle_converged=False, warnings=[FALLBACK_APPLIED_WARNING])
    for ident in draft['moving']:
        rw.scan(s, ident)['position_status'] = 'Virtually positioned by pairwise fallback · review residuals; full bundle did not converge'


def verify_draft(s, draft):
    ids = set(draft['moving']) | set(draft['fixed'])
    if draft['bundle_stamp'] != visual.fingerprint(s, ids) or draft['bundle_assets'] != cloud.assets(s, draft['bundle_cloud_ids']):
        raise ValueError('Scans, masks, targets or links changed after review. Run the bundle again.')


def start(s, body):
    _options(body)
    token = uuid.uuid4().hex
    current = dict(token=token, context=id(s.STATE), state='running')
    s._bundle_registration = current
    request = copy.deepcopy(body)
    def run():
        try:
            draft, preview = build(s, request, lambda m: job_api.progress(s, m), lambda: _cancel(s))
            current.update(state='ready', draft=draft, preview=preview)
            job_api.progress(s, 'Bundle ready for review. No scan positions have been saved.')
        except (bundle.Cancelled, icp._Cancelled):
            current.update(state='cancelled', reason='Cancelled; no positions were applied.')
        except Exception as exc:
            current.update(state='failed', reason=str(exc))
            raise
    s.start_job(run, kind='job', native_progress=True)
    return dict(started=True, token=token)


def _current(s, body):
    current = getattr(s, '_bundle_registration', None)
    if not current or current['token'] != body.get('token') or current['context'] != id(s.STATE):
        raise ValueError('This bundle review is no longer current. Run it again.')
    return current


def status(s, body):
    current = _current(s, body)
    out = {k: current[k] for k in ('token', 'state', 'reason') if k in current}
    if current['state'] == 'ready':
        verify_draft(s, current['draft'])
        out.update(solution=current['draft']['solution'], preview=current['preview'])
    return out


def apply(s, body):
    current = _current(s, body)
    if current['state'] != 'ready':raise ValueError('Run and review the bundle before applying it.')
    verify_draft(s, current['draft'])
    old = copy.deepcopy(s.STATE)
    try:
        s.STATE['scan_link_draft'] = copy.deepcopy(current['draft'])
        job_api.CANCEL.clear();s.JOB_CANCEL_FILE.unlink(missing_ok=True)
        rw.accept(s, current['draft']['id'], merge=False)
    except BaseException:
        s.STATE.clear();s.STATE.update(old);raise
    current['state'] = 'applied'
    return dict(applied=True, id=current['draft']['id'], scan_count=current['draft']['solution']['scan_count'],
                fallback_used=bool(current['draft']['solution'].get('fallback_used')))


def solve_link(s, a, b, matches, tolerance=5, mode='rigid', anchor=None,
               progress=lambda message: None, references=None):
    """Compatibility entry: adding a target loop uses the same bundle engine."""
    rw.persist(s)
    ids = rw.component(s, a) | rw.component(s, b)
    anchor = anchor or a
    if a == b or anchor not in ids:raise ValueError('Choose two different setups and a fixed setup in their bundle.')
    direct = [e for e in s.STATE.get('scan_links', []) if {e['a'], e['b']} == {a, b}]
    if len(direct) > 1:raise ValueError('Remove duplicate links before adjusting.')
    resolved = rw.matched_targets(rw.scan(s, a), rw.scan(s, b), matches)
    proposed = dict(id=direct[0]['id'] if direct else uuid.uuid4().hex, a=a, b=b,
                    matches=resolved, kind='target_match', mode=mode,
                    loop_closure=not direct and b in rw.component(s, a))
    if mode == 'vertical':proposed['vertical_axis'] = direct[0].get('vertical_axis', [0., 0., 1.]) if direct else [0., 0., 1.]
    draft, _ = build(s, dict(anchor=anchor, mode=mode, tolerance_mm=tolerance), progress,
                     lambda: _cancel(s), proposed=proposed, references=references)
    draft['replaces_link'] = direct[0]['id'] if direct else None
    s.STATE['scan_link_draft'] = draft
    return draft
