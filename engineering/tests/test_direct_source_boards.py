"""Direct references use unchanged source pixels and never relax output alpha."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import artwork
from newpath.boardplan import plan_boards, SOURCE_BUDGET_POLICY
from newpath.native import MODE
from newpath.prompts import BOARD_REFERENCE_POLICY

POLICY = 'direct-source-board-reference/1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DirectSourceBoards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-direct-source-board-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.png'
        image = Image.new('RGB', (1920, 1080), (245, 232, 210))
        draw = ImageDraw.Draw(image)
        draw.rectangle([200, 200, 399, 399], fill=(20, 70, 130))
        draw.text([220, 230], 'REAL INTERNAL LOGO', fill='white')
        draw.rectangle([220, 397, 379, 439], fill=(160, 0, 0))
        draw.text([230, 405], 'SEPARATE LABEL', fill='white')
        draw.ellipse([700, 100, 899, 299], fill=(20, 130, 60))
        image.save(self.source)
        self.arts = [
            {'id': 'photo', 'kind': 'art', 'box': [200, 200, 200, 200],
             'description': 'Own rectangular content image.', 'embedded_image': True},
            {'id': 'caption', 'kind': 'art', 'box': [220, 397, 160, 43],
             'description': 'An independent red label.', 'lettering': 'LABEL'},
            {'id': 'icon', 'kind': 'art', 'box': [700, 100, 200, 200],
             'description': 'An independent green circular icon.'},
        ]

    def prepare(self, **options):
        function = getattr(artwork, 'prepare_source_boards', None)
        self.assertTrue(callable(function), 'Direct source references need their own explicit entry')
        return function(self.source, self.arts, self.root / 'boards', **options)

    def test_opaque_original_is_allowed_and_is_explicitly_bound_as_source(self):
        boards = self.prepare(reference_policy=BOARD_REFERENCE_POLICY)
        source_pixels = Image.open(self.source).convert('RGBA')
        for board in boards:
            self.assertEqual(board['source_board_reference_policy'], POLICY)
            self.assertEqual(board['background_mode'], MODE)
            self.assertEqual(board['reference_provenance'], {
                'kind': 'unchanged-working-source', 'source_sha256': sha(self.source),
                'rgba_pixels_sha256': hashlib.sha256(source_pixels.tobytes()).hexdigest(),
                'size': [1920, 1080]})
            self.assertNotIn('generated', board['reference_provenance']['kind'])
            self.assertTrue(np.any(np.asarray(Image.open(board['image']))[..., 3] == 0))

    def test_all_member_source_boxes_are_exact_before_resampling(self):
        before = self.source.read_bytes(), copy.deepcopy(self.arts)
        captured = []
        fromarray = artwork.Image.fromarray

        def observe(value, *arguments, **options):
            captured.append(np.asarray(value).copy())
            return fromarray(value, *arguments, **options)

        with mock.patch.object(artwork.Image, 'fromarray', side_effect=observe):
            boards = self.prepare(reference_policy=BOARD_REFERENCE_POLICY)
        original = np.asarray(Image.open(self.source).convert('RGBA'))
        for plan in plan_boards(self.arts):
            for placement in plan['placements']:
                x0, y0, x1, y1 = placement['crop']
                expected = original[y0:y1, x0:x1].copy()
                x, y, width, height = placement['art']['box']
                keep = np.zeros(expected.shape[:2], dtype=bool)
                keep[y-y0:y+height-y0, x-x0:x+width-x0] = True
                expected[~keep] = 0
                self.assertTrue(any(value.shape == expected.shape and np.array_equal(value, expected)
                                    for value in captured), placement['art']['id'])
        photo = next(member for board in boards for member in board['members'] if member['id'] == 'photo')
        self.assertEqual(photo['context_exclusions'][0]['id'], 'caption')
        self.assertEqual((self.source.read_bytes(), self.arts), before)

    def test_rgba_source_inside_pixels_and_hidden_rgb_remain_exact(self):
        pixels = np.asarray(Image.open(self.source).convert('RGBA')).copy()
        pixels[260:270, 260:270] = [123, 45, 67, 0]
        Image.fromarray(pixels).save(self.source)
        before = self.source.read_bytes()
        self.test_all_member_source_boxes_are_exact_before_resampling()
        self.assertEqual(self.source.read_bytes(), before)

    def test_signature_is_folder_independent_but_binds_entire_source_bytes(self):
        first = self.prepare(sampling_policy=SOURCE_BUDGET_POLICY)
        function = artwork.prepare_source_boards
        second = function(self.source, self.arts, self.root / 'other',
                          sampling_policy=SOURCE_BUDGET_POLICY)
        self.assertEqual([board['signature'] for board in first],
                         [board['signature'] for board in second])
        self.assertEqual([Path(board['image']).read_bytes() for board in first],
                         [Path(board['image']).read_bytes() for board in second])
        pixels = np.asarray(Image.open(self.source)).copy()
        pixels[1000, 1800] = [12, 34, 56]
        Image.fromarray(pixels).save(self.source)
        changed = function(self.source, self.arts, self.root / 'changed',
                           sampling_policy=SOURCE_BUDGET_POLICY)
        self.assertNotEqual(first[0]['signature'], changed[0]['signature'])

    def test_source_file_identity_changes_for_reencoding_without_claiming_pixel_change(self):
        first = self.prepare()
        image = Image.open(self.source).copy()
        image.save(self.source, compress_level=0)
        second = artwork.prepare_source_boards(self.source, self.arts, self.root / 'reencoded')
        self.assertNotEqual(first[0]['reference_provenance']['source_sha256'],
                            second[0]['reference_provenance']['source_sha256'])
        self.assertEqual(first[0]['reference_provenance']['rgba_pixels_sha256'],
                         second[0]['reference_provenance']['rgba_pixels_sha256'])
        self.assertNotEqual(first[0]['signature'], second[0]['signature'])

    def test_bad_source_geometry_or_format_and_unknown_modes_are_rejected(self):
        function = getattr(artwork, 'prepare_source_boards', None)
        self.assertTrue(callable(function))
        for name, size, image_format in [('small.png', (100, 100), 'PNG'),
                                         ('not-png.jpg', (1920, 1080), 'JPEG')]:
            path = self.root / name
            Image.new('RGB', size, 'white').save(path, format=image_format)
            with self.subTest(source=name), self.assertRaises(ValueError):
                function(path, self.arts, self.root / 'bad')
        for options in [{'background_mode': 'unknown'}, {'reference_policy': 'unknown'},
                        {'sampling_policy': 'unknown'}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                function(self.source, self.arts, self.root / 'bad', **options)

    def test_duplicate_ids_keep_existing_failure_and_empty_request_stays_empty(self):
        function = getattr(artwork, 'prepare_source_boards', None)
        self.assertTrue(callable(function))
        with self.assertRaises(ValueError):
            function(self.source, [self.arts[0], self.arts[0]], self.root / 'duplicates')
        self.assertEqual(function(self.source, [], self.root / 'empty'), [])
        with self.assertRaises(ValueError):
            function(self.source, [], self.root / 'empty', sampling_policy='unknown')

    def test_generated_outputs_still_require_true_native_transparency(self):
        board = self.prepare()[0]
        opaque = self.root / 'opaque-generated.png'
        Image.new('RGB', board['size'], 'red').save(opaque)
        with self.assertRaises(ValueError):
            artwork.extract_assets(board, opaque, self.root / 'assets')

    def test_legacy_foreground_reference_all_fields_and_png_remain_r18_exact(self):
        old_path = PACKAGE.parent / 'source-r18/compat-runtime/newpath/artwork.py'
        if not old_path.is_file():
            self.skipTest('Frozen predecessor is private to this checkout')
        spec = importlib.util.spec_from_file_location('newpath._direct_source_r18_artwork', old_path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        foreground = self.root / 'foreground.png'
        pixels = np.asarray(Image.open(self.source).convert('RGBA')).copy()
        pixels[np.all(pixels[..., :3] == [245, 232, 210], axis=2)] = 0
        Image.fromarray(pixels).save(foreground)
        for policy in [None, BOARD_REFERENCE_POLICY]:
            with self.subTest(reference_policy=policy):
                options = {'background_mode': MODE, 'reference_policy': policy}
                out = self.root / ('same-' + str(policy is not None))
                previous = old.prepare_boards(self.source, foreground, self.arts, out, **options)
                pngs = {board['id']: Path(board['image']).read_bytes() for board in previous}
                current = artwork.prepare_boards(self.source, foreground, self.arts, out, **options)
                self.assertEqual(current, previous)
                self.assertEqual({board['id']: Path(board['image']).read_bytes() for board in current}, pngs)

    def test_real_r18_source_references_need_no_foreground_and_keep_original_job_hashes(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r18-medium-done/job'
        if not job.exists():
            self.skipTest('Actual R18 source fixture supplied privately')
        before = {str(path.relative_to(job)): sha(path) for path in job.rglob('*') if path.is_file()}
        state = json.loads((job / 'state.json').read_text('utf-8'))
        arts = [item for item in state['layout']['items'] if item['kind'] == 'art' and item['id'] != 'o001']
        function = getattr(artwork, 'prepare_source_boards', None)
        self.assertTrue(callable(function))
        boards = function(job / 'input/working.png', arts, self.root / 'real',
                          sampling_policy=state['board_sampling_policy'],
                          reference_policy=BOARD_REFERENCE_POLICY)
        self.assertEqual(sum(len(board['members']) for board in boards), 24)
        self.assertTrue(all(board['source_board_reference_policy'] == POLICY for board in boards))
        self.assertEqual(before, {str(path.relative_to(job)): sha(path)
                                  for path in job.rglob('*') if path.is_file()})

    def test_legacy_opaque_matte_reference_is_r18_exact_without_reusing_cached_png(self):
        old_path = PACKAGE.parent / 'source-r18/compat-runtime/newpath/artwork.py'
        if not old_path.is_file():
            self.skipTest('Frozen predecessor is private to this checkout')
        spec = importlib.util.spec_from_file_location('newpath._direct_source_r18_matte', old_path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        foreground = self.root / 'opaque-foreground.png'
        image = Image.new('RGB', (1920, 1080), 'magenta')
        draw = ImageDraw.Draw(image)
        draw.rectangle([200, 200, 399, 399], fill='blue')
        draw.rectangle([220, 397, 379, 439], fill='red')
        draw.ellipse([700, 100, 899, 299], fill='green')
        image.save(foreground)
        previous = old.prepare_boards(self.source, foreground, self.arts, self.root / 'old-matte')
        current = artwork.prepare_boards(self.source, foreground, self.arts, self.root / 'new-matte')
        self.assertEqual(len(previous), len(current))
        for before, after in zip(previous, current):
            self.assertEqual(Path(before['image']).read_bytes(), Path(after['image']).read_bytes())
            after = {**after, 'image': before['image']}
            self.assertEqual(after, before)

    def test_real_r18_foreground_references_keep_all_old_fields_and_fresh_png_bytes(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r18-medium-done/job'
        old_path = PACKAGE.parent / 'source-r18/compat-runtime/newpath/artwork.py'
        if not job.exists() or not old_path.is_file():
            self.skipTest('Actual R18 source and predecessor supplied privately')
        spec = importlib.util.spec_from_file_location('newpath._direct_source_r18_real', old_path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        state = json.loads((job / 'state.json').read_text('utf-8'))
        arts = [item for item in state['layout']['items'] if item['kind'] == 'art' and item['id'] != 'o001']
        options = {'background_mode': MODE, 'sampling_policy': state['board_sampling_policy'],
                   'reference_policy': BOARD_REFERENCE_POLICY}
        previous = old.prepare_boards(job / 'input/working.png', job / 'layers/foreground.png',
                                      arts, self.root / 'old-real', **options)
        current = artwork.prepare_boards(job / 'input/working.png', job / 'layers/foreground.png',
                                         arts, self.root / 'new-real', **options)
        self.assertEqual(len(previous), len(current))
        for before, after in zip(previous, current):
            self.assertEqual(Path(before['image']).read_bytes(), Path(after['image']).read_bytes())
            after = {**after, 'image': before['image']}
            self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
