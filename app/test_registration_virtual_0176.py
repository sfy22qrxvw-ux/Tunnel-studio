# Copyright (c) 2026 Damian Gulczynski.
import copy,tempfile,unittest
from pathlib import Path
from unittest import mock
import numpy as np
import registration_workspace as rw, registration_pose, jobdata

class FakeServer:
    def __init__(self,work):
        self.WORK=Path(work);self.STATE={'job_items':[],'scan_links':[],'survey_controls':{}}
        self.JOB_CANCEL_FILE=self.WORK/'cancel';self.saved=0
    def save(self):self.saved+=1
    def progress(self,message):pass

def add_scan(s,name,shift):
    base=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float);points=base+shift
    f=s.WORK/(name+'.xyz64');points.astype('<f8').tofile(f)
    a=s.WORK/(name+'.attr32');np.zeros((len(points),4),dtype='<f4').tofile(a)
    meta={'name':name,'file':str(f),'attributes_file':str(a),'count':len(points),'minimum':points.min(0).tolist(),'maximum':points.max(0).tolist(),'attribute_counts':[len(points)]*4}
    targets={'A':points[0].tolist(),'B':points[1].tolist(),'C':points[2].tolist()}
    item={'id':name,'kind':'scan','name':name,'meta':meta,'visible':True,'source_bytes':0,'hidden_boxes':[],'isolate_box':None,'edit_history':[],'redo_history':[],'scan_origin':shift.tolist(),'scan_orientation':np.eye(3).tolist()}
    item['registration_state']={'cloud':copy.deepcopy(meta),'rows':[{'id':k,'role':'fit','source':v,'control':[None,None,None]} for k,v in targets.items()],'candidates':[],'solution':None,'transformed':None,'revision':'r'}
    s.STATE['job_items'].append(item);return item

class VirtualRegistration0176Tests(unittest.TestCase):
    def test_24_setup_chain_adds_no_xyz_cache_files(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);scans=[add_scan(s,f's{i:02d}',np.array([i*10.,i*.2,0.])) for i in range(24)]
            baseline={p.name for p in Path(d).glob('*.xyz64')}
            # A registration acceptance must not need disk headroom for another
            # transformed copy. Make any accidental room() call fail loudly.
            with mock.patch.object(jobdata,'room',side_effect=AssertionError('registration attempted cache allocation')):
                for i in range(1,len(scans)):
                    draft=rw.solve_pair(s,scans[i-1]['id'],scans[i]['id'])
                    rw.accept(s,draft['id'],True,merge=False)
            self.assertEqual({p.name for p in Path(d).glob('*.xyz64')},baseline)
            self.assertEqual(len(s.STATE['scan_links']),23)
            self.assertFalse(any(x.get('registered_merge') for x in s.STATE['job_items']))
            # Every setup now occupies the same world geometry while its source
            # file remains untouched in its original stored frame.
            for x in scans:
                world=registration_pose.points(x['meta'],np.fromfile(x['meta']['file'],'<f8').reshape(-1,3))
                np.testing.assert_allclose(world,np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float),atol=1e-9)
    def test_manual_merge_applies_virtual_pose(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);a=add_scan(s,'a',np.array([0.,0.,0.]));b=add_scan(s,'b',np.array([10.,0.,0.]))
            draft=rw.solve_pair(s,'a','b');rw.accept(s,draft['id'],True,merge=False)
            merged=rw.build_merge(s,'a',lambda m:None);q=np.fromfile(merged['meta']['file'],'<f8').reshape(-1,3)
            np.testing.assert_allclose(q[:4],q[4:],atol=1e-9)

    def test_preview_uses_virtual_world_pose(self):
        import jobdata
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);x=add_scan(s,'p',np.array([0.,0.,0.]))
            registration_pose.set_pose(x['meta'],np.eye(3),np.array([100.,200.,3.]))
            packed=jobdata._item_sample_uncached(x,100,np.array([100.,200.,3.]),[.2,.3,.4],mode='bundle')
            self.assertGreater(len(packed),0)
            # Display coordinates are world minus the supplied floating origin.
            self.assertTrue(np.all(packed[:,:3].max(0) <= 1.000001))
            self.assertTrue(np.all(packed[:,:3].min(0) >= -0.000001))

if __name__=='__main__':unittest.main()
