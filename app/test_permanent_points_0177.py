# Copyright (c) 2026 Damian Gulczynski.
import copy,tempfile,unittest
from pathlib import Path
from unittest import mock
import numpy as np
import scan_compaction

class FakeServer:
    def __init__(self,work):
        self.WORK=Path(work);self.WORKSPACE_HISTORY={'undo':[],'redo':[]};self.SELECTION=None
        self.STATE={'job_items':[],'active_job_scan':None,'active_job_registration':None,'surface_source_item':None,'project_id':None}
        self.JOB_CANCEL_FILE=self.WORK/'cancel';self.JOB={'message':''};self.HIGHLIGHT_STATS=None
    def save(self):pass
    def progress(self,message):self.JOB['message']=message

def add_scan(s,name,n=1000,delete_every=5):
    pts=np.arange(n*3,dtype=np.float64).reshape(n,3)/1000
    xyz=s.WORK/(name+'.xyz64');pts.astype('<f8').tofile(xyz)
    attr=s.WORK/(name+'.attr32');np.zeros((n,4),dtype='<f4').tofile(attr)
    mask=s.WORK/(name+'.mask');m=np.zeros(n,dtype='u1');m[::delete_every]=1;m.tofile(mask)
    meta={'name':name,'file':str(xyz),'attributes_file':str(attr),'mask_file':str(mask),'count':n,
          'minimum':pts.min(0).tolist(),'maximum':pts.max(0).tolist(),'attribute_counts':[n]*4}
    item={'id':name,'kind':'scan','name':name,'meta':meta,'visible':True,'source_bytes':0,'edit_history':[],'redo_history':[],
          'deleted_groups':[{'id':'d','name':'Deleted','count':int(m.sum()),'active':True,'kind':'delete'}]}
    s.STATE['job_items'].append(item);return item

class PermanentPoints0177Tests(unittest.TestCase):
    def test_single_pass_preview_is_written_with_compaction(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);item=add_scan(s,'a',1234,7);review=scan_compaction.preview(s,item)
            def done(server,candidates,created,count,written,rv):
                return {'id':rv['id'],'name':rv['name'],'removed_points':count-written,'remaining_points':written,
                        'forgotten_archived_points':0,'net_reclaimed_bytes':0,'reclamation_complete':True,'warnings':[]}
            with mock.patch.object(scan_compaction,'finish',side_effect=done):
                report=scan_compaction.compact(s,item,True,review['revision'])
            self.assertEqual(report['removed_points'],review['removed_points'])
            self.assertEqual(item['meta']['count'],review['remaining_points'])
            preview=Path(item['meta']['preview_file']);self.assertTrue(preview.is_file())
            self.assertEqual(preview.stat().st_size,item['meta']['preview_count']*24)
            retained=np.fromfile(item['meta']['file'],'<f8').reshape(-1,3)
            shown=np.fromfile(preview,'<f8').reshape(-1,3)
            np.testing.assert_allclose(shown,retained[::item['meta']['preview_stride']])

    def test_batch_validates_every_scan_then_processes_all(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeServer(d);a=add_scan(s,'a',100,4);b=add_scan(s,'b',120,3)
            selection=[{'id':x['id'],'revision':scan_compaction.preview(s,x)['revision']} for x in (a,b)]
            calls=[]
            def fake_compact(server,item,confirmed,revision):
                calls.append(item['id'])
                rv=scan_compaction.preview(server,item)
                return {'id':item['id'],'name':item['name'],'removed_points':rv['removed_points'],'remaining_points':rv['remaining_points'],
                        'forgotten_archived_points':0,'net_reclaimed_bytes':10,'reclamation_complete':True,'warnings':[]}
            with mock.patch.object(scan_compaction,'compact',side_effect=fake_compact):
                report=scan_compaction.compact_many(s,selection,True)
            self.assertEqual(calls,['a','b']);self.assertTrue(report['batch']);self.assertEqual(report['scan_count'],2)
            self.assertGreater(report['removed_points'],0);self.assertEqual(report['net_reclaimed_bytes'],20)

    def test_block_is_large_enough_for_high_throughput(self):
        self.assertGreaterEqual(scan_compaction.BLOCK,1_000_000)

if __name__=='__main__':unittest.main()
