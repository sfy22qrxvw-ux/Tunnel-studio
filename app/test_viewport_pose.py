import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import detail_preview as d
import preview_store,preview_index,registration_pose

class ViewportPoseTests(unittest.TestCase):
 def test_hash_prefilter_preserves_exact_sample_and_colours(self):
  from checker_evidence import priority_keys
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);q=np.random.default_rng(9).uniform(-3,3,(10000,3));q.astype('<f8').tofile(p/'q')
   flags=np.zeros(len(q),'u1');flags[::7]=1;flags.tofile(p/'mask')
   attrs=np.random.default_rng(4).uniform(0,1,(len(q),4)).astype('<f4');attrs.tofile(p/'attrs')
   m=dict(file=str(p/'q'),attributes_file=str(p/'attrs'),mask_file=str(p/'mask'),count=len(q),minimum=q.min(0).tolist(),maximum=q.max(0).tolist())
   v=dict(target=[0,0,0],right=[1,0,0],up=[0,1,0],forward=[0,0,1],scale=1,aspect=1)
   eligible=np.flatnonzero(d._view_mask(q,v,d.validate_view(v))&(flags==0));expected=np.sort(eligible[np.argsort(priority_keys(eligible))[:20]]);found=[]
   with patch.object(preview_store,'read',return_value=None),patch.object(preview_store,'write'),patch.object(preview_index,'cached_bounds',return_value=np.zeros((1,2,3))),patch.object(preview_index,'candidate_blocks',return_value=iter((i,min(i+100,len(q))) for i in range(0,len(q),100))),patch('fine_view_index.candidates',return_value=None),patch.object(d,'READ_POINTS',100):
    result=d.sample(dict(id='t',kind='scan',meta=m),20,[0,0,0],[.2,.4,.6],'rgb',None,v,decorate=lambda ix,p:found.append(ix.copy()))
   np.testing.assert_array_equal(found[0],expected);np.testing.assert_array_equal(result[:,:3],q[expected].astype('<f4'));np.testing.assert_array_equal(result[:,3:],attrs[expected,1:])
 def test_identity_rotation_translation_and_deleted_ids(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);q=np.random.default_rng(42).uniform(-1,1,(120,3));q.astype('<f8').tofile(p/'q')
   mask=np.zeros(120,'u1');mask[::7]=1;mask.tofile(p/'mask')
   for rotation,translation in [(np.eye(3),np.zeros(3)),(np.array([[0,-1,0],[1,0,0],[0,0,1]]),np.array([4,5,6]))]:
    m=dict(file=str(p/'q'),mask_file=str(p/'mask'),count=120,minimum=q.min(0).tolist(),maximum=q.max(0).tolist(),registration_rotation=rotation.tolist(),registration_translation=translation.tolist())
    item=dict(id='test',kind='scan',meta=m,visible=True)
    v=dict(target=translation.tolist(),right=[1,0,0],up=[0,1,0],forward=[0,0,1],scale=3,aspect=1)
    ids=[]
    with patch.object(preview_store,'read',return_value=None),patch.object(preview_store,'write'),patch.object(preview_index,'cached_bounds',return_value=None),patch.object(preview_index,'BoundsBuilder'),patch.object(d,'READ_POINTS',30):
     result=d.sample(item,200,[0,0,0],[.2,.4,.6],'bundle',None,v,decorate=lambda i,p:ids.append(i.copy()))
    expected_ids=np.flatnonzero(mask==0)
    np.testing.assert_array_equal(ids[0],expected_ids)
    np.testing.assert_array_equal(result[:,:3],registration_pose.points(m,q[expected_ids]).astype('<f4'))
    np.testing.assert_array_equal(result[:,3:],np.tile(np.array([.2,.4,.6],'<f4'),(len(expected_ids),1)))
    with self.assertRaises(InterruptedError):d.sample(item,200,[0,0,0],[.2,.4,.6],'bundle',None,v,cancel=lambda:True)

if __name__=='__main__':unittest.main()
