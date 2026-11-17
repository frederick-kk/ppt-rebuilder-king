"""Outgoing layer-payload contracts; actual scene ownership needs web QA."""
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "compat-runtime"))
from newpath import prompts
from newpath.native import MODE


# Frozen r8 payload hashes, obtained before this implementation was edited.
OLD_PAYLOADS = [
    ({"offset": [0, 0], "resized": [1920, 1080]}, "background", None,
     "0280594a5452548e2e194ed39944557c0b7a80858dbb008155511d9e1c725e98"),
    ({"offset": [0, 0], "resized": [1920, 1080]}, "background", MODE,
     "0280594a5452548e2e194ed39944557c0b7a80858dbb008155511d9e1c725e98"),
    ({"offset": [0, 0], "resized": [1920, 1080]}, "foreground", None,
     "287e8ea114cadd0d85c975b6cdcdb67e1fa53e3c9e39d5e7c414446555ef62b9"),
    ({"offset": [0, 0], "resized": [1920, 1080]}, "foreground", MODE,
     "247a64e6160ee7b00ff507df49aa4928ff1f270f89aa0f3d70395935770d2da3"),
    ({"offset": [0, 100], "resized": [1920, 880]}, "background", None,
     "7a66c08e16beff7f6e06c4e65560b9abb1ac722281fb3443c499cba9565701f5"),
    ({"offset": [0, 100], "resized": [1920, 880]}, "background", MODE,
     "7a66c08e16beff7f6e06c4e65560b9abb1ac722281fb3443c499cba9565701f5"),
    ({"offset": [0, 100], "resized": [1920, 880]}, "foreground", None,
     "a6be348656fda937e4ddfdbed3597b8f8d8eb389f27160a41bb01e13e16fa02a"),
    ({"offset": [0, 100], "resized": [1920, 880]}, "foreground", MODE,
     "1fb4c5b635244c2b46c3d3586766156bc88a25da54e2051bdaf150b047e85b78"),
]


class DecorativeBackgroundPolicyPayload(unittest.TestCase):
    def brief(self, role, transform=None, mode=MODE, enabled=True):
        parameters = inspect.signature(prompts.layer_brief).parameters
        self.assertIn("decorative_background", parameters,
                      "Layer requests need an explicit optional decorative-container policy")
        source = transform or {"offset": [0, 0], "resized": [1920, 1080]}
        return prompts.layer_brief(role, source, background_mode=mode,
                                   decorative_background=enabled)

    def test_omitted_option_keeps_all_frozen_r8_request_bytes(self):
        for transform, role, mode, expected in OLD_PAYLOADS:
            with self.subTest(transform=transform, role=role, mode=mode):
                result = prompts.layer_brief(role, transform, background_mode=mode)
                self.assertEqual(hashlib.sha256(result.encode()).hexdigest(), expected)

    def test_explicit_false_keeps_all_frozen_r8_request_bytes(self):
        for transform, role, mode, expected in OLD_PAYLOADS:
            with self.subTest(transform=transform, role=role, mode=mode):
                result = self.brief(role, transform, mode, enabled=False)
                self.assertEqual(hashlib.sha256(result.encode()).hexdigest(), expected)

    def test_option_defaults_to_false_and_requires_keyword_binding(self):
        self.brief("background")
        parameter = inspect.signature(prompts.layer_brief).parameters["decorative_background"]
        self.assertIs(parameter.default, False)
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_background_retains_empty_decorative_containers_at_source_geometry(self):
        result = self.brief("background")
        task = result.split("Background task: ", 1)[1].split("Geometry: ", 1)[0]
        self.assertIn("empty decorative paper", task)
        self.assertIn("gold frames", task)
        self.assertIn("card outlines", task)
        self.assertIn("cloud ornaments", task)
        self.assertIn("original positions and sizes", task)
        self.assertIn("texture", task)
        self.assertIn("Remove all ordinary page text", task)
        self.assertIn("artistic writing", task)
        self.assertIn("independent content images", task)
        self.assertIn("icons", task)
        self.assertIn("brush-stroke title labels", task)
        self.assertIn("Outer fitting margins stay empty white", task)

    def test_foreground_keeps_movable_content_and_omits_background_containers(self):
        result = self.brief("foreground")
        task = result.split("Foreground task: ", 1)[1].split("Geometry: ", 1)[0]
        self.assertIn("ordinary page text", task)
        self.assertIn("artistic writing", task)
        self.assertIn("independent content images", task)
        self.assertIn("icons", task)
        self.assertIn("brush-stroke title labels", task)
        self.assertIn("Omit the background scene and all decorative containers", task)
        self.assertIn("original positions and sizes", task)
        self.assertNotIn("paper or card panels", result)

    def test_new_policy_keeps_complete_embedded_images_and_native_basic_geometry(self):
        for role in ("background", "foreground"):
            result = self.brief(role)
            self.assertIn(prompts.EMBEDDED_IMAGE_POLICY, result)
            self.assertIn("Flat simple geometry and regular tables remain native", result)
            self.assertNotIn("Separate page elements belong to the foreground: artistic headings, paper or card panels", result)

    def test_new_policy_preserves_padded_geometry_and_both_substrates(self):
        transform = {"offset": [160, 0], "resized": [1600, 1080]}
        original = copy.deepcopy(transform)
        expected = {"working_pixels": [1920, 1080], "content_offset": [160, 0],
                    "content_size": [1600, 1080]}
        for role in ("background", "foreground"):
            for mode in (None, MODE):
                result = self.brief(role, transform, mode)
                self.assertEqual(json.loads(result.split("Geometry: ", 1)[1]), expected)
                if role == "foreground" and mode == MODE:
                    self.assertIn("one transparent PNG", result)
                    self.assertIn("alpha 0", result)
                    self.assertNotIn("must be solid magenta", result)
                    self.assertNotIn("uniform RGB(255,0,255)", result)
                    self.assertNotIn("paper/card material", result)
                else:
                    self.assertIn("one opaque PNG", result)
                if role == "foreground" and mode is None:
                    self.assertIn("uniform RGB(255,0,255)", result)
                    self.assertIn("solid magenta", result)
        self.assertEqual(transform, original)

    def test_unknown_material_mode_still_fails_in_new_policy(self):
        self.brief("background")
        for role in ("background", "foreground"):
            with self.assertRaisesRegex(ValueError, "Unknown material background mode"):
                prompts.layer_brief(role, {"offset": [0, 0], "resized": [1920, 1080]},
                                    background_mode="unknown", decorative_background=True)

    def test_new_policy_calls_do_not_mutate_later_legacy_requests(self):
        first = self.brief("background")
        self.assertEqual(self.brief("background"), first)
        self.brief("foreground")
        for transform, role, mode, expected in OLD_PAYLOADS:
            result = prompts.layer_brief(role, transform, background_mode=mode)
            self.assertEqual(hashlib.sha256(result.encode()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
