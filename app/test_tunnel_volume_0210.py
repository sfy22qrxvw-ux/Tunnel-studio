"""Independent engineering checks for a scan mesh cut at explicit chainages."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from core import Alignment
from jobdata import Cancelled
import measure_tools
import tunnel_volume


def alignment(centres):
    centres = np.asarray(centres, dtype=float)
    stations = 1000+np.r_[0., np.cumsum(np.linalg.norm(np.diff(centres, axis=0), axis=1))]
    return Alignment([dict(chainage=c, e=x, n=y, h=z, radius=4.)
                      for c, (x, y, z) in zip(stations, centres)], 'test axis')


def tube(rings):
    count = len(rings[0])
    triangles = []
    for i in range(len(rings)-1):
        for j in range(count):
            a, b = i*count+j, i*count+(j+1)%count
            triangles.extend(((a, b, a+count), (b, b+count, a+count)))
    return np.vstack(rings), np.asarray(triangles, dtype=np.uint32)


def rectangle(centre, right, up):
    return np.asarray([centre + x*right+y*up for x, y in ((-2,-1), (2,-1), (2,1), (-2,1))])


class Volume0210Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        a = np.array([452001., 581033., 48.])
        self.axis = alignment([a, a+np.array([6., 8., 2.])])
        self.lo, self.hi = self.axis.a[[0,-1], 0]
        rings = []
        for ch in np.linspace(self.lo, self.hi, 7):
            centre, _, right, up, _ = self.axis.at(ch)
            rings.append(rectangle(centre, right, up))
        self.vertices, self.faces = tube(rings)

    def mesh(self, vertices=None, faces=None):
        v = self.vertices if vertices is None else vertices
        f = self.faces if faces is None else faces
        vp, fp = self.folder/'wall.vertices64', self.folder/'wall.faces32'
        np.asarray(v, '<f8').tofile(vp)
        np.asarray(f, '<u4').tofile(fp)
        return dict(kind='mesh', id='mesh1', name='Repaired lining',
                    meta=dict(vertices_file=str(vp), faces_file=str(fp), vertices=len(v), triangles=len(f)))

    def test_sloping_survey_frame_cuts_and_reverse_winding(self):
        for f in (self.faces, self.faces[:, ::-1]):
            result = tunnel_volume.measure(self.mesh(faces=f), self.axis, self.lo+1.25, self.lo+8.75)
            self.assertAlmostEqual(result['volume_m3'], 8*7.5, places=7)
            self.assertAlmostEqual(result['start_area_m2'], 8., places=7)
            self.assertAlmostEqual(result['end_area_m2'], 8., places=7)
            self.assertEqual(result['boundary_edges'], 0)

    def test_end_planes_on_mesh_rings(self):
        result = tunnel_volume.measure(self.mesh(), self.axis, self.lo, self.hi)
        self.assertAlmostEqual(result['volume_m3'], 8*(self.hi-self.lo), places=7)

    def test_curved_reach_distinct_end_normals(self):
        a = np.array([1000., 2000., 1.])
        bends = [a, a+[10,0,0], a+[16,8,0]]
        axis = alignment(bends)
        r0 = np.array([0.,-1.,0.])
        r1 = np.array([.8,-.6,0.])
        miter = (r0+r1)/(1+r0@r1)
        up = np.array([0.,0.,1.])
        rings = [rectangle(c,r,up) for c,r in zip(bends, (r0,miter,r1))]
        v,f = tube(rings)
        result = tunnel_volume.measure(self.mesh(v,f), axis, 1003.25, 1017.5)
        self.assertAlmostEqual(result['volume_m3'], 8*(17.5-3.25), places=7)

    def test_interior_hole_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'openings|boundary'):
            tunnel_volume.measure(self.mesh(faces=np.delete(self.faces,19,axis=0)), self.axis, self.lo, self.hi)

    def test_invalid_faces_and_extra_shell(self):
        for face,reason in (([0,0,1],'degenerate'),(self.faces[9],'non-manifold')):
            with self.assertRaisesRegex(ValueError,reason):
                tunnel_volume.measure(self.mesh(faces=np.vstack((self.faces,face))),self.axis,self.lo,self.hi)
        v = np.vstack((self.vertices,self.vertices+[0,12,0]))
        f = np.vstack((self.faces,self.faces+len(self.vertices)))
        with self.assertRaisesRegex(ValueError,'multiple|openings'):
            tunnel_volume.measure(self.mesh(v,f),self.axis,self.lo,self.hi)

    def test_alignment_and_range_guards(self):
        for axis,start,end in ((None, self.lo,self.hi),(self.axis,self.hi,self.lo),
                               (self.axis,self.lo-1,self.hi),(self.axis,self.lo,float('nan'))):
            with self.assertRaises(ValueError):
                tunnel_volume.measure(self.mesh(),axis,start,end)
        axis = alignment([[0,0,0],[10,0,0],[10,10,0],[0,10,0]])
        with self.assertRaisesRegex(ValueError, 'Another part'):
            tunnel_volume.measure(self.mesh(), axis, 1002., 1008.)

    def test_cancelled_at_native_validation(self):
        messages=[]
        with self.assertRaises(Cancelled):
            tunnel_volume.measure(self.mesh(),self.axis,self.lo,self.hi, messages.append,
                                  lambda: bool(messages and 'validating the closed solid' in messages[-1]))

    def test_editable_saved_snapshot_and_axis_change(self):
        source = self.mesh()
        state = dict(axis=self.axis, job_items=[source], project_id='p', scene_objects=[])
        service = SimpleNamespace(STATE=state, JOB={}, WORK=self.folder, save=lambda:None,
                                  start_job=lambda fn,*args,**kwargs:fn(),progress=lambda text:None)
        handler = SimpleNamespace(send=lambda value:None)
        stamp = lambda: {k:measure_tools.state(service)[k] for k in ('context','revision')}
        inputs = dict(operation='tunnel_volume', object_id='mesh1', start_chainage=self.lo+1, end_chainage=self.lo+3)
        measure_tools.post(service,handler,'/api/measure-preview',dict(input=inputs,**stamp()))
        prev=service._measure_preview
        self.assertAlmostEqual(prev['result']['value'],16,places=7)
        measure_tools.post(service,handler,'/api/measure-action',dict(action='save', token=prev['token'], name='Reach A',**stamp()))
        saved=measure_tools.state(service)['measurements'][0]['data']
        self.assertEqual(saved['inputs'],inputs)
        self.assertEqual(saved['volume_check']['boundary_edges'],0)
        self.assertAlmostEqual(saved['metrics'][0]['value'],16,places=7)
        measure_tools.post(service,handler,'/api/measure-preview',dict(input=inputs,**stamp()))
        stale=service._measure_preview['token']
        self.axis.name='Replacement alignment'
        with self.assertRaisesRegex(ValueError,'Calculate and review'):
            measure_tools.post(service,handler,'/api/measure-action',dict(action='save',token=stale,**stamp()))

if __name__=='__main__':
    unittest.main()
