"""REGISTER 360 recognition is read-only and never guesses a name from a UUID."""
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

import leica_catalog as catalogue
import leica_project
import project_browser

GUID='aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb'


def metadata_fixture(root,filename='project_deliv_1000.db',label='North tunnel site map'):
    """Construct only the schemas observed in supplied Leica SQLite files."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    info=root/'ProjectInfoDb.db'
    if not info.exists():
        with sqlite3.connect(info) as c:
            c.execute('CREATE TABLE Hds_OdbProjectMetadata (mProjectId TEXT)')
            c.execute('INSERT INTO Hds_OdbProjectMetadata VALUES (?)',(GUID,))
    path=root/filename
    with sqlite3.connect(path) as c:
        c.executescript('''
        CREATE TABLE Hds_OdbSitemap (mId INTEGER,mName TEXT,mIsDeleted INTEGER);
        CREATE TABLE Hds_OdbSetup (mId INTEGER,mGuid TEXT,mName TEXT,mCloudURI TEXT,mIsUnstructured INTEGER,mIsDeleted INTEGER,mIsPhantom INTEGER,mPanoURI TEXT,mMetadata TEXT);
        CREATE TABLE Hds_OdbSetupRef (mSetupId INTEGER,mSitemapId INTEGER,mPositionX REAL,mPositionY REAL,mPositionZ REAL,mRotationX REAL,mRotationY REAL,mRotationZ REAL,mRotationW REAL);
        CREATE TABLE Hds_OdbLink (mId INTEGER,mGuid TEXT,mpLeftSetup INTEGER,mpRightSetup INTEGER,mUseTargetCorrespondences INTEGER,mTransformToUse INTEGER,mIsDeleted INTEGER);
        CREATE TABLE Hds_OdbTarget (mId INTEGER,mGuid TEXT,mSetupId INTEGER,mLabel TEXT,m3dPosX REAL,m3dPosY REAL,m3dPosZ REAL,mNormalX REAL,mNormalY REAL,mNormalZ REAL,mIsDeleted INTEGER);
        CREATE TABLE Hds_OdbProjectProperty (mName TEXT,mType INTEGER,mValue BLOB);
        CREATE TABLE Hds_OdbPointCloud (mPointCount INTEGER);
        ''')
        c.execute('INSERT INTO Hds_OdbSitemap VALUES (1,?,0)',(label,))
        for i in range(3):
            c.execute('INSERT INTO Hds_OdbSetup VALUES (?,?,?,?,0,0,0,NULL,NULL)',(i,'guid-'+str(i),'Setup '+str(i),'SetupsFolder\\'+str(i)+'.hspca'))
            c.execute('INSERT INTO Hds_OdbSetupRef VALUES (?,1,?,?,?,0,0,0,1)',(i,500000+i*4,200000,50))
        c.execute('INSERT INTO Hds_OdbLink VALUES (1,"link",0,1,0,0,0)')
        c.execute('INSERT INTO Hds_OdbPointCloud VALUES (921209989)')
    return path


class LeicaCatalog0222Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/GUID
        self.db=metadata_fixture(self.project);self.s=SimpleNamespace(WORK=self.root)

    def test_parent_and_direct_uuid_folder_are_recognised(self):
        for folder in (self.root,self.project):
            result=project_browser.request(self.s,dict(action='browse',folder=str(folder)))
            self.assertEqual(len(result['projects']),1)
            row=result['projects'][0]
            self.assertEqual(row['kind'],'leica_register360')
            self.assertEqual(row['project_title'],'North tunnel site map')
            self.assertEqual(row['title_source'],'site_map')
            self.assertNotEqual(row['project_title'],GUID)

    def test_actual_project_title_from_guid_matched_catalogue(self):
        with sqlite3.connect(self.root/'ProjectCatalogue.db') as c:
            c.execute('CREATE TABLE Projects (mGuid TEXT,mName TEXT)')
            c.executemany('INSERT INTO Projects VALUES (?,?)',[(GUID,'North tunnel — proper project name'),('cccccccc-1111-2222-3333-dddddddddddd','Wrong other project')])
        result=catalogue.describe(self.project,self.root)
        self.assertEqual(result['project_title'],'North tunnel — proper project name')
        self.assertEqual(result['title_source'],'project_metadata')

    def test_other_catalogue_project_is_never_used(self):
        with sqlite3.connect(self.root/'Projects.db') as c:
            c.execute('CREATE TABLE Projects (ProjectGuid TEXT,Name TEXT)')
            c.execute('INSERT INTO Projects VALUES (?,?)',('cccccccc-1111-2222-3333-dddddddddddd','Not this project'))
        self.assertEqual(catalogue.describe(self.project)['title_source'],'site_map')

    def test_explicit_text_project_title_and_identity(self):
        (self.project/'ProjectInfo.json').write_text(json.dumps(dict(projectId=GUID,projectName='Saved Leica project title')))
        self.assertEqual(catalogue.describe(self.project)['project_title'],'Saved Leica project title')
        (self.project/'ProjectInfo.json').write_text(json.dumps(dict(projectId='cccccccc-1111-2222-3333-dddddddddddd',projectName='Another project')))
        self.assertEqual(catalogue.describe(self.project)['title_source'],'site_map')

    def test_typed_project_property_utf16_is_distinct_from_report_heading(self):
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO Hds_OdbProjectProperty VALUES ("ProjectName",1,?)',('Stored project name'.encode('utf-16-le'),))
            c.execute('INSERT INTO Hds_OdbProjectProperty VALUES ("ReportHeading",1,?)',(b'Wrong report title',))
        info=catalogue.describe(self.project)
        self.assertEqual(info['project_title'],'Stored project name')
        self.assertEqual(info['title_source'],'project_metadata')

    def test_preview_contains_labelled_positions_not_scan_observations(self):
        result=project_browser.request(self.s,dict(action='browse',folder=str(self.root)))
        row=result['projects'][0]
        value=project_browser.request(self.s,dict(action='preview',browse_id=result['browse_id'],key=row['key']))['preview']
        self.assertEqual(value['quality'],'setup_layout');self.assertEqual(value['setup_count'],3)
        self.assertEqual(value['source_points'],0)
        xyz=[[p[i]+value['origin'][i] for i in range(3)] for p in value['points']]
        self.assertEqual(xyz,[[500000+i*4,200000,50] for i in range(3)])
        self.assertIn('not present',value['note'])

    def test_snapshots_are_one_project_and_selection_is_explicit(self):
        second=metadata_fixture(self.project,'project_deliv_2000.db')
        result=project_browser.request(self.s,dict(action='browse',folder=str(self.root)))
        self.assertEqual(len(result['projects']),1);row=result['projects'][0]
        initial=project_browser.request(self.s,dict(action='preview',browse_id=result['browse_id'],key=row['key']))['preview']
        self.assertEqual(initial['points'],[])
        chosen=project_browser.request(self.s,dict(action='preview',browse_id=result['browse_id'],key=row['key'],database=str(second)))['preview']
        self.assertEqual(len(chosen['points']),3)
        with self.assertRaises(ValueError):
            project_browser.request(self.s,dict(action='preview',browse_id=result['browse_id'],key=row['key'],database=str(self.root/'other.db')))

    def test_version_only_project_uses_existing_import_inspector(self):
        self.db.rename(self.project/'project_version_1000.db')
        info=leica_project.inspect(self.project)
        self.assertEqual(info['project_title'],'North tunnel site map')
        self.assertEqual(len(info['setups']),3)

    def test_hashes_and_directory_inventory_unchanged(self):
        before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        for _ in range(2):
            info=catalogue.describe(self.project);catalogue.preview(info)
            project_browser.request(self.s,dict(action='browse',folder=str(self.root)))
        after={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_journal_pending_and_unknown_encoding_are_not_treated_as_readable(self):
        Path(str(self.db)+'-wal').write_bytes(b'pending')
        info=catalogue.describe(self.project)
        self.assertFalse(info['import_available']);self.assertTrue(any('journal' in w for w in info['warnings']))
        Path(str(self.db)+'-wal').unlink();self.db.write_bytes(b'not sqlite')
        self.assertFalse(catalogue.describe(self.project)['import_available'])

    def test_metadata_revision_change_refuses_old_preview(self):
        result=project_browser.request(self.s,dict(action='browse',folder=str(self.root)))
        with sqlite3.connect(self.db) as c:c.execute('UPDATE Hds_OdbSitemap SET mName="Changed"')
        with self.assertRaisesRegex(ValueError,'changed'):
            project_browser.request(self.s,dict(action='preview',browse_id=result['browse_id'],key=result['projects'][0]['key']))

    def test_unknown_text_schema_and_xml_entity_are_reported(self):
        (self.project/'ProjectInfo.xml').write_text('<!DOCTYPE Project [<!ENTITY x SYSTEM "file:///etc/passwd">]><Project><ProjectName>&x;</ProjectName></Project>')
        info=catalogue.describe(self.project)
        self.assertEqual(info['title_source'],'site_map');self.assertTrue(info['warnings'])

    def test_invalid_ini_does_not_hide_recognised_project(self):
        (self.project/'project.ini').write_text('not a valid ini file')
        info=catalogue.describe(self.project)
        self.assertEqual(info['title_source'],'site_map');self.assertTrue(info['warnings'])

    def test_snapshot_name_from_a_different_identity_is_not_used(self):
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE Projects (ProjectGuid TEXT,ProjectName TEXT)')
            c.execute('INSERT INTO Projects VALUES (?,?)',('cccccccc-1111-2222-3333-dddddddddddd','Other project'))
        self.assertEqual(catalogue.describe(self.project)['title_source'],'site_map')

    def test_project_asset_name_is_not_mistaken_for_project_title(self):
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE Hds_OdbProjectAsset (mProjectId TEXT,mName TEXT)')
            c.execute('INSERT INTO Hds_OdbProjectAsset VALUES (?,?)',(GUID,'Project icon'))
        self.assertEqual(catalogue.describe(self.project)['title_source'],'site_map')


if __name__=='__main__':unittest.main()
