"""Read the observed Leica CUBE/JPEG panorama container without image decoding.

Offsets and camera models come from the container; face order is never guessed
from filenames. Unsupported camera models fail closed for measured picking.
"""
from pathlib import Path
import hashlib,struct
import numpy as np

MAX_BYTES=128*1024*1024

def metadata(data,start,end,depth=0):
    if depth>8 or start+2>end:raise ValueError('Truncated photograph metadata.')
    count=struct.unpack_from('<H',data,start)[0];pos=start+2;out={}
    if count>128:raise ValueError('Unsupported photograph metadata.')
    for _ in range(count):
        if pos+2>end:raise ValueError('Truncated photograph property.')
        size=struct.unpack_from('<H',data,pos)[0];pos+=2
        if not 1<=size<=160 or pos+size+2>end or data[pos+size-1]!=0:raise ValueError('Invalid photograph property name.')
        name=data[pos:pos+size-1].decode('ascii');pos+=size
        if name in out:raise ValueError('Duplicate photograph property.')
        length=struct.unpack_from('<H',data,pos)[0];pos+=2
        if not length:
            value,pos=metadata(data,pos,end,depth+1)
        else:
            if pos+3+length>end:raise ValueError('Truncated photograph property value.')
            components,version,kind=data[pos:pos+3];pos+=3
            if version!=1:raise ValueError('Unsupported photograph metadata version.')
            raw=data[pos:pos+length];pos+=length
            if kind==0:value=raw.rstrip(b'\0').decode('utf8')
            elif kind==1 and length==1:value=bool(raw[0])
            elif kind in (6,7) and length==4:value=struct.unpack('<i',raw)[0]
            elif kind==8 and length==8:value=struct.unpack('<Q',raw)[0]
            elif kind in (10,11):
                width=4 if kind==10 else 8
                if length!=components*width:raise ValueError('Invalid photograph vector.')
                value=list(struct.unpack('<'+('f' if width==4 else 'd')*components,raw))
            else:value=raw
        out[name]=value
    return out,pos

def jpeg_size(blob):
    if not blob.startswith(b'\xff\xd8') or not blob.endswith(b'\xff\xd9'):raise ValueError('Incomplete panorama JPEG.')
    pos=2
    while pos+4<=len(blob):
        if blob[pos]!=255:raise ValueError('Invalid panorama JPEG header.')
        while pos<len(blob) and blob[pos]==255:pos+=1
        marker=blob[pos];pos+=1
        if marker in (0xda,0xd9):break
        size=struct.unpack_from('>H',blob,pos)[0]
        if size<2 or pos+size>len(blob):raise ValueError('Invalid panorama JPEG segment.')
        if marker in (0xc0,0xc1,0xc2):
            if size<8:raise ValueError('Truncated photograph JPEG frame.')
            bits,h,w,channels=struct.unpack_from('>BHHB',blob,pos+2)
            if bits!=8 or channels!=3:raise ValueError('Only RGB JPEG setup photographs are supported.')
            return w,h
        pos+=size
    raise ValueError('Panorama JPEG has no supported image frame.')

def decode(data):
    if data[:8]==b'TSPHOTO1':
        from e57_setup_photos import decode as decode_e57
        return decode_e57(data)
    if not 128<len(data)<=MAX_BYTES or data[:4]!=b'CUBE':raise ValueError('This file is not a supported Leica photograph panorama.')
    offsets=list(struct.unpack_from('<8Q',data,len(data)-64))
    if offsets[0]!=4 or offsets[-1]!=len(data)-64 or any(a>=b for a,b in zip(offsets,offsets[1:])):raise ValueError('Invalid photograph face offsets.')
    cube,end=metadata(data,offsets[6],offsets[7])
    if end!=offsets[7] or cube.get('LayerEnum')!=0 or cube.get('PixelType')!=3:raise ValueError('Only the standard RGB photograph layer is supported.')
    offset=np.asarray(cube.get('CubemapOffset'),float)
    if offset.shape!=(3,) or not np.isfinite(offset).all() or not np.allclose(offset,0,atol=1e-9):raise ValueError('This photograph camera offset is not supported for picking.')
    faces=[]
    for i in range(6):
        begin,end=offsets[i:i+2]
        if data[end-8:end]!=b'HXMETAV1':raise ValueError('Unsupported photograph face metadata.')
        meta_start=begin+struct.unpack_from('<Q',data,end-16)[0]
        if not begin<meta_start<end-16:raise ValueError('Invalid photograph metadata offset.')
        m,finish=metadata(data,meta_start,end-16)
        if finish!=end-16:raise ValueError('Unrecognised photograph face trailer.')
        width,height,levels=m.get('Width'),m.get('Height'),m.get('Pyramid')
        if width!=height or width not in (512,1024,2048,4096) or levels!=width.bit_length():raise ValueError('Unsupported photograph resolution pyramid.')
        table=meta_start-levels*8
        if table<=begin:raise ValueError('Invalid photograph pyramid table.')
        starts=[begin+x for x in struct.unpack_from('<'+'Q'*levels,data,table)]+[table]
        if starts[0]!=begin or any(a>=b for a,b in zip(starts,starts[1:])):raise ValueError('Invalid photograph JPEG offsets.')
        for level,(a,b) in enumerate(zip(starts,starts[1:])):
            if jpeg_size(data[a:b])!=(width>>level,height>>level):raise ValueError('Photograph pyramid dimensions do not match.')
        model=np.asarray(m.get('ScanImage',{}).get('CameraModel'),float)
        if model.shape!=(60,) or not np.isfinite(model).all():raise ValueError('The panorama has no supported camera calibration.')
        R=model[5:14].reshape(3,3);K=model[17:26].reshape(3,3)
        expected=np.array([[(width-1)/2,0,(width-1)/2],[0,(height-1)/2,(height-1)/2],[0,0,0]])
        if not (np.allclose(model[:5],[1,0,0,width,height],atol=1e-9) and np.allclose(R@R.T,np.eye(3),atol=1e-8) and np.linalg.det(R)>.999999 and np.allclose(model[14:17],0,atol=1e-9) and np.allclose(K,expected,atol=1e-8) and np.allclose(model[26:],0,atol=1e-9)):
            raise ValueError('This panorama camera model is not supported for measured picking.')
        faces.append(dict(index=i,width=width,height=height,rotation=R.tolist(),fx=float(K[0,0]),fy=float(K[1,1]),cx=float(K[0,2]),cy=float(K[1,2]),levels=[dict(offset=a,length=b-a,width=width>>j) for j,(a,b) in enumerate(zip(starts,starts[1:]))]))
    axes=np.asarray([f['rotation'][2] for f in faces])
    if not np.allclose(np.sort(axes@axes.T,axis=1),np.tile([-1,0,0,0,0,1],(6,1)),atol=1e-7):raise ValueError('Photograph faces do not cover a complete cube.')
    return dict(sha256=hashlib.sha256(data).hexdigest(),faces=faces,bytes=len(data))

def read(path):
    p=Path(path)
    if not p.is_file():raise ValueError('The setup photograph file is missing.')
    if p.stat().st_size>MAX_BYTES:raise ValueError('This panorama file exceeds the supported size.')
    return decode(p.read_bytes())

def frame_is_setup(blob):
    """Require the explicit source-project flag before using photographs for XYZ."""
    try:
        values,end=metadata(bytes(blob),0,len(blob))
        return end==len(blob) and values.get('PanosAreInSetupFrame') is True
    except (ValueError,UnicodeError,struct.error):return False
