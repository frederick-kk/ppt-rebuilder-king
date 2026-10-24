"""Asset acceptance at real canvas edges, using actual extraction and PNGs.

Synthetic returned/reference boards only; no image service or controller.
"""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

PACKAGE = Path(__file__).resolve().parents[2]
RUNTIME = PACKAGE / "compat-runtime"
sys.dont_write_bytecode = True
sys.path.insert(0, str(RUNTIME))
from newpath.artwork import extract_assets
from newpath.cellmap import resolve_cells


MAGENTA = (255, 0, 255, 255)
CORNER_RESIDUE = (239, 34, 237, 255)
SUBJECT = (18, 76, 122, 255)


class EdgeSubjectAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def board(self, columns=1):
        width = 8 + columns * 80 + (columns - 1) * 8 + 8
        return {"id": "synthetic-edge-board", "size": [width, 96], "key": "magenta",
                "grid": {"rows": 1, "columns": columns, "margin": 8, "gap": 8,
                         "cell_size": [80, 80]},
                "members": [{"id": "item-" + str(index), "slot": [8 + index * 88, 8, 80, 80],
                             "box": [100 + index * 100, 200, 20, 20],
                             "target": [100 + index * 100, 200, 20, 20],
                             "inset_xy": [4, 4], "inset_px": 4}
                            for index in range(columns)]}

    def canvas(self, board):
        width, height = board["size"]
        return np.full((height, width, 4), MAGENTA, dtype=np.uint8)

    def save(self, name, pixels):
        path = self.root / name
        Image.fromarray(pixels).save(path, format="PNG")
        return path

    def extract(self, board, returned, reference, suffix=""):
        board = dict(board)
        if reference is not None:
            board["image"] = str(self.save("reference" + suffix + ".png", reference))
        raw = self.save("returned" + suffix + ".png", returned)
        return extract_assets(board, raw, self.root / ("assets" + suffix))

    def warning_codes(self, result):
        return {entry["code"] for entry in result["warnings"]}

    def assert_omitted(self, result, code="subject_clipped_at_canvas_edge"):
        self.assertEqual(result["assets"], [])
        self.assertIn(code, self.warning_codes(result))
        self.assertIn("ambiguous_member_omitted", self.warning_codes(result))

    def test_reference_supported_interior_subject_survives_single_corner_residue(self):
        board = self.board()
        reference = self.canvas(board)
        reference[38:58, 38:58] = SUBJECT
        returned = reference.copy()
        returned[-1, 0] = CORNER_RESIDUE
        result = self.extract(board, returned, reference)
        self.assertEqual([asset["id"] for asset in result["assets"]], ["item-0"])
        with Image.open(result["assets"][0]["path"]) as image:
            pixels = np.asarray(image.convert("RGBA"))
        self.assertEqual(pixels.shape, (20, 20, 4))
        self.assertTrue(np.all(pixels == SUBJECT))
        self.assertNotIn("subject_clipped_at_canvas_edge", self.warning_codes(result))

    def test_two_grid_members_survive_two_unrelated_canvas_corner_residues(self):
        board = self.board(columns=2)
        reference = self.canvas(board)
        reference[38:58, 38:58] = SUBJECT
        reference[38:58, 126:146] = SUBJECT
        returned = reference.copy()
        returned[-1, 0] = CORNER_RESIDUE
        returned[-1, -1] = CORNER_RESIDUE
        result = self.extract(board, returned, reference)
        self.assertEqual([asset["id"] for asset in result["assets"]], ["item-0", "item-1"])
        for asset in result["assets"]:
            with Image.open(asset["path"]) as image:
                pixels = np.asarray(image.convert("RGBA"))
            self.assertEqual(pixels.shape, (20, 20, 4))
            self.assertTrue(np.all(pixels == SUBJECT))

    def test_real_subject_connected_to_canvas_edge_is_still_omitted(self):
        board = self.board()
        reference = self.canvas(board)
        reference[38:58, 0:20] = SUBJECT
        result = self.extract(board, reference.copy(), reference)
        self.assert_omitted(result)

    def test_reference_supported_disconnected_edge_detail_stays_conservative(self):
        for alpha in (255, 7):
            with self.subTest(alpha=alpha):
                board = self.board()
                reference = self.canvas(board)
                reference[38:58, 38:58] = SUBJECT
                reference[75, 0] = (*SUBJECT[:3], alpha)
                result = self.extract(board, reference.copy(), reference, "-edge-" + str(alpha))
                self.assert_omitted(result)

    def test_partial_reference_match_cannot_hide_a_shifted_clipped_detail(self):
        fragments = {
            "non_key_subject": (SUBJECT, False),
            "dark_purple": ((70, 5, 70, 255), False),
            "low_alpha_key_hue": ((239, 34, 237, 7), False),
            "near_matte_opaque_block": (CORNER_RESIDUE, False),
            "near_matte_noncorner_singleton": (CORNER_RESIDUE, True),
        }
        for name, (fragment, singleton) in fragments.items():
            with self.subTest(fragment=name):
                board = self.board()
                reference = self.canvas(board)
                reference[38:58, 38:58] = SUBJECT
                reference[15:25, 15:25] = SUBJECT
                returned = self.canvas(board)
                returned[38:58, 38:58] = SUBJECT
                # The center still matches, but a genuine detached detail has
                # moved to and is truncated at the physical left canvas edge.
                if singleton:
                    returned[75, 0] = fragment
                else:
                    returned[72:84, :8] = fragment
                result = self.extract(board, returned, reference, "-partial-" + name)
                self.assert_omitted(result)

    def test_earlier_safe_asset_records_final_ambiguous_mapping_status(self):
        board = self.board(columns=2)
        reference = self.canvas(board)
        reference[38:58, 38:58] = SUBJECT
        reference[38:58, 126:] = SUBJECT
        result = self.extract(board, reference.copy(), reference)
        self.assertEqual([asset["id"] for asset in result["assets"]], ["item-0"])
        self.assertEqual(result["cell_mapping"]["status"], "ambiguous")
        self.assertEqual(result["cell_mapping"]["unsafe_members"], ["item-1"])
        self.assertEqual(result["assets"][0]["cell_mapping_status"],
                         result["cell_mapping"]["status"])

    def test_reference_supported_interior_one_pixel_and_low_alpha_details_survive(self):
        board = self.board()
        reference = self.canvas(board)
        reference[38:58, 38:58] = SUBJECT
        reference[15, 15] = (*SUBJECT[:3], 7)
        reference[25, 20] = SUBJECT
        result = self.extract(board, reference.copy(), reference)
        self.assertEqual(len(result["assets"]), 1)
        asset = result["assets"][0]
        with Image.open(asset["path"]) as image:
            pixels = np.asarray(image.convert("RGBA"))
        left, top, _, _ = asset["crop_box"]
        self.assertEqual(pixels[15 - top, 15 - left].tolist(), [18, 76, 122, 7])
        self.assertEqual(pixels[25 - top, 20 - left].tolist(), list(SUBJECT))
        self.assertEqual(int(np.count_nonzero(pixels[..., 3])), 402)

    def test_raw_canvas_edge_guard_is_retained_without_reliable_reference(self):
        for mode in ("absent", "empty", "mismatch"):
            with self.subTest(reference=mode):
                board = self.board()
                returned = self.canvas(board)
                returned[60:70, 60:70] = SUBJECT
                returned[-1, -1] = CORNER_RESIDUE
                reference = None if mode == "absent" else self.canvas(board)
                if mode == "mismatch":
                    reference[10:20, 10:20] = SUBJECT
                result = self.extract(board, returned, reference, "-" + mode)
                self.assert_omitted(result)

    def test_true_cross_grid_subject_is_not_accepted(self):
        board = self.board(columns=2)
        returned = self.canvas(board)
        returned[38:58, 30:54] = SUBJECT
        returned[38:58, 120:144] = SUBJECT
        returned[47:49, 54:120] = SUBJECT
        result = self.extract(board, returned, returned.copy())
        self.assert_omitted(result, "unseparable_grid_boundary")

    def test_disconnected_fragment_with_ambiguous_grid_identity_is_not_accepted(self):
        board = self.board(columns=2)
        returned = self.canvas(board)
        returned[38:58, 30:50] = SUBJECT
        returned[38:58, 134:154] = SUBJECT
        returned[44:48, 90:94] = SUBJECT
        result = self.extract(board, returned, returned.copy())
        self.assert_omitted(result, "ambiguous_grid_fragments")

    def test_standalone_cellmap_default_keeps_raw_canvas_edge_guard(self):
        board = self.board()
        alpha = np.zeros((96, 96), dtype=np.uint8)
        alpha[38:58, 38:58] = 255
        alpha[-1, 0] = 255
        mapping = resolve_cells(board, alpha)
        self.assertEqual(mapping["unsafe_members"], ["item-0"])
        self.assertIn("subject_clipped_at_canvas_edge", {entry["code"] for entry in mapping["warnings"]})


if __name__ == "__main__":
    unittest.main()
