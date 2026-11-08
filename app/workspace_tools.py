# Copyright (c) 2026 Damian Gulczynski.
"""Project grouping and exact, bounded-memory highlighted-point exports."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import copy, hashlib, json, tempfile, uuid
import numpy as np
import jobdata, scan_export, registration_pose, registered_cloud


def bundles(s):
    """Bundles are connected/merged scan components, never import folders.

    Only metadata is visited. No coordinate cache is opened or modified.
    """
    items=s.STATE.get('job_items',[])
    scans={x['id']:x for x in items if x.get('kind')=='scan' and not x.get('registered_merge') and not x.get('merge_archived')}
    parent={ident:ident for ident in scans}
    def find(ident):
        while parent[ident]!=ident:
            parent[ident]=parent[parent[ident]];ident=parent[ident]
        return ident
    def join(a,b):
        if a in parent and b in parent:parent[find(b)]=find(a)
    for link in s.STATE.get('scan_links',[]):
        # Saved links retain a connected component after a network adjustment
        # or a target-edit warning. Neither status means the geometry split.
        join(link.get('a'),link.get('b'))
    for x in items:
        ids=[i for i in x.get('merge_source_ids',[]) if i in scans] if x.get('registered_merge') and not x.get('merge_stale') else []
        for other in ids[1:]:join(ids[0],other)
    groups={}
    for ident in scans:groups.setdefault(find(ident),[]).append(ident)
    previous={b['id']:b for b in s.STATE.get('bundles',[]) if b.get('id')!='default'}
    rows=[];assigned={};used=set()
    for ids in sorted((sorted(v) for v in groups.values() if len(v)>=2)):
        old=next((scans[i].get('bundle_id') for i in ids if scans[i].get('bundle_id') in previous and scans[i].get('bundle_id') not in used),None)
        ident=old or 'joined_'+hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16]
        used.add(ident);row=dict(id=ident,name=previous[old]['name'] if old else 'Bundle '+str(len(rows)+1),scan_ids=ids)
        rows.append(row);assigned.update({i:ident for i in ids})
    for x in items:
        if x['id'] in scans:x['bundle_id']=assigned.get(x['id'])
        elif x.get('registered_merge'):
            # A completed cloud owns its stored geometry. Source membership
            # describes provenance and must not make deletion own the result.
            x['bundle_id']=None
        elif x.get('bundle_id') not in used:x['bundle_id']=None
    cad=s.STATE.get('cad') or {}
    for x in cad.get('objects',[]):
        if x.get('bundle_id') and x['bundle_id'] not in used:x['bundle_id']=None;cad['revision']=uuid.uuid4().hex
    s.STATE['bundles']=rows
    return rows


def destination_bundle(s,ident=None):
    rows=bundles(s)
    if ident in (None,'','default'):return None
    if not isinstance(ident,str) or not any(b['id']==ident for b in rows):
        raise ValueError('The destination bundle is no longer available. Import into the project or choose a joined bundle.')
    return ident


def inventory(s):
    return copy.deepcopy(bundles(s))


def bundle_action(s,body):
    rows=bundles(s);action=body.get('action');ident=body.get('id')
    cad=s.STATE.get('cad') or {};items=s.STATE.get('job_items',[])+cad.get('objects',[])
    if action=='create':
        name=str(body.get('name','New bundle')).strip()[:120]
        if not name:raise ValueError('Enter a bundle name.')
        selected=set(body.get('ids',[]));joined=next((b for b in rows if len(selected&set(b['scan_ids']))>=2 and selected<=set(b['scan_ids'])),None)
        if joined is None:raise ValueError('Join at least two scans using Registration first. A bundle describes joined scans.')
        ident=joined['id'];joined['name']=name
    else:
        row=next((b for b in rows if b['id']==ident),None)
        if row is None:raise ValueError('This bundle is no longer available.')
        if action=='rename':
            name=str(body.get('name','')).strip()[:120]
            if not name:raise ValueError('Enter a bundle name.')
            row['name']=name
        elif action not in ('assign','visibility','isolate'):raise ValueError('Unknown bundle command.')
    if action in ('assign','create'):
        ids=body.get('ids',[])
        if not isinstance(ids,list) or not set(ids)<={x['id'] for x in items}:raise ValueError('An item in the selection is no longer available.')
        # Include archived original setups when moving their registered result.
        selected=set(ids)
        for _ in range(len(s.STATE.get('job_items',[]))):
            extra={x['id'] for x in s.STATE.get('job_items',[]) if x.get('merge_parent') in selected}
            if extra<=selected:break
            selected.update(extra)
        for x in items:
            if x['id'] in selected:
                if x.get('kind')=='scan' and x.get('bundle_id')!=ident:raise ValueError('Register this scan with the bundle first; moving a row does not join scan geometry.')
                x['bundle_id']=ident
    elif action in ('visibility','isolate'):
        members=[x for x in items if x.get('bundle_id')==ident and not x.get('merge_archived')]
        if action=='isolate':
            registered_cloud.show_current(items,{x['id'] for x in members})
        else:
            for x in members:
                if x.get('kind')=='scan':registered_cloud.set_visibility(items,x,bool(body.get('visible')))
                else:x['visible']=bool(body.get('visible'))
    if cad and action in ('assign','create','visibility','isolate'):cad['revision']=uuid.uuid4().hex
    s.STATE['export_ready']=None;s.save();return {'id':ident,'saved':True}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def export_meshes(entries,dest,progress):
    total=0;offset=0
    try:
        with Path(dest).open('w',encoding='utf8',newline='\n') as out:
            out.write('# Tunnel Studio selected full meshes; project coordinates in metres\n')
            for entry in entries:
                m=entry['meta'];nv=int(m['vertices']);nf=int(m['triangles'])
                vertices=np.memmap(m['vertices_file'],'<f8','r',shape=(nv,3));faces=np.memmap(m['faces_file'],'<u4','r',shape=(nf,3))
                try:
                    out.write('o '+str(entry['name']).replace('\n',' ').replace('\r',' ')+'\n')
                    for start in range(0,nv,scan_export.CHUNK):
                        # Seventeen significant digits round-trip stored float64
                        # survey coordinates, including large world offsets.
                        np.savetxt(out,vertices[start:start+scan_export.CHUNK],fmt='v %.17g %.17g %.17g');progress(f'Exporting {entry["name"]}: {min(nv,start+scan_export.CHUNK):,} vertices')
                    flags=np.fromfile(m['face_flags_file'],'u1') if m.get('face_flags_file') else None
                    if flags is not None and len(flags)!=nf:raise ValueError('Mesh reconstruction markers are incomplete.')
                    for start in range(0,nf,scan_export.CHUNK):
                        f=np.asarray(faces[start:start+scan_export.CHUNK],dtype=np.int64)
                        if f.size and f.max()>=nv:raise ValueError('Mesh contains an invalid triangle index.')
                        if flags is None:np.savetxt(out,f+offset+1,fmt='f %d %d %d')
                        else:
                            labels=flags[start:start+len(f)];edges=np.r_[0,np.flatnonzero(np.diff(labels))+1,len(labels)]
                            for a,b in zip(edges[:-1],edges[1:]):
                                out.write('g '+('reconstructed_surface' if labels[a] else 'scan_supported_surface')+'\n')
                                np.savetxt(out,f[a:b]+offset+1,fmt='f %d %d %d')
                        progress(f'Exporting {entry["name"]}: {min(nf,start+scan_export.CHUNK):,} triangles')
                    offset+=nv;total+=nf
                finally:vertices._mmap.close();faces._mmap.close()
        return {'count':total,'warnings':['Selected full meshes in project coordinates; no preview simplification.']}
    except BaseException:Path(dest).unlink(missing_ok=True);raise


def sources(s,request):
    by_id={x['id']:x for x in s.STATE.get('job_items',[])}
    ids=request.get('ids')
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):raise ValueError('Highlight points or select scans first.')
    if any(i not in by_id or by_id[i]['kind']!='scan' for i in ids):raise ValueError('A highlighted source scan is no longer available.')
    selected=[registered_cloud.require_current(by_id[i]) for i in ids]
    covered=set()
    for x in selected:
        source_ids=set(x.get('merge_source_ids',[])) if x.get('registered_merge') else {x['id']}
        if covered&source_ids:raise ValueError('Select the registered cloud or its original setups, not both copies of the same points.')
        covered.update(source_ids)
    for ident,version in request.get('versions',{}).items():
        if ident not in by_id or registration_pose.revision(by_id[ident]['meta'])!=version:raise ValueError('A highlighted source changed. Select the points again.')
    return selected


def revision(s,request):
    if request.get('deleted_groups') is not None:return __import__('deleted_points').export_revision(s,request)
    entries=sources(s,request)
    value=[[x['id'],scan_export.signature(x),x.get('hidden_boxes'),x.get('isolate_box'),x.get('limit_box'),x.get('limit_hide_outside')] for x in entries]
    if request.get('review'):
        r=s.STATE.get('surface_result') or {}
        if not r or r.get('id')!=request['review'].get('id') or r.get('revision')!=request['review'].get('revision'):raise ValueError('The cleaning review changed. Highlight the result again.')
        if len(entries)!=1 or r.get('source_item_id')!=entries[0]['id']:raise ValueError('The highlighted review belongs to a different scan.')
        if scan_export.signature({'meta':r['source_meta']})!=scan_export.signature({'meta':entries[0]['meta']}):raise ValueError('The source scan changed. Run the cleaning review again.')
        value.append([r.get('id'),r.get('revision'),r.get('labels_file'),r.get('point_ids_file')])
    return digest(value)


def batches(s,request,progress):
    """Yield exact selected originals without mapping an entire source/result.

    Every owned XYZ, attribute, mask and review-label reader is closed when the
    generator finishes, is cancelled or is explicitly closed by its caller.
    """
    if request.get('deleted_groups') is not None:
        yield from __import__('deleted_points').export_batches(s,request,progress)
        return
    from bounded_rows import RowFile
    block=min(scan_export.CHUNK,100000)
    entries=sources(s,request);review=request.get('review');spec=request.get('selection');whole=request.get('whole') is True
    if not spec and not review and not whole:raise ValueError('No highlighted points. Draw a selection first.')
    if spec:spec=jobdata.selection_spec(spec)
    with ExitStack() as review_files:
        if review:
            revision(s,request)
            r=s.STATE['surface_result'];length=int(r['statistics']['input_points'])
            ids=review_files.enter_context(RowFile(r['point_ids_file'],'<i8',length,block=block))
            labels=review_files.enter_context(RowFile(r['labels_file'],'<i4',length,block=block))
            chosen=review.get('clusters',[]);valid={-1,*[c['id'] for c in r['clusters']]}
            if not isinstance(chosen,list) or not set(chosen)<=valid or (not chosen and not spec):raise ValueError('Select review clusters or points first.')
        for entry in entries:
            meta=entry['meta'];n=int(meta['count'])
            with ExitStack() as files:
                p=files.enter_context(RowFile(meta['file'],'<f8',n,3,block))
                a=files.enter_context(RowFile(meta['attributes_file'],'<f4',n,4,block)) if meta.get('attributes_file') else None
                mask=files.enter_context(RowFile(meta['mask_file'],'u1',n,block=block)) if meta.get('mask_file') else None
                total=length if review else n
                for start in range(0,total,block):
                    stop=min(total,start+block)
                    ix=ids[start:stop] if review else np.arange(start,stop,dtype=np.int64)
                    q=registration_pose.points(meta,p.take(ix) if review else p[start:stop]);use=np.isfinite(q).all(1)
                    if mask is not None:use &= (mask.take(ix) if review else mask[start:stop])==0
                    current_labels=labels[start:stop] if review else None
                    if review and chosen:use &= np.isin(current_labels,chosen)
                    if review and 'visible_clusters' in review:use &= np.isin(current_labels,review['visible_clusters'])
                    if spec:use &= jobdata.selection_mask(q,spec)
                    # Whole-object export includes retained points hidden only
                    # for viewing; highlights respect those visibility filters.
                    if not whole:
                        for hidden in entry.get('hidden_boxes',[]):use &= ~jobdata.selection_mask(q,hidden)
                        if entry.get('isolate_box'):use &= jobdata.selection_mask(q,entry['isolate_box'])
                        if entry.get('limit_box') and entry.get('limit_hide_outside',True):use &= jobdata.selection_mask(q,entry['limit_box'])
                    progress(f'Checking {entry["name"]}: {stop:,} / {total:,} original points')
                    if np.any(use):
                        attrs=(a.take(ix) if review else a[start:stop])[use] if a is not None else np.full((int(use.sum()),4),np.nan,dtype='<f4')
                        yield q[use],attrs


@contextmanager
def highlighted(s,request,progress):
    before=revision(s,request)
    with tempfile.TemporaryDirectory(prefix='highlighted-',dir=s.WORK) as folder:
        xyz=Path(folder)/'points.xyz64';attr=Path(folder)/'attributes.attr32'
        count=0;lo=np.full(3,np.inf);hi=-lo;counts=np.zeros(4,dtype=np.int64);amin=np.full(4,np.inf);amax=-amin
        with xyz.open('wb') as out,attr.open('wb') as ao:
            for points,attrs in batches(s,request,progress):
                points.astype('<f8').tofile(out);attrs.astype('<f4').tofile(ao);count+=len(points)
                lo=np.minimum(lo,points.min(0));hi=np.maximum(hi,points.max(0))
                for j in range(4):
                    values=attrs[np.isfinite(attrs[:,j]),j];counts[j]+=len(values)
                    if len(values):amin[j]=min(amin[j],float(values.min()));amax[j]=max(amax[j],float(values.max()))
        if not count:raise ValueError('The selected deleted area contains no matching points. No file was exported.' if request.get('deleted_groups') else 'The highlight contains no retained points. No file was exported.')
        if revision(s,request)!=before:raise ValueError('The source changed during export. Select the points again.')
        result=dict(name='Highlighted points',file=str(xyz),count=count,minimum=lo.tolist(),maximum=hi.tolist())
        if request.get('deleted_groups') is not None:
            metas=[g.get('archive',{}).get('source_meta',item['meta']) for item,g in __import__('deleted_points')._export_choices(s,request)]
        else:metas=[x['meta'] for x in sources(s,request)]
        if any(m.get('experimental_reconstruction') for m in metas):result['experimental_reconstruction']=True
        if counts.any():result.update(attributes_file=str(attr),attribute_counts=counts.tolist(),attribute_min=[float(v) if np.isfinite(v) else None for v in amin],attribute_max=[float(v) if np.isfinite(v) else None for v in amax])
        yield result,before


def post(s,h,path,body):
    if path not in ('/api/bundle','/api/highlight-statistics'):return False
    if s.JOB['busy']:raise ValueError('Wait until the current operation finishes.')
    if path=='/api/bundle':
        old=copy.deepcopy(s.STATE)
        try:result=bundle_action(s,body)
        except BaseException:s.STATE.clear();s.STATE.update(old);raise
        h.send(result)
    else:
        revision(s,body);request=copy.deepcopy(body);s.HIGHLIGHT_STATS=None
        def calculate():
            before=revision(s,request);count=0;lo=np.full(3,np.inf);hi=-lo
            for p,_ in batches(s,request,s.progress):count+=len(p);lo=np.minimum(lo,p.min(0));hi=np.maximum(hi,p.max(0))
            if revision(s,request)!=before:raise ValueError('The highlighted sources changed while counting.')
            s.HIGHLIGHT_STATS=dict(count=count,minimum=lo.tolist() if count else None,maximum=hi.tolist() if count else None)
        s.start_job(calculate);h.send({'started':True})
    return True
