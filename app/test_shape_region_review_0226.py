"""Coverage membership, global order and inspection navigation share one view."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

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
