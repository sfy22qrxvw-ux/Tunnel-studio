"""Physical Project Explorer deletion: ownership, rollback and reference-only edits.

All files are small isolated fixtures. These tests never use user survey data.
"""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

import cad_api
import explorer_deletion
import job_api
import preview_store
import projects
import scan_edits
import scene_objects
import workflows
import workspace_history
import workspace_tools


class ExplorerDeletion0183Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'workspace';self.root.mkdir()
        self.s=SimpleNamespace(WORK=self.root,STATE=workflows.empty(),JOB={'busy':False},
            WORKSPACE_HISTORY={'undo':[],'redo':[]},SELECTION=None,FEATURE_SELECTION=None,
            HIGHLIGHT_STATS=None,GEOMETRY_PREVIEW=None,progress=lambda message:None)
        self.s.STATE.update(job_items=[],scene_objects=[],scan_links=[],cad=cad_api.empty())
        self.s.save=lambda:projects.atomic_json(self.root/'project.json',workflows.serial(self.s))
        self.s.save()

    def tearDown(self):
        self.finish_purges()
        self.temp.cleanup()

    def finish_purges(self):
        # Delete may return after same-volume quarantine. Run queued erasure
        # synchronously for deterministic byte assertions, without sleeping.
        folder=self.root/projects._PURGE_ROOT
        if folder.is_dir():
            for child in list(folder.iterdir()):
                if child.is_dir():projects._purge_stage(child)

    def file(self,name,data):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data);return str(path)

    def scan(self,name='Scan',offset=0.,count=24):
        stem=hashlib.md5(name.encode()).hexdigest()
        points=np.c_[np.arange(count,dtype=float)+offset,np.zeros(count),np.ones(count)]
        xyz=self.file(stem+'.xyz64',points.astype('<f8').tobytes())
        attrs=self.file(stem+'.attr32',np.full((count,4),.5,dtype='<f4').tobytes())
        mask=self.file(stem+'.mask',bytes(count))
        preview=self.file(stem+'.preview64',points[::3].astype('<f8').tobytes())
        meta=dict(name=name,file=xyz,count=count,minimum=points.min(0).tolist(),maximum=points.max(0).tolist(),
                  attributes_file=attrs,mask_file=mask,preview_file=preview,preview_count=len(points[::3]),preview_stride=3,
                  attribute_counts=[count]*4,attribute_min=[.5]*4,attribute_max=[.5]*4)
        row=dict(id=stem,kind='scan',name=name,meta=meta,visible=True,source_bytes=0,
                 hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[])
        self.s.STATE['job_items'].append(row)
        return row

    def mesh(self,name='Mesh'):
        stem=hashlib.md5(name.encode()).hexdigest()
        vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],dtype='<f8')
        faces=np.array([[0,1,2],[0,1,3]],dtype='<u4')
        meta=dict(name=name,vertices_file=self.file(stem+'.vertices64',vertices.tobytes()),
                  faces_file=self.file(stem+'.faces32',faces.tobytes()),vertices=4,triangles=2,
                  vertex_colours_file=self.file(stem+'.vcolour32',np.ones((4,3),dtype='<f4').tobytes()),
                  face_flags_file=self.file(stem+'.face_flags',b'\0\0'),
                  minimum=vertices.min(0).tolist(),maximum=vertices.max(0).tolist())
        row=dict(id=stem,kind='mesh',name=name,meta=meta,visible=True,source_bytes=0)
        self.s.STATE['job_items'].append(row)
        return row

    def cad(self,name='Drawn line',bundle_id=None):
        row=dict(id='cad:'+hashlib.md5(name.encode()).hexdigest(),shape='polyline',name=name,
                 vertices=[[0.,0.,0.],[1.,0.,0.]],faces=[],closed=False,solid=False,
                 minimum=[0.,0.,0.],maximum=[1.,0.,0.],area_m2=0.,volume_m3=0.,
                 visible=True,layer='Model',colour='#62bdcf',bundle_id=bundle_id)
        self.s.STATE['cad']['objects'].append(row)
        return row

    def saved_current(self):
        saved=projects.save_named(self.root,workflows.serial(self.s),'Saved tunnel',True)
        self.s.STATE.update({key:saved[key] for key in ('project_id','project_title','saved_at')})
        self.s.save();return self.root/'projects'/(saved['project_id']+'.json')

    def recovery(self,state=None):
        folder=self.root/'startup_recovery';folder.mkdir(exist_ok=True)
        path=folder/'workspace_current.json'
        projects.atomic_json(path,copy.deepcopy(state) if state is not None else workflows.serial(self.s))
        return path

    def review(self,ids=(),bundle_ids=()):
        return explorer_deletion.review(self.s,dict(ids=list(ids),bundle_ids=list(bundle_ids)))

    def commit(self,ids=(),bundle_ids=(),review=None,history=False):
        result=review if review is not None else self.review(ids,bundle_ids)
        body=dict(ids=list(ids),bundle_ids=list(bundle_ids),revision=result['revision'],confirmed=True)
        # Suppress the daemon race; finish_purges executes precisely the same
        # erasure path after the transaction publishes its result.
        with mock.patch.object(projects,'_schedule_purge'):
            if history:
                with workspace_history.request(self.s,'/api/explorer-delete'):
                    workspace_history.configure('/api/explorer-delete',body)
                    out=explorer_deletion.delete(self.s,body)
            else:out=explorer_deletion.delete(self.s,body)
        self.finish_purges()
        return out

    def disk_snapshot(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def owned(self,row):
        return projects._local_asset_paths(self.root,row)

    def test_scan_and_mesh_delete_reclaims_all_exclusive_cache_bytes(self):
        scan=self.scan();mesh=self.mesh();keep=self.scan('Keep',100.)
        index=Path(scan['meta']['file']+'.viewindex.npz');index.write_bytes(b'index content')
        overview=preview_store.overview_path(scan['meta']);overview.parent.mkdir(exist_ok=True)
        overview.write_bytes(b'disposable measured overview')
        owned=self.owned(scan)|self.owned(mesh)|{overview}
        keep_bytes={p:p.read_bytes() for p in self.owned(keep)}
        expected=sum(p.stat().st_size for p in owned)
        self.s.save();before=self.review([scan['id'],mesh['id']])
        self.assertEqual(before['item_count'],2)
        self.assertEqual(before['exclusive_bytes'],expected)
        self.assertEqual(before['shared_bytes'],0)
        out=self.commit([scan['id'],mesh['id']],review=before)
        self.assertEqual(out['removed_count'],2)
        self.assertEqual([x['id'] for x in self.s.STATE['job_items']],[keep['id']])
        self.assertTrue(all(not p.exists() for p in owned))
        self.assertEqual({p:p.read_bytes() for p in keep_bytes},keep_bytes)
        self.assertFalse(projects.storage_purge_status(self.root)['running'])
        self.assertEqual(self.s.STATE['last_explorer_delete']['removed_count'],2)

    def test_duplicate_in_current_project_protects_shared_files_until_last_owner(self):
        scan=self.scan();duplicate=copy.deepcopy(scan);duplicate.update(id='duplicate',name='Copy')
        self.s.STATE['job_items'].append(duplicate);assets=self.owned(scan)
        contents={p:p.read_bytes() for p in assets};self.s.save()
        review=self.review([scan['id']]);self.assertEqual(review['exclusive_bytes'],0)
        self.assertEqual(review['shared_bytes'],sum(len(b) for b in contents.values()))
        self.commit([scan['id']],review=review)
        self.assertEqual({p:p.read_bytes() for p in assets},contents)
        self.commit([duplicate['id']])
        self.assertTrue(all(not p.exists() for p in assets))

    def test_other_saved_project_and_unknown_reference_key_protect_shared_cache(self):
        scan=self.scan();assets=self.owned(scan)
        other=projects.save_named(self.root,{'future_owned_data':{'future_unknown_key':scan['meta']['file']}},'Other',True)
        other_path=self.root/'projects'/(other['project_id']+'.json');other_bytes=other_path.read_bytes()
        self.s.save();review=self.review([scan['id']])
        self.assertEqual(review['shared_bytes'],Path(scan['meta']['file']).stat().st_size)
        self.commit([scan['id']],review=review)
        self.assertTrue(Path(scan['meta']['file']).is_file())
        self.assertEqual(other_path.read_bytes(),other_bytes)
        self.assertTrue(all(not p.exists() for p in assets if str(p)!=scan['meta']['file']))

    def test_other_project_unavailable_json_protects_shared_cache_and_original_source(self):
        scan=self.scan('Shared recoverable scan');shared=Path(scan['meta']['file'])
        original=Path(self.file('e'*32+'.xyz64',np.arange(18,dtype='<f8').reshape(6,3).tobytes()))
        # Both files look eligible in the current item's metadata. Only the
        # other project's decoded recovery record marks the second as original.
        scan['meta']['auxiliary_cache']=str(original)
        decoded=dict(id='recoverable',kind='scan',name='Recoverable source',meta=dict(
            file=str(shared),count=scan['meta']['count'],minimum=scan['meta']['minimum'],
            maximum=scan['meta']['maximum'],original_path=str(original)))
        archived=dict(project_recovery={'unavailable':[dict(kind='scan',name='Recoverable source',
            reason='Earlier missing companion metadata',original_data_json=json.dumps(decoded))]})
        saved=projects.save_named(self.root,archived,'Other recoverable project',True)
        other_path=self.root/'projects'/(saved['project_id']+'.json');other_bytes=other_path.read_bytes()
        protected={path:path.read_bytes() for path in (shared,original)}
        reclaimed=self.owned(scan)-set(protected)
        self.s.save();review=self.review([scan['id']])
        self.assertEqual(review['shared_bytes'],shared.stat().st_size)
        self.assertEqual(review['exclusive_bytes'],sum(path.stat().st_size for path in reclaimed))
        self.commit([scan['id']],review=review)
        self.assertFalse(self.s.STATE['job_items'])
        self.assertEqual({path:path.read_bytes() for path in protected},protected)
        self.assertTrue(all(not path.exists() for path in reclaimed))
        self.assertEqual(other_path.read_bytes(),other_bytes)
        reopened=workflows.prepare_data(self.s,projects.read_json(other_path))
        self.assertEqual(json.loads(reopened['project_recovery']['unavailable'][0]['original_data_json']),decoded)
        self.assertEqual({path:path.read_bytes() for path in protected},protected)

    def test_malformed_embedded_recovery_ownership_rejects_review_and_delete_without_mutation(self):
        scan=self.scan('Protected by unreadable ownership');self.s.save()
        reviewed=self.review([scan['id']])
        projects.save_named(self.root,{'project_recovery':{'unavailable':[dict(kind='scan',name='Unknown owner',
            reason='Fixture damaged recovery metadata',original_data_json='{"meta":{"file":')]}},'Unreadable ownership',True)
        before_state=copy.deepcopy(self.s.STATE);before_files=self.disk_snapshot()
        expected='A project recovery ownership record contains invalid JSON. Restore or repair the project metadata before deleting working files.'
        for action in ('review','delete'):
            with self.subTest(action=action):
                with self.assertRaises(ValueError) as error:
                    if action=='review':self.review([scan['id']])
                    else:self.commit([scan['id']],review=reviewed)
                self.assertEqual(str(error.exception),expected)
                self.assertEqual(self.s.STATE,before_state)
                self.assertEqual(self.disk_snapshot(),before_files)
                self.assertFalse(projects.storage_purge_status(self.root)['running'])

    def test_external_and_in_workspace_original_sources_are_preserved(self):
        scan=self.scan()
        external=self.base/'original.xyz64';external.write_bytes(b'external original')
        inside=self.root/'source.e57';inside.write_bytes(b'original E57 inside workspace')
        export=self.root/'survey-export.csv';export.write_text('id,e,n,h\n1,1,2,3\n')
        scan['meta'].update(source_path=str(inside),original_file=str(external),export_path=str(export))
        protected={p:p.read_bytes() for p in (external,inside,export)}
        self.s.save();self.commit([scan['id']])
        self.assertEqual({p:p.read_bytes() for p in protected},protected)
        self.assertFalse(Path(scan['meta']['file']).exists())

    def test_originals_inside_legacy_display_cache_survive_review_delete_and_reopen(self):
        scan=self.scan('Legacy display source');owned=self.owned(scan)
        cache=self.root/'display_cache';cache.mkdir()
        original_npz=cache/'original-survey.npz';original_npy=cache/'original-coordinates.npy'
        disposable_npz=cache/'obsolete-colour-view.npz';disposable_npy=cache/'obsolete-pose-view.npy'
        points=np.arange(36,dtype='<f8').reshape(12,3)
        np.savez(original_npz,points=points);np.save(original_npy,points)
        np.savez(disposable_npz,points=points[::3]);np.save(disposable_npy,points[::4])
        scan['meta'].update(source_path=str(original_npz),original_file=str(original_npy))
        protected={path:path.read_bytes() for path in (original_npz,original_npy)}
        reclaim=owned|{disposable_npz,disposable_npy}
        expected_bytes=sum(path.stat().st_size for path in reclaim)
        named=self.saved_current();recovery=self.recovery();self.s.save()
        before=self.disk_snapshot();review=self.review([scan['id']])
        self.assertEqual(self.disk_snapshot(),before)
        self.assertEqual(review['exclusive_bytes'],expected_bytes)
        self.assertEqual(review['exclusive_files'],len(reclaim))
        self.assertEqual(review['external_files'],2)
        self.assertTrue(review['original_imports_preserved'])
        self.commit([scan['id']],review=review)
        self.assertTrue(all(not path.exists() for path in reclaim))
        self.assertEqual({path:path.read_bytes() for path in protected},protected)
        # Exercise the same metadata preparation used when reopening the
        # current workspace, named project, or matching startup recovery.
        for path in (self.root/'project.json',named,recovery):
            reopened=workflows.prepare_data(self.s,projects.read_json(path))
            self.assertFalse(reopened['job_items'])
            self.assertEqual({original:original.read_bytes() for original in protected},protected)

    def test_current_saved_and_recovery_remove_only_selected_item_without_saving_other_edits(self):
        selected=self.scan('Remove');selected_assets=self.owned(selected);keep=self.scan('Saved keep',100.)
        self.s.STATE['notes']='Saved notes';named=self.saved_current();recovery=self.recovery()
        self.s.STATE['notes']='Unrelated unsaved notes'
        keep['name']='Unrelated unsaved name';self.s.save()
        review=self.review([selected['id']]);self.assertTrue(review['updates_saved_project'])
        self.commit([selected['id']],review=review)
        for path in (named,recovery):
            data=projects.read_json(path)
            self.assertEqual([x['id'] for x in data['job_items']],[keep['id']])
            self.assertEqual(data['job_items'][0]['name'],'Saved keep')
            self.assertEqual(data['notes'],'Saved notes')
        self.assertEqual(self.s.STATE['notes'],'Unrelated unsaved notes')
        self.assertEqual(self.s.STATE['job_items'][0]['name'],'Unrelated unsaved name')
        self.assertTrue(all(not p.exists() for p in selected_assets))

    def test_different_and_unnamed_recovery_keep_their_referenced_assets(self):
        for recovered_id in ('f'*32,None):
            with self.subTest(recovered_id=recovered_id):
                scan=self.scan(str(recovered_id))
                recovered={'project_id':recovered_id,'cloud':copy.deepcopy(scan['meta'])}
                path=self.recovery(recovered);before=path.read_bytes();assets=self.owned(scan)
                self.s.save();review=self.review([scan['id']])
                self.assertEqual(review['exclusive_bytes'],0)
                self.commit([scan['id']],review=review)
                self.assertEqual(path.read_bytes(),before)
                self.assertTrue(all(p.is_file() for p in assets));path.unlink()

    def test_selected_scan_removal_invalidates_active_aliases_and_dependent_results(self):
        scan=self.scan();meta=copy.deepcopy(scan['meta'])
        derived=self.file('d'*32+'.analysis64',np.zeros((24,4),dtype='<f8').tobytes())
        surface_ids=self.file('a'*32+'.surface.ids64',np.arange(24,dtype='<i8').tobytes())
        surface_labels=self.file('b'*32+'.surface.labels32',np.zeros(24,dtype='<i4').tobytes())
        result=self.file('c'*32+'.inspection',b'result')
        self.s.STATE.update(active_job_scan=scan['id'],cloud=copy.deepcopy(meta),derived=derived,
            active_job_registration=scan['id'],registration={'cloud':copy.deepcopy(meta)},
            surface_source_item=scan['id'],surface_source=copy.deepcopy(meta),
            surface_result={'id':'analysis','source_item_id':scan['id'],'source_meta':meta,
                            'point_ids_file':surface_ids,'labels_file':surface_labels},
            inspection_result={'id':'inspect','source_id':scan['id'],'results_file':result},
            setup_view={'id':scan['id']})
        # Serializing this deliberately minimal inspection fixture would ask the
        # real API to interpret it; keep the fixture autosave writer direct.
        self.s.save=lambda:projects.atomic_json(self.root/'project.json',self.s.STATE)
        self.s.save();self.commit([scan['id']])
        for key in ('active_job_scan','cloud','derived','active_job_registration','surface_source_item','surface_source','surface_result','inspection_result','setup_view'):
            self.assertFalse(self.s.STATE.get(key),key)
        self.assertFalse((self.s.STATE.get('registration') or {}).get('cloud'))
        self.assertTrue(all(not Path(p).exists() for p in (derived,surface_ids,surface_labels,result)))

    def test_corrupt_ownership_manifest_fails_before_state_or_files_change(self):
        scan=self.scan();self.s.save()
        for relative in ('projects/'+'9'*32+'.json','startup_recovery/workspace_broken.json'):
            with self.subTest(manifest=relative):
                broken=self.root/relative;broken.parent.mkdir(parents=True,exist_ok=True)
                broken.write_text('{broken JSON');state=copy.deepcopy(self.s.STATE);files=self.disk_snapshot()
                with self.assertRaises(ValueError):self.review([scan['id']])
                self.assertEqual(self.s.STATE,state);self.assertEqual(self.disk_snapshot(),files)
                broken.unlink()

    def test_stale_review_after_new_project_reference_does_not_delete_anything(self):
        scan=self.scan();self.s.save();review=self.review([scan['id']])
        projects.save_named(self.root,{'cloud':copy.deepcopy(scan['meta'])},'New owner',True)
        before=self.disk_snapshot();state=copy.deepcopy(self.s.STATE)
        with self.assertRaises(ValueError):self.commit([scan['id']],review=review)
        self.assertEqual(self.disk_snapshot(),before);self.assertEqual(self.s.STATE,state)

    def test_stale_review_after_item_change_does_not_delete_anything(self):
        scan=self.scan();self.s.save();review=self.review([scan['id']])
        scan['name']='Renamed after review';self.s.save();before=self.disk_snapshot()
        with self.assertRaises(ValueError):self.commit([scan['id']],review=review)
        self.assertEqual(self.disk_snapshot(),before)
        self.assertEqual(self.s.STATE['job_items'][0]['name'],'Renamed after review')

    def test_storage_move_failure_rolls_back_every_item_and_manifest(self):
        scan=self.scan();mesh=self.mesh();self.saved_current();self.recovery();self.s.save()
        review=self.review([scan['id'],mesh['id']]);before=self.disk_snapshot();state=copy.deepcopy(self.s.STATE)
        blocked=Path(mesh['meta']['faces_file']).resolve();replace=os.replace;failed=[]
        def deny_once(source,destination):
            if Path(source).resolve()==blocked and not failed:
                failed.append(True);raise PermissionError('Fixture file held by Windows')
            return replace(source,destination)
        with mock.patch.object(os,'replace',side_effect=deny_once):
            with self.assertRaises((OSError,ValueError)):
                self.commit([scan['id'],mesh['id']],review=review)
        self.assertTrue(failed)
        self.assertEqual(self.s.STATE,state)
        # Transaction journals may be removed or left for safe recovery, but
        # every pre-existing byte and original path must be restored.
        for relative,contents in before.items():self.assertEqual((self.root/relative).read_bytes(),contents,relative)
        self.assertFalse(projects.storage_purge_status(self.root)['running'])

    def test_metadata_commit_failure_restores_current_saved_and_recovery_state(self):
        scan=self.scan();self.saved_current();self.recovery();self.s.save()
        review=self.review([scan['id']]);before=self.disk_snapshot();state=copy.deepcopy(self.s.STATE)
        replace=os.replace;failed=[];current=self.root/'project.json'
        def deny_once(source,destination):
            if Path(destination).resolve()==current.resolve() and not failed:
                failed.append(True);raise OSError('Fixture metadata drive failure')
            return replace(source,destination)
        with mock.patch.object(os,'replace',side_effect=deny_once):
            with self.assertRaises((OSError,ValueError)):self.commit([scan['id']],review=review)
        self.assertTrue(failed)
        self.assertEqual(self.s.STATE,state)
        for relative,contents in before.items():self.assertEqual((self.root/relative).read_bytes(),contents,relative)
        self.assertFalse(projects.storage_purge_status(self.root)['running'])

    def test_multi_ring_reference_delete_preserves_points_and_supports_undo(self):
        scan=self.scan();source=Path(scan['meta']['file']);original=source.read_bytes()
        self.s.STATE['surface_result']=dict(id='seams',revision='result',source_item_id=scan['id'],
            source_meta=copy.deepcopy(scan['meta']),settings={'method':'lining_features'},
            features=[dict(id=i,kind='ring_seam',name='Ring seam '+str(i),points=[[float(i),0.,0.],[float(i),1.,0.]]) for i in (1,2,3)])
        self.s.save();ids=['lining:seams:1','lining:seams:2'];review=self.review(ids)
        self.assertEqual(review['exclusive_bytes'],0);self.assertFalse(review['history_cleared'])
        self.commit(ids,review=review,history=True)
        remaining={row['id'] for row in scene_objects.public(self.s)}
        self.assertFalse(set(ids)&remaining);self.assertIn('lining:seams:3',remaining)
        self.assertEqual(source.read_bytes(),original)
        self.assertTrue(workspace_history.status(self.s)['undo_count'])
        workspace_history.apply(self.s)
        restored={row['id'] for row in scene_objects.public(self.s)}
        self.assertTrue(set(ids)<=restored);self.assertEqual(source.read_bytes(),original)

    def test_generated_structure_delete_reclaims_its_mesh_json_and_keeps_other_geometry(self):
        scan=self.scan();original={path:path.read_bytes() for path in self.owned(scan)}
        mesh=json.dumps({'vertices':[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]],'faces':[[0,1,2]]}).encode()
        selected=Path(self.file('a'*32+'.mesh.json',mesh));keep=Path(self.file('b'*32+'.mesh.json',mesh))
        self.s.STATE['structures']=[dict(id=ident,name=ident,kind='scan_mesh',mesh_file=str(path),visible=True)
                                    for ident,path in (('selected',selected),('keep',keep))]
        self.s.save();review=self.review(['structure:selected'])
        self.assertEqual(review['exclusive_bytes'],len(mesh));self.assertEqual(review['shared_bytes'],0)
        self.commit(['structure:selected'],review=review)
        self.assertFalse(selected.exists());self.assertEqual(keep.read_bytes(),mesh)
        self.assertEqual([row['id'] for row in self.s.STATE['structures']],['keep'])
        self.assertEqual({path:path.read_bytes() for path in original},original)

    def test_shared_generated_mesh_json_is_kept_until_last_structure_owner_deleted(self):
        scan=self.scan();source=Path(scan['meta']['file']).read_bytes()
        mesh=json.dumps({'vertices':[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]],'faces':[[0,1,2]]}).encode()
        path=Path(self.file('c'*32+'.mesh.json',mesh))
        self.s.STATE['structures']=[dict(id=ident,name=ident,kind='scan_mesh',mesh_file=str(path),visible=True)
                                    for ident in ('selected','shared')]
        self.s.save();review=self.review(['structure:selected'])
        self.assertEqual(review['exclusive_bytes'],0);self.assertEqual(review['shared_bytes'],len(mesh))
        self.commit(['structure:selected'],review=review)
        self.assertEqual(path.read_bytes(),mesh)
        self.assertEqual([row['id'] for row in self.s.STATE['structures']],['shared'])
        self.commit(['structure:shared'])
        self.assertFalse(path.exists());self.assertFalse(self.s.STATE['structures'])
        self.assertEqual(Path(scan['meta']['file']).read_bytes(),source)

    def test_transient_feature_preview_cache_follows_its_selected_source_only(self):
        source=self.scan('Feature source');unrelated=self.scan('Unrelated deleted scan',50.);keep=self.scan('Keep scan',100.)
        keep_bytes={path:path.read_bytes() for path in self.owned(keep)}
        original=Path(self.file('original-survey.e57',b'Original imported survey bytes'))
        source['meta']['source_path']=str(original);original_bytes=original.read_bytes()
        values={'points_file':('.feature_xyz64',np.ones((5,3),dtype='<f8').tobytes()),
                'point_ids_file':('.feature_ids',np.arange(5,dtype='<i8').tobytes()),
                'labels_file':('.feature_labels',np.ones(5,dtype='<i4').tobytes()),
                'selected_file':('.feature_selected',np.ones(5,dtype='u1').tobytes())}
        paths={key:self.file('d'*32+suffix,data) for key,(suffix,data) in values.items()}
        self.s.FEATURE_SELECTION=dict(id='feature',source_id=source['id'],count=5,**paths)
        preview=copy.deepcopy(self.s.FEATURE_SELECTION)
        caches={Path(path):Path(path).read_bytes() for path in paths.values()}
        self.s.save();self.commit([unrelated['id']])
        self.assertEqual(self.s.FEATURE_SELECTION,preview)
        self.assertEqual({path:path.read_bytes() for path in caches},caches)
        self.commit([source['id']])
        self.assertIsNone(self.s.FEATURE_SELECTION)
        self.assertTrue(all(not path.exists() for path in caches))
        self.assertEqual(original.read_bytes(),original_bytes)
        self.assertEqual({path:path.read_bytes() for path in keep_bytes},keep_bytes)

    def test_bundle_delete_expands_joined_items_and_keeps_unselected_scan(self):
        first=self.scan('Joined A');second=self.scan('Joined B',100.);keep=self.scan('Outside',200.)
        self.s.STATE['scan_links']=[dict(id='link',a=first['id'],b=second['id'],status='applied')]
        group=workspace_tools.inventory(self.s)[0]
        model=self.cad(bundle_id=group['id']);self.s.save()
        review=self.review(bundle_ids=[group['id']]);self.assertEqual(review['item_count'],3)
        self.commit(bundle_ids=[group['id']],review=review)
        self.assertEqual([x['id'] for x in self.s.STATE['job_items']],[keep['id']])
        self.assertFalse(self.s.STATE['cad']['objects']);self.assertFalse(self.s.STATE['scan_links'])
        self.assertTrue(Path(keep['meta']['file']).is_file())

    def test_point_delete_remains_mask_based_and_reversible_after_explorer_changes(self):
        scan=self.scan();original=Path(scan['meta']['file']).read_bytes()
        scan_edits.apply(self.s,scan,'delete_selection',
                         {'mode':'box','minimum':[3.,-1.,0.],'maximum':[7.,1.,2.]},reason='Fixture points')
        current=self.s.STATE['job_items'][0]
        mask=np.fromfile(current['meta']['mask_file'],'u1')
        np.testing.assert_array_equal(np.flatnonzero(mask),np.arange(3,8))
        self.assertEqual(Path(current['meta']['file']).read_bytes(),original)
        scan_edits.apply(self.s,current,'undo_edit')
        np.testing.assert_array_equal(np.fromfile(current['meta']['mask_file'],'u1'),np.zeros(24,dtype='u1'))
        self.assertEqual(Path(current['meta']['file']).read_bytes(),original)

    def test_deleted_separated_scan_cannot_be_resurrected_by_point_undo_redo(self):
        parent=self.scan('Source before separation')
        source_bytes=Path(parent['meta']['file']).read_bytes()
        self.s.STATE.update(active_job_scan=parent['id'],cloud=copy.deepcopy(parent['meta']))
        scan_edits.apply(self.s,parent,'separate_selection',
            {'mode':'box','minimum':[3.,-1.,0.],'maximum':[7.,1.,2.]},reason='Separate fixture points')
        child=next(row for row in self.s.STATE['job_items'] if row['id']!=parent['id'])
        child_id=child['id'];child_assets=self.owned(child)
        self.assertEqual(child['meta']['count'],5)
        self.assertEqual(parent['edit_history'][-1]['separated_item']['id'],child_id)
        self.assertEqual(self.s.STATE['edits'][-1]['separated_item']['id'],child_id)
        named=self.saved_current();self.s.save()
        self.commit([child_id])
        self.assertTrue(all(not path.exists() for path in child_assets))
        parent=next(row for row in self.s.STATE['job_items'] if row['id']==parent['id'])
        self.assertNotIn('separated_item',parent['edit_history'][-1])
        self.assertTrue(parent['edit_history'][-1]['separated_item_removed'])
        self.assertNotIn('separated_item',self.s.STATE['edits'][-1])
        saved=projects.read_json(named)
        saved_parent=next(row for row in saved['job_items'] if row['id']==parent['id'])
        self.assertNotIn('separated_item',saved_parent['edit_history'][-1])
        self.assertNotIn(child_id,{row['id'] for row in workflows.prepare_data(self.s,saved)['job_items']})
        # The surviving source retains its reversible point mask, but the
        # explicitly erased independent child is never reconstructed by Redo.
        scan_edits.apply(self.s,parent,'undo_edit')
        np.testing.assert_array_equal(np.fromfile(parent['meta']['mask_file'],'u1'),np.zeros(24,dtype='u1'))
        self.assertNotIn(child_id,{row['id'] for row in self.s.STATE['job_items']})
        scan_edits.apply(self.s,parent,'redo_edit')
        np.testing.assert_array_equal(np.flatnonzero(np.fromfile(parent['meta']['mask_file'],'u1')),np.arange(3,8))
        self.assertNotIn(child_id,{row['id'] for row in self.s.STATE['job_items']})
        self.assertTrue(all(not path.exists() for path in child_assets))
        self.assertEqual(Path(parent['meta']['file']).read_bytes(),source_bytes)

    def test_interrupted_uncommitted_journal_restores_original_bytes_before_reopen(self):
        scan=self.scan();self.saved_current();self.s.save()
        original=Path(scan['meta']['file']);contents=original.read_bytes()
        current=self.root/'project.json';metadata=current.read_bytes()
        stage=self.root/explorer_deletion.TRANSACTIONS/'interrupted_fixture'
        held=stage/'assets'/original.name;held.parent.mkdir(parents=True)
        backup=stage/'before'/'project.json';backup.parent.mkdir(parents=True);backup.write_bytes(metadata)
        os.replace(original,held)
        current.write_text('{"job_items":[]}')
        projects.atomic_json(stage/'journal.json',dict(status='prepared',assets=[original.name],
            metadata=[{'path':'project.json','existed':True}],bytes=len(contents),created_at='fixture'))
        explorer_deletion.recover(self.root)
        self.assertEqual(original.read_bytes(),contents)
        self.assertEqual(current.read_bytes(),metadata)
        self.assertFalse(stage.exists())

    def test_committed_journal_resumes_physical_erasure_without_restoring_deleted_items(self):
        scan=self.scan();self.s.save();original=Path(scan['meta']['file']);size=original.stat().st_size
        stage=self.root/explorer_deletion.TRANSACTIONS/'committed_fixture'
        held=stage/'assets'/original.name;held.parent.mkdir(parents=True);os.replace(original,held)
        current=self.root/'project.json';current.write_text('{"job_items":[]}');metadata=current.read_bytes()
        backup=stage/'before'/'project.json';backup.parent.mkdir(parents=True);backup.write_text('{"job_items":["old"]}')
        projects.atomic_json(stage/'journal.json',dict(status='committed',assets=[original.name],
            metadata=[{'path':'project.json','existed':True}],bytes=size,created_at='fixture'))
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            explorer_deletion.recover(self.root);schedule.assert_called_once()
        self.finish_purges()
        self.assertFalse(original.exists());self.assertFalse(stage.exists())
        self.assertEqual(current.read_bytes(),metadata)
        self.assertFalse(projects.storage_purge_status(self.root)['running'])

    def test_deleting_original_setup_keeps_independent_registered_merge_without_cache_pinning(self):
        original=self.scan('Original setup');other=self.scan('Other setup',100.)
        merged=self.scan('Independent merged result',count=48)
        merged.update(registered_merge=True,merge_source_ids=[original['id'],other['id']],
            merge_ranges=[dict(scan_id=x['id'],source_file=x['meta']['file'],source_mask=x['meta']['mask_file']) for x in (original,other)],
            merge_source_revisions={x['id']:{'file':x['meta']['file'],'mask_file':x['meta']['mask_file'],'count':x['meta']['count']} for x in (original,other)})
        for row in (original,other):row.update(merge_parent=merged['id'],visible=False)
        original_assets=self.owned(original);merged_assets={p:p.read_bytes() for p in self.owned({'meta':merged['meta']})}
        named=self.saved_current();self.s.save();self.commit([original['id']])
        self.assertTrue(all(not p.exists() for p in original_assets))
        self.assertEqual({p:p.read_bytes() for p in merged_assets},merged_assets)
        current=next(x for x in self.s.STATE['job_items'] if x['id']==merged['id'])
        record=current['merge_source_revisions'][original['id']]
        self.assertNotIn('file',record);self.assertNotIn('mask_file',record)
        self.assertTrue(record['source_removed'])
        source_range=next(row for row in current['merge_ranges'] if row['scan_id']==original['id'])
        self.assertNotIn('source_file',source_range);self.assertNotIn('source_mask',source_range)
        self.assertTrue(source_range['source_removed'])
        saved=projects.read_json(named)
        self.assertNotIn(original['id'],{x['id'] for x in saved['job_items']})
        recovered=workflows.prepare_data(self.s,saved)
        self.assertNotIn(original['id'],{x['id'] for x in recovered['job_items']})
        self.assertIn(merged['id'],{x['id'] for x in recovered['job_items']})


if __name__=='__main__':unittest.main()
