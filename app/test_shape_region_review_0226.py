"""Coverage membership, global order and inspection navigation share one view."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import feature_selection
import shape_inspection
import shape_matching
import shape_selection


class ShapeRegionReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Path(temp.name) / 'review.sqlite'
        self.matches = [(i, float(71-i), .2, .70+(i % 7)*.01, 1, 10+i, 10+i)
                        for i in range(1,71)]
        self.region_points = {i: i % 9+1 for i in range(1,71)}
        with sqlite3.connect(db) as connection:
            connection.execute(shape_matching.MATCH_SCHEMA)
            connection.execute(shape_matching.REGION_SCHEMA)
            connection.executemany('INSERT INTO matches VALUES(?,?,?,?,?,?,?)',self.matches)
            connection.executemany('INSERT INTO match_regions VALUES(?,?,?)',
                                   ((region,i,n if region==0 else 10+i-n)
                                    for i,n in self.region_points.items() for region in (0,1)))
        self.row = dict(review_file=str(db),id='shape',revision='initial',source_id='scan',source_name='Fixture',
                        mode='shape',settings={},statistics={},warnings=[],method='',research_dois=[],
                        coverage=[dict(region=name,candidate_count=size)
                                  for name,size in (('Crown',70),('Upper side A',70),('Side A',0))])
        self.server = SimpleNamespace(SHAPE_SELECTION=self.row,STATE={'job_items':[]})

    def view(self, page=0, region=None, sort='id'):
        return shape_selection.public(self.server,page,region,sort,include_points=False)

    def test_score_order_applies_before_pagination_with_stable_ties(self):
        best = sorted(self.matches,key=lambda row:(-row[3],row[0]))
        for page,expected in ((0,best[:40]),(1,best[40:])):
            result = self.view(page,sort='best')
            self.assertEqual(result['group_count'],70)
            self.assertEqual([group['id'] for group in result['groups']],[row[0] for row in expected])
            self.assertNotIn('preview',result)
        self.assertEqual(self.view(sort='worst')['groups'][0]['id'],7)
        self.assertEqual(self.view(sort='station')['groups'][0]['id'],70)

    def test_region_point_sort_and_cross_region_counts(self):
        first,second = self.view(region=0,sort='most'),self.view(region=1)
        expected = sorted(self.region_points,key=lambda key:(-self.region_points[key],key))
        self.assertEqual([group['id'] for group in first['groups']],expected[:40])
        for group in second['groups']:
            self.assertEqual(group['region_points']+self.region_points[group['id']],group['points'])
        self.assertEqual(self.view(page=9,region=2)['groups'],[])
        self.assertEqual(self.view(page=9,region=2)['page'],0)
        self.assertEqual(self.view(region=2)['total_group_count'],70)

    def test_inspection_neighbours_follow_filtered_order_across_pages(self):
        result = self.view(region=1,sort='fewest')
        current = result['groups'][-1]
        following = self.view(1,1,'fewest')['groups'][0]
        before = shape_inspection._group(self.row,current['id'],1,'fewest')
        after = shape_inspection._group(self.row,following['id'],1,'fewest')
        self.assertEqual((before['next'],before['page']),(following['id'],0))
        self.assertEqual((after['previous'],after['page']),(current['id'],1))
        with self.assertRaisesRegex(ValueError,'unavailable'):
            shape_inspection._group(self.row,current['id'],2,'fewest')

    def test_unknown_region_and_sort_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'coverage region'):
            self.view(region=8)
        with self.assertRaisesRegex(ValueError,'sort order'):
            self.view(sort='score; DROP TABLE matches')

    def test_bulk_deselection_preserves_other_groups_with_a_small_sqlite_parameter_limit(self):
        folder = Path(self.row['review_file']).parent
        n = 1202
        coordinates = folder / 'scan.xyz64'
        labels = folder / 'matches.labels'
        flags = folder / 'original.selected'
        np.zeros((n * 2, 3), '<f8').tofile(coordinates)
        np.repeat(np.arange(1, n+1, dtype='<i4'), 2).tofile(labels)
        np.ones(n * 2, 'u1').tofile(flags)
        item = dict(id='scan', kind='scan', meta=dict(file=str(coordinates), count=n*2))
        self.server.STATE['job_items'] = [item]
        self.server.progress = lambda _: None
        self.row.update(folder=str(folder), labels_file=str(labels), selected_file=str(flags),
                        count=n*2, source_fingerprint=feature_selection.fingerprint(item))
        self.row['statistics']['selected_points'] = n*2
        with sqlite3.connect(self.row['review_file']) as db:
            db.execute('DELETE FROM matches')
            db.executemany('INSERT INTO matches VALUES(?,?,?,?,?,?,?)',
                           ((i, 0, 0, 1, 1, 2, 2) for i in range(1, n+1)))
        connect = sqlite3.connect

        def limited(path):
            connection = connect(path)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return connection

        original = coordinates.read_bytes()
        with patch.object(shape_selection.sqlite3, 'connect', side_effect=limited):
            shape_selection.choose(self.server, dict(
                action='select', operation='toggle', preview_id='shape', revision='initial',
                group_ids=[*range(1, n), 1], selected=False))
        self.assertEqual(self.row['statistics']['selected_points'], 2)
        self.assertEqual(np.flatnonzero(np.fromfile(self.row['selected_file'], 'u1')).tolist(),
                         [2*n-2, 2*n-1])
        self.assertEqual(coordinates.read_bytes(), original)

    def test_close_up_joins_original_ids_and_preserves_review_colours(self):
        folder = Path(self.row['review_file']).parent
        coordinates = folder / 'scan.xyz64'
        points = np.column_stack((np.arange(12), np.zeros((12, 2)))).astype('<f8')
        points.tofile(coordinates)
        mask = folder / 'deleted.mask'
        excluded = np.zeros(12, 'u1')
        excluded[4] = 1
        excluded.tofile(mask)
        item = dict(id='scan', kind='scan', meta=dict(file=str(coordinates), count=len(points),
                    minimum=points.min(0).tolist(), maximum=points.max(0).tolist(), mask_file=str(mask)))
        self.server.STATE['job_items'] = [item]
        for key, values, dtype in (
                ('point_ids_file', [2, 5, 6, 9], '<i8'),
                ('labels_file', [1, 1, 2, 2], '<i4'),
                ('selected_file', [1, 1, 0, 1], 'u1')):
            file = folder / key
            np.asarray(values, dtype=dtype).tofile(file)
            self.row[key] = str(file)
        self.row.update(count=4, source_fingerprint=feature_selection.fingerprint(item))
        view = dict(target=[5, 0, 0], right=[1, 0, 0], up=[0, 0, 1], forward=[0, 1, 0],
                    scale=1.6, aspect=1)
        review = dict(preview_id='shape', revision='initial')
        before = coordinates.read_bytes()
        result = shape_selection.view_detail(self.server, review, view)
        self.assertEqual(result['match_ids'], [1, 2])
        close = result['packed']
        np.testing.assert_array_equal(close[:, :3]+result['origin'], points[[5, 6]])
        np.testing.assert_allclose(close[result['match_offsets'], 3:], [[1, .55, .12], [.1, .8, .85]])
        with sqlite3.connect(self.row['review_file']) as db:
            db.execute('DELETE FROM match_regions WHERE region=0 AND match_id=2')
        regional = shape_selection.view_detail(self.server, review, view, region=0)
        self.assertEqual(regional['match_ids'], [1])
        self.assertEqual(coordinates.read_bytes(), before)
        self.assertEqual(self.row['revision'], 'initial')
