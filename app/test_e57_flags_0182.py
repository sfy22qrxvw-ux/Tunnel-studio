"""E57 validity channels must be read, not dropped or treated as real intensity."""
from pathlib import Path
from unittest import mock
import hashlib,tempfile,unittest,uuid
import numpy as np
import pye57
from pye57 import libe57
import pointio,jobdata


def write_flagged(path):
    n=7
    values={name:np.arange(n,dtype=float)+offset for name,offset in
            [('cartesianX',0),('cartesianY',1),('cartesianZ',2),('intensity',10)]}
    values.update({name:np.full(n,30000+j,dtype=float) for j,name in enumerate(('colorRed','colorGreen','colorBlue'))})
    values.update({name:np.zeros(n,dtype='B') for name in ('cartesianInvalidState','isIntensityInvalid','isColorInvalid')})
    values['cartesianInvalidState'][5]=1
    values['isIntensityInvalid'][1]=1
    values['isColorInvalid'][2]=1
    with pye57.E57(str(path),'w') as f:
        im=f.image_file;node=libe57.StructureNode(im);proto=libe57.StructureNode(im)
        node.set('guid',libe57.StringNode(im,str(uuid.uuid4())))
        node.set('name',libe57.StringNode(im,'Validity and pose regression'))
        for key,a in values.items():
            if 'Invalid' in key:proto.set(key,libe57.IntegerNode(im,0,0,2))
            elif key.startswith('color'):proto.set(key,libe57.IntegerNode(im,0,0,65535))
            else:proto.set(key,libe57.FloatNode(im,float(a[0]),libe57.E57_DOUBLE,float(a.min()),float(a.max())))
        bounds=libe57.StructureNode(im)
        for key,axis in zip(('cartesianX','cartesianY','cartesianZ'),'xyz'):
            bounds.set(axis+'Minimum',libe57.FloatNode(im,float(values[key].min())))
            bounds.set(axis+'Maximum',libe57.FloatNode(im,float(values[key].max())))
        node.set('cartesianBounds',bounds)
        pose=libe57.StructureNode(im);rot=libe57.StructureNode(im);translation=libe57.StructureNode(im)
        for key in 'wxyz':rot.set(key,libe57.FloatNode(im,1. if key=='w' else 0.))
        for key,v in zip('xyz',(10.,20.,30.)):translation.set(key,libe57.FloatNode(im,v))
        pose.set('rotation',rot);pose.set('translation',translation);node.set('pose',pose)
        points=libe57.CompressedVectorNode(im,proto,libe57.VectorNode(im,True));node.set('points',points);f.data3d.append(node)
        buffers=libe57.VectorSourceDestBuffer()
        for key,a in values.items():buffers.append(libe57.SourceDestBuffer(im,key,a,n,True,True))
        writer=points.writer(buffers)
        try:writer.write(n)
        finally:writer.close()
    return values


class E57ValidityTests(unittest.TestCase):
    def test_chunked_validity_pose_and_wide_colours(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'flags.e57';values=write_flagged(path)
            before=hashlib.sha256(path.read_bytes()).digest()
            with mock.patch.object(pointio,'CHUNK',3):rows=list(pointio.records(path))
            p=np.concatenate([v[0] for v in rows]);a=np.concatenate([v[1] for v in rows])
            valid=values['cartesianInvalidState']==0
            expected=np.column_stack([values['cartesian'+k] for k in 'XYZ'])[valid]+[10,20,30]
            np.testing.assert_array_equal(p,expected)
            np.testing.assert_array_equal(a[:,0][np.isfinite(a[:,0])],[10,12,13,14,16])
            self.assertTrue(np.isnan(a[1,0]));self.assertTrue(np.isnan(a[2,1:]).all())
            np.testing.assert_array_equal(a[0,1:],[30000,30001,30002])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)

    def test_actual_isolated_import_preserves_flagged_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'flagged.e57';write_flagged(path)
            meta=jobdata.import_scan(path,root,expected=7)
            self.assertEqual(meta['count'],6)
            self.assertEqual(meta['attribute_counts'],[5,5,5,5])
            p=np.fromfile(meta['file'],'<f8').reshape(-1,3)
            np.testing.assert_array_equal(p[0],[10,21,32])
            attrs=np.fromfile(meta['attributes_file'],'<f4').reshape(-1,4)
            self.assertTrue(np.isnan(attrs[1,0]));self.assertTrue(np.isnan(attrs[2,1:]).all())
            np.testing.assert_array_equal(attrs[0,1:],[30000,30001,30002])


if __name__=='__main__':unittest.main()
