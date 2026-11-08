from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
from unittest import TestCase

import numpy as np

import jobdata
import project_data_layout as layout
import project_shapefiles
import projects
import registered_cloud
import workspace_folders


class DedicatedAssetTests(TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.work=self.base/'Drive D'/'Ring project'
        self.work.mkdir(parents=True)
        (self.work/workspace_folders.MARKER).write_text(json.dumps({
            'format':'TunnelStudioWorkspace','version':1,'id':'3'*32,'name':'Ring project'}))
        self.s=SimpleNamespace(WORK=self.work,STATE={'job_items':[]},JOB={'busy':False},
                               progress=lambda _:None,save=lambda:None)
        layout.create_categories(self.work)

    def cloud(self):
        p=self.base/'ring.xyz'
        p.write_text('0 0 0\n1 0 0\n2 1 1\n3 2 1\n')
        return jobdata.import_scan(p,self.work),p

    def test_native_writer_merge_derivation_and_portable_restore_into_categories(self):
        meta,p=self.cloud()
        items=[dict(id=key,kind='scan',name=key,meta=dict(meta)) for key in ('A','B')]
        merged=registered_cloud.build(self.s,items,'reg')
        derived=jobdata.extract_ids(meta,iter([np.array([0,2],dtype=np.int64)]),2,self.work,'Lining')
        self.s.STATE['job_items']=[*items,merged,dict(id='C',kind='scan',name='Lining',meta=derived,cleaning_provenance={'method':'test'})]
        obj=self.base/'ring.obj';obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        mesh=jobdata.import_obj(obj,self.work)
        self.s.STATE['job_items'].append(dict(id='M',kind='mesh',name='Model',meta=mesh))
        expected=('imported','imported','registered','derived','mesh')
        for item,kind in zip(self.s.STATE['job_items'],expected):
            for key in ('file','vertices_file','faces_file','attributes_file','preview_file'):
                path=item['meta'].get(key)
                if path:self.assertTrue(Path(path).is_relative_to(self.work/layout.CATEGORIES[kind]),path)
        archive=self.base/'copy.tstudio'
        projects.portable(self.work,self.s.STATE,archive)
        opened=projects.unpack(self.work,archive)
        for item,kind in zip(opened['job_items'],expected):
            key='vertices_file' if kind=='mesh' else 'file'
            path=Path(item['meta'][key]);self.assertTrue(path.is_file());self.assertTrue(path.is_relative_to(self.work/layout.CATEGORIES[kind]),path)
        projects.cleanup_import(self.work,opened)
        self.assertFalse(list(self.work.rglob('project_assets_*')))
        self.assertTrue(p.exists())
        self.assertEqual(meta['count'],4);self.assertEqual(merged['meta']['count'],8)

    def test_shapefile_family_preserves_names_in_workspace_and_portable_project(self):
        src=self.base/'Chainage.SHP'
        parts={ext:ext.encode()*12 for ext in ('.SHP','.SHX','.dbf','.prj','.cpg','.shp.xml','.Station.atx')}
        for ext,data in parts.items():(self.base/('Chainage'+ext)).write_bytes(data)
        (self.base/'Chainage.notes.txt').write_text('personal')
        result=project_shapefiles.archive(self.s,src)
        def contents(row):
            files=[Path(member['file']) for member in row['members']]
            self.assertEqual(len({p.parent for p in files}),1)
            self.assertTrue(files[0].is_relative_to(self.work/'GIS/Shapefiles'))
            self.assertEqual({p.name:p.read_bytes() for p in files},{'Chainage'+ext:data for ext,data in parts.items()})
        contents(result)
        archive=self.base/'copy.tstudio'
        projects.portable(self.work,self.s.STATE,archive)
        restored=projects.unpack(self.work,archive)
        contents(restored['gis_resources'][0])
        projects.release_import(restored)
        contents(restored['gis_resources'][0])

    def test_incomplete_or_cancelled_gis_copy_leaves_source_and_prior_state(self):
        src=self.base/'Alignment.shp';src.write_bytes(b'xyz')
        with self.assertRaisesRegex(ValueError,'missing'):project_shapefiles.archive(self.s,src)
        for ext in ('.dbf','.shx'):src.with_suffix(ext).touch()
        self.s.progress=lambda _:(_ for _ in ()).throw(jobdata.Cancelled('Stop'))
        with self.assertRaises(jobdata.Cancelled):project_shapefiles.archive(self.s,src)
        self.assertFalse(list((self.work/'GIS/Shapefiles').iterdir()))
        self.assertNotIn('gis_resources',self.s.STATE)
        self.assertEqual(src.read_bytes(),b'xyz')

    def test_legacy_root_is_unmodified_and_linked_output_dir_is_blocked(self):
        legacy=self.base/'old';legacy.mkdir()
        self.assertEqual(layout.folder(legacy,'registered'),legacy)
        self.assertEqual(list(legacy.iterdir()),[])
        link=self.work/'Meshes';link.rmdir()
        link.symlink_to(legacy,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'real folder'):layout.folder(self.work,'mesh')
