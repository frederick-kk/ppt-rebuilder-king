"""Only alpha=1 edge overspray may continue without regenerating title artwork."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))
from newpath import wordart


class WordartEdgeOverspray(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.member={'id':'title','kind':'art','box':[72,192,720,160],
                     'target':[72,192,720,160],'z':5,'lettering':'汇报目录'}

    def pixels(self):
        pixels=np.empty((32,80,4),dtype=np.uint8);pixels[:]=[71,21,143,0]
        pixels[8:24,10:70]=[15,130,220,255]
        return pixels

    def save(self,pixels):
        path=self.root/'raw.png';Image.fromarray(pixels).save(path);return path

    def extract_preserving_raw(self,path):
        before=path.read_bytes()
        try:return wordart.extract_wordart_asset(self.member,path,self.root/'assets')
        finally:self.assertEqual(path.read_bytes(),before)

    def assert_overspray_warning(self,warnings):
        self.assertTrue(any(w.get('reason')=='native_wordart_alpha1_edge_overspray' for w in warnings))

    def test_alpha_one_on_every_edge_passes_with_explicit_warning(self):
        for x,y in [(0,15),(79,15),(40,0),(40,31)]:
            with self.subTest(point=(x,y)):
                pixels=self.pixels();pixels[y,x]=[255,31,170,1]
                path=self.save(pixels);info=wordart.validate_wordart_output(path)
                self.assert_overspray_warning(info['warnings'])
                self.assertEqual(info['quality'],'not_evaluated')
                np.testing.assert_array_equal(np.array(Image.open(path)),pixels)

    def test_all_original_rgba_and_hidden_colours_survive_tight_crop(self):
        pixels=self.pixels();pixels[15,0]=[255,31,170,1];pixels[31,30]=[21,220,255,1]
        result=self.extract_preserving_raw(self.save(pixels));asset=result['assets'][0]
        self.assertEqual(asset['crop_box'],[0,8,70,32])
        np.testing.assert_array_equal(np.array(Image.open(asset['path'])),pixels[8:32,0:70])
        self.assert_overspray_warning(result['warnings'])
        self.assertEqual(asset['placement']['ink_bbox'],[10,8,70,24])
        self.assertEqual(asset['placement']['visible_ink_target'],[132.,192.,600.,160.])
        self.assertEqual(asset['stretch'],[10.,10.])
        self.assertTrue(asset['placement']['all_rgba_pixels_preserved'])

    def test_clear_canvas_edges_have_no_new_warning(self):
        info=wordart.validate_wordart_output(self.save(self.pixels()))
        self.assertEqual(info['warnings'],[])

    def test_alpha_greater_than_one_on_each_edge_remains_rejected(self):
        for alpha in [2,8,16,32,255]:
            for x,y in [(0,15),(79,15),(40,0),(40,31)]:
                with self.subTest(alpha=alpha,point=(x,y)):
                    pixels=self.pixels();pixels[y,x]=[255,31,170,alpha]
                    with self.assertRaisesRegex(ValueError,'edge'):
                        wordart.validate_wordart_output(self.save(pixels))

    def test_alpha_one_only_art_touching_edge_remains_rejected(self):
        pixels=self.pixels();pixels[pixels[...,3]>0,3]=1;pixels[15,0,3]=1
        with self.assertRaisesRegex(ValueError,'edge'):
            wordart.validate_wordart_output(self.save(pixels))

    def test_no_fully_transparent_pixels_remains_rejected(self):
        pixels=self.pixels();pixels[pixels[...,3]==0,3]=1
        with self.assertRaisesRegex(ValueError,'fully transparent'):
            wordart.validate_wordart_output(self.save(pixels))

    def test_rgb_checkerboard_remains_rejected(self):
        yy,xx=np.indices((32,80));checker=np.repeat(np.where(((xx//4+yy//4)%2)[...,None],190,255),3,axis=2).astype('uint8')
        with self.assertRaisesRegex(ValueError,'native transparency'):
            wordart.validate_wordart_output(self.save(checker))

    def test_painted_rgba_background_with_one_transparent_pixel_is_not_softcase(self):
        pixels=self.pixels();pixels[...,3]=255;pixels[0,0,3]=0
        with self.assertRaisesRegex(ValueError,'edge'):
            wordart.validate_wordart_output(self.save(pixels))

    @unittest.skipUnless(os.environ.get('PRK_TITLE_RETRY_EVIDENCE'),'real title retry evidence not supplied')
    def test_real_retry_one_continues_without_losing_any_rgba_or_changing_placement(self):
        path=Path(os.environ['PRK_TITLE_RETRY_EVIDENCE'])/'external_tool_returns/a_clean_transparent_background_graphic_png_with_c.png'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),'78284626d2ad1699812e9914886452bd339c65af007a58902af58d0acafe70ee')
        with Image.open(path) as image:raw=np.array(image.convert('RGBA'))
        result=self.extract_preserving_raw(path);asset=result['assets'][0]
        self.assertEqual(asset['crop_box'],[0,112,1670,941])
        np.testing.assert_array_equal(np.array(Image.open(asset['path'])),raw[112:941,0:1670])
        self.assertEqual(asset['placement']['ink_bbox'],[79,293,1591,836])
        self.assertAlmostEqual(asset['stretch'][0],160/543)
        self.assertEqual(asset['stretch'][0],asset['stretch'][1])
        self.assert_overspray_warning(result['warnings'])
        self.assertEqual(result['validation']['quality'],'not_evaluated')

    @unittest.skipUnless(os.environ.get('PRK_TITLE_RETRY_EVIDENCE'),'real title retry evidence not supplied')
    def test_real_no_alpha_checkerboard_and_no_zero_photo_remain_rejected(self):
        root=Path(os.environ['PRK_TITLE_RETRY_EVIDENCE'])/'external_tool_returns'
        for name in ['a_clean_graphic_image_on_a_transparent_background.png',
                     'a_wide_well_lit_auditorium_stage_presentation_sli.png']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                wordart.validate_wordart_output(root/name)


if __name__=='__main__':unittest.main()
