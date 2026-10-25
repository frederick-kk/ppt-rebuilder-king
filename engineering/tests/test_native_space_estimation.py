"""Missing-font ASCII spaces must not falsely exhaust the native text budget."""
import copy
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
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import renderer
from newpath.layout import normalize_layout


class NativeSpaceEstimation(unittest.TestCase):
    def item(self, **changes):
        # Literal captured o017 fields; no lost full job is reconstructed here.
        raw = {'id': 'o017', 'kind': 'text', 'box': [1062, 398, 350, 40],
               'z': 50, 'size': 29, 'color': '171717',
               'text': '快板打节奏 + 英文 Rap 说唱'}
        raw.update(changes)
        return normalize_layout({'items': [raw]})['items'][0]

    def fit_missing(self, item):
        # Font discovery only reads the host environment. Replacing it makes
        # the captured unavailable-font branch reproducible on Windows/Linux.
        with mock.patch.object(renderer, '_find_font', return_value=None):
            return renderer._fit_text(item, textfit_policy=renderer.TEXT_FIT_POLICY)

    def fit_expected(self, item):
        try:
            return self.fit_missing(item)
        except ValueError as error:
            self.fail('Ordinary spaces falsely exhausted the native text fit budget: ' + str(error))

    def test_captured_mixed_line_fits_without_rewriting_or_expanding_its_box(self):
        # Catches treating U+0020 as a full CJK glyph or escaping the authored box.
        item = self.item()
        before = copy.deepcopy(item)
        fitted, record, warnings = self.fit_expected(item)
        self.assertEqual(item, before)
        for field in ('text', 'box', 'font', 'tracking', 'z'):
            self.assertEqual(fitted[field], before[field])
        self.assertGreaterEqual(fitted['size'], 21.75)  # 75% of the captured 29px.
        self.assertLessEqual(fitted['size'], 29)
        measurement = record['measurement']
        self.assertLessEqual(measurement['fitted_width_px'], 350)
        self.assertEqual(measurement['remaining_overflow_px'], 0)
        self.assertEqual(measurement['status'], 'estimated')
        self.assertIsNone(measurement['face'])
        self.assertFalse(measurement['estimate_is_strict_upper_bound'])
        self.assertTrue(any('estimated' in note for note in warnings))

    def test_mixed_runs_keep_original_content_styles_and_fixed_tracking(self):
        # Catches flattening runs or shrinking character spacing to gain width.
        item = self.item(tracking=.4, runs=[
            {'text': '快板打节奏 + 英文 ', 'size': 29, 'color': '171717'},
            {'text': 'Rap', 'size': 27, 'bold': True, 'color': 'AA0000'},
            {'text': ' 说唱', 'size': 28, 'color': '171717'},
        ])
        before = copy.deepcopy(item)
        fitted, record, unused = self.fit_expected(item)
        self.assertEqual(item, before)
        self.assertEqual(fitted['box'], [1062, 398, 350, 40])
        self.assertEqual(fitted['text'], before['text'])
        self.assertEqual(fitted['tracking'], .4)
        for original, actual in zip(before['runs'], fitted['runs']):
            for field in ('text', 'color', 'bold', 'italic', 'underline'):
                self.assertEqual(actual.get(field), original.get(field))
            self.assertGreaterEqual(actual['size'] / original['size'], .75)
        self.assertLessEqual(record['measurement']['fitted_width_px'], 350)

    def test_matching_font_measurement_is_not_replaced_by_a_space_estimate(self):
        # Catches applying the unavailable-font heuristic to a measured face.
        item = self.item()
        with mock.patch.object(renderer, '_measured_width',
                               return_value=(490.0, 'measured', 'Microsoft YaHei')):
            with self.assertRaisesRegex(ValueError, '25 percent.*status=measured'):
                renderer._fit_text(item, textfit_policy=renderer.TEXT_FIT_POLICY)

    def test_ideographic_space_keeps_its_full_width_and_can_still_fail(self):
        # Catches broad isspace() treatment of the full-width U+3000 character.
        item = self.item(text='甲\u3000乙', size=50, box=[100, 100, 100, 60])
        with self.assertRaisesRegex(ValueError, '25 percent'):
            self.fit_missing(item)

    def test_genuinely_narrow_box_still_refuses_more_than_25_percent_reduction(self):
        # Catches weakening the minimum size or widening a failed target box.
        item = self.item(box=[1062, 398, 250, 40])
        with self.assertRaisesRegex(ValueError, '25 percent'):
            self.fit_missing(item)
        self.assertEqual(item['box'], [1062, 398, 250, 40])

    def test_fixed_tracking_cannot_be_hidden_by_narrower_space_estimation(self):
        # Catches dropping independently authored tracking from the width budget.
        item = self.item(tracking=20)
        with self.assertRaisesRegex(ValueError, '25 percent'):
            self.fit_missing(item)
        self.assertEqual(item['tracking'], 20)

    def test_representable_25_percent_boundary_never_emits_smaller_text(self):
        # Hand checked: 40px at exactly 75% is 30px and representable at .02px.
        item = self.item(size=40, box=[1000, 398, 429.6, 50])
        fitted, record, unused = self.fit_expected(item)
        self.assertGreaterEqual(fitted['size'], 30)
        self.assertLessEqual(record['measurement']['fitted_width_px'], 429.6)
        self.assertEqual(fitted['box'], item['box'])

    def test_legacy_job_retains_its_existing_missing_font_behavior(self):
        # Catches accidentally applying the opt-in change to pre-policy jobs.
        item = self.item()
        with mock.patch.object(renderer, '_find_font', return_value=None):
            fitted, record, unused = renderer._fit_text(item)
        self.assertEqual(fitted, item)
        self.assertNotIn('textfit_policy', record)
        self.assertGreater(record['measurement']['remaining_overflow_px'], 0)

    def test_recovered_line_is_native_editable_text_in_the_original_pptx_frame(self):
        # Exercises actual renderer + Node + DrawingML, not mocked assembly.
        item = self.item()
        with tempfile.TemporaryDirectory(prefix='prk-native-space-') as folder:
            root = Path(folder)
            background = root / 'background.png'
            Image.new('RGB', (1920, 1080), 'white').save(background)
            output = root / 'native.pptx'
            with mock.patch.object(renderer, '_find_font', return_value=None):
                try:
                    report = renderer.build_presentation(
                        {'items': [item]}, background, [], output,
                        textfit_policy=renderer.TEXT_FIT_POLICY)
                except ValueError as error:
                    self.fail('Captured line still prevents native PPTX assembly: ' + str(error))
            self.assertTrue(output.is_file())
            self.assertEqual(report['native_counts']['text_frames'], 1)
            record = next(row for row in report['objects'] if row['id'] == 'o017')
            self.assertEqual(record['authored_box'], [1062, 398, 350, 40])
            with ZipFile(output) as archive:
                tree = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
            ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
                  'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
            shapes = [shape for shape in tree.findall('.//p:sp', ns)
                      if ''.join(node.text or '' for node in shape.findall('.//a:t', ns)) == item['text']]
            self.assertEqual(len(shapes), 1)
            text_shape = shapes[0]
            transform = text_shape.find('./p:spPr/a:xfrm', ns)
            self.assertEqual(transform.find('a:off', ns).attrib,
                             {'x': '6743700', 'y': '2527300'})
            self.assertEqual(transform.find('a:ext', ns).attrib,
                             {'cx': '2222500', 'cy': '254000'})
            sizes = [int(node.attrib['sz']) for node in text_shape.findall('.//a:rPr', ns)
                     if 'sz' in node.attrib]
            self.assertTrue(sizes)
            self.assertTrue(all(1088 <= size <= 1450 for size in sizes))


if __name__ == '__main__':
    unittest.main()
