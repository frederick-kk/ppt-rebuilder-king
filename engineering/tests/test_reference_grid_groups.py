"""Reference-supported grouped artwork: real extraction, fixed local PNGs."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

PACKAGE = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE / "compat-runtime"))
from newpath.artwork import extract_assets
from newpath.cellmap import resolve_cells

MATTE = (255, 0, 255, 255)
BLUE = (18, 76, 122, 255)


class ReferenceGridGroups(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, horizontal=False):
        board = {"id": "group-grid", "size": [120, 220], "key": "magenta",
                 "grid": {"rows": 2, "columns": 1, "margin": 10, "gap": 20,
                          "cell_size": [100, 90]},
                 "members": [
                     {"id": "group-0", "slot": [10, 10, 100, 90],
                      "box": [100, 100, 30, 80], "target": [100, 100, 30, 80]},
                     {"id": "group-1", "slot": [10, 120, 100, 90],
                      "box": [100, 250, 70, 60], "target": [100, 250, 70, 60]}]}
        returned = np.full((220, 120, 4), MATTE, dtype=np.uint8)
        for start, stop in ((15, 30), (50, 65), (80, 95)):
            returned[start:stop, 45:75] = BLUE
        returned[145:205, 25:95] = BLUE
        reference = np.full((220, 120, 4), MATTE, dtype=np.uint8)
        reference[15:95, 45:75] = BLUE
        reference[145:205, 25:95] = BLUE
        if horizontal:
            board["size"] = [220, 120]
            board["grid"] = {"rows": 1, "columns": 2, "margin": 10, "gap": 20,
                             "cell_size": [90, 100]}
            for member in board["members"]:
                for key in ("slot", "box", "target"):
                    x, y, width, height = member[key]
                    member[key] = [y, x, height, width]
            returned = returned.transpose(1, 0, 2).copy()
            reference = reference.transpose(1, 0, 2).copy()
        return board, returned, reference

    def extract(self, board, returned, reference, label):
        directory = self.root / label
        directory.mkdir()
        board = dict(board)
        if reference is not None:
            path = directory / "reference.png"
            Image.fromarray(reference).save(path)
            board["image"] = str(path)
        raw = directory / "returned.png"
        Image.fromarray(returned).save(raw)
        return extract_assets(board, raw, directory / "assets")

    def assert_groups_preserved(self, result, horizontal=False):
        self.assertEqual([asset["id"] for asset in result["assets"]], ["group-0", "group-1"])
        for index, expected_count in ((0, 1350), (1, 4200)):
            with Image.open(result["assets"][index]["path"]) as image:
                pixels = np.asarray(image.convert("RGBA"))
            visible = pixels[..., 3] > 0
            self.assertEqual(int(visible.sum()), expected_count)
            self.assertTrue(np.all(pixels[visible] == BLUE))
            if index == 0:
                expected = np.zeros((80, 30), dtype=bool)
                expected[0:15] = True
                expected[35:50] = True
                expected[65:80] = True
                self.assertTrue(np.array_equal(visible, expected.T if horizontal else expected))
        self.assertEqual(result["cell_mapping"]["unsafe_members"], [])

    def assert_strict_rejection(self, result):
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["cell_mapping"]["unsafe_members"], ["group-0", "group-1"])
        self.assertIn("ambiguous_member_omitted", {note["code"] for note in result["warnings"]})

    def test_vertical_internal_group_gaps_do_not_lose_reference_supported_parts(self):
        board, returned, reference = self.fixture()
        self.assert_groups_preserved(self.extract(board, returned, reference, "vertical"))

    def test_horizontal_internal_group_gaps_do_not_lose_reference_supported_parts(self):
        board, returned, reference = self.fixture(horizontal=True)
        self.assert_groups_preserved(self.extract(board, returned, reference, "horizontal"), horizontal=True)

    def test_absent_empty_or_spatially_mismatched_reference_keeps_strict_guard(self):
        for mode in ("absent", "empty", "mismatch"):
            with self.subTest(reference=mode):
                board, returned, reference = self.fixture()
                if mode == "absent":
                    reference = None
                else:
                    reference[:] = MATTE
                    if mode == "mismatch":
                        reference[15:95, 105:115] = BLUE
                        reference[145:205, 105:115] = BLUE
                self.assert_strict_rejection(self.extract(board, returned, reference, mode))

    def test_unknown_returned_fragment_between_groups_keeps_strict_guard(self):
        board, returned, reference = self.fixture()
        returned[125:127, 50:60] = BLUE
        self.assert_strict_rejection(self.extract(board, returned, reference, "unknown-fragment"))

    def test_occupied_planned_gutter_is_not_resolved_by_nearest_blank_band(self):
        for mode in ("fragment", "bridge"):
            with self.subTest(gutter=mode):
                board, returned, reference = self.fixture()
                if mode == "fragment":
                    returned[105:107, 50:60] = BLUE
                else:
                    returned[90:150, 55:60] = BLUE
                self.assert_strict_rejection(self.extract(board, returned, reference, "gutter-" + mode))

    def test_reference_fragment_in_planned_gutter_keeps_strict_guard(self):
        board, returned, reference = self.fixture()
        reference[105:107, 50:60] = BLUE
        self.assert_strict_rejection(self.extract(board, returned, reference, "reference-gutter"))

    def test_standalone_cellmap_without_reference_remains_strict(self):
        board, returned, _ = self.fixture()
        alpha = np.where(np.any(returned != MATTE, axis=2), 255, 0).astype(np.uint8)
        result = resolve_cells(board, alpha)
        self.assertEqual(result["unsafe_members"], ["group-0", "group-1"])
        self.assertIn("ambiguous_grid_fragments", {note["code"] for note in result["warnings"]})


if __name__ == "__main__":
    unittest.main()
