# Copyright (c) 2026 Damian Gulczynski.
"""Streaming neutral-format exports. E57 uses double coordinates, no fake scanner poses."""
from pathlib import Path
import json, uuid
import numpy as np
import pointio


def export_cloud(meta,destination,fmt,progress=lambda s:None):
    path=Path(destination)
    if fmt not in ('e57','las','laz','ply','pts','xyz','csv'): raise ValueError('Choose a supported scan export format.')
    count=0; lo=np.full(3,np.inf); hi=-lo; amin=np.full(4,np.inf); amax=-amin; acount=np.zeros(4,dtype='i8')
    for p,a in pointio.chunks(meta):
        count+=len(p); lo=np.minimum(lo,p.min(0)); hi=np.maximum(hi,p.max(0))
        if a is not None:
            for j in range(4):
                v=a[np.isfinite(a[:,j]),j]; acount[j]+=len(v)
                if len(v): amin[j]=min(amin[j],float(v.min())); amax[j]=max(amax[j],float(v.max()))
        progress(f'Checking export bounds: {count:,} retained points')
    if not count: raise ValueError('No retained points to export.')
    intensity=bool(acount[0]==count); colour=bool((acount[1:]==count).all())
    warnings=['Unstructured points only. Scanner setups, row/column grids, panoramas, classifications, return numbers and registration links are not exported.']
    generated=bool(meta.get('generated_geometry'))
    if generated:warnings.append('Generated samples from triangle mesh geometry, not measured scan observations. No measured intensity was created.')
    if any((acount>0)&(acount<count)): warnings.append('Incomplete attribute channels omitted; no missing values were invented.')
    def normal_i(a,maxval=65535):
        return np.rint(np.clip((a[:,0]-amin[0])/max(amax[0]-amin[0],1e-15),0,1)*maxval)
    def rgb(a,maxval=255):
        # Preserve conventional 8/16 bit RGB rather than stretching image contrast.
        peak=max(amax[1:]); scale=1 if peak<=1 else 255 if peak<=255 else 65535
        return np.rint(np.clip(a[:,1:]/scale,0,1)*maxval)
    done=0
    try:
        if fmt=='e57':
            import pye57
            from pye57 import libe57
            with pye57.E57(str(path),'w') as f:
                im=f.image_file; node=libe57.StructureNode(im); node.set('guid',libe57.StringNode(im,'{'+str(uuid.uuid4())+'}'))
                node.set('name',libe57.StringNode(im,'Tunnel Studio sampled mesh geometry' if generated else 'Tunnel Studio registered points' if meta.get('registration_id') else 'Tunnel Studio points'))
                node.set('description',libe57.StringNode(im,'Generated samples in the saved mesh coordinate frame; not survey observations. No scanner pose or acquisition grid.' if generated else 'Unstructured points in metres. No scanner pose or acquisition grid.'))
                bounds=libe57.StructureNode(im); proto=libe57.StructureNode(im); names=['cartesianX','cartesianY','cartesianZ']
                for j,axis in enumerate('xyz'):
                    bounds.set(axis+'Minimum',libe57.FloatNode(im,float(lo[j]))); bounds.set(axis+'Maximum',libe57.FloatNode(im,float(hi[j])))
                    proto.set(names[j],libe57.FloatNode(im,float((lo[j]+hi[j])/2),libe57.E57_DOUBLE,float(lo[j]),float(hi[j])))
                node.set('cartesianBounds',bounds)
                if intensity:
                    names.append('intensity'); proto.set('intensity',libe57.FloatNode(im,float(amin[0]),libe57.E57_DOUBLE,float(amin[0]),float(amax[0])))
                    limits=libe57.StructureNode(im); limits.set('intensityMinimum',libe57.FloatNode(im,float(amin[0]))); limits.set('intensityMaximum',libe57.FloatNode(im,float(amax[0]))); node.set('intensityLimits',limits)
                if colour:
                    limits=libe57.StructureNode(im)
                    for name in ['colorRed','colorGreen','colorBlue']:
                        names.append(name); proto.set(name,libe57.IntegerNode(im,0,0,255)); limits.set(name+'Minimum',libe57.IntegerNode(im,0)); limits.set(name+'Maximum',libe57.IntegerNode(im,255))
                    node.set('colorLimits',limits)
                data,buffers=f.make_buffers(names,pointio.CHUNK); points=libe57.CompressedVectorNode(im,proto,libe57.VectorNode(im,True)); node.set('points',points); f.data3d.append(node)
                writer=points.writer(buffers)
                try:
                    for p,a in pointio.chunks(meta):
                        n=len(p)
                        for j,name in enumerate(names[:3]): data[name][:n]=p[:,j]
                        if intensity: data['intensity'][:n]=a[:,0]
                        if colour:
                            c=rgb(a)
                            for j,name in enumerate(['colorRed','colorGreen','colorBlue']): data[name][:n]=c[:,j]
                        writer.write(n); done+=n; progress(f'Writing E57: {done:,} / {count:,}')
                finally: writer.close()
        elif fmt in ('las','laz'):
            import laspy
            header=laspy.LasHeader(point_format=3 if colour else 1,version='1.2'); header.scales=np.array([.0001]*3); header.offsets=lo
            if generated:header.generating_software='Tunnel Studio mesh samples';header.system_identifier='Generated mesh geometry'
            if np.any((hi-lo)/.0001>np.iinfo('i4').max): raise ValueError('Extent exceeds the LAS 0.1 mm quantisation range. Use E57.')
            warnings.append('LAS/LAZ XYZ quantised to 0.1 mm. Intensity scaled across this export to unsigned 16-bit; RGB stored as 16-bit.')
            with laspy.open(path,mode='w',header=header,do_compress=fmt=='laz') as f:
                for p,a in pointio.chunks(meta):
                    record=laspy.ScaleAwarePointRecord.zeros(len(p),header=header); record.x=p[:,0]; record.y=p[:,1]; record.z=p[:,2]
                    if intensity: record.intensity=normal_i(a).astype('u2')
                    if colour: c=rgb(a,65535).astype('u2'); record.red=c[:,0]; record.green=c[:,1]; record.blue=c[:,2]
                    f.write_points(record); done+=len(p); progress(f'Writing {fmt.upper()}: {done:,} / {count:,}')
        else:
            with path.open('w',encoding='ascii',newline='\n') as f:
                if fmt=='ply':
                    f.write(f'ply\nformat ascii 1.0\ncomment Tunnel Studio; coordinates in metres\nelement vertex {count}\nproperty double x\nproperty double y\nproperty double z\n')
                    if generated:f.write('comment Generated mesh samples; not measured scan observations\n')
                    if intensity: f.write('property float intensity\n')
                    if colour: f.write('property uchar red\nproperty uchar green\nproperty uchar blue\n')
                    f.write('end_header\n')
                elif fmt=='pts': f.write(str(count)+'\n')
                elif fmt=='csv': f.write('e,n,h'+(',intensity' if intensity else '')+(',red,green,blue' if colour else '')+'\n')
                for p,a in pointio.chunks(meta):
                    columns=[p]; formats=['%.9f']*3
                    if fmt!='xyz':
                        if intensity: columns.append((normal_i(a,4095)-2048 if fmt=='pts' else a[:,0])[:,None]); formats.append('%.9g' if fmt!='pts' else '%d')
                        elif fmt=='pts' and colour: columns.append(np.zeros((len(p),1))); formats.append('%d')
                        if colour: columns.append(rgb(a)); formats+=['%d']*3
                    np.savetxt(f,np.column_stack(columns),fmt=formats,delimiter=',' if fmt=='csv' else ' ')
                    done+=len(p); progress(f'Writing {fmt.upper()}: {done:,} / {count:,}')
            if fmt=='pts' and intensity: warnings.append('PTS intensity scaled to -2048 through 2047.')
            if fmt=='pts' and colour and not intensity: warnings.append('PTS contains a constant zero intensity placeholder; RGB is retained.')
            if fmt=='xyz': warnings.append('XYZ contains only coordinates.')
        if colour and fmt not in ('las','laz','xyz'): warnings.append('RGB converted to 8-bit using its 0–1, 0–255 or 0–65535 source range.')
    except Exception: path.unlink(missing_ok=True); raise
    return {'file':str(path),'format':fmt,'count':count,'intensity':intensity and fmt!='xyz','colour':colour and fmt!='xyz','warnings':warnings}
