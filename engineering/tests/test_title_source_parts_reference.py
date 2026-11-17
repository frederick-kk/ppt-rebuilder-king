"""A title reference isolates declared parts without rewriting source pixels."""
import copy
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from newpath import source
from newpath.storage import sha256


POLICY = 'source-parts-title-reference/1'


class TitleSourcePartsReference(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-title-source-parts-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'input').mkdir()
        yy, xx = np.indices((1080, 1920))
        pixels = np.stack((xx % 256, yy % 256, (xx + yy) % 256,
                           (xx * 3 + yy) % 256), axis=2).astype('uint8')
        self.working = Image.fromarray(pixels)
        path = self.work / 'input/working.png'
        self.working.save(path)
        self.prepared = {'working': 'input/working.png', 'working_sha256': sha256(path)}
        self.member = {
            'id': 'main-title', 'box': [100, 200, 900, 100],
            'source_parts': [
                {'name': 'writing', 'bounds': [100, 200, 300, 250]},
                {'name': 'detached-brush-tail', 'bounds': [980, 285, 1000, 300]},
            ],
        }

    def isolated(self, member=None, policy=POLICY):
        self.assertIn('reference_policy', inspect.signature(
            source.prepare_wordart_source_crop).parameters,
            'The title source helper still exposes only an unisolated rectangle.')
        return source.prepare_wordart_source_crop(
            self.work, self.prepared, member or self.member, reference_policy=policy)

    def pixels(self, detail):
        return np.asarray(Image.open(self.work / detail['path']).convert('RGBA'))

    def test_parts_union_retains_every_rgba_including_detached_tail_and_half_alpha(self):
        before = copy.deepcopy(self.member)
        detail = self.isolated()
        expected = np.zeros((280, 1080, 4), dtype=np.uint8)
        working = np.asarray(self.working)
        expected[90:140, 90:290] = working[200:250, 100:300]
        expected[175:190, 970:990] = working[285:300, 980:1000]
        actual = self.pixels(detail)
        self.assertTrue(np.array_equal(actual, expected))
        self.assertTrue(np.any(actual[..., 3] == 128))
        self.assertTrue(np.any((actual[..., 3] == 0) & np.any(actual[..., :3] != 0, axis=2)))
        self.assertEqual(detail['crop_box'], [10, 110, 1090, 390])
        self.assertEqual(detail['member_box'], self.member['box'])
        self.assertEqual(detail['source_parts'], self.member['source_parts'])
        self.assertEqual(detail['reference_policy'], POLICY)
        self.assertEqual(detail['zoom'], 1)
        self.assertEqual(detail['source_sha256'], self.prepared['working_sha256'])
        self.assertEqual(detail['reference_provenance'], {
            'kind': 'masked-working-source', 'retained_rgba_unchanged': True,
            'outside_parts_rgba_zero': True, 'generated': False,
        })
        self.assertEqual(self.member, before)

    def test_no_parts_uses_member_box_with_transparent_context(self):
        detail = self.isolated({'box': [100, 200, 900, 100]})
        actual = self.pixels(detail)
        expected = np.zeros((280, 1080, 4), dtype=np.uint8)
        expected[90:190, 90:990] = np.asarray(self.working)[200:300, 100:1000]
        self.assertTrue(np.array_equal(actual, expected))
        self.assertEqual(detail['source_parts'], [
            {'name': 'member-box', 'bounds': [100, 200, 1000, 300]}])

    def test_fractional_parts_keep_all_intersecting_source_pixels(self):
        member = {'box': [100.2, 200.7, 4.9, 4.5],
                  'source_parts': [{'bounds': [100.2, 200.7, 105.1, 205.2]}]}
        detail = self.isolated(member)
        expected = np.zeros((38, 38, 4), dtype=np.uint8)
        expected[16:22, 16:22] = np.asarray(self.working)[200:206, 100:106]
        self.assertEqual(detail['crop_box'], [84, 184, 122, 222])
        self.assertTrue(np.array_equal(self.pixels(detail), expected))

    def test_overlapping_parts_and_order_keep_the_same_source_union(self):
        member = {'box': [100, 200, 100, 60], 'source_parts': [
            {'bounds': [100, 200, 170, 240]},
            {'bounds': [150, 220, 200, 260]},
        ]}
        first = self.isolated(member)
        reversed_member = {**member, 'source_parts': list(reversed(member['source_parts']))}
        second = self.isolated(reversed_member)
        self.assertTrue(np.array_equal(self.pixels(first), self.pixels(second)))
        expected = np.zeros((92, 132, 4), dtype=np.uint8)
        expected[16:56, 16:86] = np.asarray(self.working)[200:240, 100:170]
        expected[36:76, 66:116] = np.asarray(self.working)[220:260, 150:200]
        self.assertTrue(np.array_equal(self.pixels(first), expected))

    def test_default_and_explicit_none_keep_legacy_fields_pixels_and_encoding(self):
        first = source.prepare_wordart_source_crop(self.work, self.prepared, self.member)
        expected_image = self.working.crop((10, 110, 1090, 390))
        encoded = io.BytesIO()
        expected_image.save(encoded, format='PNG')
        self.assertEqual((self.work / first['path']).read_bytes(), encoded.getvalue())
        self.assertEqual(set(first), {'path', 'sha256', 'crop_box', 'member_box', 'zoom', 'source_sha256'})
        self.assertEqual(first, self.isolated(policy=None))
        old_bytes = (self.work / first['path']).read_bytes()
        isolated = self.isolated()
        self.assertNotEqual(isolated['path'], first['path'])
        self.assertEqual((self.work / first['path']).read_bytes(), old_bytes)

    def test_same_input_is_deterministic_and_changed_parts_have_distinct_identity(self):
        first = self.isolated()
        original_bytes = (self.work / first['path']).read_bytes()
        self.assertEqual(self.isolated(), first)
        second = self.isolated({**self.member, 'source_parts': self.member['source_parts'][:1]})
        self.assertNotEqual(first['path'], second['path'])
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual((self.work / first['path']).read_bytes(), original_bytes)

    def test_changed_working_source_is_rejected_without_rewriting_reference(self):
        detail = self.isolated()
        encoded = (self.work / detail['path']).read_bytes()
        Image.new('RGBA', (1920, 1080), 'purple').save(self.work / self.prepared['working'])
        with self.assertRaisesRegex(ValueError, 'working source changed'):
            self.isolated()
        self.assertEqual((self.work / detail['path']).read_bytes(), encoded)

    def test_changed_saved_mask_is_rejected_and_preserved(self):
        detail = self.isolated()
        path = self.work / detail['path']
        Image.new('RGBA', (1080, 280), 'orange').save(path)
        encoded = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'different pixels'):
            self.isolated()
        self.assertEqual(path.read_bytes(), encoded)

    def test_unknown_policy_is_rejected_without_creating_a_reference(self):
        before = {p.name for p in (self.work / 'input').iterdir()}
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.isolated(policy='unknown-isolation-policy')
        self.assertEqual({p.name for p in (self.work / 'input').iterdir()}, before)

    def test_malformed_parts_are_rejected_without_guessing_a_mask(self):
        for parts in ([], None, [{'bounds': [1, 2, 1, 9]}], [{'bounds': [1, 2, 2001, 9]}]):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                self.isolated({**self.member, 'source_parts': parts})


@unittest.skipUnless(os.environ.get('PRK_R19_BOOTSTRAP_REVIEW_JOB'), 'private actual source fixture not configured')
class ActualBootstrapSourceReference(unittest.TestCase):
    def test_real_neighbor_titles_are_excluded_and_declared_parts_keep_source_rgba(self):
        self.assertIn('reference_policy', inspect.signature(source.prepare_wordart_source_crop).parameters,
                      'The actual title reference still retains surrounding subtitles and chapter labels.')
        job = Path(os.environ['PRK_R19_BOOTSTRAP_REVIEW_JOB'])
        before = {str(p.relative_to(job)): sha256(p) for p in job.rglob('*') if p.is_file()}
        state = json.loads((job / 'state.json').read_text('utf-8'))
        task = next(t for t in state['tasks'].values() if t['role'] == 'wordart')
        with tempfile.TemporaryDirectory(prefix='prk-real-title-source-') as name:
            work = Path(name)
            (work / 'input').mkdir()
            shutil.copyfile(job / state['source']['working'], work / 'input/working.png')
            prepared = {**state['source'], 'working': 'input/working.png'}
            old = source.prepare_wordart_source_crop(work, prepared, task['member'])
            self.assertEqual({k: v for k, v in old.items() if k != 'sha256'},
                             {k: v for k, v in task['source_detail'].items() if k != 'sha256'})
            self.assertTrue(np.array_equal(
                np.asarray(Image.open(work / old['path']).convert('RGBA')),
                np.asarray(Image.open(job / task['source_detail']['path']).convert('RGBA'))))
            isolated = source.prepare_wordart_source_crop(
                work, prepared, task['member'], reference_policy=POLICY)
            self.assertEqual(isolated['crop_box'], [4, 0, 1159, 246])
            actual = np.asarray(Image.open(work / isolated['path']).convert('RGBA'))
            working = np.asarray(Image.open(work / 'input/working.png').convert('RGBA'))
            expected = np.zeros((246, 1155, 4), dtype=np.uint8)
            expected[22:116, 164:1058] = working[22:116, 168:1062]
            expected[5:149, 97:274] = working[5:149, 101:278]
            self.assertTrue(np.array_equal(actual, expected))
            self.assertTrue(np.all(actual[181:239] == 0))
        self.assertEqual(before, {str(p.relative_to(job)): sha256(p) for p in job.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
