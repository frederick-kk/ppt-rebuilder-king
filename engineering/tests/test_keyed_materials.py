"""Keyed generation is converted locally while the actual return stays immutable."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import prompts
from newpath.native import MODE, validate_native_output


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class KeyedMaterials(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def api(self):
        spec = importlib.util.find_spec('newpath.keyed')
        self.assertIsNotNone(spec, 'new keyed material normalization API is missing')
        from newpath import keyed
        self.assertEqual(keyed.POLICY, 'keyed-materials/1')
        return keyed

    def image(self, name, array, mode='RGBA'):
        path = self.root / name
        Image.fromarray(array, mode=mode).save(path)
        return path

    def keyed_pixels(self, color=(255, 0, 255)):
        image = np.full((32, 48, 4), (*color, 255), dtype=np.uint8)
        image[8:24, 12:36] = (165, 12, 30, 255)
        return image

    def test_rgb_solid_key_becomes_real_alpha_and_preserves_subject(self):
        api = self.api()
        pixels = self.keyed_pixels()
        raw = self.image('raw.png', pixels[..., :3], 'RGB')
        digest = sha(raw)
        output = self.root / 'derived.png'
        info = api.prepare_material(raw, output, 'magenta')
        actual = np.array(Image.open(output).convert('RGBA'))
        self.assertEqual(sha(raw), digest)
        self.assertEqual(info['source_sha256'], digest)
        self.assertEqual(info['sha256'], sha(output))
        self.assertEqual(info['conversion'], 'keyed-to-alpha')
        self.assertEqual(info['path'], str(output.resolve()))
        np.testing.assert_array_equal(actual[8:24, 12:36], pixels[8:24, 12:36])
        self.assertTrue(np.all(actual[0] == 0))
        self.assertGreater(info['removed_pixels'], 0)
        validate_native_output(output)

    def test_all_four_keys_and_observed_near_key_noise(self):
        api = self.api()
        for key, color in [('magenta', (250, 5, 250)), ('green', (3, 250, 3)),
                           ('cyan', (4, 250, 250)), ('yellow', (250, 250, 4))]:
            with self.subTest(key=key):
                raw = self.image(key + '.png', self.keyed_pixels(color))
                info = api.prepare_material(raw, self.root / (key + '-derived.png'), key)
                self.assertEqual(info['conversion'], 'keyed-to-alpha')
                validate_native_output(info['path'])

    def test_opaque_key_border_preserves_translucent_subject_and_white_material(self):
        api = self.api()
        pixels = self.keyed_pixels()
        pixels[8:24, 12:36, 3] = 254
        pixels[12:16, 18:24] = (255, 255, 255, 255)
        raw = self.image('hybrid.png', pixels)
        try:
            info = api.prepare_material(raw, self.root / 'hybrid-alpha.png', 'magenta')
        except ValueError as error:
            self.fail('An observed opaque key border must not reject existing subject alpha254: ' + str(error))
        actual = np.array(Image.open(info['path']))
        self.assertEqual(info['conversion'], 'keyed-to-alpha')
        np.testing.assert_array_equal(actual[8:24, 12:36], pixels[8:24, 12:36])
        self.assertTrue(np.all(actual[0] == 0))

    def test_valid_native_alpha_preserves_encoded_bytes_and_hidden_rgb(self):
        api = self.api()
        pixels = self.keyed_pixels()
        pixels[..., 3] = 0
        pixels[8:24, 12:36, 3] = 254
        raw = self.image('native.png', pixels)
        out = self.root / 'native-copy.png'
        info = api.prepare_material(raw, out, 'magenta')
        self.assertEqual(info['conversion'], 'native-pass-through')
        self.assertEqual(raw.read_bytes(), out.read_bytes())
        np.testing.assert_array_equal(np.array(Image.open(out)), pixels)

    def test_native_alpha0_does_not_hide_an_observed_opaque_key_field(self):
        api = self.api()
        pixels = self.keyed_pixels()
        pixels[0, 0] = (31, 40, 50, 0)
        raw = self.image('existing-alpha-and-key.png', pixels)
        info = api.prepare_material(raw, self.root / 'existing-alpha-copy.png', 'magenta')
        self.assertEqual(info['conversion'], 'keyed-to-alpha')
        self.assertGreater(info['removed_pixels'], 0)
        actual = np.array(Image.open(info['path']))
        np.testing.assert_array_equal(actual[0, 0], pixels[0, 0])
        np.testing.assert_array_equal(actual[8:24, 12:36], pixels[8:24, 12:36])
        self.assertEqual(int(np.count_nonzero(actual[..., 3])), 16 * 24)

    def test_transparent_perimeter_keeps_real_opaque_key_coloured_subject(self):
        api = self.api()
        pixels = np.zeros((32, 48, 4), dtype=np.uint8)
        pixels[..., :3] = (255, 0, 255)
        pixels[8:24, 12:36] = (255, 0, 255, 255)
        raw = self.image('real-key-subject.png', pixels)
        info = api.prepare_material(raw, self.root / 'real-key-subject-copy.png', 'magenta')
        self.assertEqual(info['conversion'], 'native-pass-through')
        self.assertEqual(info['removed_pixels'], 0)
        self.assertEqual(Path(info['path']).read_bytes(), raw.read_bytes())

    def test_palette_trns_native_png_keeps_original_encoding(self):
        api = self.api()
        raw = self.root / 'palette.png'
        image = Image.new('P', (48, 32), 0)
        image.putpalette([255, 0, 255, 165, 12, 30] + [0] * 762)
        image.paste(1, (12, 8, 36, 24))
        image.save(raw, transparency=0)
        info = api.prepare_material(raw, self.root / 'palette-copy.png', 'magenta')
        self.assertEqual(info['conversion'], 'native-pass-through')
        self.assertEqual(Path(info['path']).read_bytes(), raw.read_bytes())

    def test_white_grey_painted_checker_and_wrong_key_rejected(self):
        api = self.api()
        arrays = [self.keyed_pixels((255, 255, 255)), self.keyed_pixels((128, 128, 128)),
                  self.keyed_pixels((0, 255, 0))]
        checker = self.keyed_pixels((240, 240, 240))
        yy, xx = np.indices(checker.shape[:2])
        checker[(yy // 4 + xx // 4) % 2 == 0] = (170, 170, 170, 255)
        arrays.append(checker)
        for index, array in enumerate(arrays):
            with self.subTest(index=index):
                raw = self.image(str(index) + '.png', array)
                output = self.root / (str(index) + '-output.png')
                with self.assertRaises(ValueError):
                    api.prepare_material(raw, output, 'magenta')
                self.assertFalse(output.exists())

    def test_pure_key_empty_alpha_and_faded_whole_canvas_rejected(self):
        api = self.api()
        for name, array in [('pure-key', np.full((32, 48, 4), (255, 0, 255, 255), dtype=np.uint8)),
                            ('empty', np.zeros((32, 48, 4), dtype=np.uint8)),
                            ('faded', np.full((32, 48, 4), (255, 0, 255, 254), dtype=np.uint8))]:
            with self.subTest(name=name):
                output = self.root / (name + '-output.png')
                with self.assertRaises(ValueError):
                    api.prepare_material(self.image(name + '.png', array), output, 'magenta')
                self.assertFalse(output.exists())

    def test_original_and_unrelated_existing_output_never_overwritten(self):
        api = self.api()
        raw = self.image('raw.png', self.keyed_pixels())
        before = raw.read_bytes()
        with self.assertRaises(ValueError):
            api.prepare_material(raw, raw, 'magenta')
        other = self.root / 'existing.png'
        other.write_bytes(b'existing unrelated evidence')
        with self.assertRaises(ValueError):
            api.prepare_material(raw, other, 'magenta')
        self.assertEqual(other.read_bytes(), b'existing unrelated evidence')
        self.assertEqual(raw.read_bytes(), before)

    def test_repeat_same_conversion_is_idempotent(self):
        api = self.api()
        raw = self.image('raw.png', self.keyed_pixels())
        out = self.root / 'same.png'
        first = api.prepare_material(raw, out, 'magenta')
        data = out.read_bytes()
        second = api.prepare_material(raw, out, 'magenta')
        self.assertEqual(first, second)
        self.assertEqual(data, out.read_bytes())

    def test_non_png_and_invalid_key_rejected(self):
        api = self.api()
        jpeg = self.root / 'image.jpg'
        Image.new('RGB', (20, 20), (255, 0, 255)).save(jpeg)
        with self.assertRaises(ValueError):
            api.prepare_material(jpeg, self.root / 'out.png', 'magenta')
        for key in ('white', '', None, []):
            with self.subTest(key=key), self.assertRaises(ValueError):
                api.prepare_material(jpeg, self.root / 'out.png', key)

    def test_key_selection_ignores_hidden_rgb_and_avoids_visible_collision(self):
        api = self.api()
        clear = np.zeros((32, 48, 4), dtype=np.uint8)
        clear[..., :3] = (255, 0, 255)
        clear[8:24, 12:36] = (165, 12, 30, 255)
        self.assertEqual(api.choose_material_key(self.image('clear.png', clear)), 'magenta')
        clear[12:16, 18:24] = (255, 0, 255, 255)
        self.assertNotEqual(api.choose_material_key(self.image('conflict.png', clear)), 'magenta')


class KeyedBriefs(unittest.TestCase):
    def layer(self):
        method = getattr(prompts, 'keyed_layer_brief', None)
        self.assertTrue(callable(method), 'keyed_layer_brief is missing')
        return method

    def board(self):
        method = getattr(prompts, 'keyed_board_brief', None)
        self.assertTrue(callable(method), 'keyed_board_brief is missing')
        return method

    @staticmethod
    def fixture():
        return {'size': [256, 256], 'key': None, 'background_mode': MODE,
                'board_reference_policy': prompts.BOARD_REFERENCE_POLICY,
                'members': [{'id': 'photo', 'description': 'Full photograph with real white writing',
                             'box': [200, 100, 80, 90], 'slot': [8, 8, 240, 240], 'inset_px': 8,
                             'embedded_image': True, 'foreground_box': [205, 105, 80, 90],
                             'foreground_crop_pixels': [205, 105, 285, 195]}]}

    def test_layer_helper_uses_selected_solid_field_and_keeps_scope(self):
        method = self.layer()
        transform = {'offset': [0, 0], 'resized': [1920, 1080]}
        for key, rgb in [('magenta', 'RGB(255,0,255)'), ('green', 'RGB(0,255,0)'),
                         ('cyan', 'RGB(0,255,255)'), ('yellow', 'RGB(255,255,0)')]:
            with self.subTest(key=key):
                brief = method('foreground', transform, key, decorative_background=True,
                               foreground_components_only=True)
                self.assertIn('uniform ' + rgb, brief)
                self.assertIn('one opaque PNG', brief)
                self.assertNotIn('must be fully transparent with alpha 0', brief)
                self.assertIn('actual component-sampling reference', brief)
                self.assertIn('Preserve complete rectangular photos', brief)

    def test_background_helper_keeps_original_brief_exact(self):
        method = self.layer()
        transform = {'offset': [0, 0], 'resized': [1920, 1080]}
        self.assertEqual(method('background', transform, 'green', decorative_background=True),
                         prompts.layer_brief('background', transform, decorative_background=True))

    def test_board_helper_keeps_all_assignments_ownership_and_metadata(self):
        method = self.board()
        board = self.fixture()
        before = copy.deepcopy(board)
        brief = method(board, 'green')
        self.assertEqual(board, before)
        self.assertIn('uniform opaque RGB(0,255,0)', brief)
        self.assertNotIn('Use a true transparent PNG field', brief)
        self.assertNotIn('Keep every area outside each cutout subject fully transparent', brief)
        self.assertIn('generated-foreground sampling window', brief)
        self.assertIn('not image-internal writing', brief)
        self.assertIn('foreground_crop_pixels', brief)
        self.assertIn('Preserve the image\'s original opacity', brief)

    def test_bad_keys_fail_without_mutating_board(self):
        method = self.board()
        board = self.fixture()
        before = copy.deepcopy(board)
        with self.assertRaises(ValueError):
            method(board, 'white')
        self.assertEqual(board, before)


if __name__ == '__main__':
    unittest.main()
