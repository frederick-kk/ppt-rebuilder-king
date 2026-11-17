"""Recover only reference-anchored complete groups, keeping original pixels."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import test_weak_native_grid_specks as weak_tests
from newpath import cellmap
from newpath.artwork import extract_assets
from newpath.native import MODE


class ReferenceFragmentRecovery(unittest.TestCase):
    setUp=weak_tests.WeakNativeGridSpecks.setUp

    def fixture(self):
        board={'id':'large-reference-fixture','size':[600,300],'key':None,'background_mode':MODE,
            'grid':{'rows':1,'columns':2,'margin':30,'gap':60,'cell_size':[240,240]},
            'members':[{'id':'left','slot':[30,30,240,240],'box':[100,100,100,80]},
                       {'id':'right','slot':[330,30,240,240],'box':[400,100,100,80]}]}
        alpha=np.zeros((300,600),dtype='uint8')
        alpha[90:210,75:225]=255
        alpha[90:210,375:525]=255
        reference=alpha.copy()
        alpha[215,75:225]=5
        alpha[140:145,290:297]=2
        for y,x in [(y,x) for y in range(50,80,3) for x in range(280,310,3)][:80]:
            alpha[y,x]=2
        return board,alpha,reference

    def test_finite_reference_context_attaches_whole_faint_frame_and_recovers_members(self):
        board,alpha,reference=self.fixture()
        before=alpha.copy()
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertEqual(result['unsafe_members'],[])
        report=result['reference_fragment_recovery']
        self.assertEqual(report['ignored_geometry_pixels'],115)
        self.assertEqual(report['ignored_alpha_limit'],3)
        self.assertEqual(report['max_ignored_alpha'],2)
        self.assertEqual(report['per_component_budget_px'],57)
        self.assertEqual(report['total_budget_px'],176)
        self.assertTrue(any(item['owner']=='left' and item['max_alpha']==5
                            for item in report['attached_fragments']))
        self.assertEqual(set(result['member_geometry_boxes']),{'left','right'})
        np.testing.assert_array_equal(alpha,before)

    def test_component_over_relative_budget_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[140:145,290:297]=0
        alpha[140:148,290:298]=2
        self.assertEqual(cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)['unsafe_members'],
                         ['left','right'])

    def test_total_over_relative_budget_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        points=[(y,x) for y in range(40,200,3) for x in range(280,310,3)]
        for y,x in points[:200]:alpha[y,x]=2
        self.assertEqual(cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)['unsafe_members'],
                         ['left','right'])

    def test_reference_bridge_cannot_be_split_even_when_faint(self):
        board,alpha,reference=self.fixture()
        alpha[150,224:376]=2
        self.assertEqual(cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)['unsafe_members'],
                         ['left','right'])

    def test_wrong_reference_cannot_supply_missing_subject_anchors(self):
        board,alpha,reference=self.fixture()
        reference=np.roll(reference,150,axis=0)
        self.assertEqual(cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)['unsafe_members'],
                         ['left','right'])

    def test_new_attached_owner_cannot_change_cells_in_small_grid(self):
        board={'id':'small-owner-counterexample','size':[200,100],
            'grid':{'rows':1,'columns':2,'margin':10,'gap':20,'cell_size':[80,80]},
            'members':[{'id':'left','slot':[10,10,80,80]}, {'id':'right','slot':[110,10,80,80]}]}
        reference=np.zeros((100,200),dtype='uint8')
        reference[30:70,20:90]=255;reference[30:70,125:175]=255
        alpha=np.zeros_like(reference)
        alpha[30:70,20:75]=255;alpha[30:70,95:175]=255
        alpha[5,83]=2;alpha[50,92]=5
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertEqual(result['unsafe_members'],['left','right'])
        self.assertNotIn('reference_fragment_recovery',result)

    def test_new_attached_owner_cannot_change_cells_in_wide_grid(self):
        board={'id':'wide-owner-counterexample','size':[582,300],
            'grid':{'rows':1,'columns':2,'margin':10,'gap':2,'cell_size':[280,280]},
            'members':[{'id':'left','slot':[10,10,280,280]}, {'id':'right','slot':[292,10,280,280]}]}
        reference=np.zeros((300,582),dtype='uint8')
        reference[90:210,200:270]=255;reference[90:210,350:430]=255
        reference[100,289]=255
        alpha=np.zeros_like(reference)
        alpha[90:210,200:270]=255;alpha[90:210,350:430]=255
        alpha[100,295]=5
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertEqual(result['unsafe_members'],['left','right'])
        self.assertNotIn('reference_fragment_recovery',result)

    def test_already_safe_result_does_not_enter_recovery(self):
        board,alpha,reference=weak_tests.WeakNativeGridSpecks.fixture(self)
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertEqual(result['unsafe_members'],[])
        self.assertNotIn('reference_fragment_recovery',result)
        self.assertEqual(result['cells'],{'left':[0,0,100,100],'right':[100,0,200,100]})

    def test_recovery_keeps_rgba_source_bytes_and_original_faint_pixels(self):
        board,alpha,reference=self.fixture()
        raw=np.full((300,600,4),[30,80,230,0],dtype='uint8');raw[...,3]=alpha
        reference_raw=raw.copy();reference_raw[...,3]=reference
        actual=self.root/'actual.png';ref=self.root/'reference.png'
        Image.fromarray(raw).save(actual);Image.fromarray(reference_raw).save(ref)
        board['image']=str(ref);before=actual.read_bytes()
        result=extract_assets(board,actual,self.root/'assets')
        self.assertEqual(len(result['assets']),2)
        count=0
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];a,b,c,d=asset['crop_box']
            crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
            np.testing.assert_array_equal(crop,raw[y+b:y+d,x+a:x+c])
            count+=int(np.count_nonzero(crop[...,3]))
            self.assertEqual(asset['placement']['geometry_source'],'reference_fragment_recovery')
        self.assertEqual(count,int(np.count_nonzero(alpha)))
        self.assertEqual(actual.read_bytes(),before)

    @unittest.skipUnless(os.environ.get('PRK_R2_BOARD_JOB'),'real board supplied privately')
    def test_actual_board_recovers_all_members_without_changing_source_rgba(self):
        job=Path(os.environ['PRK_R2_BOARD_JOB'])
        state=json.loads((job/'state.json').read_text('utf-8'))
        task=next(t for t in state['tasks'].values() if t['role']=='board' and t['board']['grid']['columns']==3)
        board=copy.deepcopy(task['board'])
        board['image']=str(job/'boards'/Path(board['image']).name)
        actual=job/task['result']['raw'];before=actual.read_bytes()
        pixels=np.asarray(Image.open(actual).convert('RGBA'))
        result=extract_assets(board,actual,self.root/'real-assets')
        self.assertEqual(result['cell_mapping']['unsafe_members'],[])
        self.assertEqual({asset['id'] for asset in result['assets']},{member['id'] for member in board['members']})
        self.assertEqual(result['cell_mapping']['cells'],task['extraction']['cell_mapping']['cells'])
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];a,b,c,d=asset['crop_box']
            np.testing.assert_array_equal(np.asarray(Image.open(asset['path']).convert('RGBA')),pixels[y+b:y+d,x+a:x+c])
        self.assertEqual(actual.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
