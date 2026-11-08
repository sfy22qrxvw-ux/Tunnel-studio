"""Both-sided band selection and exact reversible separation of original IDs."""
from pathlib import Path
import hashlib,tempfile,unittest
import numpy as np
import lining_departures as ld
import feature_selection as feature
import scan_edits,workspace_history
import test_deleted_points_0213 as restore_fixture
import test_bolt_scope_0213 as bolt_fixture


class LiningDepartureTests(unittest.TestCase):
    def test_local_band_has_no_diameter_or_isolated_point_filter(self):
        x,y=np.meshgrid(np.arange(-.5,.501,.01),np.arange(-.5,.501,.01));xy=np.c_[x.ravel(),y.ravel()]
        z=.02*xy[:,0]**2;low=(xy[:,0]>-.4)&(xy[:,0]<-.05)&(abs(xy[:,1])<.10)
        high=(xy[:,0]>.20)&(xy[:,0]<.30)&(abs(xy[:,1])<.05)
        z[low]-=.04;z[high]+=.02;isolated=np.argmax(xy[:,0]+xy[:,1]);z[isolated]+=.012
        p=np.c_[xy,z];cfg=feature.settings({'feature_kind':'departures','min_height_mm':5})
        labels,groups,stats,warnings=ld.local_labels(p,np.zeros(3),[0,0,1],cfg,lambda _:None)
        expected=np.zeros(len(p),int);expected[low]=2;expected[high]=1;expected[isolated]=1
        np.testing.assert_array_equal(labels,expected);self.assertLess(stats['reference_rms_mm'],.001)
        self.assertEqual({g['id'] for g in groups},{1,2});self.assertEqual(labels[isolated],1)

    def test_whole_stream_classifies_both_sides_above_one_million_ids(self):
        m,p,truth=bolt_fixture.BoltScopeTests().scene();repeat=6;total=len(p)*repeat
        def batches():
            for k in range(repeat):
                for start in range(0,len(p),100000):
                    q=p[start:start+100000];yield np.arange(k*len(p)+start,k*len(p)+start+len(q),dtype='<i8'),q,{'mode':'full'}
        with tempfile.TemporaryDirectory() as tmp:
            ids=Path(tmp)/'ids';labs=Path(tmp)/'labels'
            r=ld.run_batches(batches,ids,labs,{'lining_depth_mm':5},m,lambda _:None,lambda:False,total)
            self.assertGreater(total,1000000);self.assertEqual(r['statistics']['input_points'],total)
            labels=np.fromfile(labs,'<i4');self.assertTrue((labels==1).any());self.assertTrue((labels==2).any())
            self.assertEqual(sum(c['points'] for c in r['clusters']),total)
            for k in range(1,repeat):np.testing.assert_array_equal(labels[:len(p)],labels[k*len(p):(k+1)*len(p)])
            self.assertTrue(all(c['departure_candidate'] for c in r['clusters'] if c['id'] in (1,2)))

    def test_exact_selected_file_separation_preserves_coordinates_and_undo(self):
        fx=restore_fixture.DeletedPointAreaTests();fx.setUp();self.addCleanup(fx.doCleanups);item,p=fx.scan('posed',shift=1000)
        selected=np.array([4,9,25],'<i8');path=fx.root/'chosen.patch_ids';selected.tofile(path)
        digest=hashlib.sha256(Path(item['meta']['file']).read_bytes()).hexdigest()
        with workspace_history.request(fx.s,'/api/job-action'):
            workspace_history.configure('/api/job-action',{'action':'separate_selection'})
            scan_edits.apply(fx.s,item,'separate_selection',selected_file={'file':str(path),'count':3})
        child=fx.s.STATE['job_items'][-1];self.assertEqual(child['meta']['count'],3)
        np.testing.assert_array_equal(np.fromfile(child['meta']['file'],'<f8').reshape(-1,3),p[selected])
        self.assertEqual(child['meta']['registration_translation'],[1000,0,0]);self.assertEqual(np.fromfile(item['meta']['mask_file'],'u1').sum(),3)
        self.assertEqual(hashlib.sha256(Path(item['meta']['file']).read_bytes()).hexdigest(),digest)
        workspace_history.apply(fx.s);self.assertEqual(len(fx.s.STATE['job_items']),1)
        workspace_history.apply(fx.s,redo=True);self.assertEqual(len(fx.s.STATE['job_items']),2)

if __name__=='__main__':unittest.main()
