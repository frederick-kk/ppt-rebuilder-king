"""Bound geometry-only exclusion by complete groups and opacity mass."""
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

from newpath.wordart import _ink_bounds,extract_wordart_asset


class WordartAlphaMassGeometry(unittest.TestCase):
    def fixture(self):
        alpha=np.zeros((512,1024),dtype='uint8')
        alpha[200:400,100:900]=255
        alpha[20:40,40:60]=2
        return alpha

    def test_large_disconnected_low_opacity_group_changes_only_geometry(self):
        alpha=self.fixture()
        before=alpha.copy()
        bounds,report=_ink_bounds(alpha)
        self.assertEqual(bounds,[100,200,900,400])
        self.assertEqual(report['policy'],'bounded-weak-wordart-alpha-mass/1')
        self.assertEqual(report['alpha_mass'],800)
        self.assertEqual(report['retained_alpha_mass'],40800000)
        self.assertEqual(report['mass_ratio_denominator'],4096)
        self.assertEqual(report['pixel_count'],400)
        np.testing.assert_array_equal(alpha,before)

    def test_alpha4_group_is_retained_in_geometry(self):
        alpha=self.fixture()
        alpha[20:40,40:60]=4
        self.assertEqual(_ink_bounds(alpha),([40,20,900,400],None))

    def test_strong_disconnected_punctuation_is_retained(self):
        alpha=self.fixture()
        alpha[80,75]=255
        bounds,report=_ink_bounds(alpha)
        self.assertEqual(bounds,[75,80,900,400])
        self.assertEqual(report['pixel_count'],400)

    def test_weak_bridge_connected_to_ink_is_retained_whole(self):
        alpha=self.fixture()
        alpha[40:201,50]=2
        alpha[200,50:101]=2
        self.assertEqual(_ink_bounds(alpha),([40,20,900,400],None))

    def test_large_opacity_mass_remains_in_original_geometry(self):
        alpha=self.fixture()
        alpha[200:400,100:900]=0
        alpha[200:220,100:120]=255
        self.assertEqual(_ink_bounds(alpha),([40,20,120,220],None))

    def test_uniformly_faint_title_uses_original_geometry(self):
        alpha=self.fixture()
        alpha[200:400,100:900]=3
        self.assertEqual(_ink_bounds(alpha),([40,20,900,400],None))

    def test_alpha_mass_limit_is_inclusive_without_float_rounding(self):
        alpha=np.zeros((200,200),dtype='uint8')
        alpha[100:117,100:117]=255
        alpha[100,117]=33
        alpha[10:13,10:13]=2
        bounds,report=_ink_bounds(alpha)
        self.assertEqual(bounds,[100,100,118,117])
        self.assertEqual(report['alpha_mass'],18)
        self.assertEqual(report['retained_alpha_mass'],73728)
        alpha[100,100]=254
        self.assertEqual(_ink_bounds(alpha),([10,10,118,117],None))

    def test_one_pixel_antialias_extent_keeps_original_geometry(self):
        alpha=np.zeros((512,1024),dtype='uint8')
        alpha[200:400,100:900]=255
        alpha[240:260,100:110]=0
        alpha[250,99]=2
        for index in range(70):
            y=220+(index//20)*30
            x=150+(index%20)*30
            alpha[y-1:y+2,x-1:x+2]=0
            alpha[y,x]=2
        self.assertEqual(_ink_bounds(alpha),([99,200,900,400],None))

    def extract(self,alpha,member,directory):
        raw=np.full((*alpha.shape,4),[35,80,210,0],dtype='uint8')
        raw[...,3]=alpha
        path=directory/'raw.png'
        Image.fromarray(raw).save(path)
        before=path.read_bytes()
        result=extract_wordart_asset(member,path,directory/'assets')
        asset=result['assets'][0]
        a,b,c,d=asset['crop_box']
        crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
        np.testing.assert_array_equal(crop,raw[b:d,a:c])
        self.assertEqual(int(np.count_nonzero(crop[...,3])),int(np.count_nonzero(alpha)))
        self.assertEqual(path.read_bytes(),before)
        return result

    def test_candidate_over_four_page_budget_uses_safe_original_geometry(self):
        alpha=np.zeros((1200,2000),dtype='uint8')
        alpha[1000:1100,1000:1100]=255
        alpha[100:120,100:110]=2
        member={'id':'title','box':[100,100,1000,600],
                'target':[100,100,1000,600],'lettering':'TITLE','description':'source title'}
        with tempfile.TemporaryDirectory() as temporary:
            result=self.extract(alpha,member,Path(temporary))
        asset=result['assets'][0]
        self.assertEqual(asset['target'],[300,100,600,600])
        self.assertEqual(asset['placement']['ink_bbox'],[100,100,1100,1100])
        self.assertNotIn('geometry_recovery',asset['placement'])
        self.assertTrue(any(warning['reason']=='native_wordart_alpha_mass_budget_fallback'
                            for warning in result['warnings']))

    def test_valid_recovery_preserves_complete_raw_rgba(self):
        member={'id':'title','box':[100,100,800,200],'lettering':'TITLE','description':'source title'}
        with tempfile.TemporaryDirectory() as temporary:
            result=self.extract(self.fixture(),member,Path(temporary))
        self.assertEqual(result['assets'][0]['placement']['visible_ink_target'],[100,100,800,200])

    @unittest.skipUnless(os.environ.get('PRK_R7_WORDART_JOB'),'real title supplied privately')
    def test_actual_r6_title_recovers_complete_main_group_and_original_rgba(self):
        job=Path(os.environ['PRK_R7_WORDART_JOB'])
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(task for task in state['tasks'].values() if task['role']=='wordart')
        path=job/task['result']['raw']
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        raw=np.asarray(Image.open(path).convert('RGBA'))
        with tempfile.TemporaryDirectory() as directory:
            result=extract_wordart_asset(copy.deepcopy(task['member']),path,directory)
            asset=result['assets'][0]
            self.assertEqual(asset['placement']['ink_bbox'],[49,399,1488,641])
            self.assertAlmostEqual(asset['placement']['visible_ink_target'][2],642.198347107438)
            self.assertEqual(asset['placement']['visible_ink_target'][3],108)
            report=asset['placement']['geometry_recovery']
            self.assertEqual(report['alpha_mass'],2038)
            self.assertEqual(report['retained_alpha_mass'],25326797)
            self.assertEqual(report['pixel_count'],1017)
            self.assertEqual(report['component_count'],354)
            self.assertEqual(report['preserved_extent_difference_px'],1)
            a,b,c,d=asset['crop_box']
            crop=np.asarray(Image.open(asset['path']).convert('RGBA'))
            np.testing.assert_array_equal(crop,raw[b:d,a:c])
            self.assertEqual(int(np.count_nonzero(crop[...,3])),344158)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)


if __name__=='__main__':
    unittest.main()
