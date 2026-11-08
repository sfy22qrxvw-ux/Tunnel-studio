"""Per-scan display styles preserve measured data, geometry identity and Undo."""
from pathlib import Path
import copy,json,struct,unittest
from unittest import mock
import numpy as np
import detail_preview,feature_selection,jobdata,pointio,scan_appearance,scan_export,scene_objects,workspace_history
import test_deleted_points_0213 as fixture


class ScanAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.DeletedPointAreaTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.s=self.fx.s;self.a,_=self.fx.scan('tunnel',n=37);self.b,_=self.fx.scan('pockets',n=43,shift=50)
        self.files={p:Path(p).read_bytes() for item in (self.a,self.b) for p in (item['meta']['file'],item['meta']['attributes_file'])}

    def apply(self,item,mode='solid',colour='#e14a92'):
        return scan_appearance.apply(self.s,dict(id=item['id'],mode=mode,colour=colour))

    def test_independent_styles_persist_without_changing_source_or_analytical_identity(self):
        before=copy.deepcopy(self.b);signatures=[f(self.a) for f in (scan_export.signature,feature_selection.fingerprint,scene_objects.source_revision)]
        self.apply(self.a,colour='#FF7043')
        self.assertEqual(self.a['display_colour'],'#ff7043');self.assertEqual(self.b,before)
        self.assertEqual(signatures,[f(self.a) for f in (scan_export.signature,feature_selection.fingerprint,scene_objects.source_revision)])
        self.apply(self.b,'intensity-gray')
        saved=json.loads((self.fx.root/'saved.json').read_text())['job_items']
        self.assertEqual(saved[0]['display_mode'],'solid');self.assertEqual(saved[1]['display_mode'],'intensity-gray')
        self.assertEqual(scan_appearance.resolve(saved[0],[1,1,1],'rgb'),([1.,112/255,67/255],'bundle'))
        for path,data in self.files.items():self.assertEqual(Path(path).read_bytes(),data)

    def test_style_has_one_undo_redo_and_global_reset(self):
        with workspace_history.request(self.s,'/api/scan-appearance'):
            workspace_history.configure('/api/scan-appearance',{'action':'set'});self.apply(self.a)
        self.assertEqual(workspace_history.status(self.s)['undo_label'],'Change scan colour')
        workspace_history.apply(self.s);self.assertNotIn('display_mode',self.s.STATE['job_items'][0])
        workspace_history.apply(self.s,redo=True);self.assertEqual(self.s.STATE['job_items'][0]['display_colour'],'#e14a92')
        scan_appearance.apply(self.s,dict(id='tunnel',mode='global'))
        item=self.s.STATE['job_items'][0];self.assertNotIn('display_mode',item);self.assertNotIn('display_colour',item)

    def test_invalid_style_busy_wrong_item_and_failed_save_preserve_project(self):
        before=copy.deepcopy(self.s.STATE)
        for args in ({'id':'missing','mode':'solid','colour':'#ff0000'},{'id':'tunnel','mode':[]},{'id':'tunnel','mode':'solid','colour':'red'},{'id':'tunnel','mode':'solid','colour':'#123456;background:red'}):
            with self.assertRaises(ValueError):scan_appearance.apply(self.s,args)
            self.assertEqual(self.s.STATE,before)
        self.s.JOB['busy']=True
        with self.assertRaisesRegex(ValueError,'Wait'):self.apply(self.a)
        self.s.JOB['busy']=False
        with mock.patch.object(self.s,'save',side_effect=OSError('save failed')):
            with self.assertRaisesRegex(OSError,'save failed'):self.apply(self.a)
        self.assertEqual(self.s.STATE,before)

    def test_solid_colour_reaches_legacy_binary_and_detail_previews_only_for_chosen_scan(self):
        self.apply(self.a,colour='#ff3300');self.apply(self.b,colour='#00ccff')
        with mock.patch.object(pointio,'arrays',side_effect=AssertionError('whole-cloud mapping')):
            for item,colour in ((self.a,[1,.2,0]),(self.b,[0,.8,1])):
                for mode in ('rgb','intensity-gray','elevation'):
                    packed=jobdata._item_sample(item,100,np.zeros(3),[.5]*3,mode)
                    np.testing.assert_allclose(packed[:,3:],np.tile(colour,(len(packed),1)),atol=1e-7)
                view=dict(target=[18,0,0],right=[1,0,0],up=[0,1,0],forward=[0,0,1],scale=100,aspect=1)
                detail=detail_preview.sample(item,100,np.zeros(3),[.5]*3,'rgb',(0,1),view)
                self.assertGreater(len(detail),0);np.testing.assert_allclose(detail[:,3:],np.tile(colour,(len(detail),1)),atol=1e-7)
            legacy=np.asarray(jobdata.preview([self.a,self.b])['points'])
            np.testing.assert_allclose(legacy[:37,3:],np.tile([1,.2,0],(37,1)),atol=1e-7)
            data,meta=jobdata.preview_binary([self.a,self.b],detail=100,mode='intensity-gray',owners=True)
            packed=np.frombuffer(data,'<f4',count=meta['sample_count']*6,offset=struct.calcsize('<4sI3d3d')).reshape(-1,6)
            ranges=json.loads(data[struct.calcsize('<4sI3d3d')+packed.nbytes:])
            for start,end,ident in ranges:
                colour=[1,.2,0] if ident=='tunnel' else [0,.8,1]
                self.assertGreater(end,start);np.testing.assert_allclose(packed[start:end,3:],np.tile(colour,(end-start,1)),atol=1e-7)
        for path,data in self.files.items():self.assertEqual(Path(path).read_bytes(),data)

    def test_recolour_invalidates_only_colour_cache_and_measured_modes_remain_available(self):
        self.apply(self.a,colour='#ff0000');a=jobdata._item_sample(self.a,100,np.zeros(3),[.5]*3)
        self.apply(self.a,colour='#0000ff');b=jobdata._item_sample(self.a,100,np.zeros(3),[.5]*3)
        np.testing.assert_array_equal(a[:,:3],b[:,:3]);self.assertFalse(np.array_equal(a[:,3:],b[:,3:]))
        self.apply(self.a,'intensity-gray');grey=jobdata._item_sample(self.a,100,np.zeros(3),[.5]*3)
        np.testing.assert_array_equal(grey[:,3],grey[:,4]);self.assertGreater(np.ptp(grey[:,3]),.5)
        self.apply(self.a,'rgb');rgb=jobdata._item_sample(self.a,100,np.zeros(3),[.5]*3)
        np.testing.assert_allclose(rgb[:,3:],1.)

    def test_corrupt_saved_style_and_non_scan_objects_use_existing_fallback(self):
        for value in (None,[],{},42,'unsupported'):
            self.a['display_mode']=value
            self.assertEqual(scan_appearance.resolve(self.a,[.2,.3,.4],'rgb'),([.2,.3,.4],'rgb'))
        self.a['display_mode']='solid';self.a['display_colour']='invalid'
        self.assertEqual(scan_appearance.resolve(self.a,[.2,.3,.4],'intensity-gray'),([.2,.3,.4],'intensity-gray'))
        self.assertEqual(scan_appearance.resolve({'kind':'mesh','display_mode':'solid','display_colour':'#ff0000'},[1,1,1],'rgb'),([1,1,1],'rgb'))


if __name__=='__main__':unittest.main()
