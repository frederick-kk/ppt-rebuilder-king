"""Recover complete native cells only behind faint, reference-empty dividers."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))

from newpath import cellmap
from newpath.artwork import extract_assets
from newpath.native import MODE
from newpath.renderer import build_presentation


POLICY = 'native-fixed-grid-low-alpha-background/1'


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rebased_board(job, task):
    board = copy.deepcopy(task['board'])
    board['image'] = str(job / 'boards' / Path(board['image']).name)
    return board


def frozen_extractor():
    runtime = Path(os.environ['PRK_R16_BASELINE_RUNTIME'])
    modules = {}
    for name in ['cellmap', 'artwork']:
        specification = importlib.util.spec_from_file_location(
            'newpath._frozen_r16_' + name, runtime / 'newpath' / (name + '.py'))
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        modules[name] = module
    modules['artwork'].resolve_cells = modules['cellmap'].resolve_cells
    return modules['artwork'].extract_assets


class FixedNativeGridRecovery(unittest.TestCase):
    def fixture(self):
        board = {
            'id': 'fixed-native-grid', 'size': [120, 104], 'key': None,
            'background_mode': MODE,
            'grid': {'rows': 2, 'columns': 2, 'margin': 8, 'gap': 8,
                     'cell_size': [48, 40]},
            'members': [
                {'id': 'a', 'slot': [8, 8, 48, 40], 'box': [100, 100, 80, 30]},
                {'id': 'b', 'slot': [64, 8, 48, 40], 'box': [300, 100, 80, 30]},
                {'id': 'c', 'slot': [8, 56, 48, 40], 'box': [100, 300, 80, 30]},
            ],
        }
        reference = np.zeros((104, 120), dtype=np.uint8)
        reference[20:38, 20:44] = 255
        reference[20:38, 76:100] = 255
        reference[68:86, 20:44] = 255
        alpha = np.full(reference.shape, 2, dtype=np.uint8)
        alpha[0, :] = alpha[-1, :] = 0
        alpha[:, 0] = alpha[:, -1] = 0
        for left, top, right, bottom in [(20, 20, 44, 38), (76, 20, 100, 38),
                                        (20, 68, 44, 86)]:
            alpha[top - 2:bottom + 2, left - 2:right + 2] = 0
        alpha[reference > 0] = 255
        return board, alpha, reference

    def resolve(self, board, alpha, reference):
        return cellmap.resolve_cells(board, alpha, native_geometry=True,
                                     reference_alpha=reference)

    def assert_unsafe(self, board, alpha, reference):
        self.assertTrue(self.resolve(board, alpha, reference)['unsafe_members'])

    def test_full_cells_partition_low_alpha_field_with_all_unique_real_cores(self):
        board, alpha, reference = self.fixture()
        original = alpha.copy()
        strict = cellmap._resolve_cells(board, alpha, native_geometry=True,
                                        reference_alpha=reference)
        self.assertEqual(set(strict['unsafe_members']), {'a', 'b', 'c'})
        result = self.resolve(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertEqual(result['geometry_policy'], POLICY)
        self.assertEqual(result['cells'], {
            'a': [8, 8, 60, 52], 'b': [60, 8, 112, 52], 'c': [8, 52, 60, 96]})
        report = result['fixed_grid_recovery']
        self.assertEqual(report['identity_quality'], 'not_evaluated')
        self.assertTrue(report['coverage']['each_pixel_exactly_once'])
        self.assertEqual(report['coverage']['partition_pixels'], alpha.size)
        self.assertGreater(report['split_alpha_above1_components'], 0)
        self.assertEqual(report['original_multi_owner_components'], 0)
        self.assertTrue(all(line['nonzero_alpha_pixels'] for line in report['dividers']))
        self.assertEqual(len(report['background_partitions']), 5)
        self.assertTrue(all(note in result['warnings'] for note in strict['warnings']))
        np.testing.assert_array_equal(alpha, original)

    def test_full_rgba_cell_including_zero_alpha_rgb_is_saved_without_tight_crop(self):
        board, alpha, reference = self.fixture()
        alpha[13, 13] = 0
        pixels = np.full((*alpha.shape, 4), [61, 109, 211, 0], dtype=np.uint8)
        pixels[..., 3] = alpha
        reference_pixels = pixels.copy()
        reference_pixels[..., 3] = reference
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            actual = directory / 'actual.png'
            reference_path = directory / 'reference.png'
            Image.fromarray(pixels).save(actual)
            Image.fromarray(reference_pixels).save(reference_path)
            board['image'] = str(reference_path)
            actual_before = file_hash(actual)
            result = extract_assets(board, actual, directory / 'assets')
            self.assertEqual(len(result['assets']), 3)
            coverage = np.zeros(alpha.shape, dtype=np.uint8)
            reconstructed = np.zeros_like(pixels)
            for asset in result['assets']:
                x, y, width, height = asset['actual_slot']
                self.assertEqual(asset['crop_box'], [0, 0, width, height])
                material = np.asarray(Image.open(asset['path']).convert('RGBA'))
                np.testing.assert_array_equal(material, pixels[y:y + height, x:x + width])
                coverage[y:y + height, x:x + width] += 1
                reconstructed[y:y + height, x:x + width] = material
                self.assertEqual(asset['placement']['geometry_source'], POLICY)
                self.assertEqual(asset['placement']['alpha_threshold_for_geometry_only'], 16)
                self.assertEqual(asset['placement']['visible_core_target'],
                                 next(member['box'] for member in board['members']
                                      if member['id'] == asset['id']))
            for part in result['cell_mapping']['fixed_grid_recovery']['background_partitions']:
                x0, y0, x1, y1 = part['box']
                coverage[y0:y1, x0:x1] += 1
                reconstructed[y0:y1, x0:x1] = pixels[y0:y1, x0:x1]
            self.assertTrue(np.all(coverage == 1))
            np.testing.assert_array_equal(reconstructed, pixels)
            self.assertEqual(file_hash(actual), actual_before)

    def test_missing_empty_and_faint_only_reference_stay_unsafe(self):
        board, alpha, reference = self.fixture()
        for candidate in [None, np.zeros_like(reference), np.minimum(reference, 3)]:
            with self.subTest(reference='absent' if candidate is None else int(candidate.max())):
                self.assert_unsafe(board, alpha, candidate)

    def test_reference_at_a_divider_cannot_be_erased_as_background(self):
        board, alpha, reference = self.fixture()
        reference[25, 59:61] = 255
        self.assert_unsafe(board, alpha, reference)

    def test_alpha4_or_strong_material_at_any_divider_stays_unsafe(self):
        board, alpha, reference = self.fixture()
        for value in [4, 16, 17, 255]:
            for y, x in [(25, 59), (51, 25), (8, 25), (95, 25)]:
                with self.subTest(alpha=value, position=(y, x)):
                    candidate = alpha.copy()
                    candidate[y, x] = value
                    self.assert_unsafe(board, candidate, reference)

    def test_two_subjects_joined_by_a_strong_bridge_stay_unsafe(self):
        board, alpha, reference = self.fixture()
        alpha[28, 43:77] = 255
        self.assert_unsafe(board, alpha, reference)

    def test_faint_bridge_connecting_two_reference_owned_subjects_stays_unsafe(self):
        board, alpha, reference = self.fixture()
        alpha[28, 43:77] = 2
        self.assert_unsafe(board, alpha, reference)

    def test_sparse_faults_without_a_background_field_keep_existing_budgets(self):
        board, alpha, reference = self.fixture()
        alpha = reference.copy()
        alpha[15:40, 58:61] = 2
        self.assert_unsafe(board, alpha, reference)

    def test_unowned_strong_material_in_used_empty_or_outer_partition_is_rejected(self):
        board, alpha, reference = self.fixture()
        for y, x in [(42, 50), (70, 80), (2, 2)]:
            with self.subTest(position=(y, x)):
                candidate = alpha.copy()
                candidate[y:y + 2, x:x + 2] = 255
                self.assert_unsafe(board, candidate, reference)

    def test_absent_weak_or_wrong_slot_core_stays_unsafe(self):
        board, alpha, reference = self.fixture()
        for value in [0, 3, 16]:
            with self.subTest(alpha=value):
                candidate = alpha.copy()
                candidate[68:86, 20:44] = value
                self.assert_unsafe(board, candidate, reference)
        candidate = np.roll(reference, 30, axis=1)
        self.assert_unsafe(board, alpha, candidate)

    def test_deformed_or_duplicate_planned_slot_is_rejected(self):
        board, alpha, reference = self.fixture()
        for slot in [[9, 8, 48, 40], [8, 8, 47, 40]]:
            with self.subTest(slot=slot):
                altered = copy.deepcopy(board)
                altered['members'][0]['slot'] = slot
                self.assert_unsafe(altered, alpha, reference)
        board['members'][0]['slot'] = [64, 8, 48, 40]
        with self.assertRaises(ValueError):
            self.resolve(board, alpha, reference)

    def test_return_size_change_uses_shared_scaled_midlines(self):
        board, alpha, reference = self.fixture()
        for factor in [0.5, 2]:
            with self.subTest(scale=factor):
                scaled = np.asarray(Image.fromarray(alpha).resize(
                    (int(alpha.shape[1] * factor), int(alpha.shape[0] * factor)),
                    Image.Resampling.NEAREST))
                result = self.resolve(board, scaled, reference)
                self.assertEqual(result['unsafe_members'], [])
                self.assertEqual(result['geometry_policy'], POLICY)
                self.assertEqual(result['cells']['a'], [round(value * factor)
                                                       for value in [8, 8, 60, 52]])

    def test_ambiguous_reference_cannot_assign_one_strong_core_to_two_members(self):
        board, alpha, reference = self.fixture()
        alpha[28, 43:77] = 255
        reference[28, 43:77] = 255
        self.assert_unsafe(board, alpha, reference)

    def test_explicit_native_mode_is_required_for_this_new_fallback(self):
        board, alpha, reference = self.fixture()
        board.pop('background_mode')
        self.assert_unsafe(board, alpha, reference)

    def test_already_safe_non_native_and_legacy_mapping_are_unchanged(self):
        board, alpha, reference = self.fixture()
        result = self.resolve(board, reference, reference)
        strict = cellmap._resolve_cells(board, reference, native_geometry=True,
                                        reference_alpha=reference)
        self.assertEqual(result, strict)
        non_native = cellmap.resolve_cells(board, alpha, reference_alpha=reference)
        self.assertNotEqual(non_native.get('geometry_policy'), POLICY)
        board.pop('grid')
        legacy = self.resolve(board, alpha, reference)
        self.assertEqual(legacy['status'], 'legacy')
        self.assertNotIn('fixed_grid_recovery', legacy)

    def test_faint_corner_in_background_is_retained_without_becoming_a_core(self):
        board, alpha, reference = self.fixture()
        alpha[-2, -2] = 4
        result = self.resolve(board, alpha, reference)
        self.assertEqual(result['unsafe_members'], [])
        self.assertEqual(result['fixed_grid_recovery']['identity_quality'], 'not_evaluated')


@unittest.skipUnless(os.environ.get('PRK_R16_REVIEW_JOB'), 'actual R16 fixture supplied privately')
class ActualR16FixedGridRecovery(unittest.TestCase):
    def setUp(self):
        self.job = Path(os.environ['PRK_R16_REVIEW_JOB'])
        self.state = json.loads((self.job / 'state.json').read_text(encoding='utf-8'))
        self.task = next(task for task in self.state['tasks'].values()
                         if len(task.get('board', {}).get('members', [])) == 9)

    def test_actual_nine_members_use_normal_extractor_and_compile_all_twenty_five(self):
        original_files = {str(path.relative_to(self.job)): file_hash(path)
                          for path in self.job.rglob('*') if path.is_file()}
        board = rebased_board(self.job, self.task)
        actual = self.job / self.task['result']['raw']
        raw = np.asarray(Image.open(actual).convert('RGBA'))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = extract_assets(board, actual, directory / 'assets')
            self.assertEqual(result['cell_mapping']['unsafe_members'], [])
            self.assertEqual(len(result['assets']), 9)
            self.assertEqual(result['cell_mapping']['geometry_policy'], POLICY)
            self.assertEqual(result['cell_mapping']['fixed_grid_recovery']
                             ['split_alpha_above1_components'], 17)
            for asset in result['assets']:
                x, y, width, height = asset['actual_slot']
                self.assertEqual(asset['crop_box'], [0, 0, width, height])
                np.testing.assert_array_equal(
                    np.asarray(Image.open(asset['path']).convert('RGBA')),
                    raw[y:y + height, x:x + width])
            assets = copy.deepcopy(self.state['assets'])
            for asset in assets:
                asset['path'] = str(self.job / 'assets' / Path(asset['path']).parent.name
                                    / Path(asset['path']).name)
                self.assertTrue(Path(asset['path']).is_file())
            assets.extend(result['assets'])
            self.assertEqual(len(assets), 25)
            background_task = next(task for task in self.state['tasks'].values()
                                   if task['role'] == 'background')
            layout = json.loads((self.job / 'layout.json').read_text(encoding='utf-8'))
            receipt = build_presentation(layout, self.job / background_task['result']['raw'],
                                         assets, directory / 'technical-replay.pptx')
            self.assertEqual(receipt['skipped'], [])
        self.assertEqual(original_files, {str(path.relative_to(self.job)): file_hash(path)
                                         for path in self.job.rglob('*') if path.is_file()})

    @unittest.skipUnless(os.environ.get('PRK_R16_BASELINE_RUNTIME'), 'frozen baseline supplied privately')
    def test_real_safe_board_every_asset_field_and_png_remain_exact(self):
        baseline = frozen_extractor()
        safe_task = next(task for task in self.state['tasks'].values()
                         if len(task.get('board', {}).get('members', [])) == 15)
        board = rebased_board(self.job, safe_task)
        actual = self.job / safe_task['result']['raw']
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = baseline(board, actual, directory)
            hashes = {asset['id']: file_hash(asset['path']) for asset in old['assets']}
            new = extract_assets(board, actual, directory)
            self.assertEqual(new, old)
            self.assertEqual({asset['id']: file_hash(asset['path']) for asset in new['assets']}, hashes)

    def test_real_failed_board_missing_reference_cannot_be_recovered(self):
        board = rebased_board(self.job, self.task)
        board['image'] = str(self.job / 'boards' / 'absent-reference.png')
        with tempfile.TemporaryDirectory() as temporary:
            result = extract_assets(board, self.job / self.task['result']['raw'], temporary)
            self.assertEqual(len(result['assets']), 0)
            self.assertEqual(len(result['cell_mapping']['unsafe_members']), 9)

    def test_real_failed_board_alpha4_divider_and_extra_strong_core_stay_unsafe(self):
        board = rebased_board(self.job, self.task)
        actual = self.job / self.task['result']['raw']
        pixels = np.asarray(Image.open(actual).convert('RGBA'))
        for position, value in [((100, 768), 4), ((970, 1470), 255)]:
            with self.subTest(position=position, alpha=value):
                changed = pixels.copy()
                changed[position[0], position[1], 3] = value
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    path = directory / 'negative.png'
                    Image.fromarray(changed).save(path)
                    result = extract_assets(board, path, directory / 'assets')
                    self.assertEqual(len(result['assets']), 0)
                    self.assertEqual(len(result['cell_mapping']['unsafe_members']), 9)


@unittest.skipUnless(os.environ.get('PRK_R15_REVIEW_JOB')
                     and os.environ.get('PRK_R16_BASELINE_RUNTIME'),
                     'actual R15 and frozen baseline supplied privately')
class ActualR15OldBoardCompatibility(unittest.TestCase):
    def test_every_real_board_keeps_all_asset_fields_and_png_bytes(self):
        job = Path(os.environ['PRK_R15_REVIEW_JOB'])
        state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        baseline = frozen_extractor()
        count = 0
        with tempfile.TemporaryDirectory() as temporary:
            for task in state['tasks'].values():
                if task['role'] != 'board':
                    continue
                with self.subTest(task=task['id']):
                    count += 1
                    board = rebased_board(job, task)
                    actual = job / task['result']['raw']
                    old = baseline(board, actual, Path(temporary) / task['id'])
                    hashes = {asset['id']: file_hash(asset['path']) for asset in old['assets']}
                    new = extract_assets(board, actual, Path(temporary) / task['id'])
                    self.assertEqual(new, old)
                    self.assertEqual({asset['id']: file_hash(asset['path'])
                                      for asset in new['assets']}, hashes)
        self.assertGreater(count, 0)


if __name__ == '__main__':
    unittest.main()
