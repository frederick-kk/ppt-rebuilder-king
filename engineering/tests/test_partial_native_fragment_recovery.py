"""Recover failed native members while preserving every accepted asset field."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))

import copy
import json
import os
import tempfile
import unittest
from unittest import mock
import numpy as np
from PIL import Image

from newpath import cellmap,artwork
from newpath.native import MODE


class PartialNativeFragmentRecovery(unittest.TestCase):
    def fixture(self):
        board={'id':'partial-native-fixture','size':[900,300],'key':None,'background_mode':MODE,
               'grid':{'rows':1,'columns':3,'margin':30,'gap':60,'cell_size':[240,240]},
               'members':[{'id':'left','slot':[30,30,240,240],'box':[10,10,100,80]},
                          {'id':'middle','slot':[330,30,240,240],'box':[300,10,100,80]},
                          {'id':'right','slot':[630,30,240,240],'box':[600,10,100,80]}]}
        reference=np.zeros((300,900),dtype='uint8')
        for x in [75,375,675]:reference[90:210,x:x+150]=255
        alpha=reference.copy()
        alpha[90:210,375:525]=0
        alpha[65:185,375:525]=255
        alpha[62,400:410]=9
        alpha[140:142,597:605]=2
        return board,alpha,reference

    def compare_extraction(self,board,raw,reference,directory):
        actual=directory/'actual.png'
        reference_path=directory/'reference.png'
        Image.fromarray(raw).save(actual)
        Image.fromarray(reference).save(reference_path)
        board['image']=str(reference_path)
        strict=cellmap._resolve_cells(board,raw[...,3],reference_alpha=reference[...,3],native_geometry=True)
        output=directory/'assets'
        with mock.patch.object(artwork,'resolve_cells',return_value=copy.deepcopy(strict)):
            baseline=artwork.extract_assets(board,actual,output)
        old={asset['id']:copy.deepcopy(asset) for asset in baseline['assets']}
        old_bytes={identity:Path(asset['path']).read_bytes() for identity,asset in old.items()}
        before=actual.read_bytes()
        result=artwork.extract_assets(board,actual,output)
        self.assertEqual(actual.read_bytes(),before)
        actual_assets={asset['id']:asset for asset in result['assets']}
        for identity,asset in old.items():
            self.assertEqual(actual_assets[identity],asset)
            self.assertEqual(Path(actual_assets[identity]['path']).read_bytes(),old_bytes[identity])
        self.assertEqual(result['cell_mapping']['warnings'][:len(strict['warnings'])],strict['warnings'])
        retained=0
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];a,b,c,d=asset['crop_box']
            crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
            np.testing.assert_array_equal(crop,raw[y+b:y+d,x+a:x+c])
            retained+=int(np.count_nonzero(crop[...,3]))
        return result,strict,retained,len(old)

    def test_partial_board_recovers_failed_members_and_preserves_safe_asset(self):
        board,alpha,reference=self.fixture()
        raw=np.full((300,900,4),[30,85,220,0],dtype='uint8');raw[...,3]=alpha
        ref=raw.copy();ref[...,3]=reference
        with tempfile.TemporaryDirectory() as temporary:
            result,strict,retained,safe_count=self.compare_extraction(board,raw,ref,Path(temporary))
        self.assertEqual(strict['unsafe_members'],['middle','right'])
        self.assertEqual(safe_count,1)
        self.assertEqual(len(result['assets']),3)
        self.assertEqual(result['cell_mapping']['unsafe_members'],[])
        self.assertEqual(retained,int(np.count_nonzero(alpha)))

    def assert_keeps_strict_failure(self,board,alpha,reference):
        strict=cellmap._resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        result=cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertTrue(result['unsafe_members'])
        self.assertEqual(result,strict)

    def test_missing_empty_or_wrong_reference_keeps_strict_failure(self):
        board,alpha,reference=self.fixture()
        for candidate in [None,np.zeros_like(reference),np.roll(reference,150,axis=0)]:
            with self.subTest(reference='missing' if candidate is None else 'unmatched'):
                self.assert_keeps_strict_failure(board,alpha,candidate)

    def test_strong_unknown_boundary_group_keeps_strict_failure(self):
        board,alpha,reference=self.fixture()
        alpha[140:142,597:605]=4
        self.assert_keeps_strict_failure(board,alpha,reference)

    def test_connected_two_member_subjects_keep_strict_failure(self):
        board,alpha,reference=self.fixture()
        alpha[150,524:676]=255
        self.assert_keeps_strict_failure(board,alpha,reference)

    def test_missing_member_keeps_strict_failure(self):
        board,alpha,reference=self.fixture()
        alpha[90:210,675:825]=0
        self.assert_keeps_strict_failure(board,alpha,reference)

    def test_large_weak_group_keeps_existing_area_budget(self):
        board,alpha,reference=self.fixture()
        alpha[140:142,597:605]=0
        alpha[140:150,590:620]=2
        self.assert_keeps_strict_failure(board,alpha,reference)

    def test_strong_canvas_pixel_keeps_strict_failure(self):
        board,alpha,reference=self.fixture()
        alpha[0,0]=255
        self.assert_keeps_strict_failure(board,alpha,reference)

    def candidate(self,board,alpha,reference):
        context={}
        strict=cellmap._resolve_cells(board,alpha,reference_alpha=reference,
                                      native_geometry=True,context=context)
        candidate=cellmap._recover_reference_fragments(board,alpha,context,True,
                    actual_anchor_support=True,observed_mapping=strict)
        self.assertIsNotNone(candidate)
        return context,strict,candidate

    def test_changed_candidate_cell_stays_unsafe_while_other_member_recovers(self):
        board,alpha,reference=self.fixture()
        context,strict,candidate=self.candidate(board,alpha,reference)
        candidate['cells']['right']=list(candidate['cells']['right'])
        candidate['cells']['right'][0]-=1
        result=cellmap._restore_partial_members(board,context,strict,candidate)
        self.assertEqual(result['unsafe_members'],['right'])
        self.assertEqual(result['status'],'ambiguous')
        self.assertEqual(set(result['member_geometry_boxes']),{'middle'})
        self.assertEqual(result['cells'],strict['cells'])
        self.assertEqual(result['partial_member_recovery']['recovered_members'],['middle'])

    def test_wrong_original_owner_is_never_reassigned(self):
        board,alpha,reference=self.fixture()
        context,strict,candidate=self.candidate(board,alpha,reference)
        middle_labels=[label for label,owners in context['owners'].items() if owners=={'middle'}]
        self.assertTrue(middle_labels)
        for label in middle_labels:context['owners'][label]={'right'}
        result=cellmap._restore_partial_members(board,context,strict,candidate)
        self.assertIn('middle',result['unsafe_members'])
        self.assertNotIn('middle',result.get('member_geometry_boxes',{}))

    def test_new_attached_owner_is_rechecked_against_original_cells(self):
        board,alpha,reference=self.fixture()
        context,strict,candidate=self.candidate(board,alpha,reference)
        fragments=candidate['reference_fragment_recovery']['attached_fragments']
        self.assertTrue(fragments)
        fragments[0]['owner']='right'
        result=cellmap._restore_partial_members(board,context,strict,candidate)
        self.assertIn('middle',result['unsafe_members'])
        self.assertNotIn('middle',result.get('member_geometry_boxes',{}))

    @unittest.skipUnless(os.environ.get('PRK_R8_BOARD_JOB'),'real board supplied privately')
    def test_actual_r7_fifteen_members_preserve_all_nine_original_asset_records(self):
        job=Path(os.environ['PRK_R8_BOARD_JOB'])
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(task for task in state['tasks'].values()
                  if len(task.get('board',{}).get('members',[]))==15)
        board=copy.deepcopy(task['board'])
        raw=np.asarray(Image.open(job/task['result']['raw']).convert('RGBA'))
        reference=np.asarray(Image.open(job/'boards'/Path(board['image']).name).convert('RGBA'))
        with tempfile.TemporaryDirectory() as temporary:
            result,strict,retained,safe_count=self.compare_extraction(board,raw,reference,Path(temporary))
        self.assertEqual(len(strict['unsafe_members']),6)
        self.assertEqual(safe_count,9)
        self.assertEqual(len(result['assets']),15)
        self.assertEqual(result['cell_mapping']['unsafe_members'],[])
        self.assertEqual(result['cell_mapping']['cells'],strict['cells'])
        self.assertEqual(retained,716737)


if __name__=='__main__':
    unittest.main()
