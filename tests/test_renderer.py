"""Inspect generated OOXML, without launching a slide application."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image

try:
    from newpath.renderer import build_presentation
except ImportError:
    build_presentation = None

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def named(root, name):
    for elem in root.iter():
        nv = elem.find("p:nvSpPr/p:cNvPr", NS)
        if nv is None:
            nv = elem.find("p:nvPicPr/p:cNvPr", NS)
        if nv is None:
            nv = elem.find("p:nvGrpSpPr/p:cNvPr", NS)
        if nv is not None and nv.get("name") == name:
            return elem
    raise AssertionError(f"missing OOXML object {name}")


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.background = self.base / "background.png"
        Image.new("RGB", (1920, 1080), "white").save(self.background)
        self.foreground = self.base / "foreground.png"
        Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(self.foreground)
        self.output = self.base / "result.pptx"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, items, assets=None, output=None):
        self.assertIsNotNone(build_presentation, "presentation renderer has not been implemented")
        return build_presentation({"items": items}, self.background, assets or [], output or self.output)

    def xml(self, path=None):
        with ZipFile(path or self.output) as archive:
            self.assertIsNone(archive.testzip())
            return ET.fromstring(archive.read("ppt/slides/slide1.xml"))

    def test_background_is_picture_fill_and_each_foreground_stretches_to_exact_target(self):
        report = self.build([{"id": "art1", "kind": "art", "description": "icon", "box": [0, 0, 80, 80],
                              "target": [144, 288, 432, 144]}],
                            [{"id": "art1", "path": str(self.foreground), "target": [144, 288, 432, 144]}])
        root = self.xml()
        self.assertIsNotNone(root.find("p:cSld/p:bg/p:bgPr/a:blipFill/a:blip", NS))
        self.assertEqual(len(root.findall(".//p:pic", NS)), 1)
        pic = named(root, "art1")
        xfrm = pic.find("p:spPr/a:xfrm", NS)
        self.assertEqual(xfrm.find("a:off", NS).attrib, {"x": "914400", "y": "1828800"})
        self.assertEqual(xfrm.find("a:ext", NS).attrib, {"cx": "2743200", "cy": "914400"})
        self.assertIsNotNone(pic.find("p:blipFill/a:stretch/a:fillRect", NS))
        self.assertEqual(report["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(Path(report["pptx"]), self.output)
        self.assertEqual(report["native_counts"]["foreground_images"], 1)

    def test_rich_text_is_single_frame_single_paragraph_with_east_asian_font(self):
        self.build([{"id": "title", "kind": "text", "box": [144, 144, 1000, 80], "size": 40,
                     "tracking": 2, "runs": [{"text": "Alpha ", "bold": True},
                                               {"text": "中文", "color": "cc0000", "size": 48}]}])
        shape = named(self.xml(), "title")
        self.assertEqual([t.text for t in shape.findall(".//a:t", NS)], ["Alpha ", "中文"])
        self.assertEqual(len(shape.findall("p:txBody/a:p", NS)), 1)
        self.assertEqual(len(shape.findall(".//a:br", NS)), 0)
        self.assertEqual(shape.find("p:txBody/a:bodyPr", NS).get("wrap"), "none")
        runs = shape.findall("p:txBody/a:p/a:r/a:rPr", NS)
        self.assertEqual([r.get("sz") for r in runs], ["2000", "2400"])
        self.assertEqual(runs[0].get("b"), "1")
        self.assertEqual(runs[1].find("a:solidFill/a:srgbClr", NS).get("val"), "CC0000")
        self.assertEqual([r.find("a:ea", NS).get("typeface") for r in runs], ["Microsoft YaHei"] * 2)

    def test_vertical_text_uses_eavert_without_rotating_glyphs(self):
        self.build([{"id": "v", "kind": "text", "text": "天地人", "vertical": True,
                     "size": 48, "box": [100, 100, 100, 400]}])
        shape = named(self.xml(), "v")
        self.assertEqual(shape.find("p:txBody/a:bodyPr", NS).get("vert"), "eaVert")
        self.assertIn(shape.find("p:spPr/a:xfrm", NS).get("rot"), (None, "0"))

    def test_native_line_keeps_endpoint_direction_and_arrow_types(self):
        self.build([{"id": "arrow", "kind": "line", "points": [600, 100, 100, 400],
                     "start_arrow": "oval", "end_arrow": "stealth", "stroke_width": 4, "dash": "dot"}])
        shape = named(self.xml(), "arrow")
        xfrm = shape.find("p:spPr/a:xfrm", NS)
        self.assertEqual(xfrm.get("flipH"), "1")
        self.assertNotEqual(xfrm.get("flipV"), "1")
        self.assertEqual(xfrm.find("a:off", NS).attrib, {"x": "635000", "y": "635000"})
        self.assertEqual(xfrm.find("a:ext", NS).attrib, {"cx": "3175000", "cy": "1905000"})
        line = shape.find("p:spPr/a:ln", NS)
        self.assertEqual(line.find("a:headEnd", NS).get("type"), "oval")
        self.assertEqual(line.find("a:tailEnd", NS).get("type"), "stealth")
        self.assertEqual(line.find("a:prstDash", NS).get("val"), "sysDot")

    def test_table_is_one_group_of_editable_fills_lines_and_single_line_texts(self):
        report = self.build([{"id": "tab", "kind": "table", "box": [100, 100, 400, 200],
                              "rows": [["A\nB", "C"], ["D", "E"]], "fill": "eeeeee"}])
        root = self.xml()
        self.assertIsNone(root.find(".//a:tbl", NS))
        group = named(root, "tab")
        self.assertEqual(group.tag, "{" + NS["p"] + "}grpSp")
        self.assertEqual([t.text for t in group.findall(".//a:t", NS)], ["A", "B", "C", "D", "E"])
        self.assertEqual(len(group.findall("p:sp/p:txBody/a:p", NS)), 5)
        self.assertEqual(len(group.findall("p:sp/p:spPr/a:prstGeom[@prst='rect']", NS)), 9)
        self.assertEqual(report["native_counts"]["table_groups"], 1)
        self.assertEqual(report["native_counts"]["table_cells"], 4)
        self.assertEqual(report["native_counts"]["text_frames"], 5)

    def test_missing_art_is_explicitly_skipped_without_native_substitute(self):
        report = self.build([{"id": "paint", "kind": "art", "description": "painted title",
                              "lettering": "艺术字", "box": [100, 100, 300, 80]}])
        root = self.xml()
        self.assertEqual(root.findall(".//p:pic", NS), [])
        self.assertEqual(root.findall(".//a:t", NS), [])
        self.assertEqual(report["skipped"], [{"id": "paint", "reason": "missing_asset"}])
        self.assertTrue(any("paint" in warning for warning in report["warnings"]))

    def test_default_layers_keep_containers_below_art_and_text_with_explicit_z_winning(self):
        items = [{"id": "txt", "kind": "text", "text": "Top", "box": [100, 100, 300, 80]},
                 {"id": "art", "kind": "art", "description": "x", "box": [100, 100, 200, 200]},
                 {"id": "container", "kind": "shape", "fill": "eeeeee", "box": [0, 0, 800, 800]},
                 {"id": "back", "kind": "text", "z": -100, "text": "Behind", "box": [10, 10, 100, 40]}]
        self.build(items, [{"id": "art", "path": str(self.foreground), "target": [100, 100, 200, 200]}])
        tree = self.xml().find("p:cSld/p:spTree", NS)
        names = [e.get("name") for child in tree for e in child.findall(".//p:cNvPr", NS) if e.get("name")]
        self.assertEqual(names, ["back", "container", "art", "txt"])

    def test_fitting_never_shrinks_for_height_and_limits_width_shrink(self):
        report = self.build([{"id": "tinyheight", "kind": "text", "text": "Short", "size": 60, "box": [0, 0, 1000, 1]},
                             {"id": "overflow", "kind": "text", "text": "M" * 100, "size": 60, "box": [0, 100, 100, 200]}])
        objects = {item["id"]: item for item in report["objects"]}
        self.assertEqual(objects["tinyheight"]["effective_size_px"], 60)
        self.assertEqual(objects["overflow"]["effective_size_px"], 54)
        self.assertIn(objects["overflow"]["measurement"]["status"], {"measured", "estimated"})
        self.assertTrue(any("overflow" in w for w in report["warnings"]))

    def test_missing_node_is_readable_and_leaves_no_final(self):
        self.assertIsNotNone(build_presentation, "presentation renderer has not been implemented")
        with patch("newpath.renderer.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Node"):
                self.build([])
        self.assertFalse(self.output.exists())

    def test_existing_final_is_not_overwritten(self):
        self.output.write_bytes(b"user file")
        self.assertIsNotNone(build_presentation, "presentation renderer has not been implemented")
        with self.assertRaises(FileExistsError):
            self.build([])
        self.assertEqual(self.output.read_bytes(), b"user file")

    def test_small_width_overhang_is_tolerated_and_unavailable_fonts_are_reported(self):
        self.assertIsNotNone(build_presentation, "presentation renderer has not been implemented")
        with patch("newpath.renderer._font_directories", return_value=[]):
            report = self.build([{"id": "near", "kind": "text", "text": "ABC", "size": 50,
                                  "box": [0, 0, 80, 1]}])
        record = report["objects"][0]
        self.assertEqual(record["effective_size_px"], 50)
        self.assertEqual(record["measurement"]["status"], "estimated")
        self.assertEqual(record["measurement"]["width_px"], 84)
        self.assertEqual(named(self.xml(), "near").find(".//a:ea", NS).get("typeface"), "Microsoft YaHei")
        self.assertTrue(any("estimated" in w for w in report["warnings"]))

    def test_table_cell_lines_apply_the_same_bounded_width_fit(self):
        report = self.build([{"id": "tab", "kind": "table", "box": [100, 100, 100, 100],
                              "rows": [["M" * 100]], "size": 60}])
        text = named(self.xml(), "tab::text.0.0.0")
        self.assertEqual(text.find(".//a:rPr", NS).get("sz"), "2700")
        self.assertEqual(report["objects"][0]["cell_lines"][0]["effective_size_px"], 54)

    def test_corrupt_assembly_output_is_rejected_before_atomic_publication(self):
        self.assertIsNotNone(build_presentation, "presentation renderer has not been implemented")
        original_run = subprocess.run

        def corrupt_output(args, **kwargs):
            completed = original_run(args, **kwargs)
            request = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
            Path(request["output"]).write_bytes(b"broken ZIP")
            return completed

        with patch("newpath.renderer.subprocess.run", side_effect=corrupt_output):
            with self.assertRaisesRegex(RuntimeError, "PPTX validation"):
                self.build([])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.base.glob(".newpath-build-*")), [])

    def test_all_supported_shapes_are_native_and_keep_authored_geometry(self):
        shapes = ["rect", "roundRect", "ellipse", "triangle", "chevron", "leftArrow", "rightArrow",
                  "upArrow", "downArrow", "leftRightArrow", "upDownArrow"]
        self.build([{"id": f"s{i}", "kind": "shape", "shape": shape,
                     "box": [144, 288, 432, 144], "fill": "00aa00", "opacity": .5,
                     "stroke": "112233", "stroke_width": 4, "rotation": 30}
                    for i, shape in enumerate(shapes)])
        root = self.xml()
        for i, shape_type in enumerate(shapes):
            shape = named(root, f"s{i}")
            self.assertEqual(shape.find("p:spPr/a:prstGeom", NS).get("prst"), shape_type)
            self.assertEqual(shape.find("p:spPr/a:xfrm", NS).get("rot"), "1800000")
            self.assertEqual(shape.find("p:spPr/a:xfrm/a:ext", NS).attrib,
                             {"cx": "2743200", "cy": "914400"})
            self.assertEqual(shape.find("p:spPr/a:solidFill/a:srgbClr/a:alpha", NS).get("val"), "50000")

    def test_formula_uses_named_equation_and_cambria_math_collection_face(self):
        report = self.build([{"id": "eq1", "kind": "formula", "text": "x^2", "size": 48,
                              "box": [100, 100, 400, 100]}])
        self.assertEqual(report["equations"]["embedded"], ["eq1"])
        self.assertEqual(report["objects"][0]["editable_type"], "office_equation_with_text_fallback")
        root = self.xml()
        math_ns = {**NS, "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
                   "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        self.assertIsNotNone(root.find(".//mc:AlternateContent/mc:Choice/p:sp/p:txBody/a:p/{http://schemas.microsoft.com/office/drawing/2010/main}m/m:oMath", math_ns))
        self.assertEqual([t.text for t in root.findall(".//mc:Fallback//a:t", math_ns)], ["x^2"])
        self.assertEqual(report["objects"][0]["measurement"]["status"], "native_math_unmeasured")
        measurement = report["objects"][0]["measurement"]["literal_fallback"]
        if measurement["status"] == "measured":
            self.assertEqual(measurement["face"], "Cambria Math")

    def test_equivalent_native_math_spellings_keep_authored_font_size(self):
        report = self.build([{"id": "unicode", "kind": "formula", "text": "ℝ", "size": 48,
                              "box": [100, 100, 50, 100]},
                             {"id": "command", "kind": "formula", "text": r"\mathbb{R}", "size": 48,
                              "box": [200, 100, 50, 100]}])
        root = self.xml()
        math_ns = {**NS, "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        unicode_shape, command_shape = named(root, "unicode"), named(root, "command")
        self.assertEqual(ET.tostring(unicode_shape.find(".//m:oMath", math_ns)),
                         ET.tostring(command_shape.find(".//m:oMath", math_ns)))
        self.assertEqual([shape.find("p:txBody/a:p/a:pPr/a:defRPr", NS).get("sz")
                          for shape in (unicode_shape, command_shape)], ["2400", "2400"])
        self.assertEqual([obj["effective_size_px"] for obj in report["objects"]], [48, 48])
        self.assertFalse(any("overflow" in w for w in report["warnings"]))
        self.assertEqual(report["objects"][1]["measurement"]["status"], "native_math_unmeasured")
        self.assertIn("literal_fallback", report["objects"][1]["measurement"])

    def test_invalid_node_report_is_rejected_before_publishing_final(self):
        original_run = subprocess.run
        required = {"text_frames": 0, "shapes": 0, "lines": 0, "table_groups": 0,
                    "table_cells": 0, "foreground_images": 0}
        bad_reports = [{}, [], {"native_counts": None}, {"native_counts": {}},
                       {"native_counts": {**required, "shapes": -1}},
                       {"native_counts": {**required, "shapes": True}},
                       {"native_counts": {**required, "shapes": "0"}}]
        for index, bad_report in enumerate(bad_reports):
            def bad_stdout(args, **kwargs):
                completed = original_run(args, **kwargs)
                completed.stdout = json.dumps(bad_report)
                return completed

            output = self.base / f"bad-report-{index}.pptx"
            with self.subTest(report=bad_report):
                with patch("newpath.renderer.subprocess.run", side_effect=bad_stdout):
                    with self.assertRaisesRegex(RuntimeError, "report"):
                        self.build([], output=output)
                self.assertFalse(output.exists())
                self.assertEqual(list(self.base.glob(".newpath-build-*")), [])


if __name__ == "__main__":
    unittest.main()
