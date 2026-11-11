# Copyright (c) 2026 Damian Gulczynski.
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

import cad_api
import cad_geometry
import mesh_openings
import mesh_repair
import mesh_workflows
import meshing
from registration_pose import revision
from surface_api import patch_fingerprint
from test_bolt_gaps_0210 import annulus


class OpeningInventoryTests(unittest.TestCase):
    def test_detects_round_hole_but_preserves_all_boundaries(self):
        for flatten, inferred, expected in ((1, False, 1), (.25, False, 0), (1, True, 0)):
            with self.subTest(flatten=flatten, inferred=inferred):
                vertices, faces = annulus(flatten)
                flags = np.zeros(len(faces), dtype=np.uint8)
                flags[1] = inferred
                result = mesh_openings.inspect(vertices, faces, flags, None, None, lambda _:None, lambda:False)
                self.assertEqual(result['summary']['circular_rims'], expected)
                self.assertEqual(result['summary']['other_boundaries'], 2-expected)
                self.assertFalse(result['patches'])
                if expected:
                    rim, = result['holes']
                    self.assertEqual(rim['reason'], 'Unassigned')
                    self.assertAlmostEqual(rim['diameter_mm'], 100., places=6)

    def test_only_current_measured_panel_contains_rim_across_angle_wrap(self):
        with TemporaryDirectory() as path:
            root = Path(path)
            vertices, faces = annulus()
            meta = meshing._write_mesh(root, 'Lining', vertices, faces)
            scan_path = root / 'source.xyz64'
            np.zeros(3, dtype='<f8').tofile(scan_path)
            scan = dict(id='scan', kind='scan', name='Scan', meta=dict(file=str(scan_path), count=1))
            scan_revision = revision(scan['meta'])
            fingerprint = patch_fingerprint(scan)
            meta['mesh_summary'].update(source_item_id='scan', source_geometry_revision=scan_revision,
                                        source_fingerprint=fingerprint)
            origin = np.array([420000., 285000., 80.])

            def world(station, angle):
                return (origin + [station, 3*np.sin(angle), 3*np.cos(angle)]).tolist()

            corners = [world(s, a) for s,a in ((-.4,-.2), (-.4,.2), (.4,.2), (.4,-.2))]
            panel = dict(ring_interval=1, panel_number=2, label_centre=world(0,0),
                         boundary_sources=['measured']*4,
                         panel_outline=[[c, corners[(i+1)%4]] for i,c in enumerate(corners)])
            layout = dict(revision='layout', settings=dict(method='lining_features'), source_item_id='scan',
                          source_meta=scan['meta'].copy(), source_fingerprint=fingerprint, clusters=[panel],
                          lining_frame=dict(reference=origin.tolist(), axis=[1,0,0], u=[0,0,1], v=[0,1,0],
                                            nodes=[[-1,0,0,3],[1,0,0,3]], radius_m=3.))
            mesh = dict(id='lining', kind='mesh', name='Lining', meta=meta)
            s = SimpleNamespace(STATE=dict(project_id='test', job_items=[mesh, scan], surface_result=layout),
                                WORK=root, save=lambda:None, progress=lambda _:None)
            preview = mesh_repair.create_preview(s, 'lining')
            self.assertTrue(preview['inventory_only'])
            self.assertIsNone(preview['preview_token'])
            row, = preview['holes']
            self.assertEqual((row['ring_interval'], row['panel_number'], row['panel_observed_count']), (1,2,1))
            self.assertEqual(row['cap_faces'], [])
            panel['boundary_sources'][1] = 'inferred'
            self.assertEqual(mesh_repair.create_preview(s, 'lining')['summary']['unassigned'], 1)
            scan['limit_box'] = {'source_changed': True}
            self.assertFalse(mesh_repair.create_preview(s, 'lining')['summary']['layout_available'])
            for operation in ('fill_bolt_gaps', 'fill_small', 'watertight'):
                with self.assertRaisesRegex(ValueError, 'Inspect openings'):
                    mesh_workflows.refine_mesh(s, dict(source_id='lining', operation=operation))
            with self.assertRaisesRegex(ValueError, 'Inspect openings'):
                mesh_repair.apply_preview(s, 'lining', 'old-token', ['hole-1'])
            with self.assertRaisesRegex(ValueError, 'Inspect openings'):
                mesh_workflows.create_from_scan(s, dict(source_id='scan', hole_handling='fill_small'))
            with self.assertRaisesRegex(ValueError, 'Inspect openings'):
                mesh_workflows.create_from_scan(s, dict(source_id='scan', engine='watertight_tunnel'))
            self.assertEqual(s.STATE['job_items'], [mesh, scan])
            np.testing.assert_array_equal(np.fromfile(meta['faces_file'], '<u4').reshape(-1,3), faces)

    def test_cad_repair_still_offers_explicit_caps(self):
        vertices, faces = annulus()
        geometry = cad_geometry.geometry(vertices, faces, shape='surface')
        cad = cad_api.empty()
        obj = cad_api.object_record(geometry, dict(name='Synthetic CAD surface'))
        cad['objects'].append(obj)
        s = SimpleNamespace(STATE=dict(project_id='test', cad=cad))
        preview = mesh_repair.create_preview(s, obj['id'])
        self.assertFalse(preview['inventory_only'])
        self.assertIsNotNone(preview['preview_token'])
        self.assertTrue(any(row['cap_faces'] for row in preview['holes']))


if __name__ == '__main__':
    unittest.main()
