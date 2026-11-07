"""Bound faint unowned specks during native-board gutter decisions."""
from pathlib import Path
import copy
import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath.artwork import extract_assets
from newpath.cellmap import resolve_cells
from newpath.native import MODE


class WeakNativeGridSpecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-weak-grid-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self):
        board = {'id': 'weak-gap', 'size': [200, 100], 'key': None,
                 'background_mode': MODE,
                 'grid': {'rows': 1, 'columns': 2, 'margin': 10, 'gap': 20,
                          'cell_size': [80, 80]},
                 'members': [
                     {'id': 'left', 'slot': [10, 10, 80, 80], 'box': [10, 10, 50, 40]},
                     {'id': 'right', 'slot': [110, 10, 80, 80], 'box': [110, 10, 50, 40]}]}
        alpha = np.zeros((100, 200), dtype=np.uint8)
        alpha[30:70, 25:75] = 255
        alpha[30:70, 125:175] = 255
        return board, alpha, alpha.copy()

    def mapping(self, board, alpha, reference, *, native=True):
        before = alpha.copy()
        result = resolve_cells(board, alpha, reference_alpha=reference,
                               native_geometry=native)
        np.testing.assert_array_equal(alpha, before)
        return result

    def test_faint_unowned_single_pixel_does_not_omit_both_members(self):
        board, alpha, reference = self.fixture()
        alpha[50, 100] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertEqual(result['cells']['left'], [0, 0, 99, 100])
        recoveries = result['weak_speck_gutter_recoveries']
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0]['unconfirmed_core_pixels'], 1)
        self.assertEqual(recoveries[0]['max_unconfirmed_alpha'], 2)
        self.assertEqual(recoveries[0]['unconfirmed_alpha_limit'], 3)
        self.assertTrue(recoveries[0]['all_rgba_pixels_preserved'])
        self.assertEqual(recoveries[0]['identity_quality'], 'not_evaluated')

    def test_eight_alpha3_pixels_are_within_the_component_limit(self):
        board, alpha, reference = self.fixture()
        alpha[45:49, 100:102] = 3
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        note = result['weak_speck_gutter_recoveries'][0]
        self.assertEqual(note['unconfirmed_core_pixels'], 8)
        self.assertEqual(note['max_unconfirmed_component_core_pixels'], 8)

    def test_sixty_four_faint_pixels_are_within_the_board_limit(self):
        board, alpha, reference = self.fixture()
        points = [(x, y) for y in range(20, 42, 2) for x in range(95, 107, 2)]
        for x, y in points[:64]:
            alpha[y, x] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertEqual(result['weak_speck_gutter_recoveries'][0]['eligible_board_core_pixels'], 64)

    def test_sixty_five_faint_pixels_keep_the_original_rejection(self):
        board, alpha, reference = self.fixture()
        points = [(x, y) for y in range(20, 42, 2) for x in range(95, 107, 2)]
        for x, y in points[:65]:
            alpha[y, x] = 2
        self.assertEqual(self.mapping(board, alpha, reference)['unsafe_members'], ['left', 'right'])

    def test_opaque_and_alpha4_unknown_pixels_keep_the_original_rejection(self):
        for opacity in (4, 255):
            with self.subTest(opacity=opacity):
                board, alpha, reference = self.fixture()
                alpha[50, 100] = opacity
                self.assertEqual(self.mapping(board, alpha, reference)['unsafe_members'], ['left', 'right'])

    def test_complete_nine_pixel_component_exceeds_the_limit(self):
        board, alpha, reference = self.fixture()
        alpha[45:54, 100] = 2
        self.assertEqual(self.mapping(board, alpha, reference)['unsafe_members'], ['left', 'right'])

    def test_reference_is_required(self):
        for kind in ('none', 'empty'):
            with self.subTest(reference=kind):
                board, alpha, reference = self.fixture()
                alpha[50, 100] = 2
                reference = None if kind == 'none' else np.zeros_like(reference)
                self.assertEqual(self.mapping(board, alpha, reference)['unsafe_members'], ['left', 'right'])

    def test_non_native_mode_keeps_the_original_rejection(self):
        board, alpha, reference = self.fixture()
        alpha[50, 100] = 2
        self.assertEqual(self.mapping(board, alpha, reference, native=False)['unsafe_members'], ['left', 'right'])

    def test_real_alpha2_bridge_and_canvas_extension_stay_rejected(self):
        board, alpha, reference = self.fixture()
        alpha[50, 74:126] = 2
        self.assertEqual(self.mapping(board, alpha, reference)['unsafe_members'], ['left', 'right'])
        board, alpha, reference = self.fixture()
        alpha[50, 0:26] = 2
        self.assertIn('left', self.mapping(board, alpha, reference)['unsafe_members'])

    def test_complete_alpha2_member_and_reference_owned_dot_are_retained(self):
        board, alpha, reference = self.fixture()
        alpha[30:70, 125:175] = 2
        alpha[50, 85] = 2
        reference[50, 85] = 255
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertNotIn('weak_speck_gutter_recoveries', result)

    def test_weak_fragment_outside_a_disputed_search_is_not_a_recovery(self):
        board, alpha, reference = self.fixture()
        alpha[5, 5] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertNotIn('weak_speck_gutter_recoveries', result)

    def test_weak_fragment_does_not_authorize_another_opaque_boundary(self):
        board, alpha, reference = self.fixture()
        board['size'] = [300, 100]
        board['grid']['columns'] = 3
        board['members'].append({'id': 'last', 'slot': [210, 10, 80, 80], 'box': [210, 10, 50, 40]})
        alpha = np.pad(alpha, ((0, 0), (0, 100)))
        alpha[30:70, 225:275] = 255
        reference = alpha.copy()
        alpha[50, 100] = 2
        alpha[50, 200] = 255
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], ['last', 'right'])
        self.assertEqual(len(result['weak_speck_gutter_recoveries']), 1)
        self.assertEqual(result['weak_speck_gutter_recoveries'][0]['index'], 0)

    def test_budget_cannot_be_split_across_two_disputed_boundaries(self):
        board, alpha, reference = self.fixture()
        board['size'] = [300, 100]
        board['grid']['columns'] = 3
        board['members'].append({'id': 'last', 'slot': [210, 10, 80, 80], 'box': [210, 10, 50, 40]})
        alpha = np.pad(alpha, ((0, 0), (0, 100)))
        alpha[30:70, 225:275] = 255
        reference = alpha.copy()
        points = [(x, y) for y in range(10, 24, 2) for x in range(95, 107, 2)]
        for x, y in points[:33]:
            alpha[y, x] = 2
        for x, y in points[:32]:
            alpha[y, x + 100] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], ['last', 'left', 'right'])
        self.assertNotIn('weak_speck_gutter_recoveries', result)

    def test_complete_component_size_is_counted_when_search_sees_only_part(self):
        board, alpha, reference = self.fixture()
        alpha[50, 100] = 2
        # The x search starts at50. Seven pixels of this nine-pixel group
        # enter it, so a strip-only size check would undercount the group.
        alpha[10, 48:57] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], ['left', 'right'])
        self.assertNotIn('weak_speck_gutter_recoveries', result)

    def test_same_tolerance_applies_to_a_local_row_gutter(self):
        board, alpha, reference = self.fixture()
        board['size'] = [100, 200]
        board['grid'].update(rows=2, columns=1)
        for member in board['members']:
            x, y, w, h = member['slot']
            member['slot'] = [y, x, h, w]
        alpha = alpha.T.copy()
        reference = reference.T.copy()
        alpha[100, 50] = 2
        result = self.mapping(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertEqual(result['weak_speck_gutter_recoveries'][0]['axis'], 'y')
        self.assertEqual(result['weak_speck_gutter_recoveries'][0]['column'], 0)

    def test_extractor_preserves_original_rgba_and_unknown_speck_pixels(self):
        board, alpha, reference = self.fixture()
        alpha[50, 100] = 2
        pixels = np.full((100, 200, 4), [11, 22, 33, 0], dtype=np.uint8)
        pixels[..., 3] = alpha
        reference_pixels = pixels.copy()
        reference_pixels[..., 3] = reference
        actual = self.root / 'actual.png'
        reference_path = self.root / 'reference.png'
        Image.fromarray(pixels).save(actual)
        Image.fromarray(reference_pixels).save(reference_path)
        board['image'] = str(reference_path)
        original_bytes = actual.read_bytes()
        result = extract_assets(board, actual, self.root / 'assets')
        self.assertEqual(len(result['assets']), 2)
        seen = 0
        for asset in result['assets']:
            x, y, w, h = asset['actual_slot']
            left, top, right, bottom = asset['crop_box']
            crop = np.asarray(Image.open(asset['path']).convert('RGBA'))
            np.testing.assert_array_equal(crop, pixels[y + top:y + bottom, x + left:x + right])
            seen += int(np.count_nonzero(crop[..., 3]))
        self.assertEqual(seen, int(np.count_nonzero(alpha)))
        self.assertEqual(actual.read_bytes(), original_bytes)

    @unittest.skipUnless(os.environ.get('PRK_WEAK_GRID_JOB'), 'real job supplied separately')
    def test_actual_board_keeps_all_eleven_members_and_existing_pixels(self):
        job = Path(os.environ['PRK_WEAK_GRID_JOB'])
        state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        task = next(t for t in state['tasks'].values() if t['role'] == 'board')
        board = copy.deepcopy(task['board'])
        board['image'] = str(job / 'boards' / Path(board['image']).name)
        actual = job / task['result']['raw']
        pixels = np.asarray(Image.open(actual).convert('RGBA'))
        result = extract_assets(board, actual, self.root / 'real-assets')
        self.assertEqual(result['cell_mapping']['unsafe_members'], [])
        self.assertEqual(result['cell_mapping']['cells'], task['extraction']['cell_mapping']['cells'])
        self.assertEqual(len(result['assets']), 11)
        previous = {a['id']: a for a in state['assets'] if a['id'] != 'main_title'}
        for asset in result['assets']:
            x, y, w, h = asset['actual_slot']
            left, top, right, bottom = asset['crop_box']
            crop = np.asarray(Image.open(asset['path']).convert('RGBA'))
            np.testing.assert_array_equal(crop, pixels[y + top:y + bottom, x + left:x + right])
            if asset['id'] in previous:
                old = previous[asset['id']]
                old_file = job / 'assets' / task['id'] / Path(old['path']).name
                np.testing.assert_array_equal(crop, np.asarray(Image.open(old_file).convert('RGBA')))
                self.assertEqual(asset['target'], old['target'])


if __name__ == '__main__':
    unittest.main()
