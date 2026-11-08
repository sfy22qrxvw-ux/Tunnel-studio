"""Bolt candidate sizing, shape, side and original-ID regressions (0.18.2)."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.spatial.distance import pdist

import feature_selection as feature
import registration_pose
import scan_edits
import workflows


def patch(diameter=.20,shape='round',angle=0.,step=.006):
    """Measured curved lining with one known 25 mm raised footprint."""
    x,y=np.meshgrid(np.arange(-.6,.60001,step),np.arange(-.6,.60001,step))
    xy=np.c_[x.ravel(),y.ravel()]
    xy=xy[np.linalg.norm(xy,axis=1)<.59]
    c,s=np.cos(angle),np.sin(angle)
    uv=xy@np.array([[c,-s],[s,c]])
    raised=(np.linalg.norm(uv,axis=1)<diameter/2) if shape=='round' else ((abs(uv[:,0])<.11)&(abs(uv[:,1])<.01))
    z=3-np.sqrt(9-xy[:,0]**2)
    z[raised]+=.025
    return np.c_[xy,z],raised


def detect(points,seed=None,direction=None,**settings):
    return feature.bolt_labels(points,np.asarray([0,0,.025] if seed is None else seed,float),
        [0,0,1] if direction is None else direction,feature.settings(settings),lambda _:None)


class BoltGeometryTests(unittest.TestCase):
    def test_round_fitting_below_diameter_limit_is_not_measured_as_box_diagonal(self):
        p,raised=patch(diameter=.20)
        before=p.copy()
        labels,groups,stats,warnings=detect(p)
        self.assertEqual(len(groups),1)
        np.testing.assert_array_equal(labels>0,raised)
        self.assertGreater(groups[0]['diameter_mm'],190)
        self.assertLessEqual(groups[0]['diameter_mm'],200.001)
        self.assertFalse(groups[0]['selected'])
        np.testing.assert_array_equal(p,before)

    def test_shape_checks_reject_thin_bar_at_different_orientations(self):
        for angle in (0.,np.pi/4,np.pi/2):
            with self.subTest(angle=angle):
                p,_=patch(shape='bar',angle=angle)
                labels,groups,stats,warnings=detect(p)
                self.assertEqual(groups,[])
                self.assertFalse(labels.any())
                self.assertEqual(stats['rejected_reasons']['elongated'],1)
                self.assertTrue(any('elongated rather than compact' in text for text in warnings))

    def test_oversize_fitting_is_still_rejected_with_a_specific_reason(self):
        p,_=patch(diameter=.29)
        labels,groups,stats,warnings=detect(p)
        self.assertEqual(groups,[])
        self.assertFalse(labels.any())
        self.assertEqual(stats['rejected_reasons']['oversize'],1)
        self.assertTrue(any('Maximum fitting diameter' in text for text in warnings))

    def test_real_diameter_agrees_with_independent_all_pairs_reference(self):
        rng=np.random.default_rng(181)
        sets=[rng.normal(size=(75,2)),np.c_[np.linspace(-1,1,13),np.zeros(13)],
              np.c_[np.cos(np.arange(150)*2*np.pi/150),np.sin(np.arange(150)*2*np.pi/150)]]
        for q in sets:
            for angle in (0.,.7):
                c,s=np.cos(angle),np.sin(angle);rot=q@np.array([[c,-s],[s,c]])
                diameter,elongation,unique_count=feature._footprint(rot,.001)
                self.assertAlmostEqual(diameter,float(pdist(q).max()),places=12)
                self.assertEqual(unique_count,len(q))

    def test_repeated_rows_do_not_create_geometric_support(self):
        p,raised=patch(diameter=.05)
        wall=p[~raised]
        duplicates=np.repeat(np.array([[0,0,.025],[.003,0,.025],[0,.003,.025]]),20,axis=0)
        labels,groups,stats,warnings=detect(np.r_[wall,duplicates])
        self.assertEqual(groups,[])
        self.assertFalse(labels.any())
        self.assertEqual(stats['rejected_reasons']['insufficient_points'],1)

    def test_rotated_curved_wall_side_and_recesses_remain_distinct(self):
        p,raised=patch(diameter=.20)
        # Put an equal 25 mm recess elsewhere on this measured lining patch.
        recessed=np.linalg.norm(p[:,:2]-[.26,.13],axis=1)<.035
        p[recessed,2]-=.025
        az,tilt=.53,.87
        rz=np.array([[np.cos(az),-np.sin(az),0],[np.sin(az),np.cos(az),0],[0,0,1.]])
        rx=np.array([[1.,0,0],[0,np.cos(tilt),-np.sin(tilt)],[0,np.sin(tilt),np.cos(tilt)]])
        rotation=rz@rx;translation=np.array([418921.,289178.,-32.])
        world=p@rotation.T+translation;seed=np.array([0,0,.025])@rotation.T+translation
        direction=rotation@np.array([0,0,1.])
        labels,groups,stats,warnings=detect(world,seed,direction)
        self.assertEqual(len(groups),1)
        np.testing.assert_array_equal(labels>0,raised)
        self.assertGreater(stats['negative_recess_points'],10)
        opposite,reverse_groups,_,_=detect(world,seed,-direction)
        np.testing.assert_array_equal(opposite>0,recessed)
        self.assertEqual(len(reverse_groups),1)
        flipped,_,_,_=detect(world,seed,-direction,flip_side=True)
        np.testing.assert_array_equal(flipped,labels)

    def test_uncertain_one_sided_wall_still_refuses_a_bolt_fit(self):
        p,_=patch()
        with self.assertRaisesRegex(ValueError,'around the seed|span a usable|not just on one side'):
            detect(p[p[:,0]>.02])


class BoltOriginalIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.points,self.raised=patch()
        path=self.root/'measured.xyz64';self.points.astype('<f8').tofile(path)
        self.initial=np.zeros(len(self.points),'u1');self.initial[np.flatnonzero(self.raised)[::19]]=1
        mask=self.root/'initial.mask';self.initial.tofile(mask)
        meta=dict(file=str(path),count=len(self.points),name='Measured wall',minimum=self.points.min(0).tolist(),maximum=self.points.max(0).tolist(),mask_file=str(mask))
        self.rotation=np.array([[0.,0.,1.],[1.,0.,0.],[0.,1.,0.]])
        self.translation=np.array([480123.,381987.,-21.])
        registration_pose.set_pose(meta,self.rotation,self.translation)
        self.item=dict(id='setup',kind='scan',name=meta['name'],visible=True,meta=meta)
        self.state=workflows.empty();self.state['job_items']=[self.item]
        self.server=SimpleNamespace(STATE=self.state,WORK=self.root,JOB={'busy':False},SELECTION=None,progress=lambda _:None,save=lambda:None)
        self.original_hash=hashlib.sha256(path.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(Path(self.item['meta']['file']).read_bytes()).hexdigest(),self.original_hash)
        self.tmp.cleanup()

    def test_preview_select_delete_undo_redo_preserve_exact_registered_ids_and_masks(self):
        meta=self.item['meta'];seed=np.array([0,0,.025])@self.rotation.T+self.translation
        row=feature.preview(self.server,dict(source_id='setup',source_revision=registration_pose.revision(meta),mode='bolts',
            seed=seed.tolist(),view_direction=(self.rotation@np.array([0,0,1.])).tolist()))
        ids=np.fromfile(row['point_ids_file'],'<i8');labels=np.fromfile(row['labels_file'],'<i4')
        expected=np.flatnonzero(self.raised&(self.initial==0))
        np.testing.assert_array_equal(ids[labels>0],expected)
        np.testing.assert_array_equal(np.fromfile(row['points_file'],'<f8').reshape(-1,3),registration_pose.points(meta,self.points[ids]))
        self.assertEqual(row['statistics']['selected_points'],0)
        feature.select_groups(self.server,dict(preview_id=row['id'],revision=row['revision'],group_ids=[1]))
        self.assertEqual(row['statistics']['selected_points'],len(expected))
        feature.delete(self.server,dict(preview_id=row['id'],revision=row['revision'],confirmed=True))
        excluded=self.initial.copy();excluded[expected]=1
        np.testing.assert_array_equal(np.fromfile(meta['mask_file'],'u1'),excluded)
        scan_edits.apply(self.server,self.item,'undo_edit')
        np.testing.assert_array_equal(np.fromfile(meta['mask_file'],'u1'),self.initial)
        scan_edits.apply(self.server,self.item,'redo_edit')
        np.testing.assert_array_equal(np.fromfile(meta['mask_file'],'u1'),excluded)
        np.testing.assert_array_equal(registration_pose.get(meta)[0],self.rotation)

    def test_archived_and_stale_merged_sources_cannot_be_used(self):
        self.item['merge_stale']='Registration changed'
        with self.assertRaisesRegex(ValueError,'predates the current registration'):
            feature.source(self.server,'setup')
        self.item.pop('merge_stale');self.item['merge_archived']=True
        with self.assertRaisesRegex(ValueError,'archived'):
            feature.source(self.server,'setup')


if __name__=='__main__':unittest.main()
