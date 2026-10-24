"""Incomplete generated reference must not silently erase interior lettering.

Synthetic PNGs exercise the real extraction API. No real evidence files, image
service, renderer or controller are modified or invoked.
"""
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


MAGENTA = (255, 0, 255, 255)
WHITE = (245, 245, 245, 255)
CORNER_RESIDUE = (239, 34, 237, 255)


class LetteringCompleteness(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def board(self, lettering=True):
        member = {'id': 'quote', 'slot': [8, 8, 80, 80], 'box': [200, 300, 80, 40],
                  'target': [200, 300, 80, 40], 'inset_xy': [4, 4], 'presentation': 'cutout'}
        if lettering:
            member['lettering'] = '少年有梦 未来可期'
        return {'id': 'synthetic-lettering', 'size': [96, 96], 'key': 'magenta',
                'grid': {'rows': 1, 'columns': 1, 'margin': 8, 'gap': 8, 'cell_size': [80, 80]},
                'members': [member]}

    @staticmethod
    def canvas():
        return np.full((96, 96, 4), MAGENTA, dtype=np.uint8)

    def partial_reference_and_complete_return(self):
        reference = self.canvas()
        reference[38:50, 38:50] = WHITE
        returned = reference.copy()
        # Every disconnected component below is deliberately within the only
        # reliable cell; the generated reference omitted them, as in round A.
        returned[20:32, 64:76] = WHITE
        returned[68:70, 15:80] = WHITE  # Independent fine underline.
        returned[18, 18] = (245, 245, 245, 7)  # No alpha cutoff is legitimate.
        returned[60, 20] = WHITE  # No minimum component area is legitimate.
        return reference, returned

    def extract(self, board, returned, reference, suffix=''):
        if reference is not None:
            reference_path = self.root / ('reference' + suffix + '.png')
            Image.fromarray(reference).save(reference_path)
            board = {**board, 'image': str(reference_path)}
        returned_path = self.root / ('returned' + suffix + '.png')
        Image.fromarray(returned).save(returned_path)
        return extract_assets(board, returned_path, self.root / ('assets' + suffix))

    @staticmethod
    def codes(result):
        return {note['code'] for note in result['warnings']}

    def assert_complete_quote(self, result):
        self.assertEqual([asset['id'] for asset in result['assets']], ['quote'])
        asset = result['assets'][0]
        self.assertEqual(asset['crop_box'], [15, 18, 80, 70])
        with Image.open(asset['path']) as image:
            actual = np.asarray(image.convert('RGBA'))
        # Literal coordinates and pixel values are independent of runtime
        # selection. Each missing glyph, underline and tiny detail is checked.
        expected = np.zeros((52, 65, 4), dtype=np.uint8)
        expected[20:32, 23:35] = WHITE
        expected[2:14, 49:61] = WHITE
        expected[50:52, 0:65] = WHITE
        expected[0, 3] = (245, 245, 245, 7)
        expected[42, 5] = WHITE
        np.testing.assert_array_equal(actual, expected)
        self.assertNotIn('subject_clipped_at_canvas_edge', self.codes(result))

    def test_partial_reference_keeps_disconnected_interior_lettering_and_underline(self):
        reference, returned = self.partial_reference_and_complete_return()
        self.assert_complete_quote(self.extract(self.board(), returned, reference))

    def test_interior_lettering_deletions_do_not_turn_corner_residue_into_clipping(self):
        reference, returned = self.partial_reference_and_complete_return()
        returned[-1, 0] = CORNER_RESIDUE
        returned[-1, -1] = CORNER_RESIDUE
        self.assert_complete_quote(self.extract(self.board(), returned, reference))

    def test_true_white_lettering_at_physical_canvas_edge_is_still_rejected(self):
        reference, returned = self.partial_reference_and_complete_return()
        returned[54:62, :12] = WHITE
        result = self.extract(self.board(), returned, reference)
        self.assertEqual(result['assets'], [])
        self.assertIn('subject_clipped_at_canvas_edge', self.codes(result))
        self.assertIn('ambiguous_member_omitted', self.codes(result))

    def test_absent_empty_and_mismatch_reference_do_not_authorize_corner_exemption(self):
        for mode in ('absent', 'empty', 'mismatch'):
            with self.subTest(reference=mode):
                returned = self.canvas()
                returned[60:70, 60:70] = WHITE
                returned[-1, -1] = CORNER_RESIDUE
                reference = None if mode == 'absent' else self.canvas()
                if mode == 'mismatch':
                    reference[10:20, 10:20] = WHITE
                result = self.extract(self.board(), returned, reference, '-' + mode)
                self.assertEqual(result['assets'], [])
                self.assertIn('subject_clipped_at_canvas_edge', self.codes(result))

    def test_nonlettering_keeps_existing_reference_component_selection(self):
        reference = self.canvas()
        reference[38:50, 38:50] = WHITE
        returned = reference.copy()
        returned[20:32, 64:76] = WHITE
        result = self.extract(self.board(lettering=False), returned, reference)
        self.assertEqual(len(result['assets']), 1)
        self.assertEqual(result['assets'][0]['crop_box'], [38, 38, 50, 50])
        self.assertIn('outside_reference_removed', self.codes(result))
        with Image.open(result['assets'][0]['path']) as image:
            np.testing.assert_array_equal(np.asarray(image.convert('RGBA')),
                                          np.full((12, 12, 4), WHITE, dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
