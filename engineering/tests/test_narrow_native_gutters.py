"""Recover uniquely reference-owned narrow native row gutters without alpha loss."""
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

sys.dont_write_bytecode=True
PACKAGE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath.artwork import extract_assets
from newpath.cellmap import resolve_cells
from newpath.native import MODE

EVIDENCE=Path(os.environ['PRK_COMPONENT_PLACEMENT_EVIDENCE']) if os.environ.get('PRK_COMPONENT_PLACEMENT_EVIDENCE') else None


class NarrowNativeGutters(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='prk-narrow-native-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def fixture(self,gap_width=3):
        board={'id':'two-native-members','size':[200,200],'key':None,'background_mode':MODE,
          'grid':{'rows':2,'columns':1,'margin':10,'gap':24,'cell_size':[180,78]},
          'members':[{'id':'upper','slot':[10,10,180,78],'box':[100,100,80,80],'description':'Upper square'},
                     {'id':'lower','slot':[10,112,180,78],'box':[100,300,80,80],'description':'Lower square'}]}
        reference=np.zeros((200,200,4),dtype=np.uint8)
        reference[20:80,60:140]=[80,120,160,230]
        reference[120:180,60:140]=[160,120,80,230]
        pixels=np.full((200,200,4),[11,22,33,1],dtype=np.uint8);pixels[0,0,3]=0
        pixels[20:99,60:140]=[80,120,160,230]
        pixels[99+gap_width:180,60:140]=[160,120,80,230]
        path=self.root/'reference.png';Image.fromarray(reference).save(path);board['image']=str(path)
        return board,pixels,reference

    def mapping(self,board,pixels,reference=None,native=True):
        before=pixels.copy()
        result=resolve_cells(board,pixels[...,3],reference_alpha=None if reference is None else reference[...,3],native_geometry=native)
        np.testing.assert_array_equal(pixels,before)
        return result

    def extract(self,board,pixels,name='synthetic'):
        path=self.root/(name+'.png');Image.fromarray(pixels).save(path);before=path.read_bytes()
        result=extract_assets(board,path,self.root/(name+'-assets'))
        self.assertEqual(path.read_bytes(),before)
        return result

    def assert_exact_rgba(self,result,pixels,members):
        self.assertEqual(result['cell_mapping']['unsafe_members'],[])
        self.assertEqual({a['id'] for a in result['assets']},{m['id'] for m in members})
        total=0
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];left,top,right,bottom=asset['crop_box']
            expected=pixels[y:y+h,x:x+w][top:bottom,left:right]
            with Image.open(asset['path']) as image:
                actual=np.asarray(image.convert('RGBA'))
            np.testing.assert_array_equal(actual,expected)
            self.assertEqual(np.count_nonzero(actual[...,3]),np.count_nonzero(pixels[y:y+h,x:x+w,3]))
            self.assertTrue(asset['placement']['all_rgba_pixels_preserved'])
            total+=int(np.count_nonzero(actual[...,3]))
        self.assertEqual(total,int(np.count_nonzero(pixels[...,3])))

    def test_unique_two_to_five_pixel_gaps_recover_without_changing_rgba(self):
        for width in (2,3,4,5):
            with self.subTest(width=width):
                board,pixels,reference=self.fixture(width)
                result=self.extract(board,pixels,str(width))
                self.assert_exact_rgba(result,pixels,board['members'])
                notes=[w for w in result['warnings'] if w['code']=='reference_supported_narrow_grid_gutter']
                self.assertEqual(len(notes),1)
                note=notes[0];self.assertEqual(note['band'],[99,99+width]);self.assertEqual(note['width_px'],width)
                self.assertGreater(note['cut'],note['band'][0]);self.assertLess(note['cut'],note['band'][1])
                self.assertEqual(note['alpha_threshold_for_geometry_only'],1)
                self.assertEqual(note['members'],['upper','lower'])

    def test_one_pixel_gap_is_still_unsafe(self):
        board,pixels,reference=self.fixture(1)
        self.assertEqual(set(self.mapping(board,pixels,reference)['unsafe_members']),{'upper','lower'})

    def test_real_core_bridge_remains_unsafe(self):
        board,pixels,reference=self.fixture()
        pixels[95:108,90:110]=[20,40,60,2]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_multiple_narrow_gaps_do_not_guess_an_island_owner(self):
        board,pixels,reference=self.fixture()
        pixels[96:104,:,3]=1
        pixels[99:101,70:130]=[100,80,40,230]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_missing_empty_and_non_native_references_do_not_relax_geometry(self):
        board,pixels,reference=self.fixture()
        for supplied,native in ((None,True),(np.zeros_like(reference),True),(reference,False)):
            with self.subTest(supplied=supplied is not None,native=native):
                mapped=self.mapping(board,pixels,supplied,native)
                self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
                self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_nonoverlapping_reference_cannot_establish_subject_owner(self):
        board,pixels,reference=self.fixture()
        reference[20:80,60:140]=0;reference[20:80,5:40]=[80,120,160,230]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_reference_owned_detached_stroke_on_wrong_side_remains_unsafe(self):
        board,pixels,reference=self.fixture()
        pixels[...,3]=1;pixels[0,0,3]=0;reference[:]=0
        pixels[20:70,60:90]=[80,120,160,230]
        pixels[73:180,100:140]=[160,120,80,230]
        # A detached stroke belongs to upper by its own 2-D reference match,
        # but sits below the only narrow empty band. Never give it to lower.
        pixels[74:80,65:80]=[80,120,160,230]
        reference[20:80,60:90]=[80,120,160,230]
        reference[120:180,100:140]=[160,120,80,230]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_alpha_one_only_member_does_not_enter_narrow_recovery(self):
        board,pixels,reference=self.fixture();pixels[102:180,60:140,3]=1
        mapped=self.mapping(board,pixels,reference)
        self.assertNotEqual(mapped.get('geometry_policy'),'native-core-local-rows/1')
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_existing_wide_gap_keeps_ordinary_path_without_narrow_warning(self):
        board,pixels,reference=self.fixture(12)
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(mapped['unsafe_members'],[])
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))
        self.assertNotIn('reference_supported_narrow_grid_gutter',{w['code'] for w in mapped['warnings']})

    def test_large_unowned_component_cannot_be_assigned_by_a_narrow_gap(self):
        board,pixels,reference=self.fixture()
        pixels[104:144,5:24]=[240,20,20,255]  # 760px, no reference overlap.
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_unowned_component_ninth_pixel_exceeds_the_discrete_speck_budget(self):
        board,pixels,reference=self.fixture()
        pixels[104:107,5:8]=[240,20,20,255]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_many_small_unowned_specks_over_total_budget_remain_unsafe(self):
        board,pixels,reference=self.fixture()
        for index in range(8):pixels[106:108,5+6*index:9+6*index]=[240,20,20,255]
        pixels[112,5]=[240,20,20,255]  # Eight 8px components plus one = 65px.
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_small_unowned_specks_at_both_budgets_are_preserved_and_disclosed(self):
        board,pixels,reference=self.fixture()
        for index in range(8):pixels[106:108,5+6*index:9+6*index]=[240,20,20,255]
        result=self.extract(board,pixels,'bounded-specks')
        self.assert_exact_rgba(result,pixels,board['members'])
        note=result['cell_mapping']['narrow_gutter_recoveries'][0]
        self.assertEqual(note['unconfirmed_components_in_search'],8)
        self.assertEqual(note['unconfirmed_core_pixels'],64)
        self.assertEqual(note['max_unconfirmed_component_core_pixels'],8)
        self.assertEqual(note['unconfirmed_component_budget_px'],8)
        self.assertEqual(note['unconfirmed_total_budget_px'],64)
        self.assertEqual(note['identity_quality'],'not_evaluated')

    def test_full_unowned_component_size_counts_even_when_part_is_outside_search(self):
        board,pixels,reference=self.fixture()
        pixels[148:157,5]=[240,20,20,255]  # 9px component straddles search bottom.
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    def test_unowned_core_crossing_the_gap_is_not_a_narrow_empty_band(self):
        board,pixels,reference=self.fixture()
        pixels[98:104,5]=[240,20,20,255]
        mapped=self.mapping(board,pixels,reference)
        self.assertEqual(set(mapped['unsafe_members']),{'upper','lower'})
        self.assertFalse(mapped.get('narrow_gutter_recoveries'))

    @unittest.skipUnless(EVIDENCE and (EVIDENCE/'job_snapshot/ppt_rebuild_job_20261023_1452/state.json').is_file(),
                         'actual component placement diagnostic not supplied')
    def test_actual_three_member_board_recovers_all_assets_with_exact_rgba(self):
        job=EVIDENCE/'job_snapshot/ppt_rebuild_job_20261023_1452'
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(t for t in state['tasks'].values() if any(m['id']=='o021' for m in t.get('board',{}).get('members',[])))
        board=copy.deepcopy(task['board']);board['image']=str(job/'boards'/Path(board['image']).name)
        actual=job/task['result']['raw'];before=hashlib.sha256(actual.read_bytes()).hexdigest()
        with Image.open(actual) as image:pixels=np.asarray(image.convert('RGBA')).copy()
        result=extract_assets(board,actual,self.root/'actual-assets')
        self.assert_exact_rgba(result,pixels,board['members'])
        self.assertEqual({a['id'] for a in result['assets']},{'o016','o021','o026'})
        notes=[w for w in result['warnings'] if w['code']=='reference_supported_narrow_grid_gutter']
        self.assertEqual(len(notes),1);self.assertEqual(notes[0]['band'],[697,700])
        self.assertEqual(notes[0]['members'],['o021','o026']);self.assertEqual(notes[0]['normal_minimum_px'],6)
        self.assertTrue(697<notes[0]['cut']<700)
        self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
