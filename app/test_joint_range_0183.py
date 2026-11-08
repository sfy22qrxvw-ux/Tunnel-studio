"""Disk/window joint evidence against an independent complete-array oracle."""
import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

import joint_detection
from core import Alignment,suggest_joints


def reference(rows,mask,start,end,bin_width,pitch,threshold,band):
    """The original full-array detector equations, without a bin-count guard."""
    bins=math.ceil((end-start)/bin_width)
    valid=(mask==0)&np.isfinite(rows).all(1)&(rows[:,0]>=start)&(rows[:,0]<end)&(abs(rows[:,3])<=band)
    q=rows[valid]
    si=np.floor((q[:,0]-start)/bin_width).astype(int)
    sector=np.floor(np.mod(np.arctan2(q[:,2],q[:,1]),2*np.pi)*24/(2*np.pi)).astype(int)
    keys=si*24+sector
    counts=np.bincount(keys,minlength=bins*24).reshape(bins,24)
    sums=np.bincount(keys,weights=q[:,3]*1000,minlength=bins*24).reshape(bins,24)
    means=np.divide(sums,counts,out=np.full_like(sums,np.nan),where=counts>0)
    detrended=np.full_like(means,np.nan)
    width=max(5,int(min(pitch*.45,.6)/bin_width)//2*2+1)
    for sector in range(24):
        known=np.flatnonzero(np.isfinite(means[:,sector]))
        if len(known)<5:continue
        interpolated=np.interp(np.arange(bins),known,means[known,sector])
        baseline=median_filter(interpolated,size=width,mode='nearest')
        detrended[known,sector]=means[known,sector]-baseline[known]
    observed=np.isfinite(detrended).sum(1)
    positive=(detrended>=threshold).sum(1);negative=(detrended<=-threshold).sum(1)
    support=np.maximum(positive,negative);score=np.zeros(bins)
    for i in np.flatnonzero((observed>=16)&(support>=12)):
        values=detrended[i,np.isfinite(detrended[i])];score[i]=abs(float(np.median(values)))
    peaks,_=find_peaks(score,height=threshold,distance=max(1,int(pitch*.45/bin_width)),prominence=threshold*.4)
    stride=max(1,math.ceil(bins/2000));indices=np.arange(0,bins,stride)
    return dict(peaks=peaks,score=score,observed=observed,support=support,positive=positive,negative=negative,
                included=int(valid.sum()),signal=np.c_[start+(indices+.5)*bin_width,score[indices]])


class JointRange0183Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.start=100.;self.bin_width=.02;self.pitch=1.8;self.threshold=3.;self.band=.3

    def tearDown(self):self.temp.cleanup()

    def fixture(self,bins):
        index=np.repeat(np.arange(bins),24);sector=np.tile(np.arange(24),bins)
        angle=(sector+.5)*2*np.pi/24
        delta=.0007*np.sin(np.arange(bins)*.009)
        targets=[18,127,256,401,509,900,32767,32825,bins-20]
        for number,target in enumerate(targets):
            if 3<target<bins-3:delta[target]+=(-1 if number%2 else 1)*.012
        rows=np.c_[self.start+(index+.5)*self.bin_width,3.5*np.cos(angle),3.5*np.sin(angle),delta[index]]
        mask=np.zeros(len(rows),'u1')
        # Long gaps across prospective window boundaries must not be treated
        # as measured seams; a one-sided window cannot interpolate them alone.
        mask[(index>=144)&(index<=240)]=1
        mask[(index>=880)&(index<=920)&(sector<10)]=1
        mask[(index==401)&(sector>=20)]=1
        order=np.random.default_rng(42).permutation(len(rows));rows=rows[order];mask=mask[order]
        derived=self.root/'projected.analysis64';rows.astype('<f8').tofile(derived)
        mp=self.root/'source.mask';mask.tofile(mp)
        meta=dict(count=len(rows),mask_file=str(mp))
        end=self.start+bins*self.bin_width
        axis=Alignment([dict(chainage=s,e=0.,n=s,h=0.,radius=3.5) for s in (self.start,end+1.)])
        return rows,mask,meta,derived,axis,end

    def check(self,result,expected):
        peaks=expected['peaks']
        np.testing.assert_allclose([r['chainage'] for r in result['candidates']],self.start+(peaks+.5)*self.bin_width,rtol=0,atol=1e-12)
        np.testing.assert_allclose([r['signal_mm'] for r in result['candidates']],expected['score'][peaks],rtol=0,atol=1e-12)
        self.assertEqual([r['supported_sectors'] for r in result['candidates']],expected['support'][peaks].tolist())
        self.assertEqual([r['observed_sectors'] for r in result['candidates']],expected['observed'][peaks].tolist())
        self.assertEqual([r['type'] for r in result['candidates']],['Recess' if expected['positive'][i]>=expected['negative'][i] else 'Ridge' for i in peaks])
        np.testing.assert_allclose(result['signal'],expected['signal'],rtol=0,atol=1e-12)
        self.assertEqual(result['eligible_bins'],int((expected['observed']>=16).sum()))
        self.assertEqual(result['retained_points_in_scope'],expected['included'])
        self.assertEqual(result['bin_width'],self.bin_width)
        self.assertTrue(result['chainage_bin_width_preserved'])

    def test_full_range_beyond_old_20000_bins_preserves_resolution_and_detected_peaks(self):
        rows,mask,meta,derived,axis,end=self.fixture(33011)
        expected=reference(rows,mask,self.start,end,self.bin_width,self.pitch,self.threshold,self.band)
        original=hashlib.sha256(derived.read_bytes()).hexdigest()
        read=np.fromfile;reads=[];histogram=np.bincount;histograms=[]
        def owned_read(*args,**kwargs):
            reads.append(kwargs.get('count',-1));return read(*args,**kwargs)
        def bounded_histogram(*args,**kwargs):
            result=histogram(*args,**kwargs);histograms.append(len(result));return result
        with patch.object(joint_detection.np,'fromfile',side_effect=owned_read),patch.object(joint_detection.np,'bincount',side_effect=bounded_histogram):
            result=suggest_joints(meta,derived,axis,self.start,end,self.pitch,self.bin_width,self.threshold,self.band)
        self.check(result,expected)
        self.assertGreater(result['total_bins'],20000)
        self.assertEqual((result['start'],result['end']),(self.start,end))
        self.assertLessEqual(max(reads),4*joint_detection.POINT_BLOCK)
        self.assertTrue(all(0<count<=joint_detection.POINT_BLOCK for count in histograms))
        self.assertEqual(hashlib.sha256(derived.read_bytes()).hexdigest(),original)
        np.testing.assert_array_equal(np.fromfile(meta['mask_file'],'u1'),mask)

    def test_window_halos_and_global_gap_interpolation_equal_complete_profile(self):
        rows,mask,meta,derived,axis,end=self.fixture(1027)
        expected=reference(rows,mask,self.start,end,self.bin_width,self.pitch,self.threshold,self.band)
        with patch.object(joint_detection,'BIN_BLOCK',73),patch.object(joint_detection,'POINT_BLOCK',4093):
            result=suggest_joints(meta,derived,axis,self.start,end,self.pitch,self.bin_width,self.threshold,self.band)
        self.check(result,expected)

    def test_cancel_closes_temporary_maps_and_leaves_sources_unchanged(self):
        rows,mask,meta,derived,axis,end=self.fixture(1027)
        original=derived.read_bytes();before=set(self.root.iterdir());created=[]
        temporary=tempfile.TemporaryDirectory
        def scratch(*args,**kwargs):
            result=temporary(*args,dir=self.root,**kwargs);created.append(result.name);return result
        def cancel(message):
            if message.startswith('Fitting joint sector 2/'):
                raise ValueError('cancelled during median baselines')
        with patch.object(joint_detection.tempfile,'TemporaryDirectory',side_effect=scratch):
            with self.assertRaisesRegex(ValueError,'cancelled'):
                suggest_joints(meta,derived,axis,self.start,end,self.pitch,self.bin_width,self.threshold,self.band,progress=cancel)
        self.assertTrue(created);self.assertTrue(all(not Path(p).exists() for p in created))
        self.assertEqual(set(self.root.iterdir()),before)
        self.assertEqual(derived.read_bytes(),original)
        np.testing.assert_array_equal(np.fromfile(meta['mask_file'],'u1'),mask)


if __name__=='__main__':unittest.main()
