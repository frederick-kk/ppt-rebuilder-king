"""Bounded whole weak edge groups reuse existing wordart mass geometry."""
from pathlib import Path
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))

import numpy as np
from PIL import Image
from newpath import wordart


class BoundedWeakWordartEdges(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-bounded-title-edge-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.member = {'id': 'title', 'box': [100, 100, 900, 120],
                       'target': [100, 100, 900, 120], 'lettering': 'Exact source title'}

    def pixels(self):
        pixels = np.full((512, 1024, 4), [31, 47, 95, 0], dtype=np.uint8)
        pixels[200:400, 100:900] = [120, 20, 15, 255]
        pixels[100:120, 0:20] = [205, 60, 40, 2]
        return pixels

    def save(self, pixels, name='case'):
        path = self.root / (name + '.png')
        Image.fromarray(pixels).save(path)
        return path

    def assert_rejected(self, pixels):
        path = self.save(pixels)
        original = path.read_bytes()
        destination = self.root / 'rejected-assets'
        with self.assertRaises(ValueError):
            wordart.extract_wordart_asset(self.member, path, destination)
        self.assertFalse(list(destination.glob('*.png')))
        self.assertEqual(path.read_bytes(), original)

    def validate_expected(self, path):
        try:
            return wordart.validate_wordart_output(path)
        except ValueError as error:
            self.fail('The existing mass-safe inset geometry should permit this complete PNG. ' + str(error))

    def test_disconnected_bounded_mass_edge_is_registered_without_changing_rgba(self):
        pixels = self.pixels()
        path = self.save(pixels)
        original = path.read_bytes()
        info = self.validate_expected(path)
        self.assertIn('native_wordart_bounded_weak_edge_overspray',
                      {warning['reason'] for warning in info['warnings']})
        result = wordart.extract_wordart_asset(self.member, path, self.root / 'assets')
        asset = result['assets'][0]
        self.assertEqual(asset['placement']['ink_bbox'], [100, 200, 900, 400])
        self.assertEqual(asset['placement']['geometry_recovery']['policy'],
                         'bounded-weak-wordart-alpha-mass/1')
        left, top, right, bottom = asset['crop_box']
        actual = np.asarray(Image.open(asset['path']).convert('RGBA'))
        np.testing.assert_array_equal(actual, pixels[top:bottom, left:right])
        self.assertEqual(np.count_nonzero(actual[..., 3]), np.count_nonzero(pixels[..., 3]))
        self.assertEqual(path.read_bytes(), original)

    def test_weak_group_connected_to_strong_subject_still_rejects(self):
        pixels = self.pixels()
        pixels[119, 20:101, 3] = 2
        pixels[119:201, 100, 3] = 2
        self.assert_rejected(pixels)

    def test_disconnected_strong_edge_mark_still_rejects(self):
        pixels = self.pixels()
        pixels[250, 0] = [80, 20, 10, 255]
        self.assert_rejected(pixels)

    def test_alpha4_edge_group_still_rejects(self):
        pixels = self.pixels()
        pixels[100:120, 0:20, 3] = 4
        self.assert_rejected(pixels)

    def test_weak_opacity_mass_above_existing_budget_still_rejects(self):
        pixels = self.pixels()
        pixels[200:400, 100:900, 3] = 0
        pixels[200:220, 100:120, 3] = 255
        self.assert_rejected(pixels)

    def test_no_stronger_core_still_rejects(self):
        pixels = self.pixels()
        pixels[200:400, 100:900, 3] = 3
        self.assert_rejected(pixels)

    def test_whole_nontransparent_canvas_still_rejects(self):
        pixels = self.pixels()
        pixels[..., 3] = np.maximum(pixels[..., 3], 2)
        self.assert_rejected(pixels)

    def test_single_pixel_edge_uses_mass_evidence_with_original_micro_geometry(self):
        pixels = self.pixels()
        pixels[100:120, 0:20, 3] = 0
        pixels[100, 0, 3] = 2
        path = self.save(pixels)
        info = self.validate_expected(path)
        proof = info['warnings'][0]['edge_validation']
        self.assertEqual(proof['weak_alpha_mass'], 2)
        result = wordart.extract_wordart_asset(self.member, path, self.root / 'assets')
        asset = result['assets'][0]
        self.assertEqual(asset['placement']['geometry_recovery']['policy'],
                         'bounded-weak-wordart-geometry/1')

    def test_micro_alpha3_edge_group_uses_same_mass_limit(self):
        pixels = self.pixels()
        pixels[100:120, 0:20, 3] = 0
        pixels[100:102, 0:3, 3] = 3
        info = self.validate_expected(self.save(pixels))
        proof = info['warnings'][0]['edge_validation']
        self.assertEqual(proof['weak_alpha_mass'], 18)
        self.assertEqual(proof['max_weak_alpha'], 3)

    def test_one_pixel_geometry_preservation_does_not_block_safe_edge_validation(self):
        pixels = self.pixels()
        pixels[100:120, 0:20, 3] = 0
        pixels[300, 1, 3] = 255
        pixels[250, 0, 3] = 2
        for index in range(70):
            y = 220 + (index // 20) * 30
            x = 150 + (index % 20) * 30
            pixels[y - 1:y + 2, x - 1:x + 2, 3] = 0
            pixels[y, x, 3] = 2
        self.assertEqual(wordart._ink_bounds(pixels[..., 3]), ([0, 200, 900, 400], None))
        path = self.save(pixels)
        info = self.validate_expected(path)
        proof = info['warnings'][0]['edge_validation']
        self.assertEqual(proof['retained_ink_bbox'], [1, 200, 900, 400])
        result = wordart.extract_wordart_asset(self.member, path, self.root / 'assets')
        asset = result['assets'][0]
        self.assertEqual(asset['placement']['ink_bbox'], [0, 200, 900, 400])
        self.assertNotIn('geometry_recovery', asset['placement'])
        left, top, right, bottom = asset['crop_box']
        np.testing.assert_array_equal(np.asarray(Image.open(asset['path']).convert('RGBA')),
                                      pixels[top:bottom, left:right])

    def test_placement_budget_is_checked_after_bounded_edge_validation(self):
        path = self.save(self.pixels())
        self.validate_expected(path)
        member = copy.deepcopy(self.member)
        member['target'] = [10000, 20000, 900, 120]
        destination = self.root / 'budget-assets'
        with self.assertRaisesRegex(ValueError, 'placement.*budget'):
            wordart.extract_wordart_asset(member, path, destination)
        self.assertFalse(list(destination.glob('*.png')))

    def test_original_alpha1_edge_warning_is_unchanged(self):
        pixels = self.pixels()
        pixels[100:120, 0:20, 3] = 0
        pixels[100:120, 0, 3] = 1
        info = wordart.validate_wordart_output(self.save(pixels))
        self.assertEqual(info['warnings'], [{
            'reason': 'native_wordart_alpha1_edge_overspray',
            'edge_max_alpha': 1,
            'edge_nonzero_pixels': {'top': 0, 'bottom': 0, 'left': 20, 'right': 0},
            'all_rgba_pixels_preserved': True,
            'message': 'Only alpha=1 overspray touches the canvas; the alpha>1 ink core is fully inset. '
                       'All provider pixels are retained without regeneration; visual quality remains unverified.'}])

    @unittest.skipUnless(os.environ.get('PRK_R12_TITLE_EDGE_JOB'), 'actual R12 supplied privately')
    def test_actual_r12_title_reuses_existing_mass_geometry_and_all_rgba(self):
        job = Path(os.environ['PRK_R12_TITLE_EDGE_JOB'])
        state = json.loads((job / 'state.json').read_text('utf-8'))
        task = next(task for task in state['tasks'].values() if task['role'] == 'wordart')
        path = job / task['attempts'][0]['raw']
        original = path.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '0ff8b7322ce3e5b7e4156834d36e21597cf84486168652be9e42bc5c9e861065')
        raw = np.asarray(Image.open(path).convert('RGBA'))
        info = self.validate_expected(path)
        self.assertEqual(info['quality'], 'not_evaluated')
        self.assertIn('native_wordart_bounded_weak_edge_overspray',
                      {warning['reason'] for warning in info['warnings']})
        result = wordart.extract_wordart_asset(copy.deepcopy(task['member']), path, self.root / 'actual-assets')
        asset = result['assets'][0]
        self.assertEqual(asset['placement']['ink_bbox'], [23, 405, 1489, 670])
        recovery = asset['placement']['geometry_recovery']
        self.assertEqual(recovery['alpha_mass'], 2476)
        self.assertEqual(recovery['retained_alpha_mass'], 16883274)
        self.assertEqual(recovery['mass_ratio_denominator'], 4096)
        self.assertEqual(recovery['max_alpha'], 3)
        left, top, right, bottom = asset['crop_box']
        actual = np.asarray(Image.open(asset['path']).convert('RGBA'))
        np.testing.assert_array_equal(actual, raw[top:bottom, left:right])
        self.assertEqual(np.count_nonzero(actual[..., 3]), 275976)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
