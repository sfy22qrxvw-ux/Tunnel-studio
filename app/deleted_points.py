"""Restore a drawn area from deleted groups using bounded original-point reads.

The display sample supplies only the selection shape. Every candidate is read
from its immutable source cache in blocks, transformed to project coordinates,
and tested at full density. Group remainders are immutable binary ID lists;
one saved transaction covers all selected groups and supports workspace Undo.
Cleaning exclusions have different point identities from a cleaned copy, so
their selected points become separate restored scans, as for whole archives.
This restores observations; it does not reconstruct missing measurements.
"""
from contextlib import ExitStack
from pathlib import Path
import copy
import time
import uuid

import numpy as np

from bounded_rows import RowFile
from project_data_layout import folder as asset_folder
import jobdata
import pointio
import registration_pose
import scan_edits


def id_batches(value):
    """Read a persisted ID list without mapping or loading the complete file."""
    count=value.get('count')
    if type(count) is not int or count<0:
        raise ValueError('Invalid remaining deleted-point count.')
    with RowFile(value['ids_file'],'<i8',count) as reader:
        for start in range(0,count,pointio.CHUNK):
            yield reader.read(start,min(pointio.CHUNK,count-start))


def _partition(item,group,selection,work,progress,created):
    source=group.get('archive',{}).get('source_meta',item['meta'])
    folder=asset_folder(work,'working')
    selected=folder/(uuid.uuid4().hex+'.patch_ids')
    remaining=folder/(uuid.uuid4().hex+'.patch_ids')
    # Together these two lists contain at most the original group ID count.
    jobdata.room(work,int(group['count'])*8)
    created.extend((selected,remaining))
    chosen=left=seen=0
    iterator=scan_edits.group_ids(source,group)
    try:
        with ExitStack() as stack:
            points=stack.enter_context(RowFile(source['file'],'<f8',source['count'],3))
            current=None
            if not group.get('archive') and source.get('mask_file'):
                current=stack.enter_context(RowFile(source['mask_file'],'u1',source['count']))
            out=stack.enter_context(selected.open('wb'))
            rest=stack.enter_context(remaining.open('wb'))
            for batch in iterator:
                for start in range(0,len(batch),pointio.CHUNK):
                    ids=np.asarray(batch[start:start+pointio.CHUNK],dtype='<i8')
                    if current is not None:ids=ids[current.take(ids)!=0]
                    if len(ids):
                        q=registration_pose.points(source,points.take(ids))
                        use=jobdata.selection_mask(q,selection)
                        ids[use].tofile(out);ids[~use].tofile(rest)
                        chosen+=int(use.sum());left+=int((~use).sum())
                    seen+=len(ids)
                    progress(f"Checking deleted points · {item['name']} · {seen:,} checked · {chosen:,} selected")
    finally:
        close=getattr(iterator,'close',None)
        if close:close()
    return dict(ids_file=str(selected),count=chosen),dict(ids_file=str(remaining),count=left)


def restore_selection(s,groups,selection,progress=None):
    """Atomically restore selected original points from the displayed groups."""
    import job_api
    progress=progress or s.progress
    selection=jobdata.selection_spec(selection)
    if not isinstance(groups,list) or not groups or any(
            not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(v,str) or not v for v in pair)
            for pair in groups):
        raise ValueError('Select the deleted groups to restore from.')
    choices=[];seen=set()
    for owner,ident in groups:
        if (owner,ident) in seen:continue
        seen.add((owner,ident));item=job_api.item(s,owner)
        if item.get('kind')!='scan':raise ValueError('Deleted points must belong to a scan.')
        choices.append((item,scan_edits.record(item,ident)))
    previous=copy.deepcopy(s.STATE);old_selection=getattr(s,'SELECTION',None)
    created=[];outputs=[];temporary=[];total=changed=0
    try:
        for index,(item,group) in enumerate(choices):
            progress(f"Restore selected points · group {index+1}/{len(choices)} · {item['name']}")
            selected,remainder=_partition(item,group,selection,s.WORK,progress,created)
            temporary.append(Path(selected['ids_file']))
            if not selected['count']:
                temporary.append(Path(remainder['ids_file']));continue
            if not remainder['count']:temporary.append(Path(remainder['ids_file']))
            if group.get('archive'):
                source=group['archive']['source_meta']
                meta=jobdata.extract_ids(source,id_batches(selected),selected['count'],s.WORK,
                                         'Restored · '+item['name'],progress)
                outputs.append(meta)
                group.update(active=bool(remainder['count']),count=remainder['count'],remaining=remainder)
                # Empty remainders do not reference a zero-byte disposable file.
                if not remainder['count']:group.pop('remaining',None)
                child=job_api.append(s,'scan',meta,bundle_id=item.get('bundle_id'),commit=False)
                child['restoration_provenance']=dict(source_item_id=item['id'],group_id=group['id'],count=selected['count'])
                s.STATE.setdefault('audit',[]).append(dict(action='restore_deleted_selection',time=time.time(),
                    count=selected['count'],source_item_id=item['id'],created_item_id=child['id']))
            else:
                result=scan_edits.apply(s,item,'restore_deleted',group_id=group['id'],
                    selected_file={'file':selected['ids_file'],'count':selected['count']},
                    remainder=remainder,progress=progress,commit=False,rollback_state=False)
                created.extend(Path(p) for p in result['created_paths'])
            total+=selected['count'];changed+=1
        if not total:raise ValueError('No deleted full-density points are inside this selection.')
        progress(f'Saving restoration · {total:,} points from {changed} deleted group(s)')
        s.save()
        for path in temporary:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        return dict(restored_points=total,changed_groups=changed,restored_scans=len(outputs))
    except BaseException:
        s.STATE.clear();s.STATE.update(previous);s.SELECTION=old_selection
        for meta in outputs:jobdata.remove_assets(meta)
        for path in created:path.unlink(missing_ok=True)
        raise


def _export_choices(s,request):
    import job_api
    groups=request.get('deleted_groups');ids=request.get('ids')
    if not isinstance(groups,list) or not groups or any(not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(v,str) for v in pair) for pair in groups):
        raise ValueError('Choose valid deleted-point groups to export.')
    choices=[];seen=set()
    for owner,ident in groups:
        if (owner,ident) in seen:continue
        seen.add((owner,ident));item=job_api.item(s,owner)
        if item.get('kind')!='scan':raise ValueError('Deleted points must belong to a scan.')
        choices.append((item,scan_edits.record(item,ident)))
    if not isinstance(ids,list) or any(not isinstance(v,str) for v in ids) or set(ids)!={item['id'] for item,group in choices}:
        raise ValueError('The deleted-point export sources changed. Select the area again.')
    return choices


def export_revision(s,request):
    """Guard both current masks and archived source/ID/label assets."""
    import hashlib,json
    choices=_export_choices(s,request);rows=[]
    for item,group in choices:
        expected=(request.get('versions') or {}).get(item['id'])
        if expected and registration_pose.revision(item['meta'])!=expected:
            raise ValueError('The deleted-point source changed. Select the area again.')
        source=group.get('archive',{}).get('source_meta',item['meta']);assets=[]
        needed=[source.get('file'),source.get('attributes_file')]
        if not group.get('archive'):needed.append(source.get('mask_file'))
        if group.get('remaining'):needed.append(group['remaining'].get('ids_file'))
        elif group.get('archive'):needed.extend(group['archive'].get(k) for k in ('point_ids_file','labels_file'))
        else:needed.extend(group.get(k) for k in ('before_mask','after_mask'))
        for value in sorted(set(v for v in needed if v)):
            path=Path(value);info=path.stat();assets.append((str(path.resolve()),info.st_size,info.st_mtime_ns))
        rows.append([item['id'],group,source,assets])
    return hashlib.sha256(json.dumps([request.get('selection'),request.get('include_deleted_context') is True,rows],sort_keys=True,allow_nan=False).encode()).hexdigest()


def export_batches(s,request,progress):
    """Export the drawn deleted area without restoring or altering any group."""
    choices=_export_choices(s,request)
    selection=jobdata.selection_spec(request.get('selection'))
    context=request.get('include_deleted_context') is True
    if context:
        # One source snapshot per owner; an archive supplies pre-cleaning wall
        # context as well as the removed pocket. Keep each saved virtual pose.
        owners={}
        for item,group in choices:
            if item['id'] not in owners or group.get('archive'):owners[item['id']]=(item,group)
        choices=list(owners.values())
    for item,group in choices:
        source=group.get('archive',{}).get('source_meta',item['meta'])
        iterator=(iter(np.arange(start,min(source['count'],start+pointio.CHUNK),dtype='<i8') for start in range(0,source['count'],pointio.CHUNK)) if context else scan_edits.group_ids(source,group));checked=0
        try:
            with ExitStack() as stack:
                points=stack.enter_context(RowFile(source['file'],'<f8',source['count'],3))
                attrs=stack.enter_context(RowFile(source['attributes_file'],'<f4',source['count'],4)) if source.get('attributes_file') else None
                mask=None
                if not context and not group.get('archive') and source.get('mask_file'):
                    mask=stack.enter_context(RowFile(source['mask_file'],'u1',source['count']))
                for batch in iterator:
                    for start in range(0,len(batch),pointio.CHUNK):
                        ids=np.asarray(batch[start:start+pointio.CHUNK],dtype='<i8')
                        if mask is not None:ids=ids[mask.take(ids)!=0]
                        q=registration_pose.points(source,points.take(ids))
                        use=jobdata.selection_mask(q,selection)&np.isfinite(q).all(1)
                        checked+=len(ids);progress(f"Exporting deleted area · {item['name']} · {checked:,} original points checked")
                        if np.any(use):
                            a=attrs.take(ids[use]) if attrs is not None else np.full((int(use.sum()),4),np.nan,dtype='<f4')
                            yield q[use],a
        finally:
            close=getattr(iterator,'close',None)
            if close:close()
