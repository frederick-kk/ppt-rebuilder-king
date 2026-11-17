"""Bound faint fragments to one reference-confirmed actual strong subject."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE/'compat-runtime'))

from newpath import cellmap
from newpath.artwork import extract_assets
from newpath.native import MODE


class ActualAnchorFragmentRecovery(unittest.TestCase):
    def fixture(self):
        board={'id':'actual-anchor-fixture','size':[600,300],'key':None,'background_mode':MODE,
               'grid':{'rows':1,'columns':2,'margin':30,'gap':60,'cell_size':[240,240]},
               'members':[{'id':'left','slot':[30,30,240,240],'box':[100,100,100,80]},
                          {'id':'right','slot':[330,30,240,240],'box':[400,100,100,80]}]}
        reference=np.zeros((300,600),dtype='uint8')
        reference[90:210,75:225]=255
        reference[90:210,375:525]=255
        alpha=reference.copy()
        alpha[90:210,75:225]=0
        alpha[65:185,75:225]=255
        alpha[62,100:110]=9
        for y,x in [(y,x) for y in range(50,80,3) for x in range(280,310,3)][:80]:
            alpha[y,x]=2
        return board,alpha,reference

    def resolve(self,board,alpha,reference):
        return cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)

    def assertUnsafe(self,board,alpha,reference):
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_shifted_subject_faint_border_has_one_complete_actual_anchor(self):
        board,alpha,reference=self.fixture()
        before=alpha.copy()
        result=self.resolve(board,alpha,reference)
        self.assertEqual(result['unsafe_members'],[])
        report=result['reference_fragment_recovery']['actual_anchor_support']
        self.assertEqual(report['seed_alpha_threshold'],16)
        self.assertEqual(report['fragment_alpha_limit'],16)
        self.assertEqual(report['radius_px'],4)
        self.assertEqual(report['attached_pixels'],10)
        self.assertEqual(report['attached_components'],1)
        self.assertTrue(report['fixed_non_cascading_seeds'])
        np.testing.assert_array_equal(alpha,before)

    def test_alpha17_unknown_fragment_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[62,100:110]=17
        self.assertUnsafe(board,alpha,reference)

    def test_faint_fragment_outside_actual_support_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[62,100:110]=0
        alpha[58,100:110]=9
        self.assertUnsafe(board,alpha,reference)

    def test_faint_attachment_cannot_become_a_new_support_seed(self):
        board,alpha,reference=self.fixture()
        alpha[58,100:110]=9
        self.assertUnsafe(board,alpha,reference)

    def test_component_above_relative_area_budget_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[62,100:110]=0
        alpha[62:64,90:122]=9
        self.assertUnsafe(board,alpha,reference)

    def test_combined_ignored_and_attached_total_above_budget_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[62,100:110]=0
        alpha[62:64,80:105]=9
        alpha[62:64,110:135]=9
        self.assertUnsafe(board,alpha,reference)

    def test_missing_or_wrong_reference_cannot_supply_strong_anchors(self):
        board,alpha,reference=self.fixture()
        for candidate in [None,np.zeros_like(reference),np.roll(reference,150,axis=0)]:
            with self.subTest(reference='missing' if candidate is None else 'unmatched'):
                self.assertUnsafe(board,alpha,candidate)

    def test_missing_member_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[90:210,375:525]=0
        self.assertUnsafe(board,alpha,reference)

    def test_low_alpha_member_without_a_strong_anchor_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[90:210,375:525]=12
        self.assertUnsafe(board,alpha,reference)

    def test_non_native_geometry_does_not_enter_new_recovery(self):
        board,alpha,reference=self.fixture()
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=False)
        self.assertNotIn('reference_fragment_recovery',result)
        self.assertTrue(result['unsafe_members'])

    def test_legacy_board_keeps_its_scaled_slots(self):
        board,alpha,reference=self.fixture()
        board.pop('grid')
        result=self.resolve(board,alpha,reference)
        self.assertEqual(result['status'],'legacy')
        self.assertEqual(result['cells'],{'left':[30,30,270,270],'right':[330,30,570,270]})
        self.assertNotIn('reference_fragment_recovery',result)

    def test_whole_reference_bridge_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[150,224:376]=9
        self.assertUnsafe(board,alpha,reference)

    def test_strong_canvas_edge_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[0,0]=255
        self.assertUnsafe(board,alpha,reference)

    def test_two_nearby_actual_subjects_cannot_claim_one_fragment(self):
        board={'id':'two-anchor-conflict','size':[200,100],
               'grid':{'rows':1,'columns':2,'margin':10,'gap':20,'cell_size':[80,80]},
               'members':[{'id':'left','slot':[10,10,80,80]},
                          {'id':'right','slot':[110,10,80,80]}]}
        reference=np.zeros((100,200),dtype='uint8')
        reference[30:70,20:98]=255
        reference[30:70,102:180]=255
        alpha=reference.copy()
        alpha[50,100]=9
        self.assertUnsafe(board,alpha,reference)

    def test_extract_preserves_every_original_rgba_pixel(self):
        board,alpha,reference=self.fixture()
        raw=np.full((300,600,4),[35,90,210,0],dtype='uint8')
        raw[...,3]=alpha
        ref=raw.copy()
        ref[...,3]=reference
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            actual=directory/'actual.png'
            reference_path=directory/'reference.png'
            Image.fromarray(raw).save(actual)
            Image.fromarray(ref).save(reference_path)
            board['image']=str(reference_path)
            before=actual.read_bytes()
            extracted=extract_assets(board,actual,directory/'assets')
            self.assertEqual(len(extracted['assets']),2)
            retained=0
            for asset in extracted['assets']:
                x,y,w,h=asset['actual_slot']
                a,b,c,d=asset['crop_box']
                result=np.asarray(Image.open(asset['path']).convert('RGBA'))
                np.testing.assert_array_equal(result,raw[y+b:y+d,x+a:x+c])
                retained+=int(np.count_nonzero(result[...,3]))
            self.assertEqual(retained,int(np.count_nonzero(alpha)))
            self.assertEqual(actual.read_bytes(),before)

    @unittest.skipUnless(os.environ.get('PRK_R4_PURE56_JOB'),'real board supplied privately')
    def test_actual_pure56_board_recovers_all_four_with_original_rgba(self):
        job=Path(os.environ['PRK_R4_PURE56_JOB'])
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(task for task in state['tasks'].values()
                  if task.get('board',{}).get('capacity')==4)
        board=copy.deepcopy(task['board'])
        board['image']=str(job/'boards'/Path(board['image']).name)
        actual=job/task['result']['raw']
        before=hashlib.sha256(actual.read_bytes()).hexdigest()
        pixels=np.asarray(Image.open(actual).convert('RGBA'))
        with tempfile.TemporaryDirectory() as directory:
            result=extract_assets(board,actual,directory)
            self.assertEqual(result['cell_mapping']['unsafe_members'],[])
            self.assertEqual({asset['id'] for asset in result['assets']},
                             {member['id'] for member in board['members']})
            retained=0
            for asset in result['assets']:
                x,y,w,h=asset['actual_slot']
                a,b,c,d=asset['crop_box']
                crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
                np.testing.assert_array_equal(crop,pixels[y+b:y+d,x+a:x+c])
                retained+=int(np.count_nonzero(crop[...,3]))
            self.assertEqual(retained,int(np.count_nonzero(pixels[...,3])))
            report=result['cell_mapping']['reference_fragment_recovery']['actual_anchor_support']
            self.assertEqual(report['attached_components'],31)
            self.assertEqual(report['attached_pixels'],380)
        self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(),before)


if __name__=='__main__':
    unittest.main()
