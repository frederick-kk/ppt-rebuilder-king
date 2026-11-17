"""Bound weak disconnected geometry without changing any provider RGBA pixels."""
import json
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


class WeakWordartGeometry(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='prk-weak-title-')
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.member={'id':'title','box':[100,200,1100,100],
                     'target':[100,200,1100,100],'lettering':'Exact artwork'}

    def pixels(self):
        pixels=np.empty((120,300,4),dtype=np.uint8)
        yy,xx=np.indices(pixels.shape[:2])
        pixels[...,0]=xx%251;pixels[...,1]=yy%251;pixels[...,2]=173
        pixels[...,3]=0
        pixels[40:60,40:260]=[255,255,255,255]
        return pixels

    def extract(self,pixels,name='case',member=None):
        raw=self.root/(name+'.png');Image.fromarray(pixels).save(raw)
        before=raw.read_bytes()
        output=self.root/(name+'-assets')
        try:
            result=wordart.extract_wordart_asset(member or self.member,raw,output)
        finally:
            self.assertEqual(raw.read_bytes(),before)
        asset=result['assets'][0]
        left,top,right,bottom=asset['crop_box']
        np.testing.assert_array_equal(np.asarray(Image.open(asset['path']).convert('RGBA')),
                                      pixels[top:bottom,left:right])
        self.assertEqual(asset['stretch'][0],asset['stretch'][1])
        self.assertTrue(asset['placement']['all_rgba_pixels_preserved'])
        self.assertEqual(asset['placement']['alpha_threshold_for_geometry_only'],1)
        return result,asset

    def test_disconnected_tiny_weak_points_no_longer_shrink_real_ink(self):
        pixels=self.pixels()
        pixels[100,250]=[50,100,200,2]
        pixels[63:65,245:247]=[17,31,47,3]
        result,asset=self.extract(pixels)
        self.assertEqual(asset['placement']['ink_bbox'],[40,40,260,60])
        self.assertEqual(asset['stretch'],[5.,5.])
        self.assertEqual(asset['crop_box'],[40,40,260,101])
        self.assertEqual(asset['placement']['visible_ink_target'],[100.,200.,1100.,100.])
        self.assertTrue(any(w.get('reason')=='native_wordart_weak_geometry_fragments'
                            for w in result['warnings']))

    def test_weak_point_connected_to_writing_or_outline_remains_geometry(self):
        pixels=self.pixels();pixels[60:90,150]=[255,255,255,2]
        unused,asset=self.extract(pixels)
        self.assertEqual(asset['placement']['ink_bbox'],[40,40,260,90])

    def test_solid_disconnected_dot_and_white_strokes_remain_geometry(self):
        for alpha in [4,255]:
            with self.subTest(alpha=alpha):
                pixels=self.pixels();pixels[100,250]=[255,255,255,2]
                pixels[108,270]=[255,255,255,alpha]
                pixels[30:33,30:33]=[255,255,255,4]
                unused,asset=self.extract(pixels,name='solid-dot-'+str(alpha))
                self.assertEqual(asset['placement']['ink_bbox'],[30,30,271,109])

    def test_all_low_alpha_art_keeps_every_disconnected_mark_in_geometry(self):
        pixels=self.pixels();pixels[40:60,40:260,3]=2
        pixels[100,250]=[255,255,255,2]
        unused,asset=self.extract(pixels)
        self.assertEqual(asset['placement']['ink_bbox'],[40,40,260,101])

    def test_component_budget_routes_large_faint_group_to_alpha_mass_fallback(self):
        for pixels_count in [8,9]:
            with self.subTest(pixels_count=pixels_count):
                pixels=self.pixels()
                if pixels_count==8:
                    pixels[90:92,250:254]=[255,255,255,3]
                else:
                    pixels[90:93,250:253]=[255,255,255,3]
                unused,asset=self.extract(pixels,name='component-'+str(pixels_count))
                self.assertEqual(asset['placement']['ink_bbox'],[40,40,260,60])
                recovery=asset['placement']['geometry_recovery']
                if pixels_count==8:
                    self.assertEqual(recovery['policy'],'bounded-weak-wordart-geometry/1')
                    self.assertEqual(recovery['component_pixel_limit'],8)
                else:
                    self.assertEqual(recovery['policy'],'bounded-weak-wordart-alpha-mass/1')
                    self.assertEqual(recovery['alpha_mass'],27)
                    self.assertEqual(recovery['mass_ratio_denominator'],4096)

    def test_total_pixel_budget_routes_overflow_to_alpha_mass_fallback(self):
        positions=[(x,y) for y in [90,94,98] for x in range(10,290,10)]
        for count in [64,65]:
            with self.subTest(count=count):
                pixels=self.pixels()
                for x,y in positions[:count]:pixels[y,x]=[255,255,255,2]
                unused,asset=self.extract(pixels,name='global-'+str(count))
                self.assertEqual(asset['placement']['ink_bbox'],[40,40,260,60])
                recovery=asset['placement']['geometry_recovery']
                if count==64:
                    self.assertEqual(recovery['policy'],'bounded-weak-wordart-geometry/1')
                    self.assertEqual(recovery['total_pixel_limit'],64)
                else:
                    self.assertEqual(recovery['policy'],'bounded-weak-wordart-alpha-mass/1')
                    self.assertEqual(recovery['alpha_mass'],130)
                    self.assertEqual(recovery['mass_ratio_denominator'],4096)

    def test_bounded_weak_canvas_edge_point_preserves_all_rgba(self):
        pixels=self.pixels();pixels[0,100]=[255,255,255,2]
        result,asset=self.extract(pixels,'edge')
        self.assertIn('native_wordart_bounded_weak_edge_overspray',
                      {warning['reason'] for warning in result['warnings']})

    def test_recovered_tiny_ink_keeps_four_page_picture_budget(self):
        pixels=np.empty((100,200,4),dtype=np.uint8);pixels[:]=[33,66,99,0]
        pixels[10:90,20:180]=[33,66,99,1]
        pixels[50,100]=[255,255,255,255]
        pixels[85,150]=[255,255,255,2]
        raw=self.root/'budget.png';Image.fromarray(pixels).save(raw)
        before=raw.read_bytes();output=self.root/'budget-assets'
        with self.assertRaisesRegex(ValueError,'placement.*budget'):
            wordart.extract_wordart_asset(self.member,raw,output)
        self.assertFalse(list(output.glob('*.png')))
        self.assertEqual(raw.read_bytes(),before)

    @unittest.skipUnless(os.environ.get('PRK_LIVE_FOOTER_JOB'),'live footer job not supplied')
    def test_actual_footer_restores_authored_width_with_all_original_rgba(self):
        job=Path(os.environ['PRK_LIVE_FOOTER_JOB'])
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        task=next(task for task in state['tasks'].values() if task['role']=='wordart')
        raw=job/task['result']['raw'];before=raw.read_bytes()
        pixels=np.asarray(Image.open(raw).convert('RGBA'))
        result=wordart.extract_wordart_asset(task['member'],raw,self.root/'actual-footer')
        self.assertEqual(raw.read_bytes(),before)
        asset=result['assets'][0]
        self.assertEqual(asset['placement']['ink_bbox'],[67,449,1472,552])
        self.assertAlmostEqual(asset['stretch'][0],1215/1405)
        self.assertEqual(asset['stretch'][0],asset['stretch'][1])
        self.assertAlmostEqual(asset['placement']['visible_ink_target'][2],1215)
        self.assertEqual(asset['crop_box'],[0,23,1526,984])
        np.testing.assert_array_equal(np.asarray(Image.open(asset['path']).convert('RGBA')),pixels[23:984,:1526])


if __name__=='__main__':unittest.main(verbosity=2)
