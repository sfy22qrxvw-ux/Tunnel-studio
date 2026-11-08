"""Full path/owner searches must include small shared files without re-walking a large workspace."""
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import projects
from storage_catalog import Catalog


class OwnershipCatalogueTests(unittest.TestCase):
    def test_all_files_search_includes_shared_cache_outside_top_100_and_sql_literals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in range(151):
                (root/f'Original_{n:03}.e57').write_bytes(b'z'*6000)
            cache=root/('1'*32+'.xyz64');cache.write_bytes(b'123')
            for title in ('First tunnel','Second – Łódź'):
                projects.save_named(root,{'assets':[{'file':str(cache)}]},title,True)
            catalog=Catalog()
            info=catalog.scan(root,{})
            token=info['catalog_token']
            try:
                self.assertFalse(info['catalog_live_pages'])
                self.assertEqual(info['catalog_files'],154)
                self.assertNotIn(str(cache),[r['path'] for r in info['files']])
                with patch('storage_inventory.files_in',side_effect=AssertionError('cache must not rescan files')):
                    all_rows=[r for i in range(0,154,50) for r in catalog.page(root,None,token,offset=i)['files']]
                    self.assertEqual(len({r['path'] for r in all_rows}),154)
                    small=catalog.page(root,None,token,text=cache.name)['files'][0]
                    self.assertEqual(small['owners'],['First tunnel','Second – Łódź'])
                    self.assertIn(str(cache),[f['path'] for f in catalog.page(root,None,token,text='łÓDź')['files']])
                    self.assertEqual(catalog.page(root,None,token,text='%_')['total'],0)
                with self.assertRaises(ValueError):catalog.page(root/'other',None,token)
                with self.assertRaises(ValueError):catalog.page(root,None,'forged')
            finally:catalog._expire(token)

    def test_temporary_index_unavailable_rescans_only_explicit_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i in range(4):(root/f'item{i}.csv').write_text('survey')
            catalog=Catalog()
            with patch('storage_catalog.sqlite3.connect',side_effect=sqlite3.OperationalError('simulated temporary drive full')):
                info=catalog.scan(root,{})
            try:
                self.assertTrue(info['catalog_live_pages'])
                result=catalog.page(root,lambda:{},info['catalog_token'],'ITEM',offset=2,limit=2)
                self.assertEqual(result['total'],4);self.assertEqual(len(result['files']),2)
            finally:catalog._expire(info['catalog_token'])

    def test_all_hardlink_names_searchable_with_unique_logical_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'alpha.txt';alias=root/'beta.txt'
            original.write_bytes(b'1234')
            try:os.link(original,alias)
            except OSError as error:self.skipTest(str(error))
            catalog=Catalog();info=catalog.scan(root,{})
            try:
                self.assertEqual(info['used_bytes'],4)
                self.assertEqual(info['catalog_files'],2)
                page=catalog.page(root,None,info['catalog_token'])
                self.assertEqual({r['name'] for r in page['files']},{'alpha.txt','beta.txt'})
            finally:catalog._expire(info['catalog_token'])

if __name__=='__main__':unittest.main()
