# Copyright (c) 2026 Damian Gulczynski.
"""Curved gap reconstruction with independently known roof and labelled source faces."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

import compute_runtime
import mesh_geometry
import mesh_workflows
import meshing


def annulus(yscale=1):
    n = 16
    angle = np.arange(n)*2*np.pi/n
    rings = []
    for radius in (.05, .073, .09, .14, .20):
        x, y = radius*np.cos(angle), radius*np.sin(angle)*yscale
        rings.append(np.column_stack((x, y, .35*x*x+.17*y*y+.1*x*y)))
    vertices = np.vstack(rings) + [420000, 285000, 83]
    faces = []
    for k in range(len(rings)-1):
        for i in range(n):
            a, b = k*n+i, k*n+(i+1)%n
            faces.extend(((a,a+n,b+n), (a,b+n,b)))
    return vertices, np.asarray(faces, dtype=np.uint32)


class BoltGaps(unittest.TestCase):
    def test_reconstructs_curvature_leaves_outer_open_and_winds_consistently(self):
        vertices, faces = annulus()
        messages=[]
        v, f, result = meshing.fill_bolt_gaps(vertices, faces, progress=messages.append)
        self.assertEqual((result['filled'], result['preserved']), (1,1))
        np.testing.assert_array_equal(v[:len(vertices)],vertices)
        np.testing.assert_array_equal(f[:len(faces)],faces)
        x,y,z = (v[len(vertices):] - [420000,285000,83]).T
        np.testing.assert_allclose(z,.35*x*x+.17*y*y+.1*x*y,atol=1e-10)
        top=mesh_geometry.topology(v,f)
        self.assertEqual(top['boundary_edges'],16)
        edges={}
        for tri in f:
            for a,b in zip(tri,np.roll(tri,-1)):
                edges.setdefault(tuple(sorted((a,b))), []).append(1 if a<b else -1)
        self.assertTrue(all(len(v)==1 or len(v)==2 and sum(v)==0 for v in edges.values()))
        self.assertTrue(any('reconstructed' in msg for msg in messages))

    def test_protects_slots_out_of_range_and_prior_estimated_support(self):
        for v,f,options in [( *annulus(.2),{}), (*annulus(),{'gap_min_mm':110}),
                            (*annulus(),{'estimated_faces':np.ones(len(annulus()[1]), dtype=bool)})]:
            out_v, out_f, result=meshing.fill_bolt_gaps(v,f,**options)
            self.assertEqual(result['filled'],0)
            np.testing.assert_array_equal(out_v,v)
            np.testing.assert_array_equal(out_f,f)

    def test_open_tunnel_repair_then_capped_volume(self):
        # A nearly 100 mm opening in a cylindrical lining, distinct from its
        # two portals. The regular polygon supplies an independent volume oracle.
        import measure_tools
        from core import Alignment
        n = 128
        radius = .75
        angles = np.arange(n)*2*np.pi/n
        origin = np.array([420000., 285000., 83.])
        stations = np.linspace(-1., 1., 101)
        vertices = np.vstack([np.column_stack((np.full(n, s), radius*np.cos(angles),
                                                radius*np.sin(angles))) for s in stations]) + origin
        faces = []
        for section in range(len(stations)-1):
            for column in range(n):
                if 49 <= section < 52 and 31 <= column < 33:
                    continue
                a = section*n + column
                b = section*n + (column+1)%n
                faces.extend(((a,b,a+n),(b,b+n,a+n)))
        axis = Alignment([dict(chainage=1000+s,e=origin[0]+s,n=origin[1],h=origin[2],radius=radius)
                          for s in (-1,1)], 'Test alignment')
        with TemporaryDirectory() as path:
            root = Path(path)
            meta = meshing._write_mesh(root,'Cylindrical lining',vertices,np.asarray(faces))
            mesh = dict(id='lining',kind='mesh',name=meta['name'],meta=meta,visible=True)
            state = dict(axis=axis,job_items=[mesh],scene_objects=[],project_id='p')
            s = SimpleNamespace(STATE=state,WORK=root,save=lambda:None,progress=lambda _:None)
            inputs = dict(operation='tunnel_volume',object_id='lining',start_chainage=999.407,end_chainage=1000.607)
            with self.assertRaisesRegex(ValueError,'openings|boundary'):
                measure_tools.calculate(s,inputs)
            mesh_workflows.refine_mesh(s,dict(source_id=mesh['id'],operation='fill_bolt_gaps'))
            repaired = state['job_items'][-1]
            audit = repaired['meta']['repair_report']
            self.assertEqual((audit['filled'],audit['preserved']),(1,2))
            self.assertTrue(90 < audit['repairs'][0]['diameter_mm'] < 105)
            flags = np.fromfile(repaired['meta']['face_flags_file'],'u1')
            self.assertEqual(flags.sum(),audit['added_triangles'])
            result = measure_tools.calculate(s,{**inputs,'object_id':repaired['id']})
            poly_area = .5*n*radius**2*np.sin(2*np.pi/n)
            self.assertAlmostEqual(result['value'],poly_area*1.2,delta=3e-6)
            self.assertEqual(result['volume_check']['boundary_edges'],0)

    def test_checked_api_sizes_and_cancellation(self):
        v,f=annulus()
        for lo,hi in [(float('nan'),150),(100,20),(-1,30)]:
            with self.assertRaises(ValueError):
                meshing.fill_bolt_gaps(v,f,lo,hi)
        messages=[]
        with self.assertRaisesRegex(ValueError,'cancelled'):
            meshing.fill_bolt_gaps(v,f,progress=messages.append,
                                   cancel=lambda:any('Reconstructing' in text for text in messages))

    def test_real_refinement_flags_and_cancelled_output_cleanup(self):
        with TemporaryDirectory() as path:
            root = Path(path)
            v,f=annulus()
            meta=meshing._write_mesh(root,'Lining',v,f)
            # An estimated triangle far from the bolt keeps its original flag.
            flags=np.zeros(len(f),np.uint8);flags[-1]=1
            flag_path=root/'original.face_flags';flags.tofile(flag_path);meta['face_flags_file']=str(flag_path)
            item=dict(id='source',kind='mesh',name='Lining',meta=meta,visible=True)
            state=dict(job_items=[item])
            s=SimpleNamespace(STATE=state,WORK=root,save=lambda:None,progress=lambda m:None,
                              JOB_CANCEL_FILE=root/'cancel')
            body={'source_id':'source','operation':'fill_bolt_gaps','gap_min_mm':80,'gap_max_mm':110}
            mesh_workflows.refine_mesh(s,body)
            self.assertEqual(len(state['job_items']),2)
            out=state['job_items'][-1]['meta']
            stored=np.fromfile(out['face_flags_file'],dtype=np.uint8)
            np.testing.assert_array_equal(stored[:len(f)],flags)
            self.assertTrue(np.all(stored[len(f):]==1))
            self.assertEqual(out['mesh_summary']['reconstructed_triangles'],int(stored.sum()))
            self.assertEqual(out['repair_report']['repairs'][0]['estimated_face_start'],len(f))
            before=set(root.iterdir())
            s.progress=lambda m:s.JOB_CANCEL_FILE.touch() if m.startswith('Saving estimated') else None
            with self.assertRaises(compute_runtime.ProcessingCancelled):
                mesh_workflows.refine_mesh(s,body)
            self.assertEqual(len(state['job_items']),2)
            self.assertEqual(set(root.iterdir())-{s.JOB_CANCEL_FILE},before)
            np.testing.assert_array_equal(np.fromfile(flag_path,'u1'),flags)


if __name__ == '__main__':
    unittest.main()
