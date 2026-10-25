"""Keep complete native members when only one empty band respects their owners."""
from pathlib import Path
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


class OwnedGridGaps(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-owned-gaps-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self):
        # Row centers are y=49 and y=151. The complete first small written
        # group ends at y=35, outside that search, and must still keep its ID.
        board = {'id': 'owned-small-text', 'size': [200, 200], 'key': None,
                 'background_mode': MODE,
                 'grid': {'rows': 2, 'columns': 1, 'margin': 10,
                          'gap': 24, 'cell_size': [180, 78]},
                 'members': [
                     {'id': 'upper', 'slot': [10, 10, 180, 78],
                      'box': [100, 100, 80, 50], 'lettering': 'SMALL ONE'},
                     {'id': 'lower', 'slot': [10, 112, 180, 78],
                      'box': [100, 300, 80, 50], 'lettering': 'SMALL TWO'}]}
        reference = np.zeros((200, 200, 4), dtype=np.uint8)
        pixels = np.full((200, 200, 4), [11, 22, 33, 1], dtype=np.uint8)
        pixels[0, 0, 3] = 0
        for left, right in ((50, 65), (80, 95)):
            pixels[20:35, left:right] = [40, 80, 140, 230]
            pixels[120:135, left:right] = [140, 80, 40, 230]
            reference[20:40, left:right] = [40, 80, 140, 230]
            reference[125:145, left:right] = [140, 80, 40, 230]
        return board, pixels, reference

    def mapping(self, board, pixels, reference, *, native=True):
        before = pixels.copy()
        result = resolve_cells(board, pixels[..., 3],
                               reference_alpha=None if reference is None else reference[..., 3],
                               native_geometry=native)
        np.testing.assert_array_equal(pixels, before)
        return result

    def test_complete_owned_small_group_outside_center_search_is_not_omitted(self):
        board, pixels, reference = self.fixture()
        reference_path = self.root / 'reference.png'
        Image.fromarray(reference).save(reference_path)
        board['image'] = str(reference_path)
        actual = self.root / 'actual.png'
        Image.fromarray(pixels).save(actual)
        source_bytes = actual.read_bytes()
        result = extract_assets(board, actual, self.root / 'assets')
        self.assertEqual(result['cell_mapping']['unsafe_members'], [])
        self.assertEqual([x['id'] for x in result['assets']], ['upper', 'lower'])
        self.assertEqual(result['cell_mapping']['cells'],
                         {'upper': [0, 0, 200, 100], 'lower': [0, 100, 200, 200]})
        self.assertEqual(actual.read_bytes(), source_bytes)
        visible = 0
        for asset, member in zip(result['assets'], board['members']):
            x, y, width, height = asset['actual_slot']
            left, top, right, bottom = asset['crop_box']
            expected = pixels[y:y + height, x:x + width][top:bottom, left:right]
            with Image.open(asset['path']) as image:
                crop = np.asarray(image.convert('RGBA'))
            np.testing.assert_array_equal(crop, expected)
            self.assertEqual(asset['authored_target'], member['box'])
            visible += int(np.count_nonzero(crop[..., 3]))
        self.assertEqual(visible, int(np.count_nonzero(pixels[..., 3])))

    def test_missing_empty_and_mismatched_references_do_not_guess_an_owner(self):
        for kind in ('none', 'empty', 'mismatch'):
            with self.subTest(reference=kind):
                board, pixels, reference = self.fixture()
                if kind == 'none':
                    reference = None
                elif kind == 'empty':
                    reference[:] = 0
                else:
                    reference[:] = 0
                    reference[20:40, 140:160] = [40, 80, 140, 230]
                    reference[125:145, 140:160] = [140, 80, 40, 230]
                self.assertEqual(self.mapping(board, pixels, reference)['unsafe_members'],
                                 ['lower', 'upper'])

    def test_non_native_geometry_keeps_existing_strict_group_rules(self):
        board, pixels, reference = self.fixture()
        self.assertEqual(self.mapping(board, pixels, reference, native=False)['unsafe_members'],
                         ['lower', 'upper'])

    def test_unknown_fragment_cannot_be_given_to_a_known_neighbor(self):
        for size in (1, 12):
            with self.subTest(size=size):
                board, pixels, reference = self.fixture()
                pixels[70:70 + size, 150:150 + size] = [250, 30, 90, 255]
                self.assertEqual(self.mapping(board, pixels, reference)['unsafe_members'],
                                 ['lower', 'upper'])

    def test_detached_upper_stroke_and_lower_group_with_no_owned_cut_stay_unsafe(self):
        board, pixels, reference = self.fixture()
        pixels[..., 3] = 1
        pixels[0, 0, 3] = 0
        reference[:] = 0
        # The upper identity owns two detached strokes. The lower component
        # begins before its second stroke, leaving two real blank bands but
        # no band with both complete identities on their assigned sides.
        pixels[20:35, 40:55] = [40, 80, 140, 230]
        pixels[75:80, 40:55] = [40, 80, 140, 230]
        pixels[70:140, 110:125] = [140, 80, 40, 230]
        reference[20:40, 40:55] = [40, 80, 140, 230]
        reference[75:85, 40:55] = [40, 80, 140, 230]
        reference[125:145, 110:125] = [140, 80, 40, 230]
        self.assertEqual(self.mapping(board, pixels, reference)['unsafe_members'],
                         ['lower', 'upper'])

    def test_one_component_owned_by_both_members_is_not_split(self):
        board, pixels, reference = self.fixture()
        pixels[30:130, 55:60] = [40, 80, 140, 230]
        self.assertEqual(self.mapping(board, pixels, reference)['unsafe_members'],
                         ['lower', 'upper'])

    def test_one_pixel_separator_is_not_promoted_to_a_valid_wide_gap(self):
        board, pixels, reference = self.fixture()
        pixels[20:100, 50:95] = [40, 80, 140, 230]
        pixels[101:180, 50:95] = [140, 80, 40, 230]
        self.assertEqual(self.mapping(board, pixels, reference)['unsafe_members'],
                         ['lower', 'upper'])

    def test_visible_subject_at_the_physical_canvas_edge_stays_unsafe(self):
        board, pixels, reference = self.fixture()
        pixels[0:30, 55:60] = [40, 80, 140, 230]
        result = self.mapping(board, pixels, reference)
        self.assertIn('upper', result['unsafe_members'])
        self.assertIn('subject_clipped_at_canvas_edge',
                      [note['code'] for note in result['warnings']])


if __name__ == '__main__':
    unittest.main()
