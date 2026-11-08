# Copyright (c) 2026 Damian Gulczynski.
import copy,tempfile,unittest
from pathlib import Path
import numpy as np
import registration_workspace as rw

class FakeServer:
    def __init__(self,work):
        self.WORK=Path(work);self.STATE={'job_items':[],'scan_links':[],'survey_controls':{}}
        self.JOB_CANCEL_FILE=self.WORK/'cancel';self.saved=0
    def save(self):self.saved+=1
    def progress(self,message):pass

def add_scan(s,name,points,targets):
    file=s.WORK/(name+'.xyz64');np.asarray(points,float).astype('<f8').tofile(file)
    attr=s.WORK/(name+'.attr32');np.zeros((len(points),4),dtype='<f4').tofile(attr)
    meta={'name':name,'file':str(file),'attributes_file':str(attr),'count':len(points),'minimum':np.min(points,0).tolist(),'maximum':np.max(points,0).tolist(),'attribute_counts':[len(points)]*4}
    item={'id':name,'kind':'scan','name':name,'meta':meta,'visible':True,'source_bytes':0,'hidden_boxes':[],'isolate_box':None,'edit_history':[],'redo_history':[],'scan_origin':[0.,0.,0.],'scan_orientation':np.eye(3).tolist()}
    item['registration_state']={'cloud':copy.deepcopy(meta),'rows':[{'id':k,'role':'fit','source':list(v),'control':[None,None,None]} for k,v in targets.items()],'candidates':[],'solution':None,'transformed':None,'revision':'r'}
    s.STATE['job_items'].append(item);return item

class RegistrationStorage0175Tests(unittest.TestCase):
    def setup_pair(self,s):
        points=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float)
        a={'A':[0,0,0],'B':[1,0,0],'C':[0,1,0]}
        b={k:(np.asarray(v)+[10,0,0]).tolist() for k,v in a.items()}
        add_scan(s,'a',points,a);add_scan(s,'b',points+10,b)
        draft=rw.solve_pair(s,'a','b');rw.accept(s,draft['id'],True,merge=False)
        return draft
    def test_apply_geometry_does_not_create_duplicate_merged_cloud(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);self.setup_pair(s)
            self.assertEqual(len(s.STATE['scan_links']),1)
            self.assertFalse(any(x.get('registered_merge') for x in s.STATE['job_items']))
            self.assertIsNone(s.STATE.get('last_registered_merge'))
            self.assertEqual(len(s.STATE['job_items']),2)
    def test_optional_merge_estimate_build_and_release(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);self.setup_pair(s)
            estimate=rw.merge_estimate(s,'a')
            self.assertEqual(estimate['setups'],2);self.assertEqual(estimate['points'],8)
            self.assertGreaterEqual(estimate['required_free_bytes'],estimate['reserve_bytes'])
            merged=rw.build_merge(s,'a',lambda message:None)
            self.assertTrue(merged['registered_merge']);self.assertEqual(merged['meta']['count'],8)
            caches=rw.merge_caches(s);self.assertEqual(len(caches),1);self.assertFalse(caches[0]['edited'])
            result=rw.release_merge(s,merged['id'])
            self.assertGreater(result['reclaimed_bytes'],0);self.assertEqual(rw.merge_caches(s),[])
            self.assertEqual({x['id'] for x in s.STATE['job_items']},{'a','b'})
            self.assertTrue(all(x['visible'] for x in s.STATE['job_items']))
    def test_edited_merged_cloud_cannot_be_released(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);self.setup_pair(s);merged=rw.build_merge(s,'a',lambda message:None)
            item=next(x for x in s.STATE['job_items'] if x['id']==merged['id']);item['edit_history']=[{'kind':'delete'}]
            with self.assertRaisesRegex(ValueError,'edits or derived work'):rw.release_merge(s,merged['id'])

if __name__=='__main__':unittest.main()
