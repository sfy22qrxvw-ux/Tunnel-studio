# Copyright (c) 2026 Damian Gulczynski.
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy,json,tempfile,unittest,zipfile
import numpy as np
import jobdata,job_api,pointio,projects,workflows

class JobTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.work=Path(self.tmp.name)
  self.s=SimpleNamespace(STATE=workflows.empty(),WORK=self.work,JOB={'busy':False},progress=lambda m:None,save=lambda:None,SELECTION=None)
  self.s.start_job=lambda fn,kind='general':fn()
  self.h=SimpleNamespace(send=lambda d:None);job_api.CANCEL.clear()
 def tearDown(self):self.tmp.cleanup()
 def obj(self,name='mesh.obj',bad=False):
  p=self.work/name;p.write_text('v 180000 399000 50\nv 180001 399000 50\nv 180000 399001 50\nf 1 2 '+('9' if bad else '3')+'\n');return p
 def scan(self,name='scan.csv'):
  p=self.work/name;np.savetxt(p,np.c_[np.arange(1000),np.ones(1000),np.zeros(1000)],delimiter=',',header='e,n,h',comments='');return p
 def test_obj_float64_topology_zip_and_portable(self):
  source=self.obj();archive=self.work/'model.zip'
  with zipfile.ZipFile(archive,'w') as z:z.write(source,'nested/model.obj')
  m=jobdata.import_obj(archive,self.work);jobdata.validate(m,True)
  np.testing.assert_array_equal(np.fromfile(m['vertices_file'],dtype='<f8').reshape(-1,3),[[180000,399000,50],[180001,399000,50],[180000,399001,50]])
  np.testing.assert_array_equal(np.fromfile(m['faces_file'],dtype='<u4'),[0,1,2])
  x=job_api.append(self.s,'mesh',m);bundle=self.work/'job.tstudio';projects.portable(self.work,self.s.STATE,bundle)
  d=projects.unpack(self.work,bundle);workflows.restore_data(self.s,d);jobdata.validate(self.s.STATE['job_items'][0]['meta'],True)
 def test_invalid_mesh_cancellation_and_disk_check_leave_no_assets(self):
  src=self.obj(bad=True);before=set(self.work.iterdir())
  with self.assertRaisesRegex(ValueError,'missing vertices'):jobdata.import_obj(src,self.work)
  self.assertEqual(before,set(self.work.iterdir()))
  src=self.obj(bad=False)
  def cancel(message):
   if message.startswith('Checking triangle'):raise jobdata.Cancelled('cancelled')
  with self.assertRaises(jobdata.Cancelled):jobdata.import_obj(src,self.work,cancel)
  self.assertFalse(list(self.work.glob('*.vertices64')))
  with patch('jobdata.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
   with self.assertRaisesRegex(ValueError,'free space'):jobdata.import_scan(self.scan(),self.work)
 def test_job_preview_global_cap_visibility_and_mask(self):
  m=jobdata.import_scan(self.scan(),self.work)
  mask=self.work/'exclude.mask';np.ones(m['count'],dtype='u1').tofile(mask)
  for i in range(20):job_api.append(self.s,'scan',copy.deepcopy(m))
  self.s.STATE['job_items'][0]['meta']['mask_file']=str(mask)
  q=jobdata.preview(self.s.STATE['job_items'],1000)
  self.assertLessEqual(q['sample_count'],1000);self.assertEqual(q['source_points'],19000)
  for item in self.s.STATE['job_items']:item['visible']=False
  self.assertEqual(jobdata.preview(self.s.STATE['job_items'])['sample_count'],0)
 def test_rtc360_quick_preview_uses_balanced_full_cache_not_legacy_stride_file(self):
  n=10000;xyz=np.c_[np.arange(n,dtype=float),np.sin(np.arange(n)/100),np.cos(np.arange(n)/100)]
  full=self.work/'raw.xyz64';xyz.astype('<f8').tofile(full)
  preview=self.work/'legacy.preview64';np.zeros((100,3),dtype='<f8').tofile(preview)
  meta={'file':str(full),'count':n,'preview_file':str(preview),'preview_count':100,'preview_stride':100,'source_format':'rtc360_raw_experimental'}
  item={'kind':'scan','meta':meta,'visible':True,'hidden_boxes':[],'isolate_box':None}
  packed=jobdata._item_sample_uncached(item,80,np.zeros(3),[.3,.4,.5],quick=True)
  self.assertEqual(len(packed),80);self.assertGreater(np.ptp(packed[:,0]),9000)
 def test_registration_pairs_survive_switching_setup(self):
  for name in ['first.csv','second.csv']:job_api.load(self.s,'job_scan',self.scan(name))
  a,b=self.s.STATE['job_items'];job_api.post(self.s,self.h,'/api/job-action',{'action':'register','id':a['id']})
  self.s.STATE['registration']['rows']=[{'id':'Target A','source':[1,2,3]}]
  job_api.post(self.s,self.h,'/api/job-action',{'action':'register','id':b['id']})
  job_api.post(self.s,self.h,'/api/job-action',{'action':'register','id':a['id']})
  self.assertEqual(self.s.STATE['registration']['rows'][0]['id'],'Target A')
 def test_e57_setups_import_separately(self):
  import pye57
  p=self.work/'two.e57'
  with pye57.E57(str(p),'w') as z:
   for shift in [0.,10.]:
    z.write_scan_raw({'cartesianX':np.array([shift,shift+1,shift+2]),'cartesianY':np.array([1.,2.,3.]),'cartesianZ':np.array([4.,5.,6.])})
  job_api.load(self.s,'job_scan',p);entries=self.s.STATE['job_items']
  self.assertEqual(len(entries),2);self.assertEqual([e['meta']['count'] for e in entries],[3,3])
  self.assertEqual(entries[0]['meta']['e57_setup_index'],0);self.assertEqual(entries[1]['meta']['e57_setup_index'],1)
  np.testing.assert_array_equal(np.fromfile(entries[1]['meta']['file'],dtype='<f8').reshape(-1,3)[:,0],[10,11,12])
  self.assertEqual(sum(e['source_bytes'] for e in entries),p.stat().st_size)
 def test_cancel_scan_discards_partial_import(self):
  def cancel(message):raise jobdata.Cancelled('cancelled')
  with self.assertRaises(jobdata.Cancelled):jobdata.import_scan(self.scan(),self.work,progress=cancel)
  self.assertFalse(list(self.work.glob('*.xyz64')));self.assertFalse(list(self.work.glob('*.preview64')))

if __name__=='__main__':unittest.main()
