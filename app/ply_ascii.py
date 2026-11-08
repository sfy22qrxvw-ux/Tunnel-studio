# Copyright (c) 2026 Damian Gulczynski.
"""Bounded ASCII PLY reader; no vertex-count-sized RAM allocation.

Only plyfile's header parser is reused (tested with bundled plyfile 1.1.3).
Scalar records use bounded NumPy text conversion. List records are validated
one at a time, with explicit record/header limits. Declared PLY scalar types
are preserved before XYZ is promoted to float64, matching the existing reader.
"""
import io
from pathlib import Path
import numpy as np

MAX_HEADER = 1024 * 1024
MAX_LINE = 1024 * 1024
BLOCK_BYTES = 8 * 1024 * 1024


def header(stream):
    from plyfile import PlyData
    parts=[]; size=0
    while True:
        line=stream.readline(MAX_HEADER-size+1)
        size+=len(line)
        if not line or size>MAX_HEADER:
            raise ValueError('Missing or oversized PLY header (limit 1 MiB).')
        parts.append(line)
        if line.strip()==b'end_header': break
    # Header only: _parse_header constructs metadata, never element data arrays.
    result=PlyData._parse_header(io.BytesIO(b''.join(parts)))
    for element in result:
        if element.count<0: raise ValueError('PLY element count must not be negative.')
    if 'vertex' not in result: raise ValueError('PLY requires a vertex element.')
    from plyfile import PlyListProperty
    props={p.name:p for p in result['vertex'].properties}
    if not {'x','y','z'}<=props.keys():
        raise ValueError('PLY requires x, y and z vertex properties.')
    if any(isinstance(props[k],PlyListProperty) for k in ('x','y','z','intensity','red','green','blue') if k in props):
        raise ValueError('PLY coordinates and intensity/RGB must be scalar properties.')
    return result


def inspect(path):
    with Path(path).open('rb') as stream:
        data=header(stream)
        return data.text, data['vertex'].count


def _line(stream,element,index):
    line=stream.readline(MAX_LINE+1)
    if not line: raise ValueError(f'Truncated PLY {element} at row {index+1}.')
    if len(line)>MAX_LINE: raise ValueError('PLY record exceeds 1 MiB; no import was committed.')
    if not line.strip(): raise ValueError(f'Empty PLY {element} row {index+1}.')
    return line


def _rows(stream,element,start,count,progress):
    for i in range(start,start+count):
        if (i-start)%4096==0:progress(f'Reading PLY {element.name}: {i:,} / {element.count:,}')
        yield _line(stream,element.name,i)


def _list_row(line,element,index,wanted):
    from plyfile import PlyListProperty
    fields=line.split(); at=0; selected={}
    try:
        for prop in element.properties:
            if isinstance(prop,PlyListProperty):
                n=int(fields[at]); at+=1
                limits=np.iinfo(prop.len_dtype)
                if not 0<=n<=limits.max or at+n>len(fields):raise ValueError('Invalid list length')
                # Validate without storing list-valued data or allocating by n.
                convert=np.dtype(prop.val_dtype).type
                for j in range(at,at+n):convert(fields[j])
                at+=n
            else:
                value=np.dtype(prop.val_dtype).type(fields[at]);at+=1
                if prop.name in wanted:selected[prop.name]=value
        if at!=len(fields):raise ValueError('Unexpected extra values')
    except (ValueError,IndexError,OverflowError) as e:
        raise ValueError(f'Invalid PLY {element.name} row {index+1}: {e}') from e
    return selected


def records(path,chunk,progress):
    from plyfile import PlyListProperty
    with Path(path).open('rb') as stream:
        data=header(stream)
        if not data.text:raise ValueError('ASCII PLY reader received binary data.')
        for element in data:
            props=element.properties
            has_lists=any(isinstance(p,PlyListProperty) for p in props)
            wanted={'x','y','z','intensity','red','green','blue'} if element.name=='vertex' else set()
            dtype=np.dtype([(p.name,p.val_dtype) for p in props if not isinstance(p,PlyListProperty)])
            step=max(1,min(chunk,BLOCK_BYTES//max(1,dtype.itemsize)))
            for start in range(0,element.count,step):
                count=min(step,element.count-start)
                if has_lists:
                    if wanted:
                        q=np.empty((count,3),dtype='<f8');a=np.full((count,4),np.nan,dtype='<f4')
                    for j,line in enumerate(_rows(stream,element,start,count,progress)):
                        values=_list_row(line,element,start+j,wanted)
                        if wanted:
                            q[j]=[values[k] for k in ('x','y','z')]
                            for col,k in enumerate(('intensity','red','green','blue')):
                                if k in values:a[j,col]=values[k]
                else:
                    try:
                        block=np.loadtxt(_rows(stream,element,start,count,progress),dtype=dtype,
                                         comments=None,ndmin=1,encoding='ascii')
                    except (ValueError,OverflowError) as e:
                        raise ValueError(f'Invalid PLY {element.name} near row {start+1}: {e}') from e
                    if len(block)!=count:raise ValueError('PLY record count mismatch.')
                    if wanted:
                        q=np.column_stack([block[k] for k in ('x','y','z')]).astype('<f8',copy=False)
                        a=np.full((count,4),np.nan,dtype='<f4')
                        for col,k in enumerate(('intensity','red','green','blue')):
                            if k in dtype.names:a[:,col]=block[k]
                    del block
                progress(f'Reading PLY {element.name}: {start+count:,} / {element.count:,}')
                if wanted:yield q,a
