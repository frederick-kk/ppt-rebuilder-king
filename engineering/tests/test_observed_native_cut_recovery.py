"""Reuse existing observed cuts only for bounded unowned weak fragments."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))

import copy
import hashlib
import json
import os
import tempfile
import unittest
import numpy as np
from PIL import Image

from newpath import cellmap
from newpath.artwork import extract_assets
from newpath.native import MODE


class ObservedNativeCutRecovery(unittest.TestCase):
    def fixture(self):
        board={'id':'observed-cut-fixture','size':[600,300],'key':None,'background_mode':MODE,
               'grid':{'rows':1,'columns':2,'margin':30,'gap':60,'cell_size':[240,240]},
               'members':[{'id':'left','slot':[30,30,240,240],'box':[100,100,100,80]},
                          {'id':'right','slot':[330,30,240,240],'box':[400,100,100,80]}]}
        reference=np.zeros((300,600),dtype='uint8')
        reference[90:210,75:225]=255
        reference[90:210,375:525]=255
        alpha=reference.copy()
        alpha[140:142,297:305]=2
        return board,alpha,reference

    def resolve(self,board,alpha,reference):
        return cellmap.resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)

    def test_bounded_unowned_fragment_uses_existing_whole_observed_cells(self):
        board,alpha,reference=self.fixture()
        before=alpha.copy()
        strict=cellmap._resolve_cells(board,alpha,reference_alpha=reference,native_geometry=True)
        self.assertTrue(strict['unsafe_members'])
        result=self.resolve(board,alpha,reference)
        self.assertEqual(result['unsafe_members'],[])
        self.assertEqual(result['cells'],strict['cells'])
        report=result['reference_fragment_recovery']['observed_cut_recovery']
        self.assertEqual(report['crossing_ignored_components'],1)
        self.assertEqual(report['crossing_ignored_pixels'],16)
        self.assertTrue(report['all_original_core_components_unsplit'])
        np.testing.assert_array_equal(alpha,before)

    def test_extract_keeps_every_rgba_pixel_and_correct_authored_targets(self):
        board,alpha,reference=self.fixture()
        raw=np.full((300,600,4),[30,85,220,0],dtype='uint8')
        raw[...,3]=alpha
        ref=raw.copy()
        ref[...,3]=reference
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            actual=directory/'actual.png'
            reference_path=directory/'reference.png'
            Image.fromarray(raw).save(actual)
            Image.fromarray(ref).save(reference_path)
            before=actual.read_bytes()
            board['image']=str(reference_path)
            result=extract_assets(board,actual,directory/'assets')
            self.assertEqual(len(result['assets']),2)
            retained=0
            for asset in result['assets']:
                x,y,w,h=asset['actual_slot']
                a,b,c,d=asset['crop_box']
                crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
                np.testing.assert_array_equal(crop,raw[y+b:y+d,x+a:x+c])
                retained+=int(np.count_nonzero(crop[...,3]))
                self.assertEqual(asset['placement']['visible_core_target'],asset['authored_target'])
            self.assertEqual(retained,int(np.count_nonzero(alpha)))
            self.assertEqual(actual.read_bytes(),before)

    def test_original_observed_cut_that_also_crosses_weak_group_stays_unsafe(self):
        board={'id':'no-complete-observed-cut','size':[1200,600],
               'grid':{'rows':1,'columns':2,'margin':60,'gap':120,'cell_size':[480,480]},
               'members':[{'id':'left','slot':[60,60,480,480]},
                          {'id':'right','slot':[660,60,480,480]}]}
        reference=np.zeros((600,1200),dtype='uint8')
        reference[180:420,150:450]=255
        reference[180:420,750:1050]=255
        alpha=reference.copy()
        alpha[10,450:750]=2
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_alpha4_unknown_crossing_group_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[140:142,297:305]=4
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_component_above_existing_weak_budget_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[140:142,297:305]=0
        alpha[140:148,297:305]=2
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_known_reference_bridge_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[150,224:376]=2
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_strong_canvas_clip_remains_unsafe(self):
        board,alpha,reference=self.fixture()
        alpha[0,0]=255
        self.assertTrue(self.resolve(board,alpha,reference)['unsafe_members'])

    def test_absent_reference_keeps_the_original_failure(self):
        board,alpha,reference=self.fixture()
        self.assertTrue(self.resolve(board,alpha,None)['unsafe_members'])

    def test_already_safe_or_legacy_mapping_does_not_use_observed_recovery(self):
        board,alpha,reference=self.fixture()
        alpha[140:142,297:305]=0
        result=self.resolve(board,alpha,reference)
        self.assertEqual(result['unsafe_members'],[])
        self.assertNotIn('reference_fragment_recovery',result)
        board.pop('grid')
        result=self.resolve(board,alpha,reference)
        self.assertEqual(result['status'],'legacy')
        self.assertNotIn('reference_fragment_recovery',result)

    @unittest.skipUnless(os.environ.get('PRK_R5_HIGH_JOB'),'real board supplied privately')
    def test_actual_high_six_members_preserve_all_source_rgba(self):
        job=Path(os.environ['PRK_R5_HIGH_JOB'])
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(task for task in state['tasks'].values()
                  if len(task.get('board',{}).get('members',[]))==6)
        board=copy.deepcopy(task['board'])
        board['image']=str(job/'boards'/Path(board['image']).name)
        actual=job/task['result']['raw']
        before=hashlib.sha256(actual.read_bytes()).hexdigest()
        raw=np.asarray(Image.open(actual).convert('RGBA'))
        with tempfile.TemporaryDirectory() as directory:
            result=extract_assets(board,actual,directory)
            self.assertEqual(len(result['assets']),6)
            self.assertEqual(result['cell_mapping']['unsafe_members'],[])
            retained=0
            for asset in result['assets']:
                x,y,w,h=asset['actual_slot']
                a,b,c,d=asset['crop_box']
                crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
                np.testing.assert_array_equal(crop,raw[y+b:y+d,x+a:x+c])
                retained+=int(np.count_nonzero(crop[...,3]))
            self.assertEqual(retained,int(np.count_nonzero(raw[...,3])))
            report=result['cell_mapping']['reference_fragment_recovery']['observed_cut_recovery']
            self.assertEqual(report['crossing_ignored_components'],5)
            self.assertEqual(report['crossing_ignored_pixels'],36)
            self.assertEqual(result['cell_mapping']['column_row_cuts'],{'0':[367,660],'1':[361,678]})
        self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(),before)


if __name__=='__main__':
    unittest.main()
