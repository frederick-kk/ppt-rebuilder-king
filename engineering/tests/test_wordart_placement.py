"""Native title placement preserves pixels and the visible subject aspect ratio."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))
from newpath.wordart import extract_wordart_asset


class PlacementTests(unittest.TestCase):
    def extract(self,alpha,target=(20,30,600,100)):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'raw.png'
        pixels=np.zeros((*alpha.shape,4),dtype=np.uint8)
        pixels[...,:3]=[143,20,180];pixels[...,3]=alpha
        Image.fromarray(pixels).save(path)
        member={'id':'title','box':list(target),'target':list(target),'z':8}
        return extract_wordart_asset(member,path,Path(folder.name)/'out'), pixels

    def assert_contain(self,asset,ink):
        self.assertIn('authored_target',asset)
        x,y,w,h=asset['target'];tx,ty,tw,th=asset['authored_target']
        sw,sh=asset['source_size'];left,top,_,_=asset['crop_box']
        self.assertAlmostEqual(w/sw,h/sh,places=10)
        s=w/sw
        actual=[x+(ink[0]-left)*s,y+(ink[1]-top)*s,
                (ink[2]-ink[0])*s,(ink[3]-ink[1])*s]
        self.assertGreaterEqual(actual[0]+1e-8,tx)
        self.assertGreaterEqual(actual[1]+1e-8,ty)
        self.assertLessEqual(actual[0]+actual[2],tx+tw+1e-8)
        self.assertLessEqual(actual[1]+actual[3],ty+th+1e-8)
        self.assertAlmostEqual(actual[0]+actual[2]/2,tx+tw/2,places=8)
        self.assertAlmostEqual(actual[1]+actual[3]/2,ty+th/2,places=8)
        self.assertTrue(abs(actual[2]-tw)<1e-8 or abs(actual[3]-th)<1e-8)
        return actual

    def test_haze_does_not_squash_ink_and_all_pixels_survive(self):
        alpha=np.zeros((100,200),dtype=np.uint8)
        alpha[10:90,20:180]=1;alpha[30:70,20:180]=200
        r,p=self.extract(alpha);a=r['assets'][0]
        np.testing.assert_array_equal(np.array(Image.open(a['path'])),p[10:90,20:180])
        self.assertEqual(a['authored_target'],[20,30,600,100])
        self.assert_contain(a,[20,30,180,70])
        self.assertEqual(a['target'],[120.0,-20.0,400.0,200.0])
        self.assertEqual(a['z'],8)

    def test_ordinary_partial_alpha_also_uses_uniform_contain(self):
        alpha=np.zeros((100,200),dtype=np.uint8);alpha[20:80,20:180]=2
        r,_=self.extract(alpha);a=r['assets'][0]
        self.assert_contain(a,[20,20,180,80])
        self.assertAlmostEqual(a['target'][2]/a['target'][3],160/60)

    def test_three_point_zero_zero_nine_boundary_has_no_discontinuity(self):
        alpha=np.zeros((700,1530),dtype=np.uint8)
        alpha[10:663,10:1515]=1;alpha[322:539,11:1514]=254
        r,p=self.extract(alpha,target=(65,28,1325,145));a=r['assets'][0]
        self.assert_contain(a,[11,322,1514,539])
        self.assertAlmostEqual(a['stretch'][0],145/217)
        self.assertEqual(a['stretch'][0],a['stretch'][1])
        np.testing.assert_array_equal(np.array(Image.open(a['path'])),p[10:663,10:1515])

    def test_extreme_alpha_one_haze_keeps_pixels_without_anisotropy(self):
        alpha=np.zeros((100,200),dtype=np.uint8);alpha[10:90,20:180]=1
        alpha[49:51,50:150]=255
        r,p=self.extract(alpha);a=r['assets'][0]
        self.assert_contain(a,[50,49,150,51])
        self.assertEqual(a['stretch'],[6.0,6.0])
        np.testing.assert_array_equal(np.array(Image.open(a['path'])),p[10:90,20:180])

    def test_alpha_one_only_falls_back_to_complete_subject(self):
        alpha=np.zeros((100,200),dtype=np.uint8);alpha[10:90,20:180]=1
        r,_=self.extract(alpha);a=r['assets'][0]
        self.assert_contain(a,[20,10,180,90])
        self.assertEqual(a['placement']['ink_bbox_basis'],'all_nonzero_alpha_fallback')
        self.assertTrue(any(w['reason']=='native_wordart_alpha1_only' for w in r['warnings']))

    def test_vertical_subject_and_matching_aspect_are_stable(self):
        alpha=np.zeros((100,200),dtype=np.uint8);alpha[10:90,90:110]=255
        for target in ((20,30,600,100),(10,20,25,100)):
            r,_=self.extract(alpha,target);a=r['assets'][0]
            self.assert_contain(a,[90,10,110,90])
            if target==(10,20,25,100):self.assertEqual(a['target'],[10.,20.,25.,100.])

    def test_placement_metadata_does_not_claim_semantic_visual_acceptance(self):
        alpha=np.zeros((50,150),dtype=np.uint8);alpha[10:30,20:130]=128
        r,_=self.extract(alpha);a=r['assets'][0]
        self.assertIn('placement',a)
        self.assertTrue(a['placement']['all_rgba_pixels_preserved'])
        self.assertEqual(a['placement']['fit'],'contain')
        self.assertEqual(a['placement']['alpha_threshold_for_geometry_only'],1)
        self.assertEqual(r['validation']['quality'],'not_evaluated')


class PlacementBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)

    def call(self,target,pixels=None,name='case'):
        if pixels is None:
            pixels=np.zeros((14,20,4),dtype=np.uint8)
            pixels[2:11,2:18]=[143,20,180,255]
        raw=self.root/(name+'.png');Image.fromarray(pixels).save(raw)
        before=raw.read_bytes();destination=self.root/(name+'-assets')
        member={'id':'title','box':[20,30,600,100],'target':list(target),'z':8}
        try:
            result=extract_wordart_asset(member,raw,destination)
        finally:
            self.assertEqual(raw.read_bytes(),before)
        return result

    def assert_budget_rejected(self,target,pixels=None,name='bad'):
        with self.assertRaisesRegex(ValueError,'placement.*budget'):
            self.call(target,pixels,name)
        self.assertFalse(any((self.root/(name+'-assets')).glob('*.png')))

    def test_tiny_core_with_full_size_haze_stops_before_asset_png_write(self):
        pixels=np.zeros((1024,1536,4),dtype=np.uint8)
        pixels[10:1014,10:1526]=[143,20,180,1]
        pixels[512,768,3]=2
        self.assert_budget_rejected([65,28,1325,145],pixels,'tiny-core')

    def test_positive_and_negative_far_targets_are_rejected(self):
        for index,target in enumerate(([8000,0,160,90],[-8000,0,160,90],
                                       [0,5000,160,90],[0,-5000,160,90])):
            self.assert_budget_rejected(target,name='far-'+str(index))

    def test_physical_size_budget_boundary_inside_and_outside(self):
        result=self.call([0,0,7680,4320],name='limit')
        self.assertEqual(result['assets'][0]['target'],[0.,0.,7680.,4320.])
        self.assert_budget_rejected([0,0,7680.001,4320.001],name='size-outside')

    def test_picture_frame_edges_obey_four_page_bounds(self):
        for index,target in enumerate(([-7680,0,160,90],[7520,0,160,90],
                                       [0,-4320,160,90],[0,4230,160,90])):
            result=self.call(target,name='edge-in-'+str(index))
            self.assertEqual(result['assets'][0]['target'],list(map(float,target)))
        for index,target in enumerate(([-7680.001,0,160,90],[7520.001,0,160,90],
                                       [0,-4320.001,160,90],[0,4230.001,160,90])):
            self.assert_budget_rejected(target,name='edge-out-'+str(index))


class DiagnosticTitlePlacement(unittest.TestCase):
    def test_actual_653_over_217_diagnostic_title_retains_145_high_ink(self):
        source=os.environ.get('PRK_REAL_TITLE_DIAGNOSTIC')
        if not source:self.skipTest('Set PRK_REAL_TITLE_DIAGNOSTIC to the supplied diagnostic ZIP')
        with ZipFile(source) as z:
            data=z.read('title/raw_provider_output/wide_transparent_background_image_checkerboard_al.png')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'raw.png';path.write_bytes(data)
            member={'id':'o001','box':[65,28,1325,145],'target':[65,28,1325,145],'z':20}
            result=extract_wordart_asset(member,path,Path(folder)/'assets');a=result['assets'][0]
            with Image.open(io.BytesIO(data)) as raw:pixels=np.asarray(raw.convert('RGBA'))
            self.assertEqual(a['crop_box'],[16,47,1521,700])
            np.testing.assert_array_equal(np.array(Image.open(a['path'])),pixels[47:700,16:1521])
            self.assertAlmostEqual(a['stretch'][0],145/217)
            self.assertEqual(a['stretch'][0],a['stretch'][1])
            self.assertAlmostEqual(217*a['stretch'][1],145)
            self.assertEqual(a['placement']['ink_bbox'],[17,359,1520,576])
            self.assertEqual(a['authored_target'],member['target'])


if __name__=='__main__':unittest.main()
