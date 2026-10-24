"""Coordinate-guide regressions; local pixels only, no image service or Office.

The optional frozen-runtime comparison is skipped when the separate baseline is
not available. All guide and controller contracts still run in an extracted ZIP.
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "compat-runtime"))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from newpath import controller
from newpath.source import prepare_source
from newpath.storage import read_state, sha256

try:
    from newpath.ruler import render_coordinate_guide
except ModuleNotFoundError:
    render_coordinate_guide = None


def render_current(image):
    """Before implementation, exercise the existing prepare_source renderer."""
    if render_coordinate_guide is not None:
        return render_coordinate_guide(image)
    with tempfile.TemporaryDirectory(prefix="prk-old-guide-") as directory:
        root = Path(directory)
        source = root / "source.png"
        image.save(source)
        work = root / "job"
        work.mkdir()
        info = prepare_source(source, work)
        with Image.open(work / info["guide"]) as result:
            return result.copy()


class RulerPixels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.picture = Image.new("RGB", (1920, 1080), (173, 203, 227))
        cls.guide = render_current(cls.picture)
        cls.pixels = np.asarray(cls.guide)

    def test_horizontal_fifty_labels_retain_both_1900_anchors(self):
        labels = []
        original_text = ImageDraw.ImageDraw.text

        def observe(draw, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", new=observe):
            render_current(self.picture)
        for anchor in range(1100, 1901, 50):
            self.assertEqual(labels.count(str(anchor)), 2, anchor)

    def test_border_ticks_are_not_covered_by_zero_labels(self):
        tick_colour = self.pixels[0, 405]
        for coordinate, depth in ((20, 5), (25, 8), (1060, 5)):
            for edge in (self.pixels[coordinate, :depth],
                         self.pixels[coordinate, -depth:],
                         self.pixels[:depth, coordinate],
                         self.pixels[-depth:, coordinate]):
                self.assertTrue(np.all(edge == tick_colour), (coordinate, depth))

    def test_five_pixel_marks_have_four_distinct_depths_on_all_edges(self):
        # 405/410/425/450 distinguish subdivisions, tens, twenty-fives and fifties.
        # First 13 pixels cannot contain coordinate digits placed after ticks.
        expected = [3, 5, 8, 12]
        base = np.array((173, 203, 227))
        top, bottom, left, right = [], [], [], []
        for coordinate in (405, 410, 425, 450):
            top.append(int(np.count_nonzero(np.any(self.pixels[:13, coordinate] != base, axis=1))))
            bottom.append(int(np.count_nonzero(np.any(self.pixels[-13:, coordinate] != base, axis=1))))
            left.append(int(np.count_nonzero(np.any(self.pixels[coordinate, :13] != base, axis=1))))
            right.append(int(np.count_nonzero(np.any(self.pixels[coordinate, -13:] != base, axis=1))))
        # Major/minor full-page grid lines also occupy the 13th pixel. Compare
        # their tick colours, rather than interpreting the grid as longer ticks.
        # A 25px grid crosses the bottom13px at1075; count tick colour below,
        # rather than mistaking that full-page grid pixel for a longer tick.
        tick_colour = self.pixels[0, 405]
        for pixels in (self.pixels[:13], self.pixels[-13:][::-1],
                       self.pixels.transpose(1, 0, 2)[:13],
                       self.pixels.transpose(1, 0, 2)[-13:][::-1]):
            lengths = [int(np.count_nonzero(np.all(pixels[:, c] == tick_colour, axis=1)))
                       for c in (405, 410, 425, 450)]
            self.assertEqual(lengths, expected)

    def test_interior_grid_colour_origin_and_source_pixels_are_preserved(self):
        overlay = Image.new("RGBA", self.picture.size)
        draw = ImageDraw.Draw(overlay)
        for x in range(0, 1920, 25):
            draw.line((x, 0, x, 1079), fill=(35, 70, 110, 75 if x % 50 == 0 else 30))
        for y in range(0, 1080, 25):
            draw.line((0, y, 1919, y), fill=(35, 70, 110, 75 if y % 50 == 0 else 30))
        expected = Image.alpha_composite(self.picture.convert("RGBA"), overlay).convert("RGB")
        self.assertEqual(self.guide.crop((48, 48, 1872, 1032)).tobytes(),
                         expected.crop((48, 48, 1872, 1032)).tobytes())
        for point in ((405, 405), (410, 410), (426, 426), (499, 499), (1, 51)):
            self.assertEqual(self.guide.getpixel(point), self.picture.getpixel(point), point)

    def test_all_four_edges_have_digits_beyond_the_tick_depth(self):
        # Compare against untouched source in non-grid strips beside a 400 tick.
        # Dropping either bottom or right labels leaves these strips unchanged.
        rectangles = [(403, 14, 437, 35), (403, 1045, 437, 1066),
                      (14, 403, 48, 424), (1872, 403, 1906, 424)]
        for box in rectangles:
            with self.subTest(box=box):
                sample = np.asarray(self.guide.crop(box))
                yy, xx = np.indices(sample.shape[:2])
                outside_grid = ((xx + box[0]) % 25 != 0) & ((yy + box[1]) % 25 != 0)
                self.assertGreater(np.count_nonzero(np.any(sample != (173, 203, 227), axis=2) & outside_grid), 30)

    def test_drawn_glyphs_are_readable_outlined_and_wholly_inside_canvas(self):
        # Spy through the real drawing operation to inspect the actual ink
        # bounds. Nothing substitutes for drawing the real glyph pixels.
        calls = []
        original_text = ImageDraw.ImageDraw.text

        def observe(draw, xy, text, *args, **kwargs):
            bbox = draw.textbbox(xy, text, font=kwargs.get("font"),
                                 stroke_width=kwargs.get("stroke_width", 0))
            calls.append((str(text), bbox, kwargs))
            return original_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", new=observe):
            result = render_current(self.picture)
        self.assertGreaterEqual(len(calls), 116, "50px digits must exist on four edges")
        self.assertIn("50", [x[0] for x in calls])
        self.assertIn("1850", [x[0] for x in calls])
        for i, (_, a, _) in enumerate(calls):
            for _, b, _ in calls[i+1:]:
                self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], (a,b))
        for text, (left, top, right, bottom), style in calls:
            self.assertGreaterEqual(left, 0, text)
            self.assertGreaterEqual(top, 0, text)
            self.assertLessEqual(right, result.width, text)
            self.assertLessEqual(bottom, result.height, text)
            self.assertGreaterEqual(bottom - top, 11, "digits are too small to read")
            self.assertEqual(style.get("stroke_width"), 1)
            self.assertNotEqual(style.get("fill"), style.get("stroke_fill"))

    def test_input_is_unchanged_and_rendering_is_deterministic(self):
        self.assertIsNotNone(render_coordinate_guide, "a dedicated guide renderer is required")
        picture = Image.new("RGBA", (1920, 1080), (21, 91, 146, 180))
        before = picture.tobytes()
        first, second = render_current(picture), render_current(picture)
        self.assertEqual(picture.tobytes(), before)
        self.assertEqual(picture.mode, "RGBA")
        self.assertEqual(first.mode, "RGB")
        self.assertEqual(first.size, picture.size)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertIsNot(first, picture)

    def test_tiny_canvas_and_missing_font_do_not_clip_or_fail(self):
        self.assertIsNotNone(render_coordinate_guide, "a dedicated guide renderer is required")
        real_truetype = ImageFont.truetype

        def missing_disk_font(font, *args, **kwargs):
            if isinstance(font, (str, bytes, os.PathLike)):
                raise OSError("disk font unavailable")
            return real_truetype(font, *args, **kwargs)

        with mock.patch("PIL.ImageFont.truetype", side_effect=missing_disk_font):
            for size in ((1, 1), (7, 12), (80, 40), (1920, 1080)):
                with self.subTest(size=size):
                    self.assertEqual(render_coordinate_guide(Image.new("RGB", size)).size, size)

    def test_non_image_is_rejected_without_creating_output(self):
        self.assertIsNotNone(render_coordinate_guide, "a dedicated guide renderer is required")
        for value in (None, "image.png", (1920, 1080)):
            with self.subTest(value=value), self.assertRaises(TypeError):
                render_coordinate_guide(value)


class RulerSourceIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="prk-ruler-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def png(self, name, size=(160, 90), colour=(51, 112, 173, 200)):
        path = self.root / name
        image = Image.new("RGBA", size, colour)
        ImageDraw.Draw(image).rectangle((size[0] // 3, size[1] // 3, size[0] // 2, size[1] // 2), fill="orange")
        image.save(path)
        return path

    def test_source_copy_working_png_and_transform_match_frozen_runtime(self):
        runtime = Path(os.environ.get("PRK_FROZEN_RUNTIME", str(PACKAGE.parent /
                       "PPT Rebuilder King-20261022-tuned" / "compat-runtime" / "newpath")))
        if not (runtime / "source.py").is_file():
            self.skipTest("optional separate frozen runtime not available")
        package_name = "_ruler_frozen_baseline"
        spec = importlib.util.spec_from_file_location(package_name, runtime / "__init__.py",
                                                     submodule_search_locations=[str(runtime)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
        frozen = importlib.import_module(package_name + ".source")
        for size in ((160, 90), (120, 90), (90, 160), (100, 100)):
            with self.subTest(size=size):
                source = self.png("%sx%s.png" % size, size)
                before = source.read_bytes()
                old_work, new_work = self.root / (source.stem + "-old"), self.root / (source.stem + "-new")
                old_work.mkdir(); new_work.mkdir()
                old, new = frozen.prepare_source(source, old_work), prepare_source(source, new_work)
                self.assertEqual(set(old), set(new))
                self.assertEqual({k: v for k, v in old.items() if k != "guide_sha256"},
                                 {k: v for k, v in new.items() if k != "guide_sha256"})
                self.assertEqual(source.read_bytes(), before)
                for key in ("original", "working"):
                    self.assertEqual((old_work / old[key]).read_bytes(), (new_work / new[key]).read_bytes())
                with Image.open(new_work / new["working"]) as working:
                    direct = render_current(working)
                with Image.open(new_work / new["guide"]) as actual:
                    self.assertEqual(actual.tobytes(), direct.tobytes())
                self.assertNotEqual(new["guide_sha256"], old["guide_sha256"])
                self.assertEqual(new["guide_sha256"], sha256(new_work / new["guide"]))

    def test_describe_uses_same_source_guide_but_generation_keeps_clean_reference(self):
        source = self.png("source.png")
        work = self.root / "job"
        # The runtime probe checks unrelated external Node availability; all
        # preparation, registration, source checks and queue logic remain real.
        with mock.patch.object(controller, "_runtime"):
            first = controller.run(work, source=source)
        self.assertEqual(first["kind"], "generate")
        self.assertEqual([task["role"] for task in first["jobs"]], ["background", "foreground"])
        for task in first["jobs"]:
            self.assertEqual(task["references"], [str(work / "input" / "working.png")])
            output = self.png(task["role"] + ".png", colour="white" if task["role"] == "background" else (0, 0, 0, 0))
            last = controller.run(work, data={**task["result_template"], "file": str(output)})
        self.assertEqual(last["kind"], "describe")
        self.assertEqual(last["files"], [str(work / "input" / "working.png"), str(work / "input" / "ruler.png")])
        tasks_before = read_state(work)["tasks"]
        self.assertEqual(controller.run(work), last)
        self.assertEqual(read_state(work)["tasks"], tasks_before)
        layout = {"items": [
            {"kind": "art", "id": "title", "box": [120, 40, 1100, 120], "description": "brush title", "lettering": "TITLE"},
            {"kind": "art", "id": "icon", "box": [400, 400, 80, 80], "description": "orange icon"}]}
        result = controller.run(work, data={"kind": "layout", "layout": layout})
        self.assertEqual(result["kind"], "generate")
        self.assertEqual(sorted(task["role"] for task in result["jobs"]), ["board", "wordart"])
        for task in result["jobs"]:
            self.assertIn(str(work / "input" / "working.png"), task["references"])
            self.assertNotIn(str(work / "input" / "ruler.png"), task["references"])
        state = read_state(work)
        self.assertEqual(state["layout_source_sha256"], state["source"]["working_sha256"])
        self.assertEqual(len(state["tasks"]), 4)


if __name__ == "__main__":
    unittest.main()
