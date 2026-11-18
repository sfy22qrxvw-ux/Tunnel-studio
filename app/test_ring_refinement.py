import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import lining_features as lf
from ring_refinement import refine
from test_lining_features_0182 import model


class RefinementTests(unittest.TestCase):
 def fixture(self):
  m=model();t,a=np.meshgrid(np.arange(.005,6,.01),np.arange(360)*2*np.pi/360,indexing='ij')
  q=np.c_[t.ravel(),np.cos(a).ravel(),np.sin(a).ravel()];ids=np.arange(len(q))
  return m,t,q,ids
 def test_two_width_compatible_measured_traces_remain_unresolved(self):
  m,t,q,ids=self.fixture()
  values=np.where((abs(t.ravel()-2.94)<.011)|(abs(t.ravel()-3.06)<.011),200.,100.)
  d=lf.Detector(m,(0,6),dict(lining_task='seams',lining_ring_width_m=2),lambda _:None)
  d.rings=[d.add('ring_seam','anchor',station_m=x,coefficients=[x,0,0]) for x in (1.,5.)]
  before=d.grid_labels.copy()
  refine(d,lambda box:iter([(ids,q,{})]),lambda ix:values[ix])
  self.assertEqual(len(d.rings),2,'Two plausible measured joints must not become one accepted joint')
  self.assertEqual(d.refinement_checks[0]['status'],'unresolved')
  self.assertGreaterEqual(len(d.refinement_checks[0]['candidate_stations_m']),2)
  summary=lf.detection_summary(d,'seams')
  self.assertEqual(summary['unresolved_refinement_gaps'],1)
  self.assertIn('no joint was added there',summary['summary'])
  np.testing.assert_array_equal(d.grid_labels,before)
 def test_refinement_preserves_existing_bolt_and_other_labels(self):
  from types import SimpleNamespace
  from lining_tiles import DiskRows
  m,t,q,ids=self.fixture();values=np.where(abs(t.ravel()-3)<.011,200.,100.)
  for tiled in (False,True):
   with self.subTest(tiled=tiled),tempfile.TemporaryDirectory() as tmp:
    d=lf.Detector(m,(0,6),dict(lining_task='seams',lining_ring_width_m=2),lambda _:None)
    d.rings=[d.add('ring_seam','anchor',station_m=x,coefficients=[x,0,0]) for x in (1.,5.)]
    bolt=d.add('bolt_hole','existing pocket',station_m=3.,coefficients=[3.,0,0])
    row=int((3.-d.t0)/d.cell);d.grid_labels[row-1:row+2,30:54]=bolt['id']
    d.grid_labels[row-1:row+2,70:80]=99
    before=d.grid_labels.copy();parent=d
    if tiled:
     labels=DiskRows(Path(tmp)/'labels',d.nt,d.na,'<i4');ii,jj=np.indices(d.shape);labels.put(ii.ravel(),jj.ravel(),before.ravel())
     def add(value):return d.add(value.pop('kind'),value.pop('name'),**{k:v for k,v in value.items() if k!='status'})
     parent=SimpleNamespace(s=d.s,cell=d.cell,m=d.m,rings=d.rings,emit=d.emit,labels=labels,t0=d.t0,na=d.na,nt=d.nt,add=add)
    try:
     refine(parent,lambda box:iter([(ids,q,{})]),lambda ix:values[ix])
     after=labels.read(0,d.nt) if tiled else d.grid_labels
     self.assertEqual(len(parent.rings),3)
     np.testing.assert_array_equal(after[before!=0],before[before!=0])
     self.assertGreater(np.count_nonzero((before==0)&(after!=0)),0)
    finally:
     if tiled:labels.close()
 def test_refinement_intersects_original_scope_and_retained_mask(self):
  import surface_streaming,jobdata,preview_index
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);q=np.random.default_rng(32).uniform(-1,1,(200,3));q.astype('<f8').tofile(p/'q')
   flags=np.zeros(len(q),'u1');flags[::3]=1;flags.tofile(p/'mask')
   meta=dict(file=str(p/'q'),mask_file=str(p/'mask'),count=len(q))
   original=dict(mode='box',minimum=[-1,-1,-1],maximum=[0,1,1]);fine=dict(mode='box',minimum=[-.8,-.4,-1],maximum=[.8,.4,1])
   session=SimpleNamespace(STATE={},progress=lambda _:None)
   with patch.object(preview_index,'indexed_selection_blocks',return_value=[(0,len(q))]):
    rows=list(surface_streaming.batches(session,meta,'job',original,17,refinement_box=fine))
   ids=np.concatenate([r[0] for r in rows]);expected=np.flatnonzero((flags==0)&jobdata.selection_mask(q,original)&jobdata.selection_mask(q,fine))
   np.testing.assert_array_equal(ids,expected);np.testing.assert_array_equal(np.concatenate([r[1] for r in rows]),q[expected])
   with self.assertRaises(ValueError):list(surface_streaming.batches(session,meta,'job',original,17,lambda:True,refinement_box=fine))
 def test_measured_gap_and_blank_gap(self):
  m=model();t,a=np.meshgrid(np.arange(.005,6,.01),np.arange(360)*2*np.pi/360,indexing='ij')
  q=np.c_[t.ravel(),np.cos(a).ravel(),np.sin(a).ravel()];ids=np.arange(len(q))
  for present in (True,False):
   values=np.where((abs(t.ravel()-3)<.011)&present,200.,100.)
   d=lf.Detector(m,(0,6),dict(lining_task='seams',lining_ring_width_m=2),lambda _:None)
   d.rings=[d.add('ring_seam','anchor',station_m=x,coefficients=[x,0,0]) for x in (1.,5.)]
   refine(d,lambda box:iter([(ids,q,{})]),lambda ix:values[ix])
   self.assertEqual(len(d.rings),3 if present else 2)
   self.assertEqual(d.refinement_checks[0]['status'],'candidate' if present else 'unresolved')
   if present:self.assertAlmostEqual(d.rings[1]['station_m'],3,places=2)
 def test_no_unbounded_gap_or_repeated_refinement(self):
  m=model();d=lf.Detector(m,(0,12),dict(lining_task='seams',lining_ring_width_m=2),lambda _:None)
  d.rings=[dict(station_m=x) for x in (1.,9.)]
  refine(d,lambda box:self.fail('Long gap must stay unresolved'))
  self.assertEqual(d.refinement_checks,[])

if __name__=='__main__':unittest.main()
