"""ASCII digit fallback must not turn ordinary numbering into a fatal budget."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath import renderer
from newpath.layout import normalize_layout


class NativeDigitEstimation(unittest.TestCase):
    def item(self, **changes):
        item={'id':'number','kind':'text','text':'01','size':67,
              'box':[179,407,92,73],'bold':True,'color':'49A8D8'}
        item.update(changes)
        return normalize_layout({'items':[item]})['items'][0]

    def fit_missing(self,item):
        with mock.patch.object(renderer,'_find_font',return_value=None):
            return renderer._fit_text(item,textfit_policy=renderer.TEXT_FIT_POLICY)

    def test_four_real_number_labels_keep_the_authored_size_without_matching_font(self):
        for number in ('01','02','03','04'):
            with self.subTest(number=number):
                item=self.item(text=number)
                fitted,record,unused=self.fit_missing(item)
                self.assertEqual(fitted,item)
                self.assertEqual(record['measurement']['status'],'estimated')
                self.assertLessEqual(record['measurement']['fitted_width_px'],92)

    def test_small_page_number_does_not_shrink_due_to_one_em_assumption(self):
        item=self.item(id='page-number',text='02',size=24,box=[1839,1004,46,34],bold=False)
        fitted,record,unused=self.fit_missing(item)
        self.assertEqual(fitted,item)
        self.assertLessEqual(record['measurement']['fitted_width_px'],46)

    def test_digit_estimate_is_not_claimed_as_matching_face_or_strict_upper_bound(self):
        with mock.patch.object(renderer,'_find_font',return_value=None):
            width,status,face,conservative=renderer._contain_width(self.item())
        self.assertAlmostEqual(width,89.78,places=6)
        self.assertEqual(status,'estimated');self.assertIsNone(face)
        unused,record,unused_warnings=self.fit_missing(self.item())
        self.assertFalse(record['measurement'].get('estimate_is_strict_upper_bound',True))

    def test_only_ascii_digits_change_wide_letters_cjk_and_fullwidth_digits_stay_wide(self):
        cases=[('WM',100),('０１',100),('中医',100),('🙂',100)]
        for text,wanted in cases:
            with self.subTest(text=text),mock.patch.object(renderer,'_find_font',return_value=None):
                width,status,face,unused=renderer._contain_width(self.item(text=text,size=50))
                self.assertEqual(width,wanted)
                self.assertEqual(status,'estimated')

    def test_runs_use_their_own_sizes_and_fixed_tracking(self):
        item=self.item(text='01A',size=50,box=[100,100,140,80],tracking=2,
                       runs=[{'text':'01','size':60,'color':'FF0000'},
                             {'text':'A','size':40,'color':'0000FF'}])
        fitted,record,unused=self.fit_missing(item)
        self.assertEqual(fitted,item)
        self.assertAlmostEqual(record['measurement']['fitted_width_px'],124.4,places=4)

    def test_too_narrow_number_still_respects_the_reduction_budget(self):
        item=self.item(id='narrow-number',box=[100,100,60,73])
        with self.assertRaisesRegex(ValueError,'25 percent') as context:
            self.fit_missing(item)
        message=str(context.exception)
        for detail in ('narrow-number','estimated','Microsoft YaHei','89.78','60'):
            self.assertIn(detail,message)

    def test_matching_font_measurement_is_not_replaced_with_digit_estimate(self):
        item=self.item()
        with mock.patch.object(renderer,'_measured_width',return_value=(134,'measured','Microsoft YaHei')):
            width,status,face,conservative=renderer._contain_width(item)
            self.assertEqual((width,status,face,conservative),(134,'measured','Microsoft YaHei',False))
            with self.assertRaisesRegex(ValueError,'25 percent') as context:
                renderer._fit_text(item,textfit_policy=renderer.TEXT_FIT_POLICY)
        self.assertIn('number',str(context.exception))
        self.assertIn('measured',str(context.exception))

    def test_legacy_default_digit_behavior_remains_unchanged(self):
        item=self.item()
        with mock.patch.object(renderer,'_find_font',return_value=None):
            fitted,record,unused=renderer._fit_text(item)
        self.assertEqual(fitted,item)
        self.assertAlmostEqual(record['measurement']['width_px'],75.04,places=4)
        self.assertNotIn('textfit_policy',record)

    @unittest.skipUnless(os.environ.get('PRK_TEXTFIT_BUDGET_DIAGNOSTIC'),'actual textfit budget diagnostic not supplied')
    def test_actual_ten_object_layout_no_longer_fails_with_matching_font_missing(self):
        with ZipFile(os.environ['PRK_TEXTFIT_BUDGET_DIAGNOSTIC']) as archive:
            member=min((n for n in archive.namelist() if n.endswith('/job/layout.json')),key=len)
            layout=normalize_layout(json.loads(archive.read(member)))
        texts=[item for item in layout['items'] if item['kind']=='text']
        self.assertEqual(len(texts),10)
        for item in texts:
            with self.subTest(id=item['id']):
                fitted,record,unused=self.fit_missing(item)
                self.assertEqual(fitted,item)
                self.assertLessEqual(record['measurement']['fitted_width_px'],item['box'][2])


if __name__=='__main__':unittest.main()
