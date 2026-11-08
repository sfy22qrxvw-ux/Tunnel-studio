# Copyright (c) 2026 Damian Gulczynski.
"""Cache six E57 pinhole photographs using their declared camera models."""
from pathlib import Path
import hashlib, io, json, struct
import numpy as np

MAGIC=b'TSPHOTO1'
MAX_BYTES=128*1024*1024


def decode(data):
    from setup_photo_codec import jpeg_size
    if not 12<len(data)<=MAX_BYTES or data[:8]!=MAGIC:raise ValueError('Invalid setup photograph cache.')
    length=struct.unpack_from('<I',data,8)[0]
    if not 0<length<=65536 or 12+length>=len(data):raise ValueError('Invalid photograph manifest.')
    manifest=json.loads(data[12:12+length]);base=12+length
    faces=manifest.get('faces',[])
    if manifest.get('version')!=1 or len(faces)!=6:raise ValueError('Incomplete E57 photograph cube.')
    previous=0
    for i,f in enumerate(faces):
        if f['index']!=i or f['width']!=f['height'] or f['width'] not in (512,1024,2048,4096):raise ValueError('Unsupported photograph size.')
        R=np.asarray(f['rotation'],float)
        if R.shape!=(3,3) or not np.isfinite(R).all() or not np.allclose(R@R.T,np.eye(3),atol=1e-7) or np.linalg.det(R)<.999999:raise ValueError('Invalid photograph rotation.')
        if not np.isfinite([f[k] for k in ('fx','fy','cx','cy')]).all() or min(f['fx'],f['fy'])<=0:raise ValueError('Invalid photograph intrinsics.')
        levels=f['levels']
        if not 1<=len(levels)<=3 or levels[0]['width']!=f['width']:raise ValueError('Invalid photograph pyramid.')
        prior_width=f['width']*2
        for level in levels:
            off,size,w=level['offset'],level['length'],level['width']
            if any(type(x) is not int for x in (off,size,w)) or off!=previous or size<=0 or base+off+size>len(data) or not 0<w<prior_width:raise ValueError('Invalid photograph offsets.')
            if jpeg_size(data[base+off:base+off+size])!=(w,w):raise ValueError('Photograph dimensions differ from the manifest.')
            level['offset']=base+off;previous=off+size;prior_width=w
    if base+previous!=len(data):raise ValueError('Unexpected data after the photograph cache.')
    axes=np.array([f['rotation'][2] for f in faces])
    if not np.allclose(np.sort(axes@axes.T,axis=1),np.tile([-1,0,0,0,0,1],(6,1)),atol=1e-5):raise ValueError('Photographs do not form a complete cube.')
    return dict(sha256=hashlib.sha256(data).hexdigest(),faces=faces,bytes=len(data))


def extract(source, guid, scan_rotation, scan_translation, destination):
    try:
        from PIL import Image
    except ImportError:
        Image=None  # Original JPEG faces still work without optional resizing.
    from setup_photo_codec import jpeg_size
    from leica_e57_worker import value,pose
    if not source.root.isDefined('images2D'):return None,scan_translation
    nodes=[n for n in source.root['images2D'] if value(n,'associatedData3DGuid')==guid]
    if not nodes:return None,scan_translation
    if len(nodes)!=6:raise ValueError('Expected six photographs associated with this setup.')
    faces=[];chunks=[];offset=0;origin=None
    for i,node in enumerate(nodes):
        if not node.isDefined('pinholeRepresentation'):raise ValueError('Only pinhole cube photographs are supported by this import route.')
        Ri,ti,known=pose(node)
        if not known:raise ValueError('An E57 photograph has no declared pose.')
        if origin is None:origin=ti
        if np.linalg.norm(ti-origin)>1e-6:raise ValueError('The six photographs do not share a camera centre.')
        pin=node['pinholeRepresentation']
        if not pin.isDefined('jpegImage'):raise ValueError('Only JPEG E57 photographs are supported.')
        width,height=int(value(pin,'imageWidth')),int(value(pin,'imageHeight'))
        if width!=height or width not in (512,1024,2048,4096):raise ValueError('Unsupported photograph resolution.')
        f,px,py=[float(value(pin,k)) for k in ('focalLength','pixelWidth','pixelHeight')]
        cx,cy=[float(value(pin,k)) for k in ('principalPointX','principalPointY')]
        if not np.isfinite([f,px,py,cx,cy]).all() or min(f,px,py)<=0:raise ValueError('Invalid E57 camera calibration.')
        fx,fy=f/px,f/py
        if not np.allclose([fx,fy,cx,cy],[(width-1)/2]*4,atol=.01):raise ValueError('The E57 images do not use a supported 90-degree cube camera.')
        blob=pin['jpegImage'];size=blob.byteCount()
        if not 0<size<32*1024**2:raise ValueError('Oversized photograph.')
        content=np.empty(size,np.uint8);blob.read(content,0,size);jpeg=content.tobytes()
        # E57 pinhole: +X right, +Y up, -Z forward. The existing viewer uses
        # camera right/down/forward rows in the setup coordinate frame.
        rotation=np.diag([1.,-1.,-1.])@Ri.T@scan_rotation
        face=dict(index=i,width=width,height=height,fx=fx,fy=fy,cx=cx,cy=cy,rotation=rotation.tolist(),levels=[])
        if jpeg_size(jpeg)!=(width,height):raise ValueError('E57 photograph dimensions differ from its metadata.')
        if Image is None:
            face['levels'].append(dict(width=width,offset=offset,length=len(jpeg)));chunks.append(jpeg);offset+=len(jpeg)
        else:
          with Image.open(io.BytesIO(jpeg)) as image:
            image.load()
            if image.size!=(width,height) or image.mode!='RGB':raise ValueError('E57 photograph dimensions or channels do not match its metadata.')
            for side in sorted({width,min(width,2048),min(width,1024)},reverse=True):
                if side==width:data=jpeg
                else:
                    buf=io.BytesIO();image.resize((side,side),Image.Resampling.LANCZOS).save(buf,'JPEG',quality=92);data=buf.getvalue()
                face['levels'].append(dict(width=side,offset=offset,length=len(data)));chunks.append(data);offset+=len(data)
        faces.append(face)
    manifest=json.dumps(dict(version=1,faces=faces),separators=(',',':')).encode()
    data=MAGIC+struct.pack('<I',len(manifest))+manifest+b''.join(chunks)
    checked=decode(data)
    destination=Path(destination)
    try:destination.write_bytes(data)
    except BaseException:destination.unlink(missing_ok=True);raise
    return dict(file=str(destination),sha256=checked['sha256'],format='e57_cube_jpeg_v1',in_setup_frame=True,source_name='E57 associated photographs'),origin
