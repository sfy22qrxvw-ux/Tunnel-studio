# Copyright (c) 2026 Damian Gulczynski.
"""Ring-face creep from explicit design and measured chainages in metres."""
import csv, math

MAX_ROWS=10000


def calculate(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ROWS:
        raise ValueError(f'Enter between 1 and {MAX_ROWS:,} ring-face observations.')
    result=[];seen=set()
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ValueError(f'Row {i}: invalid observation.')
        ring=str(row.get('ring_id') or '').strip();reference=str(row.get('reference') or 'Ring face').strip()
        if not ring or len(ring)>100 or len(reference)>200:raise ValueError(f'Row {i}: enter a ring ID and a face reference of up to 200 characters.')
        key=(ring.casefold(),reference.casefold())
        if key in seen:raise ValueError(f'Row {i}: {ring} / {reference} appears twice. Use a distinct face reference.')
        seen.add(key);values=[]
        for name in ('design','measured'):
            value=row.get(name)
            try:
                if value is None or isinstance(value,bool) or not str(value).strip():raise ValueError()
                v=float(value)
                if not math.isfinite(v) or abs(v)>1e9:raise ValueError()
            except (TypeError,ValueError):raise ValueError(f'Row {i} ({ring}): enter a finite {name} chainage in metres.') from None
            values.append(v)
        design,measured=values
        result.append(dict(ring_id=ring,design=design,measured=measured,creep_mm=1000*(measured-design),reference=reference,source=str(row.get('source') or 'Entered chainages')[:500]))
    return sorted(result,key=lambda r:(r['design'],r['ring_id'],r['reference']))


def read_chainages(path):
    """Return None for legacy XYZ observations, preserving the existing importer."""
    aliases={'design_chainage_m':'design','design_chainage':'design','measured_chainage_m':'measured','measured_chainage':'measured','ring':'ring_id','id':'ring_id','face_reference':'reference'}
    with open(path,encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        fields=[aliases.get(k.strip().lower(),k.strip().lower()) for k in reader.fieldnames or []]
        if 'measured' not in fields:return None
        if 'design' not in fields or 'ring_id' not in fields:raise ValueError('Ring CSV needs ring_id, design_chainage_m and measured_chainage_m columns.')
        if len(set(fields))!=len(fields):raise ValueError('Ring CSV has duplicate column names.')
        reader.fieldnames=fields;rows=[]
        for row in reader:
            if any(str(v or '').strip() for v in row.values()):rows.append(row)
            if len(rows)>MAX_ROWS:raise ValueError('Ring CSV exceeds 10,000 observations.')
    return calculate(rows)


def api(s,h,path,body):
    if path!='/api/ring-creep':return False
    if s.JOB['busy']:raise ValueError('Wait until the current operation finishes.')
    rows=calculate(body.get('rows')) # validate everything before changing saved results
    s.STATE.update(rings=rows,report_ready=False);s.save();h.send({'rings':rows,'saved':True});return True
