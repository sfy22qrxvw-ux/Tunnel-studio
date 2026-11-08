# Copyright (c) 2026 Damian Gulczynski.
"""Recognise REGISTER 360 project folders and read their saved display metadata.

The observed ProjectInfoDb/Hds_OdbProjectMetadata schema identifies a project;
the observed deliverable/version databases supply site-map labels and setup
locations. Explicit project titles, when present, are read separately. A site-
map label is reported as such and is never presented as a decoded UUID. This
inspector does not import clouds, alter poses, rename folders or write databases.
Layout guides are not point-cloud previews or survey measurements.
"""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import configparser
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET

MAX_ENTRIES=20000
MAX_DEPTH=4
MAX_DATABASES=32
MAX_METADATA_BYTES=4*1024*1024
_DB=re.compile(r'(?:project_deliv_.*|project_version_.*|ProjectInfoDb(?:\(\d+\))?)\.db\Z',re.I)
_SKIP={'setupsfolder','projectdata','assets','pointcloud','pointclouds','clouds','cache','caches',
       'runtime','node_modules','.git','startup_recovery','.project_delete_queue','diagnostics'}
_NAMES=('projectname','mprojectname','projecttitle','mprojecttitle','displayname','mdisplayname','title','mname','name')
_META_NAMES={'project.json','projectinfo.json','projectmetadata.json','project.xml','projectinfo.xml',
             'projectmetadata.xml','project.ini','projectinfo.ini'}


def _normal(value):return re.sub(r'[^a-z0-9]','',str(value).lower())
def _quote(value):return '"'+str(value).replace('"','""')+'"'


def _guid(value):
    if not isinstance(value,str):return None
    try:return str(uuid.UUID(value.strip().strip('{}')))
    except ValueError:return None


def _name(value):
    if isinstance(value,bytes):
        if len(value)>1024:return None
        for encoding in ('utf-8-sig','utf-16-le','utf-16-be'):
            try:
                result=_name(value.decode(encoding))
                if result:return result
            except UnicodeError:continue
        return None
    if not isinstance(value,str):return None
    value=value.strip().strip('\x00').strip()
    if not value or len(value)>240 or any(ord(c)<32 for c in value) or _guid(value):return None
    return value


@contextmanager
def _connection(path):
    """Immutable read mode avoids creating SQLite journals, SHM or lock files.

    Live WAL content is deliberately refused rather than silently reading an
    obsolete main database. The user can close Leica and refresh the catalogue.
    """
    path=Path(path)
    from project_data_layout import is_link
    if is_link(path) or not path.is_file():raise ValueError('Linked or unavailable Leica metadata.')
    for suffix in ('-wal','-journal'):
        journal=Path(str(path)+suffix)
        if journal.exists() and journal.stat().st_size:
            raise ValueError('Leica metadata has pending journal changes. Close REGISTER 360 and refresh.')
    with path.open('rb') as stream:
        if stream.read(16)!=b'SQLite format 3\x00':raise ValueError('Unsupported Leica database encoding.')
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.2)
    try:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF')
        c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_METADATA_BYTES)
        c.setlimit(sqlite3.SQLITE_LIMIT_COLUMN,256)
        deadline=time.monotonic()+1.
        c.set_progress_handler(lambda:int(time.monotonic()>deadline),2000)
        yield c
    finally:c.close()


def _tables(c):return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 256")}
def _columns(c,table):return {r[1] for r in c.execute('PRAGMA table_info('+_quote(table)+')')}


def _project_fields(c,project_id=None,allow_properties=False):
    """Only project records with explicit name fields, never setup/asset names."""
    result=[]
    for table in sorted(_tables(c)):
        if 'project' not in table.lower() or table=='Hds_OdbProjectProperty':continue
        cols=_columns(c,table);normal={_normal(v):v for v in cols}
        id_col=next((normal[k] for k in ('mprojectid','projectid','projectguid','mprojectguid','mguid','guid','uuid','id') if k in normal),None)
        name_col=next((normal[k] for k in _NAMES if k in normal),None)
        if not name_col:continue
        if (_normal(name_col) in ('name','mname','title')
                and _normal(table) not in ('hdsodbproject','hdsodbprojects','hdsodbprojectmetadata','project','projects','projectinfo','projectmetadata','projectlist','projectcatalog','projectcatalogue')):continue
        fields=[id_col,name_col] if id_col else [name_col]
        for row in c.execute('SELECT '+','.join(map(_quote,fields))+' FROM '+_quote(table)+' LIMIT 200'):
            record_id=_guid(row[id_col]) if id_col else None
            if project_id is not None and record_id!=project_id:continue
            value=_name(row[name_col])
            if value:result.append((record_id,value,table+'.'+name_col))
    if allow_properties and 'Hds_OdbProjectProperty' in _tables(c):
        cols=_columns(c,'Hds_OdbProjectProperty')
        if {'mName','mValue'}<=cols:
            fields='mName,mValue,mType' if 'mType' in cols else 'mName,mValue'
            for row in c.execute('SELECT '+fields+' FROM Hds_OdbProjectProperty LIMIT 200'):
                if _normal(row['mName']) not in ('projectname','projecttitle','displayname'):continue
                if 'mType' in cols and row['mType']!=1:continue
                value=_name(row['mValue'])
                if value:result.append((None,value,'Hds_OdbProjectProperty.'+row['mName']))
    return result


def _text_metadata(path,project_id):
    """Explicit project-name fields in small JSON/XML/INI metadata only."""
    if path.stat().st_size>MAX_METADATA_BYTES:raise ValueError('Project text metadata exceeds 4 MiB.')
    from project_data_layout import is_link
    if is_link(path):raise ValueError('Linked project metadata is not read.')
    raw=path.read_bytes()
    if len(raw)>MAX_METADATA_BYTES:raise ValueError('Project text metadata exceeds 4 MiB.')
    text=raw.decode('utf-8-sig') if not raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-16')
    records=[]
    if path.suffix.lower()=='.json':
        value=json.loads(text)
        if isinstance(value,dict) and ('job_items' in value or str(value.get('format','')).startswith('TunnelStudio')):return []
        if isinstance(value,dict):
            records=[value]+[v for k,v in value.items() if _normal(k) in ('project','projectinfo','projectmetadata') and isinstance(v,dict)]
    elif path.suffix.lower()=='.xml':
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('XML declarations are not allowed in project metadata.')
        root=ET.fromstring(text)
        for node in [root,*list(root)]:
            if node is not root and _normal(node.tag.split('}')[-1]) not in ('project','projectinfo','projectmetadata'):continue
            records.append({**node.attrib,**{n.tag.split('}')[-1]:n.text for n in node}})
    else:
        parser=configparser.ConfigParser(interpolation=None);parser.read_string(text)
        records=[dict(parser[s]) for s in parser.sections() if _normal(s) in ('project','projectinfo','projectmetadata')]
    result=[]
    for record in records:
        record={_normal(k):v for k,v in record.items()}
        id_fields=[k for k in ('projectid','mprojectid','projectguid','mguid','guid','uuid') if k in record]
        ident=next((_guid(record[k]) for k in id_fields),None)
        if id_fields and ident is None:raise ValueError('Invalid identity in project text metadata.')
        if ident is not None and project_id is not None and ident!=project_id:continue
        # A generic root "name" can be a Tunnel Studio or unrelated document.
        value=next((_name(record[k]) for k in ('projectname','mprojectname','projecttitle','displayname') if k in record and _name(record[k])),None)
        if value:result.append((value,path.name+' · explicit project title'))
    return result


def stamp(paths):
    rows=[]
    for value in sorted(map(str,paths)):
        path=Path(value);s=path.stat()
        rows.append((value,s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _database(path, *, scan_sources=False):
    """Read setup layout and, on request, its scan references and poses."""
    out=dict(path=str(path),kind='deliverable' if path.name.lower().startswith('project_deliv_') else 'version',
             site_maps=[],setups=0,layout=[],links=[],source_points=0)
    with _connection(path) as c:
        tables=_tables(c)
        if not {'Hds_OdbSetup','Hds_OdbSetupRef'}<=tables:raise ValueError('Unsupported Leica setup metadata schema.')
        if 'Hds_OdbSitemap' in tables:
            cols=_columns(c,'Hds_OdbSitemap');where=' WHERE mIsDeleted=0' if 'mIsDeleted' in cols else ''
            out['site_maps']=[dict(r) for r in c.execute('SELECT mId,mName FROM Hds_OdbSitemap'+where+' ORDER BY mId LIMIT 200')]
        cols=_columns(c,'Hds_OdbSetup');filters=[k+'=0' for k in ('mIsDeleted','mIsPhantom') if k in cols]
        where=' WHERE '+' AND '.join(filters) if filters else ''
        setup_fields=['mId','mName']
        if scan_sources:
            setup_fields.extend(k for k in ('mCloudURI','mIsUnstructured') if k in cols)
        setups=[dict(r) for r in c.execute('SELECT '+','.join(setup_fields)+' FROM Hds_OdbSetup'+where+' ORDER BY mId LIMIT 2001')]
        out['setups']=len(setups)
        ids={r['mId'] for r in setups}
        ref_cols=_columns(c,'Hds_OdbSetupRef')
        positions=['mPosition'+axis for axis in 'XYZ']
        rotations=['mRotation'+axis for axis in 'XYZW']
        fields=['mSetupId',*positions]
        read_poses=scan_sources and set(rotations)<=ref_cols
        if read_poses:fields.extend(rotations)
        poses={}
        if set(fields)<=ref_cols:
            if 'mSitemapId' in ref_cols and out['site_maps']:
                query='SELECT '+','.join(fields)+' FROM Hds_OdbSetupRef WHERE mSitemapId=? LIMIT 2000'
                refs=c.execute(query,(out['site_maps'][0]['mId'],))
            else:refs=c.execute('SELECT '+','.join(fields)+' FROM Hds_OdbSetupRef LIMIT 2000')
            for r in refs:
                ident=r['mSetupId']
                if ident not in ids:continue
                p=[r[k] for k in positions]
                if all(isinstance(v,(int,float)) and math.isfinite(v) for v in p):out['layout'].append((ident,p))
                if read_poses:poses.setdefault(ident,[]).append((p,[r[k] for k in rotations]))
        if scan_sources:
            out['scan_sources']=[(r['mCloudURI'],*poses[r['mId']][0]) for r in setups
                                 if isinstance(r.get('mCloudURI'),str) and r['mCloudURI']
                                 and not r.get('mIsUnstructured') and len(poses.get(r['mId'],()))==1]
        if 'Hds_OdbLink' in tables:
            cols=_columns(c,'Hds_OdbLink')
            if {'mpLeftSetup','mpRightSetup'}<=cols:
                where=' WHERE mIsDeleted=0' if 'mIsDeleted' in cols else ''
                out['links']=[tuple(r) for r in c.execute('SELECT mpLeftSetup,mpRightSetup FROM Hds_OdbLink'+where+' LIMIT 2000')]
        if 'Hds_OdbPointCloud' in tables and 'mPointCount' in _columns(c,'Hds_OdbPointCloud'):
            out['source_points']=sum(r[0] for r in c.execute('SELECT mPointCount FROM Hds_OdbPointCloud LIMIT 200') if type(r[0]) is int and r[0]>=0)
        out['project_names']=_project_fields(c,allow_properties=True)
    out['modified_at']=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
    return out


def describe(folder,catalogue=None):
    root=Path(folder).resolve();catalogue=Path(catalogue).resolve() if catalogue is not None else root.parent
    from project_data_layout import is_link
    entries=sorted((p for p in root.iterdir() if p.is_file() and not is_link(p)),key=lambda p:p.name.lower())
    dbs=[p for p in entries if _DB.fullmatch(p.name)][:MAX_DATABASES]
    if not dbs:return None
    sources=[root];warnings=[];project_id=_guid(root.name);identity_ids=set();names=[];databases=[]
    # Establish the identity before considering names in snapshot databases.
    # Their filename ordering must not allow an unrelated snapshot name to win.
    for path in sorted(dbs,key=lambda p:not p.name.lower().startswith('projectinfodb')):
        sources.append(path)
        sources.extend(Path(str(path)+suffix) for suffix in ('-wal','-journal') if Path(str(path)+suffix).exists())
        try:
            if path.name.lower().startswith('projectinfodb'):
                with _connection(path) as c:
                    tables=_tables(c)
                    if 'Hds_OdbProjectMetadata' in tables and 'mProjectId' in _columns(c,'Hds_OdbProjectMetadata'):
                        ids={_guid(r[0]) for r in c.execute('SELECT mProjectId FROM Hds_OdbProjectMetadata LIMIT 8')};ids.discard(None)
                        identity_ids.update(ids)
                        if len(ids)==1:project_id=next(iter(ids))
                    names.extend((value,field) for _,value,field in _project_fields(c,project_id))
            else:
                record=_database(path);databases.append(record)
                names.extend((value,field) for ident,value,field in record['project_names'] if project_id is None or ident in (None,project_id))
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            warnings.append(path.name+': '+str(exc))
    identity_conflict=len(identity_ids)>1
    if identity_conflict:
        project_id=None;names=[];warnings.append('Several distinct Leica project identities share this folder. Review them separately.')
    else:project_id=next(iter(identity_ids)) if identity_ids else _guid(root.name)
    for path in entries:
        if (path.name.lower() in _META_NAMES or _normal(path.stem) in ('project','projectinfo','projectmetadata') and path.suffix.lower() in ('.json','.xml','.ini')):
            sources.append(path)
            try:names.extend(_text_metadata(path,project_id))
            except (OSError,ValueError,TypeError,ET.ParseError,RecursionError,configparser.Error) as exc:warnings.append(path.name+': '+str(exc))
    # Some Leica installations keep the human project title in a separate
    # catalogue. A row must match this project's ID; another title is not used.
    if project_id:
        for parent in dict.fromkeys((root.parent,catalogue)):
            for path in sorted(parent.glob('*.db'))[:32]:
                if _DB.fullmatch(path.name) or is_link(path):continue
                sources.append(path)
                try:
                    with _connection(path) as c:names.extend((value,str(path.name)+' · '+field) for _,value,field in _project_fields(c,project_id))
                except (OSError,ValueError,TypeError,sqlite3.Error):continue
    distinct={v for v,_ in names};site_maps=sorted({_name(r['mName']) for d in databases for r in d['site_maps'] if _name(r['mName'])})
    if identity_conflict:
        project_title='REGISTER 360 projects';title_source='ambiguous';name_note=warnings[-1]
    elif len(distinct)==1:
        project_title=next(iter(distinct));title_source='project_metadata';name_note='Saved project title · '+names[0][1]
    elif len(distinct)>1:
        project_title='REGISTER 360 project';title_source='ambiguous';name_note='Project title metadata disagrees. Review the metadata before choosing a name.';warnings.append(name_note)
    elif len(site_maps)==1:
        project_title=site_maps[0];title_source='site_map';name_note='Saved Leica site-map label. The original project title is not present in the available project metadata.'
    else:
        project_title='REGISTER 360 project';title_source='unavailable';name_note='Project recognised. A unique saved project title is unavailable in this folder metadata.'
    # Prefer deliverables for import, but never choose a timestamp/version as
    # the current result. Multiple candidates remain an explicit user choice.
    choices=[d for d in databases if d['kind']=='deliverable'] or databases
    layout=choices[0] if len(choices)==1 else None
    return dict(kind='leica_register360',path=str(root),project_title=project_title,project_guid=project_id,
                title_source=title_source,name_note=name_note,site_maps=site_maps,
                setups=max((d['setups'] for d in choices),default=0),
                databases=[{k:d[k] for k in ('path','kind','setups','modified_at')} for d in choices],
                saved_at=max((d['modified_at'] for d in choices),default=None),
                source_files=list(dict.fromkeys(map(str,sources))),
                revision=stamp(sources),layout=layout,warnings=warnings,
                import_available=bool(choices),metadata_database_count=len(dbs))


def discover(folder,max_projects=200):
    """Bounded metadata discovery through parent/UUID folders; no cloud reads."""
    root=Path(folder).resolve();pending=deque([(root,0)]);found=[];visited=0;limited=False
    from project_data_layout import is_link
    while pending:
        parent,depth=pending.popleft()
        try:
            with __import__('os').scandir(parent) as iterator:
                entries=[]
                for entry in iterator:
                    visited+=1
                    if visited>MAX_ENTRIES:return found,True
                    entries.append(entry)
            if any(e.is_file(follow_symlinks=False) and _DB.fullmatch(e.name) for e in entries):
                info=describe(parent,root)
                if info:found.append(info)
                if len(found)>=max_projects:return found,True
                # One project root can contain several historical databases;
                # it is one card, not a card for every saved database.
                continue
            for entry in entries:
                if entry.name.lower() in _SKIP or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):continue
                if is_link(entry.path):continue
                if depth<MAX_DEPTH:pending.append((Path(entry.path),depth+1))
                else:limited=True
        except (OSError,ValueError,sqlite3.Error):limited=True
    return found,limited


def preview(info,database=None,*,sample_scans=True):
    """Show measured scans when readable, with a separately labelled layout fallback."""
    if database is not None and database not in {d['path'] for d in info['databases']}:
        raise ValueError('Choose a saved database listed for this Leica project.')
    if database:
        record=_database(Path(database),scan_sources=sample_scans)
    else:
        record=info.get('layout')
        if record and sample_scans:
            record=_database(Path(record['path']),scan_sources=True)
    positions={ident:p for ident,p in record['layout']} if record else {}
    origin=[sum(p[i] for p in positions.values())/len(positions) if positions else 0. for i in range(3)]
    points=[[p[i]-origin[i] for i in range(3)]+[.98,.73,.3] for p in positions.values()]
    lines=[]
    if record:
        for a,b in record['links']:
            if a in positions and b in positions:
                for ident in (a,b):lines.append([positions[ident][i]-origin[i] for i in range(3)]+[.35,.8,.85])
    message='Saved scanner positions and links.'
    if record and sample_scans:
        from leica_scan_preview import measured_preview
        measured,message=measured_preview(record,Path(info['path']),origin)
        if measured:
            points,lines=measured['points'],[]
    else:
        measured=None
    if len(info['databases'])>1 and not database:message='Choose a saved database to preview its scans.'
    note=info['name_note']
    if record and record['site_maps']:note+=' Site map: '+str(record['site_maps'][0]['mName'])+'.'
    return dict(format='TunnelStudioDisplayPreview',version=1,origin=origin,points=points,lines=lines,
                quality='sampled' if measured else 'setup_layout',source_points=0,
                item_count=record['setups'] if record else info['setups'],setup_count=len(positions),
                sampled_setups=measured['setups'] if measured else 0,scan_message=message,
                note=note,warnings=info['warnings'])
