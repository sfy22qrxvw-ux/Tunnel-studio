"""Experimental decoder for observed mMPK v1 / APRN v22 setup archives.

Used by the isolated native import worker. hspca_import restricts supported
layouts and preserves the original files. Independent accuracy is unverified.
"""
from pathlib import Path
import struct, zlib, json, sys, time
import numpy as np
import zstandard as zstd

WIDTHS = [12,1,1,1,4,2,1,2,2,2,1,1]
NAMES = ['POSITION','R','G','B','ColorRgbe32','INTENSITY','Label','Normal',
         'RowColumnIndex16[0]','RowColumnIndex16[1]','SurfaceSmoothnessU8','WorkingMask']

def archive_entries(path, *, max_index_bytes=128*1024*1024):
    size=Path(path).stat().st_size
    with open(path,'rb') as f:
        h=f.read(56)
        if len(h)!=56 or h[:4]!=b'mMPK' or struct.unpack_from('<I',h,4)[0]!=1:
            raise ValueError('Not the supported research archive variant')
        n=struct.unpack_from('<I',h,12)[0]
        data_offset=struct.unpack_from('<Q',h,32)[0]
        if not 1<=n<=1000000 or not 56<=data_offset<=size:raise ValueError('Invalid archive header')
        blocks=[]
        for length in struct.unpack_from('<2I',h,24):
            if not 4<=length<=min(max_index_bytes,64*1024*1024):raise ValueError('Oversized index')
            b=f.read(length)
            if len(b)!=length:raise ValueError('Truncated index')
            expected=struct.unpack_from('<I',b)[0]
            if expected>max_index_bytes:raise ValueError('Oversized index output')
            d=zlib.decompressobj();out=d.decompress(b[4:],expected+1)
            if len(out)!=expected or not d.eof or d.unused_data or d.unconsumed_tail:raise ValueError('Index compression mismatch')
            blocks.append(out)
        if len(blocks[1])!=4*n:raise ValueError('Size table mismatch')
        sizes=struct.unpack('<'+'I'*n,blocks[1])
        if sum(sizes)+data_offset!=size:raise ValueError('Archive lengths do not match file size')
        out=[];offset=data_offset
        for length in sizes:
            if length<4 or length>64*1024*1024:raise ValueError('Unsupported entry length')
            out.append((offset,length));offset+=length
        return out

def unswap(v):
    a=np.frombuffer(v,np.uint8).copy();end=len(a)//2*2
    a[:end]=a[:end].reshape(-1,2)[:,::-1].reshape(-1)
    return a

def unmorton(low, high):
    xyz=np.zeros((len(low),3),np.uint32)
    for axis in range(3):
        for bit in range(32):
            pos=3*bit+axis
            a=low if pos<64 else high
            xyz[:,axis]|=(((a>>(pos if pos<64 else pos-64))&1).astype(np.uint32)<<bit)
    return xyz.astype(np.int64)

def decode_node(b, *, sample_size=None):
    if b[:4]!=b'APRN' or len(b)<174:raise ValueError('Not an APRN node')
    total=struct.unpack_from('<Q',b,4)[0];version=struct.unpack_from('<I',b,12)[0]
    n=struct.unpack_from('<Q',b,20)[0];header=struct.unpack_from('<Q',b,112)[0]
    if total!=len(b) or version!=22 or not 1<=n<=1000000 or not 172<header<len(b):raise ValueError('Unsupported node layout')
    base=np.array(struct.unpack_from('<3q',b,36),np.int64)
    scale=np.array(struct.unpack_from('<3d',b,60));origin=np.array(struct.unpack_from('<3d',b,84))
    bb=np.array(struct.unpack_from('<6q',b,120),np.int64).reshape(2,3)
    count=struct.unpack_from('<I',b,168)[0];p=172;names=[]
    if count!=len(NAMES):raise ValueError('This HSPCA attribute layout is not supported yet. Choose an individual scan from SetupsFolder; combined PointCloud files may use a different layout.')
    for i in range(count):
        if p+8>header:raise ValueError('Bad attribute header')
        length=struct.unpack_from('<H',b,p+6)[0];p+=8
        if not 1<=length<=100 or p+length>header:raise ValueError('Bad attribute name')
        names.append(b[p:p+length].decode('ascii'));p+=length
    if names!=NAMES or p!=header:raise ValueError('Unsupported attribute names')
    codec,groups=struct.unpack_from('<BB',b,header)
    if codec!=100 or groups!=10:raise ValueError('Unsupported payload codec')
    starts=struct.unpack_from('<10Q',b,header+2)
    p=header+2+groups*8;frames=[];frame_starts=[]
    for i in range(sum(WIDTHS)):
        frame_starts.append(p)
        if p+8>len(b):raise ValueError('Missing frame')
        length=struct.unpack_from('<Q',b,p)[0];p+=8
        if not 1<=length<=len(b)-p:raise ValueError('Truncated frame')
        frame=b[p:p+length]
        if zstd.frame_content_size(frame)!=n:raise ValueError('Unexpected frame allocation size')
        # The project browser needs only positions and intensity. Full import
        # still reads and validates every channel.
        if sample_size is None or i<12 or i in (16,17):
            v=zstd.ZstdDecompressor().decompress(frame,max_output_size=n+1)
            if len(v)!=n:raise ValueError('Attribute point count mismatch')
            frames.append(unswap(v))
        else:
            frames.append(None)
        p+=length
    if p!=len(b):raise ValueError('Unconsumed payload')
    group_indices=[0,12,16,18,19,21,23,25,26,27]
    if tuple(frame_starts[i]-frame_starts[0] for i in group_indices)!=starts:raise ValueError('Channel offset mismatch')
    lo=np.zeros(n,np.uint64);hi=np.zeros(n,np.uint64)
    for j in range(8):lo|=frames[j].astype(np.uint64)<<(8*j)
    for j in range(4):hi|=frames[8+j].astype(np.uint64)<<(8*j)
    acc=np.cumsum(lo,dtype=np.uint64)
    carry=(acc<lo).astype(np.uint64)
    hi=(np.cumsum(hi,dtype=np.uint64)+np.cumsum(carry,dtype=np.uint64))&0xffffffff
    ids=None if sample_size is None else np.linspace(0,n-1,min(n,sample_size),dtype=np.int64)
    quant=unmorton(acc,hi) if ids is None else unmorton(acc[ids],hi[ids])
    relative=quant-base
    bad=int(np.count_nonzero(np.any((relative<bb[0])|(relative>bb[1]),axis=1)))
    xyz=(quant.astype(np.float64)-2147483648.0)*scale+origin
    stats={'count':n,'bad_node_bounds':bad,'lod':struct.unpack_from('<I',b,108)[0]}
    if ids is not None:
        delta=frames[16].astype(np.uint16)|(frames[17].astype(np.uint16)<<8)
        intensity=np.cumsum(delta,dtype=np.uint16)[ids]
        return xyz,{'INTENSITY':intensity},stats
    attributes={};j=12
    # Observed payload group order differs from the schema listing. RGB is
    # transformed by an as-yet unverified colour predictor; retain raw group.
    payload_names=['ColorRgbe32','INTENSITY','Label','Normal','RowColumnIndex16[0]',
                   'RowColumnIndex16[1]','SurfaceSmoothnessU8','WorkingMask','RGB_encoded']
    for name,width in zip(payload_names,[4,2,1,2,2,2,1,1,3]):
        delta=np.zeros(n,np.uint64)
        for k in range(width):delta|=frames[j+k].astype(np.uint64)<<(8*k)
        attributes[name]=(np.cumsum(delta,dtype=np.uint64)&((1<<(8*width))-1)).astype('uint'+str(8*width) if width!=3 else 'uint32')
        j+=width
    return xyz,attributes,stats
