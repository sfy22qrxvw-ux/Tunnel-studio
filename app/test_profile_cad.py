"""The designer uses the Model CAD API, with a fixed-plane round trip."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import cad_api
import cad_geometry as g
import profile_cad
import tunnel_designer as designer
import workspace_history as history


class ProfileCadTests(unittest.TestCase):
    def setUp(self):
        self.s=SimpleNamespace(STATE={'project_id':'profile-test','cad':cad_api.empty()},
                               JOB={'busy':False},save=lambda:None,progress=lambda _:None)
        self.s.start_job=lambda run,**_:history.background(self.s,run)()
        self.design=designer.default()
        self.responses=[]
        self.handler=SimpleNamespace(send=self.responses.append)

    def call(self,path,body,workspace):
        request={**body,'context':cad_api.context(workspace),'revision':cad_api.current(workspace)['revision']}
        handler=cad_api if path=='/api/cad-action' else designer
        with history.request(self.s,path):
            history.configure(path,request)
            self.assertTrue(handler.post(self.s,self.handler,path,request))
        return self.responses[-1]

    def begin(self):
        result=self.call('/api/tunnel-design-action',dict(action='profile_begin',design=self.design,profile_id='p1'),self.s)
        self.edit=cad_api.workspace(self.s)
        return result['ids']

    def cad(self,action,**body):
        return self.call('/api/cad-action',dict(action=action,**body),self.edit)

    def finish(self,action='profile_finish',**body):
        return self.call('/api/tunnel-design-action',dict(action=action,edit_id=self.edit.id,**body),self.edit)

    def last(self):
        return cad_api.current(self.edit)['last_created']

    def test_same_cad_commands_and_undo_with_full_project_budget(self):
        obj=cad_api.object_record(g.build(dict(kind='line',points=[[50,0,0],[51,0,0]])),{})
        self.s.STATE['cad']['objects']=[obj]
        original=copy.deepcopy(self.s.STATE)
        with patch.object(g,'MAX_PROJECT_VERTICES',2):
            circle,=self.begin()
            self.cad('create',spec=dict(kind='rectangle',points=[[-2,-1,0],[2,-1,0],[2,4,0],[-2,4,0]]))
            self.assertEqual(history.status(self.edit)['undo_count'],1)
            self.assertEqual(history.status(self.s)['undo_count'],0)
            self.assertEqual(self.s.STATE,original)
            history.apply(self.edit)
            self.assertEqual([o['id'] for o in cad_api.current(self.edit)['objects']],[circle])
            history.apply(self.edit,redo=True)
            result=self.finish()
        profile=result['design']['profiles'][0]
        self.assertEqual(profile['kind'],'composite')
        self.assertEqual(len(profile['drawing_ids']),2)
        self.assertEqual(self.s.STATE['cad'],original['cad'])
        self.assertIs(cad_api.workspace(self.s),self.s)
        self.assertEqual(history.status(self.s)['undo_label'],'Edit tunnel profile')
        history.apply(self.s)
        self.assertEqual(self.s.STATE['cad'],original['cad'])
        self.assertNotIn('tunnel_design',self.s.STATE)

    def test_grips_and_transforms_keep_exact_signed_radii(self):
        self.design['profiles'][0].update(kind='custom',nodes=[[-2,-2,0],[2,-2,0],[2,2,3],[-2,2,0]])
        ident,=self.begin()
        self.cad('curve_grip',ids=[ident],grip='arc',index=0,point=[0,-2.5,0])
        self.cad('curve_grip',ids=[ident],grip='node',index=1,point=[2,-2.2,0])
        self.cad('curve_grip',ids=[ident],grip='edge',index=2,point=[0,2.8,0])
        self.cad('mirror',ids=[ident],normal=[1,0,0])
        self.cad('rotate',ids=[ident],axis=[0,0,1],angle=15)
        self.cad('scale',ids=[ident],factor=2)
        self.cad('move',ids=[ident],vector=[2.,3.,0.])
        result=self.finish()
        nodes=np.asarray(result['design']['profiles'][0]['nodes'])
        self.assertEqual(len(nodes),4)
        np.testing.assert_allclose(nodes[:,2],[-8.5,0,-6,0])

    def test_trim_join_break_extend_and_offset_use_curves(self):
        self.design['profiles'][0]['radius']=3
        circle,=self.begin()
        self.cad('offset',ids=[circle],distance=.5,frame=self.edit.frame)
        copy_id,=self.last()
        self.assertEqual(cad_api.get_object(cad_api.current(self.edit),copy_id)['radius'],3.5)
        self.cad('visibility',ids=[copy_id],visible=False)
        self.cad('create',spec=dict(kind='line',points=[[-4,0,0],[4,0,0]]))
        boundary,=self.last()
        self.cad('trim',ids=[circle,boundary],keep=[0,3,0])
        arc,=self.last()
        nodes=g.curve_nodes(cad_api.get_object(cad_api.current(self.edit),arc),self.edit.frame)
        self.assertTrue(np.all(nodes[:-1,2]==3))
        self.assertAlmostEqual(np.max(nodes[:,1]),3.)
        self.cad('break',ids=[arc],point=[3/np.sqrt(2),3/np.sqrt(2),0])
        self.cad('join',ids=list(reversed(self.last())))
        rejoined,=self.last()
        self.cad('create',spec=dict(kind='line',points=[[3,0,0],[-3,0,0]]))
        line,=self.last()
        self.cad('join',ids=[line,rejoined])
        outline,=self.last()
        result=self.finish(ids=[outline])
        self.assertEqual(result['design']['profiles'][0]['kind'],'custom')
        self.assertTrue(any(abs(n[2])==3 for n in result['design']['profiles'][0]['nodes']))
        f=g.frame()
        native=g.build(dict(kind='arc',centre=[0,0,0],radius=3,start_angle=0,end_angle=30,frame=f))
        extended=g.trim_planar_curve(native,[[0,-4,0],[0,4,0]],f,True,'end',None)
        np.testing.assert_allclose(extended['vertices'][-1],[0,3,0],atol=1e-12)
        np.testing.assert_allclose(np.linalg.norm(np.asarray(extended['vertices']),axis=1),3.)

    def test_linked_outline_keeps_id_frame_and_cancel_discards_edits(self):
        frame=g.frame(dict(origin=[400000,200000,110],x=[0,1,0],y=[0,0,1]))
        obj=cad_api.object_record(g.build(dict(kind='circle',centre=frame['origin'],radius=3.775,frame=frame)),dict(name='Linked'))
        self.s.STATE['cad']['objects']=[obj]
        self.design['profiles'][0].update(kind='cad',object_id=obj['id'],x=.5)
        ident,=self.begin()
        self.assertEqual(ident,obj['id'])
        self.cad('move',ids=[ident],vector=[0,2,1])
        self.finish('profile_cancel')
        self.assertEqual(self.s.STATE['cad']['objects'][0],obj)
        self.assertNotIn('tunnel_design',self.s.STATE)
        self.begin()
        self.cad('move',ids=[ident],vector=[0,2,1])
        result=self.finish()
        linked=self.s.STATE['cad']['objects'][0]
        self.assertEqual(linked['id'],obj['id'])
        self.assertEqual(linked['radius'],3.775)
        profile=result['design']['profiles'][0]
        xy=designer.outline(profile)
        np.testing.assert_allclose((xy.min(0)+xy.max(0))/2,[2.5,1.])

    def test_invalid_outline_and_stale_stage_do_not_change_design(self):
        circle,=self.begin()
        self.cad('visibility',ids=[circle],visible=False)
        self.cad('create',spec=dict(kind='line',points=[[0,0,0],[2,2,0]]))
        with self.assertRaisesRegex(ValueError,'Close the polyline'):
            self.finish()
        self.assertNotIn('tunnel_design',self.s.STATE)
        self.assertIs(cad_api.workspace(self.s),self.edit)
        self.s.STATE['cad']={**self.s.STATE['cad'],'revision':'changed'}
        with self.assertRaisesRegex(ValueError,'Reopen the profile'):
            self.finish()
        self.assertNotIn('tunnel_design',self.s.STATE)


if __name__=='__main__':unittest.main()
