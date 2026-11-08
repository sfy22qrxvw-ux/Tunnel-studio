"""Small observed-point intensity images, collected during full-density validation."""
import base64, struct, zlib
import numpy as np

class TargetThumbnail:
    size=128
    def __init__(self,frame):
        self.frame=frame
        self.count=np.zeros((self.size,self.size),np.int64)
        self.total=np.zeros((self.size,self.size),np.float64)
    def add(self,points,values):
        f=self.frame;q=np.asarray(points)-f['centre'];v=np.asarray(values)
        u=q@np.asarray(f['u']);w=q@np.asarray(f['w']);depth=q@np.asarray(f['normal'])
        keep=np.isfinite(v)&np.isfinite(q).all(1)&(np.abs(depth)<=f['plane_limit'])
        keep&=(np.abs(u)<f['diameter']/2)&(np.abs(w)<f['diameter']/2)
        if not keep.any():return
        x=np.floor((u[keep]/f['diameter']+.5)*self.size).astype(int)
        y=np.floor((.5-w[keep]/f['diameter'])*self.size).astype(int)
        x=np.clip(x,0,self.size-1);y=np.clip(y,0,self.size-1)
        np.add.at(self.count,(y,x),1);np.add.at(self.total,(y,x),v[keep])
    def result(self):
        valid=self.count>0;image=np.full((self.size,self.size,3),38,np.uint8)
        if valid.any():
            vals=self.total[valid]/self.count[valid]
            low,high=np.percentile(vals,[2,98]);gray=np.clip((vals-low)/max(high-low,1e-12)*255,0,255).astype(np.uint8)
            image[valid]=gray[:,None]
        # A short red cross marks the fitted crossing, leaving most of it visible.
        c=self.size//2
        for start,end in [(c-10,c-3),(c+4,c+11)]:
            image[c-1:c+1,start:end]=[255,70,70];image[start:end,c-1:c+1]=[255,70,70]
        def chunk(name,payload):
            return struct.pack('>I',len(payload))+name+payload+struct.pack('>I',zlib.crc32(name+payload)&0xffffffff)
        raw=b''.join(b'\0'+row.tobytes() for row in image)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',self.size,self.size,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
        return {'data_url':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),
                'points':int(self.count.sum()),'width':self.size,'channel':'observed contrast',
                'note':'Plane view of retained scan points; red cross = fitted centre. Contrast adjusted locally. Empty pixels are dark grey.'}
