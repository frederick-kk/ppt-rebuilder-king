"""Prefer the first complete geometry among one call's existing board returns."""
import copy
from pathlib import Path
from unittest import mock
import unittest

import numpy as np
from PIL import Image

import test_bounded_flow_recovery as recovery_tests
from test_result_role_binding import ReviewedOutputFixture
from newpath import controller as c
from newpath.storage import read_state, sha256


class BoardSurplusGeometry(ReviewedOutputFixture):
    def start(self):
        return recovery_tests.BoundedFlowRecovery.start(self,policy=getattr(self,'recovery', 'bounded-recovery/1'))

    def image(self,name,*,fused=False,color=(20,130,240)):
        raw=np.zeros((400,400,4),dtype='uint8')
        raw[...,0:3]=color
        raw[60:150,150:250,3]=255
        raw[250:340,150:250,3]=255
        if fused:raw[150:250,180:220,3]=128
        path=self.root/name
        Image.fromarray(raw).save(path)
        return path

    def board(self):
        jobs=self.start()
        for role,path in [('background',self.bg),('foreground',self.fg)]:
            self.begin(jobs[role]);self.submit(jobs[role],path)
        reference=self.image('job/reference.png',color=(230,230,230))
        items=[{'id':'upper','kind':'art','box':[100,100,80,90],'description':'upper synthetic icon'},
               {'id':'lower','kind':'art','box':[400,100,80,90],'description':'lower synthetic icon'}]
        board={'id':'board-synthetic','signature':'synthetic-board-surplus','image':str(reference),
               'size':[400,400],'key':None,'background_mode':'native-transparent-materials/1',
               'grid':{'rows':2,'columns':1,'margin':20,'gap':20,'cell_size':[360,170]},
               'members':[{**items[0],'slot':[20,20,360,170],'target':items[0]['box']},
                          {**items[1],'slot':[20,210,360,170],'target':items[1]['box']}]}
        with mock.patch.object(c,'prepare_boards',return_value=[board]):
            task=c.run(self.work,data={'kind':'layout','layout':{'items':items}})['jobs'][0]
        self.begin(task)
        return task,reference

    def surplus(self,task,paths,reviews=None,output_ids=None):
        if reviews is None:reviews=[self.review(task,path) for path in paths]
        ids=output_ids or [None]*len(paths)
        return c.run(self.work,data=self.payload(task,paths[0],output_count=len(paths),
            returned_files=[str(path) for path in paths],returned_outputs=[
                {'file':str(path),'role_review':review,'tool_output_id':identifier}
                for path,review,identifier in zip(paths,reviews,ids)]))

    def test_second_existing_complete_board_is_used_without_new_generation(self):
        task,reference=self.board()
        first=self.image('first.png',fused=True);second=self.image('second.png')
        response=self.surplus(task,[first,second])
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(second))
        self.assertEqual(saved['surplus_resolution']['selected_index'],1)
        self.assertFalse(saved['surplus_resolution']['returns'][0]['cell_geometry_preflight']['complete'])
        self.assertEqual({asset['id'] for asset in state['assets']},{'upper','lower'})
        self.assertEqual(response['kind'],'done')
        self.assertEqual(state['recovery']['used'],0)
        self.assertEqual(saved['attempt'],0)
        self.assertEqual(response['caller_reported_invocations'],3)
        for entry in saved['surplus_resolution']['returns']:
            self.assertTrue((self.work/entry['raw']).is_file())

    def test_first_complete_board_keeps_original_selection(self):
        task,reference=self.board()
        first=self.image('first.png');second=self.image('second.png',color=(40,240,70))
        self.surplus(task,[first,second])
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(first))
        self.assertEqual(saved['surplus_resolution']['selected_index'],0)

    def test_all_partial_boards_keep_original_missing_asset_delivery(self):
        task,reference=self.board()
        first=self.image('first.png',fused=True)
        second=self.image('second.png',fused=True,color=(50,230,80))
        response=self.surplus(task,[first,second])
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(first))
        self.assertEqual(saved['surplus_resolution']['selected_index'],0)
        self.assertEqual(response['kind'],'done')
        self.assertEqual(state['assets'],[])
        self.assertTrue(any(w.get('code')=='ambiguous_member_omitted' for w in state['warnings']))

    def test_wrong_role_complete_board_does_not_bypass_real_review(self):
        task,reference=self.board()
        first=self.image('first.png');second=self.image('second.png',color=(50,230,80))
        reviews=[self.review(task,first,matches_task=False,observed='Actual first return has wrong assigned subjects.'),
                 self.review(task,second)]
        self.surplus(task,[first,second],reviews)
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(second))
        self.assertTrue(saved['role_review_rejections'])

    def test_missing_review_is_not_invented_by_complete_geometry(self):
        task,reference=self.board()
        first=self.image('first.png');second=self.image('second.png',color=(50,230,80))
        response=self.surplus(task,[first,second],[None,self.review(task,second)])
        self.assertEqual(response['kind'],'registration_required')
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_source_reference_return_remains_hard_rejected(self):
        task,reference=self.board()
        good=self.image('good.png')
        response=self.surplus(task,[reference,good])
        self.assertEqual(response['kind'],'paused')
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_cross_task_file_reuse_remains_hard_rejected(self):
        task,reference=self.board()
        good=self.image('good.png')
        response=self.surplus(task,[self.bg,good])
        self.assertEqual(response['kind'],'paused')
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_old_job_keeps_first_partial_board_selection(self):
        self.recovery=None
        task,reference=self.board()
        first=self.image('first.png',fused=True);second=self.image('second.png')
        self.surplus(task,[first,second])
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(first))
        self.assertEqual(saved['surplus_resolution']['selected_index'],0)
        self.assertNotIn('cell_geometry_preflight',saved['surplus_resolution']['returns'][0])


if __name__=='__main__':
    unittest.main()
