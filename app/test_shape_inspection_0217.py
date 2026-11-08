"""Full-density pocket inspection is read-only, bounded and revision checked."""
from pathlib import Path
import copy,hashlib,json,sqlite3,struct,unittest
from unittest import mock
import numpy as np
import shape_inspection as inspect
import shape_selection
import shape_matching
import workspace_history,projects,registration_pose,storage_inventory
import test_shape_matching_0214 as fixture


def decoded(data):
    magic,version,count,length,flags,reserved=struct.unpack_from('<4s5I',data)
    assert magic==b'TSDP' and version==1 and flags==0 and reserved==0
    meta=json.loads(data[24:24+length]);offset=24+(length+7)//8*8
    return meta,np.frombuffer(data,'<f4',count=count*6,offset=offset).reshape(-1,6).copy()


class ShapeInspectionTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.ShapeReviewTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.s=self.fx.s;self.row=self.fx.row;self.item=self.fx.item;self.p=self.fx.p
        self.s.save=lambda:projects.atomic_json(self.s.WORK/'project.json',self.s.STATE)
        self.s.save()

    def body(self,gid=1):return dict(action='inspect',preview_id=self.row['id'],revision=self.row['revision'],group_id=gid)

    def page(self,meta,**args):
        class Handler:
            def send(h,data,*a):h.data=data
        h=Handler();inspect.get(self.s,h,'/api/shape-inspection',{k:[str(v)] for k,v in {'inspection':meta['id'],'part':'object','start':0,'size':100000,**args}.items()})
        return decoded(h.data)

    def test_inspect_uses_original_world_points_without_changing_selection_or_project(self):
        before=copy.deepcopy(self.s.STATE);flags=Path(self.row['selected_file']).read_bytes();revision=self.row['revision']
        m=inspect.prepare(self.s,self.body());meta,packed=self.page(m)
        np.testing.assert_allclose(packed[:,:3]+meta['origin'],self.p[[4,9]]+[1000,0,0],atol=1e-7)
        self.assertEqual(m['original_points'],2);self.assertEqual(m['next'],2);self.assertIsNone(m['previous']);self.assertTrue(meta['complete'])
        self.assertEqual(self.s.STATE,before);self.assertEqual(Path(self.row['selected_file']).read_bytes(),flags);self.assertEqual(self.row['revision'],revision)

    def test_cache_reuse_and_neighbour_navigation_do_not_reread_full_source(self):
        a=inspect.prepare(self.s,self.body());b=inspect.prepare(self.s,self.body(2))
        with mock.patch.object(inspect.shape_examples,'context',side_effect=AssertionError('cache was not reused')):
            again=inspect.prepare(self.s,self.body())
        self.assertEqual(a['id'],again['id']);self.assertNotEqual(a['id'],b['id']);self.assertEqual(b['previous'],1);self.assertIsNone(b['next'])

    def test_invalid_pages_groups_and_source_revisions_are_refused(self):
        for gid in (0,-1,True,'1',99):
            with self.assertRaises(ValueError):inspect.prepare(self.s,self.body(gid))
        m=inspect.prepare(self.s,self.body())
        for args in ({'start':-1},{'start':3},{'size':0},{'size':100001},{'part':'source'}):
            with self.assertRaises(ValueError):self.page(m,**args)
        self.row['revision']='changed'
        with self.assertRaisesRegex(ValueError,'out of date'):self.page(m)

    def test_cancellation_keeps_the_previous_completed_pocket(self):
        first=inspect.prepare(self.s,self.body());before=copy.deepcopy(self.s.STATE)
        def stop(_):raise ValueError('stopped')
        with mock.patch.object(self.s,'progress',stop):
            with self.assertRaisesRegex(ValueError,'stopped'):inspect.prepare(self.s,self.body(2))
        self.assertEqual(self.row['inspection_id'],first['id']);self.assertEqual(len(list(Path(self.row['folder']).glob('inspect-*'))),1);self.assertEqual(self.s.STATE,before)
        self.assertEqual(self.page(first)[0]['group_id'],1)

    def test_intensity_toggle_only_changes_display_colours(self):
        m=inspect.prepare(self.s,self.body());a,orange=self.page(m);b,grey=self.page(m,highlight=0)
        np.testing.assert_array_equal(orange[:,:3],grey[:,:3]);self.assertFalse(np.array_equal(orange[:,3:],grey[:,3:]));np.testing.assert_array_equal(grey[:,3],grey[:,4]);np.testing.assert_array_equal(grey[:,4],grey[:,5])

    def test_missing_intensity_has_an_honest_grey_fallback(self):
        self.item['meta'].pop('attributes_file');self.row['source_fingerprint']=shape_selection.feature_selection.fingerprint(self.item)
        m=inspect.prepare(self.s,self.body());self.assertFalse(m['has_intensity']);meta,p=self.page(m,highlight=0);self.assertTrue(np.isfinite(p).all())

    def test_mask_or_pose_change_invalidates_inspection(self):
        m=inspect.prepare(self.s,self.body());registration_pose.set_pose(self.item['meta'],np.eye(3),[2000,0,0])
        with self.assertRaisesRegex(ValueError,'source changed'):self.page(m)

    def test_more_than_a_million_pocket_points_are_paged_without_sampling_or_large_reads(self):
        n=1_100_003;root=self.fx.fx.root
        raw=np.empty((n,3),'<f8');raw[:,0]=1e8+np.arange(n)%1000*.0001;raw[:,1]=2e8+(np.arange(n)//1000%1000)*.0001;raw[:,2]=3.
        f=root/'dense.xyz64';raw.tofile(f);a=root/'dense.attr32';np.column_stack([np.arange(n)%256,np.ones((n,3))]).astype('<f4').tofile(a)
        self.item['meta'].update(file=str(f),attributes_file=str(a),count=n,minimum=raw.min(0).tolist(),maximum=raw.max(0).tolist());registration_pose.set_pose(self.item['meta'],np.eye(3),[0,0,0])
        np.arange(n,dtype='<i8').tofile(self.row['point_ids_file']);np.ones(n,'<i4').tofile(self.row['labels_file']);np.ones(n,'u1').tofile(self.row['selected_file'])
        self.row.update(count=n,source_fingerprint=shape_selection.feature_selection.fingerprint(self.item));self.row['statistics'].update(input_points=n,selected_points=n,candidate_points=n)
        db=sqlite3.connect(self.row['review_file']);db.execute('DELETE FROM matches');db.execute('INSERT INTO matches VALUES(1,0,0,1,1,?,?)',(n,n));db.commit();db.close()
        sizes=[];read=inspect.RowFile.read
        def bounded(reader,start,size):sizes.append(size);return read(reader,start,size)
        before=hashlib.sha256(f.read_bytes()).hexdigest()
        with mock.patch.object(inspect.RowFile,'read',bounded),mock.patch.object(inspect.shape_examples,'context',return_value=np.empty((0,3))):
            m=inspect.prepare(self.s,self.body());seen=0
            for start in range(0,n,100000):
                meta,p=self.page(m,start=start);np.testing.assert_allclose(p[:,:3]+meta['origin'],raw[start:start+len(p)],atol=1e-7,rtol=0);seen+=len(p)
        self.assertEqual(seen,n);self.assertLessEqual(max(sizes),100000);self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),before)
        self.assertEqual(np.fromfile(self.row['inspections'][0]['point_ids_file'],'<i8')[-1],n-1)

    def test_64_bit_original_identities_are_not_truncated(self):
        base=2**31;meta=self.item['meta'];f=self.fx.fx.root/'sparse.xyz64';count=base+31
        with f.open('wb') as out:
            out.truncate(count*24)
            for ident in self.fx.ids:
                out.seek((base+int(ident))*24);np.array([float(ident),.1,.2],'<f8').tofile(out)
        meta.update(file=str(f),count=count);meta.pop('attributes_file');(self.fx.ids+base).tofile(self.row['point_ids_file']);self.row['source_fingerprint']=shape_selection.feature_selection.fingerprint(self.item)
        with mock.patch.object(inspect.shape_examples,'context',return_value=np.empty((0,3))):m=inspect.prepare(self.s,self.body())
        ids=np.fromfile(self.row['inspections'][0]['point_ids_file'],'<i8');np.testing.assert_array_equal(ids,np.array([4,9],'<i8')+base)
        np.testing.assert_allclose(self.page(m)[1][:,:3]+m['origin'],[[1004,.1,.2],[1009,.1,.2]],atol=1e-7)

    def test_current_detail_is_protected_and_abandoned_detail_is_identified_for_cleanup(self):
        inspect.prepare(self.s,self.body());entry=self.row['inspections'][0]
        protected=projects._local_asset_paths(self.s.WORK,workspace_history.storage_state(self.s))
        self.assertTrue({Path(entry[k]).resolve() for k in ('file','point_ids_file','mask_file','attributes_file')}.issubset(protected))
        folder=self.s.WORK/'Analysis'/('shape-'+'a'*32)/('inspect-'+'b'*32);folder.mkdir(parents=True)
        f=folder/'object.xyz64';f.write_bytes(b'')
        self.assertTrue(storage_inventory.owned_name(self.s.WORK,f));self.assertFalse(storage_inventory.owned_name(self.s.WORK,folder/'user.e57'))

    def test_inspection_is_not_a_workspace_undo_checkpoint(self):
        with workspace_history.request(self.s,'/api/shape-selection'):
            workspace_history.configure('/api/shape-selection',self.body());inspect.prepare(self.s,self.body())
        self.assertEqual(workspace_history.status(self.s)['undo_count'],0)


if __name__=='__main__':unittest.main()
