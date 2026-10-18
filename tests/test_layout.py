"""Boundary tests for the small scene contract (no screenshot inference)."""
import unittest

try:
    from newpath.layout import normalize_layout
except ImportError:
    normalize_layout = None


class LayoutTests(unittest.TestCase):
    def normalize(self, payload):
        self.assertIsNotNone(normalize_layout, "layout normalizer has not been implemented")
        return normalize_layout(payload)

    def test_expands_style_and_inline_override_without_mutating_input(self):
        payload = {"styles": {"heading": {"size": 60, "bold": True, "color": "#aabbcc"}},
                   "items": [{"kind": "text", "text": "标题", "box": [10, 20, 300, 70],
                              "style": "heading", "size": 56}]}
        result = self.normalize(payload)
        self.assertEqual(result["format"], "newpath.scene/1")
        item = result["items"][0]
        self.assertEqual((item["id"], item["size"], item["color"], item["bold"]),
                         ("o001", 56, "AABBCC", True))
        self.assertNotIn("style", item)
        self.assertEqual(payload["styles"]["heading"]["color"], "#aabbcc")

    def test_rich_runs_derive_text_and_preserve_one_frame(self):
        item = self.normalize({"items": [{"id": "t", "kind": "text", "box": [0, 0, 500, 60],
            "runs": [{"text": "Hello ", "bold": True}, {"text": "世界", "color": "ff0000"}]}]})["items"][0]
        self.assertEqual(item["text"], "Hello 世界")
        self.assertEqual(len(item["runs"]), 2)
        self.assertEqual(item["font"], "Microsoft YaHei")

    def test_art_target_defaults_to_box_and_never_becomes_text(self):
        item = self.normalize({"items": [{"kind": "art", "box": [30, 40, 500, 200],
            "description": "Painted word logo", "lettering": "夏日"}]})["items"][0]
        self.assertEqual(item["target"], [30, 40, 500, 200])
        self.assertEqual(item["presentation"], "cutout")
        self.assertEqual(item["lettering"], "夏日")

    def test_optional_null_lettering_is_omitted(self):
        item = self.normalize({"items": [{"kind": "art", "box": [0, 0, 10, 10],
            "description": "icon", "lettering": None}]})["items"][0]
        self.assertNotIn("lettering", item)

    def test_table_uses_equal_dimensions_and_accepts_cell_linebreaks(self):
        item = self.normalize({"items": [{"kind": "table", "box": [100, 200, 400, 160],
            "rows": [["A\nB", "C"], ["D", "E"]]}]})["items"][0]
        self.assertEqual(item["column_widths"], [200, 200])
        self.assertEqual(item["row_heights"], [80, 80])
        self.assertEqual(item["rows"][0][0], "A\nB")

    def test_line_points_and_native_arrow_style_are_preserved(self):
        item = self.normalize({"items": [{"kind": "line", "points": [500, 100, 100, 300],
            "start_arrow": "oval", "end_arrow": "stealth", "dash": "dot"}]})["items"][0]
        self.assertEqual(item["points"], [500, 100, 100, 300])
        self.assertEqual((item["start_arrow"], item["end_arrow"]), ("oval", "stealth"))

    def test_canonical_input_is_idempotent(self):
        first = self.normalize({"items": [{"kind": "shape", "box": [10, 20, 30, 40], "shape": "ellipse"}]})
        self.assertEqual(self.normalize(first), first)

    def test_single_line_fields_reject_unicode_and_terminal_line_breaks(self):
        for separator in ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            for kind, content in (("text", {"text": "A" + separator}),
                                  ("text", {"runs": [{"text": "A" + separator}]}),
                                  ("formula", {"text": "x" + separator})):
                with self.subTest(separator=repr(separator), kind=kind, content=content):
                    with self.assertRaisesRegex(ValueError, "line"):
                        self.normalize({"items": [{"kind": kind, "box": [0, 0, 100, 50], **content}]})

    def test_invalid_contracts_fail_with_readable_errors(self):
        cases = [
            ({"items": [{"kind": "text", "text": "A\nB", "box": [0, 0, 100, 50]}]}, "line"),
            ({"items": [{"kind": "text", "text": "A", "font": "Arial", "box": [0, 0, 100, 50]}]}, "font"),
            ({"items": [{"kind": "text", "text": "A", "vertical": True, "rotation": 90, "box": [0, 0, 100, 50]}]}, "rotation"),
            ({"items": [{"kind": "text", "text": "A", "runs": [{"text": "B"}], "box": [0, 0, 100, 50]}]}, "runs"),
            ({"items": [{"kind": "shape", "box": [0, 0, float("nan"), 50]}]}, "finite"),
            ({"items": [{"kind": "shape", "box": [0, 0, -1, 50]}]}, "positive"),
            ({"items": [{"kind": "art", "description": "x", "lettering": "", "box": [0, 0, 100, 50]}]}, "lettering"),
            ({"items": [{"kind": "table", "rows": [["A"], ["B", "C"]], "box": [0, 0, 100, 50]}]}, "rectangular"),
            ({"items": [{"kind": "table", "rows": [["A", "B"]], "column_widths": [10, 10], "box": [0, 0, 100, 50]}]}, "sum"),
            ({"items": [{"kind": "text", "text": "A", "style": "missing", "box": [0, 0, 100, 50]}]}, "style"),
            ({"items": [{"id": "same", "kind": "shape", "box": [0, 0, 10, 10]}, {"id": "same", "kind": "shape", "box": [0, 0, 10, 10]}]}, "duplicate"),
            ({"items": [{"id": "../bad", "kind": "shape", "box": [0, 0, 10, 10]}]}, "id"),
            ({"items": [{"kind": [], "box": [0, 0, 10, 10]}]}, "kind"),
            ({"items": [{"kind": "shape", "shape": [], "box": [0, 0, 10, 10]}]}, "shape"),
            ({"items": [{"kind": "text", "text": "A", "bold": 1, "box": [0, 0, 100, 50]}]}, "boolean"),
            ({"items": [{"kind": "line", "points": [1, 1, 1, 1]}]}, "nonzero"),
            ({"items": [{"kind": "shape", "opacity": 1.1, "box": [0, 0, 10, 10]}]}, "opacity"),
            ({"items": [{"kind": "shape", "rotation": float("inf"), "box": [0, 0, 10, 10]}]}, "finite"),
        ]
        for payload, message in cases:
            with self.subTest(message=message, payload=payload):
                with self.assertRaisesRegex(ValueError, message):
                    self.normalize(payload)


if __name__ == "__main__":
    unittest.main()
