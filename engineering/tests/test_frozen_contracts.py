"""Synthetic contracts for the immutable runtime; no image service is called.

These tests freeze current behavior, including conservative omissions and weak
technical acceptance. They do not establish real-image quality or Office display.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "compat-runtime"))

import numpy as np
from PIL import Image, ImageDraw
from newpath.artwork import extract_assets, prepare_boards, validate_output
from newpath.boardplan import plan_boards
from newpath.cellmap import resolve_cells
from newpath.equations import NS, formula_xml
from newpath.layout import normalize_layout
from newpath.matte import choose_key, erase_matte, estimate_background
from newpath.renderer import build_presentation
from newpath.source import decoded_picture, normalize_layer, pixel_identity, prepare_source
from newpath.storage import fingerprint, sha256


def demand_cases():
    return [
        {"id": "short", "box": [50, 50, 60, 30], "description": "short lettering", "lettering": "A"},
        {"id": "long", "box": [100, 200, 1500, 80], "description": "long title", "lettering": "LONG TITLE"},
        {"id": "edge", "box": [-10, 100, 60, 100], "target": [20, 300, 180, 300], "description": "edge cropped"},
        {"id": "large", "box": [800, 350, 100, 100], "target": [500, 500, 1800, 1800], "description": "limited"},
        {"id": "small", "box": [500, 500, 90, 110], "description": "small icon"},
    ]


def two_cells():
    return {"id": "synthetic", "size": [220, 120], "key": "magenta",
            "grid": {"rows": 1, "columns": 2, "margin": 10, "gap": 20, "cell_size": [90, 100]},
            "members": [{"id": "left", "slot": [10, 10, 90, 100], "box": [0, 0, 50, 50]},
                        {"id": "right", "slot": [120, 10, 90, 100], "box": [0, 0, 50, 50]}]}


class LocalFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="prk-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def png(self, name, size=(100, 100), color="white", mode="RGBA"):
        path = self.root / name
        Image.new(mode, size, color).save(path)
        return path


class SourceContracts(LocalFiles):
    def test_four_ratios_working_ruler_transform_and_bytes_repeat(self):
        for size, resized, offset in [((160, 90), [1920, 1080], [0, 0]),
                                       ((120, 90), [1440, 1080], [240, 0]),
                                       ((90, 160), [608, 1080], [656, 0]),
                                       ((100, 100), [1080, 1080], [420, 0])]:
            with self.subTest(size=size):
                source = self.png("source-%sx%s.png" % size, size, (20, 70, 120, 255))
                outputs = []
                for index in range(2):
                    work = self.root / (source.stem + str(index)); work.mkdir()
                    info = prepare_source(source, work)
                    self.assertEqual(info["transform"]["resized"], resized)
                    self.assertEqual(info["transform"]["offset"], offset)
                    self.assertEqual(info["transform"]["original_size"], list(size))
                    self.assertEqual(info["byte_sha256"], sha256(source))
                    with Image.open(work / info["working"]) as working:
                        self.assertEqual(working.size, (1920, 1080))
                    self.assertNotEqual(info["working_sha256"], info["guide_sha256"])
                    outputs.append((info["transform"], (work / info["working"]).read_bytes(),
                                    (work / info["guide"]).read_bytes()))
                self.assertEqual(outputs[0], outputs[1])

    def test_exif_orientation_precedes_identity_and_transform(self):
        source = self.root / "rotated.jpg"
        image = Image.new("RGB", (40, 20), (30, 60, 100))
        exif = image.getexif(); exif[274] = 6
        image.save(source, exif=exif)
        decoded = decoded_picture(source)
        self.assertEqual(decoded.size, (20, 40))
        work = self.root / "job"; work.mkdir()
        self.assertEqual(prepare_source(source, work)["transform"]["original_size"], [20, 40])

    def test_transparency_is_white_backed_and_pixel_identity_is_decoded(self):
        transparent = self.png("transparent.png", (2, 1), (200, 0, 0, 0))
        opaque = self.png("opaque.png", (2, 1), (255, 255, 255, 255))
        self.assertEqual(decoded_picture(transparent).getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(pixel_identity(transparent), pixel_identity(opaque))
        self.assertNotEqual(sha256(transparent), sha256(opaque))

    def test_normalized_layers_restore_padding_matte(self):
        source = self.png("layer.png", (50, 50), "blue")
        transform = {"offset": [420, 0], "resized": [1080, 1080]}
        for role, expected in [("background", (255, 255, 255, 255)),
                                ("foreground", (255, 0, 255, 255))]:
            output = self.root / (role + ".png")
            normalize_layer(source, role, transform, output)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1920, 1080))
                self.assertEqual(image.getpixel((0, 500)), expected)
                self.assertEqual(image.getpixel((900, 500)), (0, 0, 255, 255))


class LayoutContracts(unittest.TestCase):
    def test_unknown_item_and_run_fields_rejected(self):
        cases = [{"kind": "text", "box": [0, 0, 10, 10], "text": "x", "invented": True},
                 {"kind": "text", "box": [0, 0, 10, 10], "runs": [{"text": "x", "font": "Arial"}]}]
        for item in cases:
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "unsupported"):
                normalize_layout({"items": [item]})

    def test_top_level_unknown_is_currently_ignored(self):
        # Deliberately preserve this weaker top-level boundary; do not imply all
        # unknown fields are rejected merely because item fields are strict.
        self.assertEqual(normalize_layout({"items": [], "invented": True}),
                         {"format": "newpath.scene/1", "items": []})

    def test_reserved_explicit_ids_and_derived_order_are_stable(self):
        payload = {"items": [{"kind": "text", "box": [0, 0, 100, 40], "text": "a", "order": 99},
                             {"id": "o001", "kind": "text", "box": [0, 50, 100, 40], "text": "b", "order": -8}]}
        original = copy.deepcopy(payload)
        scene = normalize_layout(payload)
        self.assertEqual(payload, original)
        self.assertEqual([(i["id"], i["order"]) for i in scene["items"]], [("o002", 0), ("o001", 1)])
        self.assertEqual(scene, normalize_layout(scene))

    def test_duplicate_and_unsafe_id_rejected(self):
        for ids in [("same", "same"), ("../escape", "ok")]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                normalize_layout({"items": [{"id": ident, "kind": "text", "box": [0, 0, 100, 40], "text": "x"}
                                            for ident in ids]})

    def test_styles_runs_table_and_supported_geometry_canonical(self):
        scene = normalize_layout({"styles": {"caption": {"size": 24, "color": "#aabbcc"}}, "items": [
            {"kind": "text", "style": "caption", "box": [0, 0, 100, 40], "text": "AB",
             "runs": [{"text": "A", "bold": True}, {"text": "B", "color": "ff0000"}]},
            {"kind": "table", "box": [0, 50, 200, 100], "rows": [["a\nb", "c"], ["d", "e"]]},
            {"kind": "line", "points": [100, 100, 0, 0], "end_arrow": "triangle"}]})
        self.assertEqual(scene["items"][0]["color"], "AABBCC")
        self.assertEqual(scene["items"][0]["runs"], [{"text": "A", "bold": True}, {"text": "B", "color": "FF0000"}])
        self.assertEqual(scene["items"][1]["column_widths"], [100, 100])
        self.assertEqual(scene, normalize_layout(scene))


class PlanningContracts(LocalFiles):
    def test_full_plan_structure_frozen_and_reordering_ids_is_equivalent(self):
        arts = demand_cases(); original = copy.deepcopy(arts)
        plan = plan_boards(arts)
        self.assertEqual(arts, original)
        self.assertEqual(plan, plan_boards(list(reversed(arts))))
        # This digest covers every field, floating value, crop, slot, inset,
        # ordering and budget, rather than only a board/member count.
        self.assertEqual(fingerprint(plan), "c8223f3a1a378c3b8e15dd1a96d2064ddf60254d7e0a9135d4f1e676aa393c98")
        self.assertEqual([[p["art"]["id"] for p in b["placements"]] for b in plan],
                         [["edge", "short", "small"], ["large"], ["long"]])
        limited = plan[1]["placements"][0]["pixel_budget"]
        self.assertTrue(limited["hardware_limited"])
        self.assertFalse(limited["meets_requirement"])
        self.assertEqual(limited["required_content_px"], [2160, 2160])

    def test_empty_and_invalid_plans(self):
        self.assertEqual(plan_boards([]), [])
        for arts in [[{"id": "x", "box": [3000, 0, 10, 10]}],
                     [{"id": "x", "box": [0, 0, 10, 10]}] * 2,
                     [{"id": "x", "box": [0, 0, float("inf"), 10]}]]:
            with self.subTest(arts=arts), self.assertRaises(ValueError): plan_boards(arts)

    def test_board_signature_bytes_and_reference_order_inputs(self):
        source = self.png("source.png", (1920, 1080), "white")
        front = self.png("foreground.png", (1920, 1080), "magenta")
        with Image.open(front) as opened: image = opened.copy()
        ImageDraw.Draw(image).rectangle((90, 90, 210, 210), fill="blue"); image.save(front)
        arts = [{"id": "icon", "box": [100, 100, 100, 100], "target": [500, 300, 200, 200], "description": "blue icon"}]
        first = prepare_boards(source, front, arts, self.root / "boards")
        repeated = prepare_boards(source, front, arts, self.root / "boards")
        self.assertEqual(first, repeated)
        board = first[0]
        self.assertEqual(board["signature"], "94c07a5fa913865679a21553a89bdbc3464893e631663e8915544a8636e1195d")
        self.assertEqual(board["id"], "board-" + board["signature"][:20])
        self.assertEqual(board["members"][0]["target"], [500, 300, 200, 200])
        self.assertEqual(board["planning_policy"], "equal-cells-pixel-budget/1")
        before = sha256(board["image"])
        with Image.open(front) as opened: changed = opened.copy()
        changed.putpixel((150, 150), (255, 255, 0, 255)); changed.save(front)
        other = prepare_boards(source, front, arts, self.root / "changed")[0]
        self.assertNotEqual(board["signature"], other["signature"])
        self.assertNotEqual(before, sha256(other["image"]))


class CellAndMatteContracts(LocalFiles):
    def test_legacy_scaled_slots_have_no_new_unsafe_judgment(self):
        board = two_cells(); board.pop("grid")
        result = resolve_cells(board, np.ones((240, 440), dtype=np.uint8))
        self.assertEqual(result, {"status": "legacy", "cells": {"left": [20, 20, 200, 220], "right": [240, 20, 420, 220]},
                                  "warnings": [], "unsafe_members": []})

    def test_aligned_complete_cells_use_canvas_outer_edges(self):
        alpha = np.zeros((120, 220), np.uint8)
        alpha[25:95, 25:90] = 255; alpha[25:95, 130:195] = 128
        result = resolve_cells(two_cells(), alpha)
        self.assertEqual(result, {"status": "aligned", "cells": {"left": [0, 0, 110, 120], "right": [110, 0, 220, 120]},
                                  "warnings": [], "unsafe_members": []})

    def test_missing_gutter_marks_both_neighbors_unsafe(self):
        alpha = np.zeros((120, 220), np.uint8); alpha[25:95, 25:195] = 255
        result = resolve_cells(two_cells(), alpha)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["unsafe_members"], ["left", "right"])
        self.assertEqual(result["warnings"], [{"id": i, "code": "unseparable_grid_boundary", "axis": "x"}
                                              for i in ["left", "right"]])

    def test_detached_fragment_does_not_guess_its_owner(self):
        alpha = np.zeros((120, 220), np.uint8)
        alpha[25:95, 25:80] = 255; alpha[50:52, 105:107] = 1; alpha[25:95, 135:195] = 255
        result = resolve_cells(two_cells(), alpha)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["unsafe_members"], ["left", "right"])
        self.assertTrue(all(w["code"] == "ambiguous_grid_fragments" for w in result["warnings"]))

    def test_unsafe_members_are_omitted_by_actual_extractor(self):
        path = self.png("fused.png", (220, 120), "magenta")
        with Image.open(path) as opened: image = opened.copy()
        ImageDraw.Draw(image).rectangle((25, 25, 194, 94), fill="blue"); image.save(path)
        result = extract_assets(two_cells(), path, self.root / "assets")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["cell_mapping"]["unsafe_members"], ["left", "right"])
        self.assertEqual([w["id"] for w in result["warnings"] if w["code"] == "ambiguous_member_omitted"], ["left", "right"])

    def test_matte_removes_observed_opaque_key_preserves_all_existing_alpha(self):
        pixels = np.full((20, 20, 4), (255, 0, 255, 255), np.uint8)
        pixels[5:15, 5:15] = (0, 0, 255, 255)
        pixels[7, 7] = (255, 0, 255, 128); pixels[8, 8] = (20, 30, 40, 0)
        original = pixels.copy(); clean = erase_matte(pixels, "magenta")
        np.testing.assert_array_equal(pixels, original)
        np.testing.assert_array_equal(clean[7, 7], original[7, 7])
        np.testing.assert_array_equal(clean[8, 8], original[8, 8])
        np.testing.assert_array_equal(clean[0, 0], [0, 0, 0, 0])
        np.testing.assert_array_equal(clean[6, 6], [0, 0, 255, 255])
        np.testing.assert_array_equal(clean, erase_matte(pixels, "magenta", experimental_unmix=False))

    def test_transparent_perimeter_preserves_opaque_key_colored_subject(self):
        pixels = np.zeros((20, 20, 4), np.uint8); pixels[5:15, 5:15] = (255, 0, 255, 255)
        self.assertIsNone(estimate_background(pixels, "magenta"))
        np.testing.assert_array_equal(erase_matte(pixels, "magenta"), pixels)
        np.testing.assert_array_equal(erase_matte(pixels, "magenta", background=None), pixels)

    def test_key_choice_is_stable_and_avoids_visible_magenta(self):
        pixels = np.full((10, 10, 4), (255, 0, 255, 255), np.uint8)
        self.assertEqual(choose_key([pixels]), "green")
        self.assertEqual(choose_key([np.full((10, 10, 4), (0, 0, 255, 255), np.uint8)]), "magenta")

    def test_technical_acceptance_accepts_white_but_rejects_empty_key_and_bad_png(self):
        white = self.png("white.png")
        self.assertEqual(validate_output(white, "magenta")["warnings"], ["key_coverage_below_2_percent"])
        self.assertEqual(validate_output(white)["visible_pixels"], 10000)
        cases = [(self.png("alpha.png", color=(0, 0, 0, 0)), None),
                 (self.png("key.png", color="magenta"), "magenta")]
        bad = self.root / "bad.png"; bad.write_bytes(b"not an image"); cases.append((bad, None))
        for path, key in cases:
            with self.subTest(path=path), self.assertRaises(ValueError): validate_output(path, key)

    def test_partial_alpha_png_is_accepted_without_erasing_key_colored_subject(self):
        path = self.png("partial-alpha.png", (20, 20), (0, 0, 0, 0))
        with Image.open(path) as opened: image = opened.copy()
        ImageDraw.Draw(image).rectangle((5, 5, 14, 14), fill=(255, 0, 255, 128)); image.save(path)
        info = validate_output(path, "magenta")
        self.assertEqual(info["visible_pixels"], 100)
        self.assertEqual(info["key_pixels"], 0)
        self.assertEqual(info["warnings"], ["key_coverage_below_2_percent"])


class EquationAndRenderContracts(LocalFiles):
    def test_supported_formula_structure_is_deterministic(self):
        expression = r"\sum_{i=1}^{n}\frac{x_i^2}{\sqrt{y}}+\alpha"
        xml = formula_xml(expression)
        self.assertEqual(xml, formula_xml(expression))
        root = ET.fromstring(xml)
        for tag in ["nary", "f", "sSubSup", "rad"]:
            self.assertIsNotNone(root.find(".//m:" + tag, NS))
        self.assertIn("α", "".join(root.itertext()))

    def test_unsupported_and_ambiguous_formula_fail_explicitly(self):
        for expression in [r"\tan{x}", r"x_i_j", r"\begin{matrix}a\end{matrix}", "中文", "x\ny", r"x^{2", ""]:
            with self.subTest(expression=expression), self.assertRaises(ValueError): formula_xml(expression)

    def test_real_render_background_objects_formula_fallback_and_no_overwrite(self):
        background = self.png("background.png", (1920, 1080), "white")
        art = self.png("art.png", (20, 20), "blue")
        scene = {"items": [
            {"id": "caption", "kind": "text", "box": [20, 20, 400, 50], "text": "Native text"},
            {"id": "panel", "kind": "shape", "box": [10, 10, 450, 200], "fill": "eeeeee"},
            {"id": "arrow", "kind": "line", "points": [20, 100, 200, 100], "end_arrow": "triangle"},
            {"id": "grid", "kind": "table", "box": [600, 30, 200, 100], "rows": [["a", "b"]]},
            {"id": "icon", "kind": "art", "box": [900, 20, 60, 80], "description": "blue icon"},
            {"id": "missing", "kind": "art", "box": [1000, 20, 60, 80], "description": "absent"},
            {"id": "math", "kind": "formula", "box": [20, 300, 300, 70], "text": r"\frac{x}{y}"},
            {"id": "unsupported", "kind": "formula", "box": [20, 400, 300, 70], "text": r"\tan{x}"}]}
        output = self.root / "real.pptx"
        report = build_presentation(scene, background, [{"id": "icon", "path": str(art), "target": [901, 21, 61, 81]}], output)
        self.assertTrue(report["validation"]["background_picture_fill"])
        self.assertFalse(report["validation"]["office_render_checked"])
        self.assertEqual(report["equations"]["embedded"], ["math"])
        self.assertEqual(report["equations"]["fallback"], ["unsupported"])
        self.assertEqual(report["skipped"], [{"id": "missing", "reason": "missing_asset"}])
        self.assertEqual(report["native_counts"], {"text_frames": 5, "shapes": 1, "lines": 6, "table_groups": 1, "table_cells": 2, "foreground_images": 1})
        with ZipFile(output) as archive:
            root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            self.assertIsNotNone(root.find(".//mc:AlternateContent/mc:Choice/p:sp//m:oMath", NS))
            self.assertIn(r"\tan{x}", "".join(root.itertext()))
            self.assertIsNotNone(root.find(".//p:grpSp/p:nvGrpSpPr/p:cNvPr[@name='grid']", NS))
            pictures = root.findall(".//p:pic", NS)
            icon = next(p for p in pictures if p.find("p:nvPicPr/p:cNvPr", NS).get("name") == "icon")
            transform = icon.find("p:spPr/a:xfrm", NS)
            self.assertEqual(transform.find("a:off", NS).attrib, {"x": str(901 * 6350), "y": str(21 * 6350)})
            self.assertEqual(transform.find("a:ext", NS).attrib, {"cx": str(61 * 6350), "cy": str(81 * 6350)})
        before = output.read_bytes()
        with self.assertRaises(FileExistsError): build_presentation(scene, background, [], output)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
