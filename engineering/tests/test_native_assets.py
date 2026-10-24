"""Native material substrate contracts using program-made RGBA pixels only."""
from __future__ import annotations

import copy
import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import artwork, source

try:
    native = importlib.import_module('newpath.native')
except ModuleNotFoundError as error:
    if error.name != 'newpath.native':
        raise
    native = None

MODE = 'native-transparent-materials/1'
FROZEN = Path(__file__).resolve().parent / 'fixtures' / 'grid25-baseline' / 'newpath'


def frozen_source():
    """Load the bundled baseline with its own relative dependencies."""
    name = '_prk_native_material_grid25_baseline'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, FROZEN / '__init__.py',
                                                      submodule_search_locations=[str(FROZEN)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(name + '.source')


class NativeMaterialTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(native, 'Native material validation is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def png(self, pixels, name='returned.png'):
        path = self.root / name
        Image.fromarray(pixels).save(path)
        return path

    def pixels(self, path):
        with Image.open(path) as image:
            return np.asarray(image.convert('RGBA')).copy()

    def returned(self):
        pixels = np.zeros((100, 120, 4), dtype=np.uint8)
        pixels[..., :3] = [5, 16, 29]
        pixels[30:60, 20:80] = [255, 255, 255, 255]
        for index, color in enumerate(((255, 0, 255), (0, 255, 0),
                                       (0, 255, 255), (255, 255, 0))):
            pixels[35:45, 25 + index*12:34 + index*12] = [*color, 255]
        pixels[12, 15] = [255, 255, 255, 1]
        pixels[75, 94] = [210, 40, 170, 7]
        return pixels

    def board(self):
        return {'id': 'native-fixture', 'size': [120, 100], 'key': None,
                'background_mode': MODE,
                'grid': {'rows': 1, 'columns': 1, 'margin': 10, 'gap': 20,
                         'cell_size': [100, 80]},
                'members': [{'id': 'material', 'slot': [10, 10, 100, 80],
                             'box': [40, 50, 300, 140], 'target': [200, 100, 500, 250],
                             'z': 6, 'inset_xy': [3, 4], 'rendered_size': [90, 70],
                             'pixel_budget': {'required_content_px': [80, 60],
                                              'meets_requirement': True}}]}

    def test_validator_rejects_rgb_opaque_empty_and_non_png_returns(self):
        samples = [(self.returned()[..., :3], 'rgb.png'),
                   (np.full((20, 30, 4), 255, dtype=np.uint8), 'opaque.png'),
                   (np.zeros((20, 30, 4), dtype=np.uint8), 'empty.png')]
        for pixels, name in samples:
            with self.subTest(name=name), self.assertRaises(ValueError):
                native.validate_native_output(self.png(pixels, name))
        jpeg = self.root / 'jpeg.jpg'
        Image.new('RGB', (30, 20), 'white').save(jpeg)
        with self.assertRaises(ValueError):
            native.validate_native_output(jpeg)

    def test_validator_reports_real_alpha_without_changing_pixels_or_quality_claim(self):
        pixels = self.returned()
        path = self.png(pixels)
        before = path.read_bytes()
        info = native.validate_native_output(path)
        self.assertEqual(info['background_mode'], MODE)
        self.assertTrue(info['transparent_background'])
        self.assertEqual(info['size'], [120, 100])
        self.assertEqual(info['visible_pixels'], 1802)
        self.assertEqual(info['transparent_pixels'], 10198)
        self.assertEqual(info['quality'], 'not_evaluated')
        self.assertEqual(path.read_bytes(), before)

    def test_foreground_frame_contact_with_alpha_one_is_legitimate(self):
        for x, y in ((0, 30), (119, 30), (50, 0), (50, 99)):
            pixels = self.returned()
            pixels[y, x] = [255, 255, 255, 1]
            with self.subTest(point=(x, y)):
                self.assertEqual(native.validate_native_output(self.png(pixels), role='foreground')
                                 ['visible_pixels'], 1803)

    def test_palette_transparency_is_native_alpha(self):
        path = self.root / 'palette.png'
        image = Image.new('P', (30, 20), 0)
        image.putpalette([5, 16, 29, 255, 255, 255] + [0, 0, 0]*254)
        for y in range(5, 10):
            for x in range(5, 15):
                image.putpixel((x, y), 1)
        image.save(path, transparency=0)
        self.assertEqual(native.validate_native_output(path)['visible_pixels'], 50)

    def test_native_normalization_preserves_rgba_and_does_not_fill_substrate(self):
        pixels = np.zeros((1080, 1920, 4), dtype=np.uint8)
        pixels[..., :3] = [12, 31, 67]
        pixels[20:80, 20:100] = [255, 255, 255, 255]
        pixels[0, 100] = [0, 255, 255, 1]
        pixels[400, 1200] = [255, 0, 255, 128]
        output = self.root / 'normalized.png'
        source.normalize_layer(self.png(pixels), 'foreground',
                               {'offset': [0, 0], 'resized': [1920, 1080]}, output,
                               background_mode=MODE)
        np.testing.assert_array_equal(self.pixels(output), pixels)

    def test_native_normalization_restores_only_source_padding_to_transparency(self):
        pixels = np.zeros((1080, 1920, 4), dtype=np.uint8)
        pixels[20:80, 400:1000] = [255, 255, 255, 255]
        pixels[0:20, 0:100] = [0, 255, 0, 128]
        output = self.root / 'padded.png'
        source.normalize_layer(self.png(pixels), 'foreground',
                               {'offset': [420, 0], 'resized': [1080, 1080]}, output,
                               background_mode=MODE)
        expected = pixels.copy()
        expected[:, :420] = [0, 0, 0, 0]
        expected[:, 1500:] = [0, 0, 0, 0]
        np.testing.assert_array_equal(self.pixels(output), expected)

    def test_background_mode_does_not_change_frozen_background_normalization(self):
        frozen = frozen_source()
        path = self.png(self.returned())
        transform = {'offset': [420, 0], 'resized': [1080, 1080]}
        old, new = self.root / 'old.png', self.root / 'new.png'
        frozen.normalize_layer(path, 'background', transform, old)
        source.normalize_layer(path, 'background', transform, new, background_mode=MODE)
        self.assertEqual(old.read_bytes(), new.read_bytes())

    def test_source_copy_working_ruler_and_transform_remain_frozen(self):
        frozen = frozen_source()
        path = self.png(self.returned())
        old, new = self.root / 'old', self.root / 'new'
        old.mkdir(); new.mkdir()
        before = path.read_bytes()
        old_info, new_info = frozen.prepare_source(path, old), source.prepare_source(path, new)
        self.assertEqual(old_info, new_info)
        self.assertEqual(old_info['transform'],
                         {'scale': 10.8, 'original_size': [120, 100], 'resized': [1296, 1080],
                          'offset': [312, 0], 'padding': [312, 0, 312, 0]})
        for key in ('original', 'working', 'guide'):
            self.assertEqual((old / old_info[key]).read_bytes(), (new / new_info[key]).read_bytes())
        self.assertEqual(path.read_bytes(), before)

    def test_frozen_source_uses_its_own_packaged_dependencies(self):
        frozen = frozen_source()
        self.assertEqual(Path(frozen.__file__).resolve(), (FROZEN / 'source.py').resolve())
        self.assertTrue(frozen.render_coordinate_guide.__module__.startswith('_prk_native_material_grid25_baseline.'))
        self.assertIsNot(frozen.render_coordinate_guide, source.render_coordinate_guide)
        self.assertIsNot(frozen.sha256, source.sha256)

    def native_boards(self):
        pixels = np.zeros((1080, 1920, 4), dtype=np.uint8)
        colors = ((255, 255, 255, 255), (255, 0, 255, 255), (0, 255, 0, 255),
                  (0, 255, 255, 255), (255, 255, 0, 255), (160, 22, 80, 1))
        for index, color in enumerate(colors):
            pixels[240:360, 210 + index*40:250 + index*40] = color
        src = self.root / 'source.png'
        Image.new('RGB', (1920, 1080), (10, 20, 90)).save(src)
        front = self.png(pixels, 'front.png')
        arts = [{'id': 'all-colors', 'box': [200, 200, 300, 200],
                 'target': [100, 120, 600, 400], 'z': 3, 'lettering': 'WHITE'}]
        return src, front, arts

    def test_native_boards_keep_planner_geometry_real_colors_and_transparent_canvas(self):
        src, front, arts = self.native_boards()
        before = copy.deepcopy(arts)
        legacy = artwork.prepare_boards(src, front, arts, self.root / 'legacy')
        boards = artwork.prepare_boards(src, front, arts, self.root / 'native', background_mode=MODE)
        self.assertEqual(arts, before)
        self.assertEqual(len(boards), len(legacy))
        for old, board in zip(legacy, boards):
            for key in ('size', 'capacity', 'members', 'planning_policy', 'pixel_budget', 'grid'):
                self.assertEqual(board[key], old[key])
            self.assertEqual(board['background_mode'], MODE)
            self.assertIsNone(board['key'])
            self.assertNotEqual(board['signature'], old['signature'])
            pixels = self.pixels(board['image'])
            self.assertEqual(tuple(pixels[0, 0]), (0, 0, 0, 0))
            self.assertTrue(np.any(pixels[..., 3] == 1))
            for color in ((255, 255, 255), (255, 0, 255), (0, 255, 0),
                          (0, 255, 255), (255, 255, 0)):
                self.assertTrue(np.any(np.all(pixels == [*color, 255], axis=2)), color)
            repeated = artwork.prepare_boards(src, front, arts, self.root / 'native', background_mode=MODE)
            self.assertEqual(boards, repeated)

    def test_native_preparation_does_not_consult_colour_key_matte_functions(self):
        src, front, arts = self.native_boards()
        with mock.patch.multiple(artwork, choose_key=mock.Mock(side_effect=AssertionError('key selection')),
                                 collision_mask=mock.Mock(side_effect=AssertionError('colour selection')),
                                 estimate_background=mock.Mock(side_effect=AssertionError('matte estimate')),
                                 erase_matte=mock.Mock(side_effect=AssertionError('colour erasure'))):
            boards = artwork.prepare_boards(src, front, arts, self.root / 'boards', background_mode=MODE)
        self.assertEqual(len(boards), 1)
        self.assertEqual(self.pixels(boards[0]['image'])[0, 0, 3], 0)

    def test_unrecognized_material_modes_are_rejected_without_keyed_downgrade(self):
        src, front, arts = self.native_boards()
        with self.assertRaisesRegex(ValueError, 'mode'):
            source.normalize_layer(front, 'foreground',
                                   {'offset': [0, 0], 'resized': [1920, 1080]},
                                   self.root / 'layer.png', background_mode='native-materials/future')
        with self.assertRaisesRegex(ValueError, 'mode'):
            artwork.prepare_boards(src, front, arts, self.root / 'boards',
                                   background_mode='native-materials/future')
        board = self.board()
        board.update(background_mode='native-materials/future', key='magenta')
        with self.assertRaisesRegex(ValueError, 'mode'):
            artwork.extract_assets(board, self.png(self.returned()), self.root / 'assets')

    def test_native_extraction_keeps_complete_slot_rgba_disconnected_marks_and_target(self):
        pixels = self.returned()
        board = self.board()
        path = self.png(pixels)
        before = path.read_bytes()
        result = artwork.extract_assets(board, path, self.root / 'assets')
        self.assertEqual(result['cell_mapping']['unsafe_members'], [])
        self.assertEqual(len(result['assets']), 1)
        asset = result['assets'][0]
        np.testing.assert_array_equal(self.pixels(asset['path']), pixels[12:76, 15:95])
        self.assertEqual(asset['crop_box'], [15, 12, 95, 76])
        self.assertEqual(asset['source_size'], [80, 64])
        self.assertEqual(asset['target'], [200, 100, 500, 250])
        self.assertEqual(asset['z'], 6)
        self.assertEqual(asset['extraction_policy'], MODE)
        self.assertEqual(asset['returned_pixel_budget']['available_content_px'], [90, 70])
        self.assertEqual(path.read_bytes(), before)

    def test_native_extraction_never_selects_erases_or_restores_colour_fragments(self):
        board = self.board()
        board['members'][0]['lettering'] = 'DETACHED'
        reference = self.png(self.returned(), 'reference.png')
        board['image'] = str(reference)
        with mock.patch.multiple(artwork, erase_matte=mock.Mock(side_effect=AssertionError('erase')),
                                 estimate_background=mock.Mock(side_effect=AssertionError('estimate')),
                                 select_subject=mock.Mock(side_effect=AssertionError('select')),
                                 _retain_interior_lettering=mock.Mock(side_effect=AssertionError('restore'))):
            result = artwork.extract_assets(board, self.png(self.returned()), self.root / 'assets')
        self.assertEqual(len(result['assets']), 1)
        np.testing.assert_array_equal(self.pixels(result['assets'][0]['path']), self.returned()[12:76, 15:95])

    def test_native_board_canvas_edge_alpha_one_is_unsafe_and_is_not_deleted(self):
        pixels = self.returned()
        pixels[20, 0] = [255, 255, 255, 1]
        path = self.png(pixels)
        before = path.read_bytes()
        result = artwork.extract_assets(self.board(), path, self.root / 'assets')
        self.assertEqual(result['assets'], [])
        self.assertEqual(result['cell_mapping']['unsafe_members'], ['material'])
        self.assertIn('subject_clipped_at_canvas_edge', {note['code'] for note in result['warnings']})
        self.assertEqual(path.read_bytes(), before)

    def test_native_board_alpha_one_haze_cannot_fake_an_empty_gutter(self):
        board = self.board()
        board.update(size=[220, 120], grid={'rows': 1, 'columns': 2, 'margin': 10,
                                          'gap': 20, 'cell_size': [90, 100]})
        board['members'] = [{'id': 'left', 'slot': [10, 10, 90, 100], 'box': [0, 0, 50, 50]},
                            {'id': 'right', 'slot': [120, 10, 90, 100], 'box': [0, 0, 50, 50]}]
        pixels = np.zeros((120, 220, 4), dtype=np.uint8)
        pixels[25:90, 30:80] = [255, 255, 255, 255]
        pixels[25:90, 140:190] = [255, 0, 255, 255]
        pixels[50, 30:190] = [255, 255, 255, 1]
        path = self.png(pixels)
        result = artwork.extract_assets(board, path, self.root / 'assets')
        self.assertEqual(result['assets'], [])
        self.assertEqual(result['cell_mapping']['unsafe_members'], ['left', 'right'])
        self.assertIn('unseparable_grid_boundary', {note['code'] for note in result['warnings']})
        np.testing.assert_array_equal(self.pixels(path), pixels)


if __name__ == '__main__':
    unittest.main()
