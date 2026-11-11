"""Cleaning scopes use the saved box independently of display clipping."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import design_api
import design_compare
import inspection
import inspection_api
import jobdata
import preview_index
import registration_pose
import section_circle
import shape_selection
import surface_api
from core import Alignment


class SavedLimitBoxTests(unittest.TestCase):
    def setUp(self):
        root=TemporaryDirectory()
        self.addCleanup(root.cleanup)
        path=Path(root.name)/'merged.xyz64'
        self.block=preview_index.BLOCK
        count=3*self.block
        p=np.column_stack([np.arange(count,dtype=float),np.zeros((count,2))])
        p.tofile(path)
        mask=path.with_suffix('.mask')
        excluded=np.zeros(count,dtype='u1')
        excluded[self.block+42]=1
        excluded.tofile(mask)
        self.meta=dict(name='Registered cloud',file=str(path),count=count,mask_file=str(mask),
                       minimum=p.min(0).tolist(),maximum=p.max(0).tolist())
        rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
        registration_pose.set_pose(self.meta,rotation,[100000.,400000.,0.])
        self.item=dict(id='merged',kind='scan',name=self.meta['name'],meta=self.meta,
                       limit_hide_outside=False,limit_box=dict(
            mode='oriented_box',centre=[100000.,400000.+self.block+99.5,0.],
            axes=rotation.T.tolist(),half_size=[80.,1.,1.]))
        self.s=SimpleNamespace(STATE={'job_items':[self.item],'surface_source':self.meta,
                                     'surface_source_item':'merged'},WORK=Path(root.name),progress=lambda _:None)
        self.expected=np.arange(self.block+20,self.block+180)
        self.expected=self.expected[excluded[self.expected]==0]
        preview_index.bounds(self.meta)

    def test_circle_default_and_explicit_whole_cloud(self):
        def fit(batches,*_,**__):
            return {'observed':np.concatenate([ids for ids,_,_ in batches()])}
        with patch.object(section_circle,'run',side_effect=fit):
            row=shape_selection.preview(self.s,dict(source_id='merged',mode='circle'))
            self.assertEqual(row['search_area'],'limit_box')
            np.testing.assert_array_equal(row['observed'],self.expected)
            row=shape_selection.preview(self.s,dict(source_id='merged',mode='circle',scope='whole'))
            self.assertEqual(row['search_area'],'whole')
            self.assertEqual(len(row['observed']),self.meta['count']-1)
            self.item['limit_box']=None
            with self.assertRaisesRegex(ValueError,'saved limit box changed'):
                shape_selection.preview(self.s,dict(source_id='merged',mode='circle',scope='limit'))

    def test_surface_reads_one_index_block_and_reuses_exact_rows(self):
        read=np.fromfile
        reads=[]
        def observe(file,*args,**kwargs):
            rows=read(file,*args,**kwargs)
            if getattr(file,'name',None)==self.meta['file']:reads.append(rows.size//3)
            return rows
        def run(server,body,cached=None):
            if body['selection']['mode']=='full':return 'whole scan'
            self.assertEqual(body['selection']['mode'],'oriented_box')
            self.assertIsNotNone(cached)
            for _ in range(2):
                ids=np.concatenate([i for i,_,_ in surface_api._cached_area_batches(cached,100,lambda:False)])
                np.testing.assert_array_equal(ids,self.expected)
        with patch.object(np,'fromfile',side_effect=observe),patch.object(surface_api,'_run',side_effect=run):
            surface_api.run(self.s,dict(source='separate',selection={'mode':'limit_box'}))
            self.assertEqual(reads,[self.block])
            self.assertEqual(surface_api.run(self.s,dict(source='separate',selection={'mode':'full'},
                                                        ignore_limit_box=True)),'whole scan')

    def test_comparison_endpoints_clip_world_points_before_reading_and_writing(self):
        self.s.JOB={'busy':False}
        self.s.STATE['axis']=Alignment([dict(chainage=v,e=100000,n=400000+v,h=0,radius=4) for v in (0,self.meta['count'])])
        self.s.STATE['structures']=[dict(id='ref',name='Reference plane',kind='plane',
            e=100000,n=400000+self.block,h=0,length=1000,width=1000)]
        self.s.save=lambda:None
        self.s.start_job=lambda run,*args,**kwargs:run()
        h=SimpleNamespace(send=lambda _:None)
        raw=np.fromfile
        reads=[]
        def observe(file,*args,**kwargs):
            rows=raw(file,*args,**kwargs)
            if getattr(file,'name',None)==self.meta['file']:reads.append(rows.size//3)
            return rows
        with patch.object(np,'fromfile',side_effect=observe),patch.object(jobdata,'room',wraps=jobdata.room) as space:
            design_api.post(self.s,h,'/api/design-compare',dict(source_id='merged',band_m=0))
            self.assertEqual(reads,[self.block])
            design=design_api.current(self.s)
            self.assertLess(max(call.args[1] for call in space.call_args_list),self.block*design_compare.RECORD.itemsize)
            reads.clear()
            inspection_api.post(self.s,h,'/api/inspect',dict(mode='cloud_geometry',source_id='merged',reference_id='structure:ref'))
            self.assertEqual(reads,[self.block])
            inspected=inspection_api.current(self.s)
        for result,dtype,id_key in [(design,design_compare.RECORD,'point_id'),(inspected,inspection.RECORD,'element_id')]:
            self.assertFalse(result['stale'])
            self.assertEqual(result['processing_area'],'Saved limit box')
            self.assertEqual(result['source_count'],self.meta['count'])
            self.assertEqual(result['record_count'],len(self.expected))
            rows=np.fromfile(result['results_file'],dtype)
            np.testing.assert_array_equal(rows[id_key],self.expected)
            np.testing.assert_array_equal(result['effective_bounds'],[rows['xyz'].min(0),rows['xyz'].max(0)])
        self.item['limit_box']=None
        self.assertTrue(design_api.current(self.s)['stale'])
        self.assertTrue(inspection_api.current(self.s)['stale'])


if __name__=='__main__':unittest.main()
