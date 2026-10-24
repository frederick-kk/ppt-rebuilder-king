"""Opt-in single-line containment, preserving native text and honest metrics."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE/'compat-runtime'))
from newpath import renderer
from newpath.layout import normalize_layout

POLICY = 'bounded-single-line-contain/1'


class TextOverflowRecovery(unittest.TestCase):
    def item(self, **changes):
        item = {'id':'body', 'kind':'text', 'box':[1646,738,268,32],
                'text':'快速、稳定地送达全国各地。', 'size':25, 'color':'273746'}
        item.update(changes)
        return normalize_layout({'items':[item]})['items'][0]

    def fit(self, item):
        # Existing implementation must fail on missing behavior, not a new API.
        if not hasattr(renderer, 'TEXT_FIT_POLICY'):
            return renderer._fit_text(item)
        return renderer._fit_text(item, textfit_policy=POLICY)

    def test_known_cjk_overflow_fits_with_matching_font_and_preserves_text(self):
        item = self.item()
        fitted, record, unused = self.fit(item)
        self.assertLess(fitted['size'], item['size'])
        self.assertGreaterEqual(fitted['size'], item['size']*.75)
        self.assertEqual(fitted['text'], item['text'])
        self.assertEqual(fitted['font'], item['font'])
        self.assertEqual(fitted['box'], item['box'])
        self.assertLessEqual(record['measurement']['fitted_width_px'], 268)
        self.assertEqual(record.get('textfit_policy'), POLICY)

    def test_missing_font_is_conservative_estimate_not_false_measurement(self):
        with mock.patch.object(renderer, '_find_font', return_value=None):
            fitted, record, warnings = self.fit(self.item())
        self.assertLess(fitted['size'], 25)
        self.assertEqual(record['measurement']['status'], 'estimated')
        self.assertIsNone(record['measurement']['face'])
        self.assertLessEqual(record['measurement']['fitted_width_px'], 268)
        self.assertTrue(any('estimated' in w for w in warnings))
        self.assertTrue(record['measurement'].get('conservative_fallback'))

    def test_missing_font_wide_ascii_does_not_use_narrow_average(self):
        item = self.item(text='WWWWWWWWWW',box=[100,100,230,32])
        with mock.patch.object(renderer, '_find_font', return_value=None):
            fitted, record, unused = self.fit(item)
        self.assertLess(fitted['size'],25)
        self.assertLessEqual(record['measurement']['fitted_width_px'],230)
        self.assertEqual(record['measurement']['status'],'estimated')

    def test_fitting_line_is_unchanged(self):
        item=self.item(text='原样保留',box=[100,100,220,40])
        fitted,record,unused=self.fit(item)
        self.assertEqual(fitted,item)
        self.assertEqual(record['effective_size_px'],25)

    def test_right_page_edge_bounds_the_text_box(self):
        item=self.item(text='天地玄黄宇宙',box=[1800,100,200,40])
        fitted,record,unused=self.fit(item)
        self.assertEqual(fitted['box'],[1800,100,120,40])
        self.assertEqual(record['authored_box'],item['box'])
        self.assertLessEqual(record['measurement']['fitted_width_px'],120)
        self.assertLessEqual(fitted['box'][0]+fitted['box'][2],1920)

    def test_runs_keep_content_colours_and_relative_sizes(self):
        item=self.item(text='前半句后半句',runs=[{'text':'前半句','size':25,'color':'FF0000'},
                                              {'text':'后半句','size':20,'color':'0000FF'}],
                       box=[100,100,120,40],tracking=.5)
        fitted,record,unused=self.fit(item)
        self.assertEqual([r['text'] for r in fitted['runs']],['前半句','后半句'])
        self.assertEqual([r['color'] for r in fitted['runs']],['FF0000','0000FF'])
        self.assertEqual(fitted['tracking'],.5)
        self.assertLessEqual(record['measurement']['fitted_width_px'],120)
        self.assertAlmostEqual(fitted['runs'][0]['size']/25,fitted['runs'][1]['size']/20,delta=.002)

    def test_excessive_required_reduction_stops_instead_of_tiny_text(self):
        with self.assertRaisesRegex(ValueError,'contain|reduction|budget'):
            self.fit(self.item(box=[100,100,40,32]))

    def test_vertical_and_rotated_text_keep_authored_geometry(self):
        for changes in ({'vertical':True},{'rotation':30}):
            item=self.item(**changes)
            fitted,record,unused=self.fit(item)
            self.assertEqual(fitted,item)
            self.assertEqual(record['effective_size_px'],25)

    def test_italic_measurement_never_claims_verified_style(self):
        item=self.item(italic=True)
        fitted,record,unused=self.fit(item)
        self.assertEqual(fitted,item)
        self.assertEqual(record['measurement']['confidence'],'unverified_italic_style')

    def test_legacy_policy_remains_unchanged_for_old_jobs(self):
        with mock.patch.object(renderer,'_find_font',return_value=None):
            fitted,record,unused=renderer._fit_text(self.item())
        self.assertEqual(fitted['size'],25)
        self.assertGreater(record['measurement']['remaining_overflow_px'],50)

    def test_policy_rejects_unknown_value(self):
        if not hasattr(renderer,'TEXT_FIT_POLICY'):
            self.fail('versioned text fit policy missing')
        with self.assertRaisesRegex(ValueError,'policy'):
            renderer._fit_text(self.item(),textfit_policy='future-unrecognized')

    def test_new_policy_flows_into_native_pptx_and_report(self):
        with tempfile.TemporaryDirectory(prefix='prk-text-contain-') as directory:
            root=Path(directory);bg=root/'background.png';Image.new('RGB',(1920,1080),'white').save(bg)
            out=root/'result.pptx';kwargs={} if not hasattr(renderer,'TEXT_FIT_POLICY') else {'textfit_policy':POLICY}
            report=renderer.build_presentation({'items':[self.item()]},bg,[],out,**kwargs)
            record=next(o for o in report['objects'] if o['id']=='body')
            self.assertLess(record['effective_size_px'],25)
            with ZipFile(out) as archive:
                tree=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
                ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                self.assertEqual(''.join(tree.itertext()).find('快速、稳定地送达全国各地。')>=0,True)
                sizes=[int(n.attrib['sz']) for n in tree.findall('.//a:rPr',ns) if 'sz' in n.attrib]
                self.assertTrue(sizes and all(size<=1030 for size in sizes))

    @unittest.skipUnless(os.environ.get('PRK_NATIVE_MISSING_DIAGNOSTIC'),'real overflow diagnostic not supplied')
    def test_actual_native_layout_overflow_line_is_contained(self):
        with ZipFile(os.environ['PRK_NATIVE_MISSING_DIAGNOSTIC']) as archive:
            state=json.loads(archive.read('job/ppt_rebuild_current/state.json'))
        item=next(i for i in state['layout']['items'] if i['id']=='o025')
        with mock.patch.object(renderer,'_find_font',return_value=None):
            fitted,record,unused=self.fit(item)
        self.assertEqual(fitted['text'],item['text'])
        self.assertLessEqual(record['measurement']['fitted_width_px'],item['box'][2])
        self.assertLess(fitted['size'],item['size'])


if __name__=='__main__':unittest.main()
