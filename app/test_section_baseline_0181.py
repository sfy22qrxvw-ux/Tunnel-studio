"""Saved deformation references must retain comparable extraction/fit settings."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock
import copy
import hashlib
import math

import numpy as np

import section_research


class SectionBaselineTests(TestCase):
    def setUp(self):
        self.report=dict(radius=3.,width=.2,band=.5,chainage=10.,
                         centre=[100.,200.,3.],right=[1.,0.,0.],up=[0.,0.,1.],
                         excluded_points=0)
        baseline=section_research.canonical(0.,0.,3.,3.,0.)
        baseline.update(fit_threshold_mm=20.,orientation_defined=False)
        self.reference=dict(name='Epoch 1',fit=baseline,
                            plane={key:copy.deepcopy(self.report[key]) for key in
                                   ('centre','right','up','chainage','width')},
                            wall_band_m=.5,design_radius=3.,source_name='First survey')
        angles=np.linspace(0.,2*math.pi,7200,endpoint=False)+.0001
        self.points=np.column_stack((3*np.cos(angles),3*np.sin(angles)))

    def reject_before_fit(self,report=None,reference=None,threshold=20.,message='reference'):
        with mock.patch.object(section_research,'fit') as fit:
            with self.assertRaisesRegex(ValueError,message):
                section_research.evaluate(report or self.report,self.points,threshold,
                                          self.reference if reference is None else reference)
            fit.assert_not_called()

    def test_filter_and_fit_mismatches_rejected_before_fit(self):
        for field,value,label in (('band',.4,'wall band'),('radius',3.01,'design radius'),
                                  ('width',.1,'slice width')):
            with self.subTest(field=field):
                report={**self.report,field:value}
                self.reject_before_fit(report=report,message=label)
        self.reject_before_fit(threshold=10.,message='fit threshold')

    def test_plane_mismatch_rejected_before_fit(self):
        for field,value in (('centre',[100.001,200.,3.]),('right',[-1.,0.,0.]),
                            ('up',[0.,0.,-1.])):
            with self.subTest(field=field):
                self.reject_before_fit(report={**self.report,field:value},message='different section plane')

    def test_legacy_missing_settings_require_new_reference(self):
        for field in ('wall_band_m','design_radius','fit_threshold_mm','width'):
            with self.subTest(field=field):
                reference=copy.deepcopy(self.reference)
                owner=reference['fit'] if field=='fit_threshold_mm' else reference['plane'] if field=='width' else reference
                owner.pop(field)
                self.reject_before_fit(reference=reference,message='does not record.*Clear and recreate')

    def test_invalid_reference_values_never_start_fit(self):
        for field in ('wall_band_m','design_radius','fit_threshold_mm','width'):
            for value in (0.,-1.,float('nan'),float('inf'),'invalid',True):
                with self.subTest(field=field,value=value):
                    reference=copy.deepcopy(self.reference)
                    owner=reference['fit'] if field=='fit_threshold_mm' else reference['plane'] if field=='width' else reference
                    owner[field]=value
                    self.reject_before_fit(reference=reference,message='finite positive')

    def test_invalid_current_values_never_start_fit(self):
        for field in ('band','radius','width','fit_threshold'):
            for value in (0.,-1.,float('nan'),float('inf'),None,True):
                with self.subTest(field=field,value=value):
                    report=copy.deepcopy(self.report)
                    threshold=value if field=='fit_threshold' else 20.
                    if field!='fit_threshold':report[field]=value
                    self.reject_before_fit(report=report,threshold=threshold,message='finite positive')

    def test_malformed_legacy_reference_or_plane_never_starts_fit(self):
        for reference in ({}, {'plane':{}}, {'fit':{}}, 'legacy'):
            with self.subTest(reference=reference):
                self.reject_before_fit(reference=reference,message='[Cc]lear and recreate')
        for value in (None,[0.,0.],[0.,0.,float('nan')]):
            reference=copy.deepcopy(self.reference);reference['plane']['centre']=value
            self.reject_before_fit(reference=reference,message='plane is incomplete or invalid')

    def test_matching_different_epoch_preserves_real_translation_and_source(self):
        # Different files/masks are expected between surveys, and must not be
        # rejected or aligned until the displacement vanishes.
        translation=np.array([.015,-.007])
        measured=self.points+translation
        report={**self.report,'source_id':'epoch-2','mask_file':'epoch-2.mask'}
        reference={**self.reference,'source_id':'epoch-1','mask_file':'epoch-1.mask'}
        with TemporaryDirectory() as folder:
            path=Path(folder)/'measured.xy64';measured.astype('<f8').tofile(path)
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            points=np.memmap(path,dtype='<f8',mode='r',shape=measured.shape)
            try:
                result=section_research.evaluate(report,points,20.,reference)
            finally:points._mmap.close()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        comparison=result['comparison']
        self.assertAlmostEqual(comparison['centre_right_mm'],15.,delta=.05)
        self.assertAlmostEqual(comparison['centre_up_mm'],-7.,delta=.05)
        self.assertAlmostEqual(comparison['centre_displacement_mm'],math.hypot(15.,7.),delta=.05)
        self.assertAlmostEqual(comparison['semi_major_change_mm'],0.,delta=.05)
        self.assertAlmostEqual(comparison['semi_minor_change_mm'],0.,delta=.05)
        self.assertGreater(comparison['radial_max_mm'],16.)
        self.assertLess(comparison['radial_min_mm'],-16.)
        self.assertEqual(result['fit']['input_points'],len(measured))

    def test_matching_saved_reference_and_numeric_round_trip(self):
        report={**self.report,'band':.5+1e-12,'radius':3.+1e-12}
        result=section_research.evaluate(report,self.points,'20',self.reference)
        self.assertEqual(result['reference_kind'],'Saved section: Epoch 1')
        self.assertLess(result['comparison']['centre_displacement_mm'],.01)

    def test_circular_design_without_saved_reference_still_works(self):
        result=section_research.evaluate(self.report,self.points,20.,None)
        self.assertEqual(result['reference_kind'],'Circular design')
        self.assertFalse(result['reference']['orientation_defined'])


if __name__=='__main__':main()
