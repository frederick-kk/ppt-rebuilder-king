"""Behavioral checks for the independent reference-board experiment."""

import importlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            self.api = importlib.import_module("newpath.artwork")
        except ModuleNotFoundError:
            self.api = None
        self.assertIsNotNone(self.api, "Reference-board implementation is missing")

    def save(self, name, pixels):
        path = self.root / name
        Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(path)
        return path

    def frames(self, source_color=(0, 0, 0, 255), foreground_color=(255, 0, 255, 255)):
        original = np.empty((1080, 1920, 4), np.uint8)
        original[:] = source_color
        foreground = np.empty_like(original)
        foreground[:] = foreground_color
        foreground[50:1000, 50:1850] = (0, 80, 220, 255)
        return self.save("source.png", original), self.save("foreground.png", foreground)

    def arts(self, count, edge=100, height=None):
        return [dict(id=f"art-{i}", kind="art", box=[200, 200, edge, height or edge],
                     target=[30 + i, 40, 300, 200], description="A visible drawing", order=i)
                for i in range(count)]

    def board(self, slot=None, target=None, size=(100, 100), **member_extra):
        member = dict(id="a", slot=slot or [0, 0, *size], box=[0, 0, 100, 100],
                      target=target or [2, 3, 300, 50], description="drawing", presentation="cutout")
        member.update(member_extra)
        return dict(id="board-test", key="magenta", size=list(size), members=[member])

    def matte(self, width=100, height=100):
        pixels = np.empty((height, width, 4), np.uint8)
        pixels[:] = (255, 0, 255, 255)
        return pixels

    def test_empty_art_list_creates_no_boards(self):
        source, foreground = self.frames()
        self.assertEqual(self.api.prepare_boards(source, foreground, [], self.root / "boards"), [])

    def test_supported_tail_capacities_for_zero_one_five_eleven_and_twenty_five(self):
        source, foreground = self.frames()
        for count, capacities in [(0, []), (1, [1]), (5, [6]), (11, [16]), (25, [24, 1])]:
            with self.subTest(count=count):
                boards = self.api.prepare_boards(source, foreground, self.arts(count), self.root / f"n{count}")
                self.assertEqual([b["capacity"] for b in boards], capacities)
                self.assertEqual(sum(len(b["members"]) for b in boards), count)

    def test_density_thresholds_constrain_full_batches(self):
        source, foreground = self.frames()
        for edge, capacity in [(120,24),(121,16),(180,16),(181,9),(240,9),(241,6),
                               (340,6),(341,4),(480,4),(481,3),(620,3),(621,2),(780,2),(781,1)]:
            with self.subTest(edge=edge):
                boards = self.api.prepare_boards(source, foreground, self.arts(25, edge), self.root / f"e{edge}")
                self.assertEqual(boards[0]["capacity"], capacity)
                self.assertTrue(all(b["capacity"] <= capacity for b in boards))

    def test_constrained_first_packing_restores_board_and_member_reading_order(self):
        source, foreground = self.frames()
        arts = self.arts(5)
        arts[3]["box"] = [200, 200, 700, 700]
        boards = self.api.prepare_boards(source, foreground, arts, self.root / "boards")
        self.assertEqual([m["id"] for m in boards[0]["members"]], ["art-0", "art-3"])
        self.assertEqual([m["id"] for m in boards[1]["members"]], ["art-1", "art-2", "art-4"])
        self.assertEqual([b["capacity"] for b in boards], [2, 3])

    def test_four_capacity_wide_layout_requires_eight_percent_gain(self):
        source, foreground = self.frames()
        for width, expected in [(431, [1024, 1024]), (432, [1024, 1024]),
                                (437, [1024, 1024]), (438, [1536, 1024]), (400, [1024, 1024])]:
            with self.subTest(width=width):
                boards = self.api.prepare_boards(source, foreground, self.arts(4, width, 400), self.root / str(width))
                self.assertEqual(boards[0]["size"], expected)

    def test_four_capacity_score_accounts_for_canvas_clamped_context(self):
        source, foreground = self.frames()
        for left, top, expected in [(0,200,[1024,1024]), (200,0,[1536,1024])]:
            with self.subTest(left=left,top=top):
                arts = self.arts(4,432,400)
                for art in arts:
                    art["box"][:2] = [left,top]
                board = self.api.prepare_boards(source,foreground,arts,self.root / f"clipped-{left}-{top}")[0]
                self.assertEqual(board["size"],expected)

    def test_four_capacity_rectangle_context_uses_its_six_percent_padding(self):
        source, foreground = self.frames()
        for width, expected in [(436,[1024,1024]),(437,[1536,1024])]:
            with self.subTest(width=width):
                arts = self.arts(4,width,400)
                for art in arts:
                    art["presentation"] = "rectangle"
                board = self.api.prepare_boards(source,foreground,arts,self.root / f"rectangle-{width}")[0]
                self.assertEqual(board["size"],expected)

    def test_reference_pixels_come_from_foreground_and_slots_have_clear_margins(self):
        source, foreground = self.frames(source_color=(220, 0, 0, 255), foreground_color=(0, 80, 220, 255))
        board = self.api.prepare_boards(source, foreground, self.arts(5), self.root / "boards")[0]
        pixels = np.asarray(Image.open(board["image"]).convert("RGBA"))
        self.assertTrue(np.any(np.all(pixels[..., :3] == [0, 80, 220], axis=2)))
        self.assertFalse(np.any(np.all(pixels[..., :3] == [220, 0, 0], axis=2)))
        self.assertEqual(board["size"], [1536, 1024])
        for member in board["members"]:
            x, y, w, h = member["slot"]
            self.assertGreaterEqual(x, 16)
            self.assertGreaterEqual(y, 16)
            self.assertFalse(np.any(np.all(pixels[y:y+h, x:x+w, :3] == [32, 32, 32], axis=2)))

    def test_target_changes_reuse_signature_id_png_bytes_and_modification_time(self):
        source, foreground = self.frames()
        arts = self.arts(1)
        first = self.api.prepare_boards(source, foreground, arts, self.root / "boards")[0]
        path = Path(first["image"])
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        arts[0]["target"] = [9, 8, 7, 6]
        second = self.api.prepare_boards(source, foreground, arts, self.root / "boards")[0]
        self.assertEqual(first["signature"], second["signature"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual(second["members"][0]["target"], [9, 8, 7, 6])
        self.assertRegex(second["id"], r"^[a-zA-Z0-9_-]+$")
        self.assertTrue(path.is_absolute())

    def test_originals_are_preserved_and_lettering_z_survive_metadata(self):
        source, foreground = self.frames()
        originals = (source.read_bytes(), foreground.read_bytes())
        arts = self.arts(1)
        arts[0].update(lettering="Exact words", z=7, presentation="rectangle")
        board = self.api.prepare_boards(source, foreground, arts, self.root / "boards")[0]
        self.assertEqual((source.read_bytes(), foreground.read_bytes()), originals)
        self.assertEqual(board["members"][0]["lettering"], "Exact words")
        self.assertEqual(board["members"][0]["z"], 7)

    def test_transparent_original_rgb_does_not_force_a_key_change(self):
        source, foreground = self.frames(source_color=(255, 0, 255, 0))
        board = self.api.prepare_boards(source, foreground, self.arts(1), self.root / "boards")[0]
        self.assertEqual(board["key"], "magenta")

    def test_alternate_key_replaces_edge_connected_magenta_but_preserves_sealed_magenta(self):
        source, foreground = self.frames(source_color=(255, 0, 255, 255))
        pixels = self.matte(1920, 1080)
        pixels[210:290, 210:290] = (0, 70, 210, 255)
        pixels[235:265, 235:265] = (255, 0, 255, 255)
        foreground = self.save("foreground.png", pixels)
        board = self.api.prepare_boards(source, foreground, self.arts(1), self.root / "boards")[0]
        result = np.asarray(Image.open(board["image"]).convert("RGBA"))
        self.assertEqual(board["key"], "green")
        self.assertTrue(np.any(np.all(result[..., :3] == [255, 0, 255], axis=2)))
        self.assertTrue(np.any(np.all(result[..., :3] == [0, 255, 0], axis=2)))

    def test_same_magenta_key_strips_sealed_magenta_globally(self):
        source, foreground = self.frames()
        pixels = self.matte(1920, 1080)
        pixels[210:290, 210:290] = (0, 70, 210, 255)
        pixels[235:265, 235:265] = (255, 0, 255, 128)
        foreground = self.save("foreground.png", pixels)
        board = self.api.prepare_boards(source, foreground, self.arts(1), self.root / "boards")[0]
        result = np.asarray(Image.open(board["image"]).convert("RGBA"))
        self.assertEqual(board["key"], "magenta")
        self.assertFalse(np.any(result[..., 3] != 255))

    def test_validation_rejects_missing_corrupt_non_png_transparent_and_all_key(self):
        corrupt = self.root / "corrupt.png"
        corrupt.write_bytes(b"not an image")
        jpeg = self.root / "not.png"
        Image.new("RGB", (10,10), "red").save(jpeg, format="JPEG")
        transparent = self.save("transparent.png", np.zeros((10,10,4), np.uint8))
        all_key = self.save("allkey.png", self.matte(10,10))
        for path in [self.root / "missing.png", corrupt, jpeg, transparent, all_key]:
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.api.validate_output(path, "magenta")

    def test_key_fraction_counts_transparent_rgb_over_whole_image(self):
        pixels = np.zeros((10,10,4), np.uint8)
        pixels[:5] = (255, 0, 255, 0)
        pixels[9,9] = (220, 0, 0, 255)
        result = self.api.validate_output(self.save("transparent-rgb.png", pixels), "magenta")
        self.assertEqual(result["key_fraction"], 0.5)

    def test_less_than_two_percent_key_removal_only_warns(self):
        pixels = np.empty((10,10,4), np.uint8)
        pixels[:] = (220, 0, 0, 255)
        pixels[0,0] = (255, 0, 255, 255)
        result = self.api.validate_output(self.save("low-key.png", pixels), "magenta")
        self.assertEqual(result["key_fraction"], 0.01)
        self.assertTrue(result["warnings"])

    def test_each_key_predicate_accepts_exact_thresholds_and_leaves_other_rgba(self):
        for key, color, near in [("magenta",(150,132,150,37),(150,133,150,37)),
                                 ("green",(132,150,132,37),(133,150,132,37)),
                                 ("cyan",(132,150,150,37),(133,150,150,37)),
                                 ("yellow",(150,150,132,37),(150,150,133,37))]:
            with self.subTest(key=key):
                pixels = np.zeros((20,20,4), np.uint8)
                pixels[:] = color
                pixels[5:15,5:15] = near
                board = self.board(size=(20,20))
                board["key"] = key
                result = self.api.extract_assets(board, self.save(f"{key}.png", pixels), self.root / key)
                asset = np.asarray(Image.open(result["assets"][0]["path"]))
                self.assertEqual(asset.shape[:2], (10,10))
                self.assertTrue(np.all(asset == near))

    def test_returned_image_dimensions_scale_x_y_separately_and_target_is_exact(self):
        pixels = self.matte(200,300)
        pixels[60:240,20:180] = (220,0,0,255)
        result = self.api.extract_assets(self.board(z=4), self.save("actual.png",pixels), self.root / "assets")
        asset = result["assets"][0]
        with Image.open(asset["path"]) as asset_image:
            self.assertEqual(asset_image.size, (160,180))
        self.assertEqual(asset["source_size"], [160,180])
        self.assertEqual(asset["actual_slot"], [0,0,200,300])
        self.assertEqual(asset["slot_size"], [200,300])
        self.assertEqual(asset["crop_box"], [20,60,180,240])
        self.assertEqual(asset["target"], [2,3,300,50])
        self.assertEqual(asset["stretch"], [300/160,50/180])
        self.assertEqual(asset["z"], 4)

    def test_component_selection_keeps_nearby_dot_and_rejects_edge_scrap(self):
        pixels = self.matte()
        pixels[40:60,40:60] = (220,0,0,255)
        pixels[35:37,49:51] = (0,80,220,255)
        pixels[0:2,0:2] = (0,255,0,255)
        result = self.api.extract_assets(self.board(), self.save("parts.png",pixels), self.root / "assets")
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertEqual(extracted.shape[:2], (25,20))
        self.assertTrue(np.any(np.all(extracted == [0,80,220,255],axis=2)))
        self.assertFalse(np.any(np.all(extracted == [0,255,0,255],axis=2)))

    def test_selected_high_alpha_pixels_clear_unselected_low_alpha_tails(self):
        pixels = self.matte()
        pixels[40:60,40:60] = (220,0,0,255)
        pixels[39,40:60] = (220,0,0,15)
        pixels[60,40:60] = (220,0,0,1)
        result = self.api.extract_assets(self.board(), self.save("tails.png",pixels), self.root / "assets")
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertEqual(extracted.shape[:2], (20,20))
        self.assertTrue(np.all(extracted[...,3] == 255))

    def test_all_low_alpha_slot_is_preserved(self):
        pixels = self.matte()
        pixels[40:60,40:60] = (220,0,0,8)
        result = self.api.extract_assets(self.board(), self.save("lowalpha.png",pixels), self.root / "assets")
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertEqual(extracted.shape[:2], (20,20))
        self.assertTrue(np.all(extracted[...,3] == 8))

    def test_eight_connectivity_retains_diagonal_points(self):
        pixels = self.matte()
        for point in range(40,60):
            pixels[point,point] = (220,0,0,255)
        result = self.api.extract_assets(self.board(), self.save("diagonal.png",pixels), self.root / "assets")
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertEqual(np.count_nonzero(extracted[...,3]),20)

    def test_whole_invalid_image_fails_while_empty_member_only_skips(self):
        pixels = self.matte(200,100)
        pixels[35:65,135:165] = (220,0,0,255)
        board = self.board(slot=[0,0,100,100],size=(200,100))
        member = dict(board["members"][0],id="b",slot=[100,0,100,100])
        board["members"].append(member)
        result = self.api.extract_assets(board,self.save("two.png",pixels),self.root / "assets")
        self.assertEqual([a["id"] for a in result["assets"]],["b"])
        self.assertTrue(any("a" in str(w) for w in result["warnings"]))
        with self.assertRaises(ValueError):
            self.api.extract_assets(board,self.save("allkey.png",self.matte()),self.root / "invalid")

    def test_touch_safe_cross_and_cross_slot_only_warn(self):
        pixels = self.matte()
        pixels[20:40,40:65] = (220,0,0,255)
        result = self.api.extract_assets(self.board(slot=[10,10,40,40]),self.save("cross.png",pixels),self.root / "assets")
        self.assertEqual(len(result["assets"]),1)
        warning_text = str(result["warnings"])
        self.assertIn("touch",warning_text)
        self.assertIn("safe",warning_text)
        self.assertIn("cross_slot",warning_text)

    def test_fully_opaque_visible_slot_is_retained_with_edge_warning(self):
        pixels = self.matte(200,100)
        pixels[:,:100] = (220,0,0,255)
        result = self.api.extract_assets(self.board(slot=[0,0,100,100],size=(200,100)),
                                         self.save("unreliable.png",pixels),self.root / "assets")
        self.assertEqual(len(result["assets"]),1)
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertTrue(np.all(extracted == [220,0,0,255]))
        self.assertIn("touch_slot_edge",str(result["warnings"]))

    def test_all_low_alpha_full_slot_is_preserved(self):
        pixels = np.empty((100,100,4),np.uint8)
        pixels[:] = (220,0,0,8)
        result = self.api.extract_assets(self.board(),self.save("full-low-alpha.png",pixels),self.root / "assets")
        self.assertEqual(len(result["assets"]),1)
        extracted = np.asarray(Image.open(result["assets"][0]["path"]))
        self.assertTrue(np.all(extracted == [220,0,0,8]))

    def test_collision_requires_both_eight_pixel_and_fraction_limits(self):
        _, foreground = self.frames()
        for key_pixels, visible_pixels, expected in [(8,14400,"magenta"),(9,14400,"green"),
                                                     (2,1000,"magenta"),(3,1000,"green")]:
            with self.subTest(key_pixels=key_pixels,visible_pixels=visible_pixels):
                pixels = np.zeros((1080,1920,4),np.uint8)
                crop = pixels[190:310,190:310].reshape(-1,4).copy()
                crop[:visible_pixels] = (0,0,0,255)
                crop[:key_pixels] = (255,0,255,255)
                pixels[190:310,190:310] = crop.reshape(120,120,4)
                original = self.save(f"audit-{key_pixels}-{visible_pixels}.png",pixels)
                board = self.api.prepare_boards(original,foreground,self.arts(1),self.root / f"audit{key_pixels}-{visible_pixels}")[0]
                self.assertEqual(board["key"],expected)

    def test_no_safe_key_minimizes_fraction_with_priority_tie_break(self):
        _, foreground = self.frames()
        colors = [(255,0,255,255),(0,255,0,255),(0,255,255,255),(255,255,0,255)]
        for widths, expected in [([30,30,30,30],"magenta"),([60,30,20,10],"yellow")]:
            pixels = np.zeros((1080,1920,4),np.uint8)
            left = 190
            for width, color in zip(widths,colors):
                pixels[190:310,left:left+width] = color
                left += width
            source = self.save(f"fallback-{expected}.png",pixels)
            board = self.api.prepare_boards(source,foreground,self.arts(1),self.root / expected)[0]
            self.assertEqual(board["key"],expected)


if __name__ == "__main__":
    unittest.main()
