"""Source pixels, reversible local references and describe-only envelope adaptation."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
import numpy as np
from PIL import Image
from newpath import controller, source
from newpath.storage import read_state, sha256
from test_result_role_binding import ReviewedOutputFixture

POLICY = 'source-layout-first/1'


class SourceReadingPixels(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-source-reading-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'
        self.work.mkdir()
        yy, xx = np.indices((180, 320))
        pixels = np.stack((xx % 256, yy % 256, (xx + yy) % 256), axis=2).astype('uint8')
        self.original = self.root / 'source.png'
        Image.fromarray(pixels).save(self.original)
        self.prepared = source.prepare_source(self.original, self.work)

    def test_four_reading_tiles_cover_working_with_reversible_nearest_pixels(self):
        self.assertTrue(hasattr(source, 'prepare_reading_images'))
        descriptors = source.prepare_reading_images(self.work, self.prepared)
        expected_boxes = [[0, 0, 1056, 594], [864, 0, 1920, 594],
                          [0, 486, 1056, 1080], [864, 486, 1920, 1080]]
        self.assertEqual([item['working_box'] for item in descriptors], expected_boxes)
        working = np.asarray(Image.open(self.work / self.prepared['working']).convert('RGB'))
        coverage = np.zeros((1080, 1920), dtype=int)
        for item, box in zip(descriptors, expected_boxes):
            self.assertEqual(item['zoom'], 2)
            self.assertEqual(item['source_sha256'], self.prepared['working_sha256'])
            path = self.work / item['path']
            self.assertTrue(path.resolve().is_relative_to((self.work / 'input').resolve()))
            self.assertEqual(sha256(path), item['sha256'])
            left, top, right, bottom = box
            expected = working[top:bottom, left:right].repeat(2, axis=0).repeat(2, axis=1)
            self.assertTrue(np.array_equal(np.asarray(Image.open(path)), expected))
            coverage[top:bottom, left:right] += 1
        self.assertTrue(np.all(coverage >= 1))
        self.assertEqual(int(coverage[540, 960]), 4)
        self.assertEqual(source.prepare_reading_images(self.work, self.prepared), descriptors)

    def test_reading_tile_with_different_pixels_is_preserved_and_rejected(self):
        self.assertTrue(hasattr(source, 'prepare_reading_images'))
        descriptors = source.prepare_reading_images(self.work, self.prepared)
        path = self.work / descriptors[0]['path']
        Image.new('RGB', (2112, 1188), 'red').save(path)
        digest = sha256(path)
        with self.assertRaisesRegex(ValueError, 'source|crop|reading|saved'):
            source.prepare_reading_images(self.work, self.prepared)
        self.assertEqual(sha256(path), digest)

    def test_wordart_detail_uses_source_box_context_larger_than36_and_clips_page(self):
        self.assertTrue(hasattr(source, 'prepare_wordart_source_crop'))
        member = {'id': 'title', 'box': [100, 200, 900, 100]}
        detail = source.prepare_wordart_source_crop(self.work, self.prepared, member)
        self.assertEqual(detail['member_box'], [100, 200, 900, 100])
        self.assertEqual(detail['crop_box'], [10, 110, 1090, 390])
        self.assertEqual(detail['zoom'], 1)
        self.assertEqual(detail['source_sha256'], self.prepared['working_sha256'])
        working = Image.open(self.work / self.prepared['working'])
        self.assertEqual(Image.open(self.work / detail['path']).tobytes(), working.crop((10, 110, 1090, 390)).tobytes())
        edge = source.prepare_wordart_source_crop(self.work, self.prepared, {'box': [1850, 1000, 100, 80]})
        self.assertEqual(edge['crop_box'], [1834, 984, 1920, 1080])
        self.assertEqual(source.prepare_wordart_source_crop(self.work, self.prepared, member), detail)

    def test_existing_detail_with_same_pixels_can_be_reencoded_but_never_overwritten(self):
        self.assertTrue(hasattr(source, 'prepare_wordart_source_crop'))
        member = {'box': [100.3, 200.7, 90.5, 40.2]}
        first = source.prepare_wordart_source_crop(self.work, self.prepared, member)
        self.assertEqual(first['crop_box'], [84, 184, 207, 257])
        path = self.work / first['path']
        image = Image.open(path).copy()
        image.save(path, compress_level=0)
        encoded = path.read_bytes()
        second = source.prepare_wordart_source_crop(self.work, self.prepared, member)
        self.assertEqual(second['sha256'], sha256(path))
        self.assertEqual(path.read_bytes(), encoded)
        Image.new('RGB', image.size, 'black').save(path)
        altered = path.read_bytes()
        with self.assertRaises(ValueError):
            source.prepare_wordart_source_crop(self.work, self.prepared, member)
        self.assertEqual(path.read_bytes(), altered)

    def test_changed_working_source_cannot_create_unbound_crop(self):
        self.assertTrue(hasattr(source, 'prepare_reading_images'))
        Image.new('RGB', (1920, 1080), 'black').save(self.work / self.prepared['working'])
        with self.assertRaisesRegex(ValueError, 'source|working'):
            source.prepare_reading_images(self.work, self.prepared)

    def test_input_directory_link_cannot_write_crops_outside_work(self):
        local = self.work / 'working-local.png'
        shutil.copyfile(self.work / self.prepared['working'], local)
        self.prepared['working'] = 'working-local.png'
        (self.work / 'input').rename(self.work / 'input-preserved')
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel.png'
        Image.new('RGB', (30, 20), 'purple').save(sentinel)
        digest = sha256(sentinel)
        if os.name == 'nt':
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.work / 'input'), str(outside)],
                                     capture_output=True)
            if created.returncode:
                self.skipTest('junction creation unavailable')
        else:
            (self.work / 'input').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            source.prepare_reading_images(self.work, self.prepared)
        self.assertEqual({path.name for path in outside.iterdir()}, {'sentinel.png'})
        self.assertEqual(sha256(sentinel), digest)


class DescribeInputAdapters(ReviewedOutputFixture):
    def source_first(self):
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                source_layout_policy=POLICY)

    @staticmethod
    def bare_layout():
        return {'format': 'newpath.scene/1', 'styles': {'body': {'size': 20}},
                'items': [{'kind': 'text', 'id': 'line', 'style': 'body',
                           'box': [100, 700, 500, 36], 'text': 'Original wording.'}],
                'background_regions': []}

    def test_bare_layout_is_wrapped_only_at_describe_and_normalized_once(self):
        self.source_first()
        bare = self.bare_layout()
        snapshot = copy.deepcopy(bare)
        try:
            response = controller.run(self.work, data=bare)
        except ValueError as error:
            self.fail('A valid bare scene at describe should use the layout route: ' + str(error))
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(bare, snapshot)
        state = read_state(self.work)
        self.assertEqual(state['layout']['items'][0]['size'], 20)
        self.assertEqual(state['layout']['background_regions'], [])
        before = (self.work / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'exact task|role'):
            controller.run(self.work, data=bare)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_unknown_kind_or_nonlayout_extra_key_is_not_reinterpreted(self):
        self.source_first()
        before = (self.work / 'state.json').read_bytes()
        for extra in [{'kind': 'unknown'}, {'role': 'unknown'}, {'unrelated': True}, {'layout': {}}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                controller.run(self.work, data={**self.bare_layout(), **extra})
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_reading_tiles_are_absolute_in_action_and_relative_in_saved_source(self):
        action = self.source_first()
        self.assertIn('reading_images', action)
        saved = read_state(self.work)['source']['reading_images']
        self.assertEqual(len(saved), 4)
        for descriptor, public in zip(saved, action['reading_images']):
            self.assertFalse(Path(descriptor['path']).is_absolute())
            self.assertEqual(public, {**descriptor, 'path': str(self.work / descriptor['path'])})
        action['reading_images'][0]['working_box'][0] = -999
        self.assertEqual(read_state(self.work)['source']['reading_images'][0]['working_box'][0], 0)

    def test_reading_tile_change_is_detected_before_source_description_resumes(self):
        self.source_first()
        current = read_state(self.work)['source']
        self.assertIn('reading_images', current)
        saved = current['reading_images']
        Image.new('RGB', (2112, 1188), 'orange').save(self.work / saved[0]['path'])
        with self.assertRaisesRegex(ValueError, 'source|reading'):
            controller.run(self.work)

    def test_primary_task_binds_real_crop_then_full_source(self):
        self.source_first()
        title = {'id': 'title', 'kind': 'art', 'box': [100, 20, 1000, 150],
                 'description': 'Red source title with a complete brush backing.', 'lettering': 'TITLE'}
        response = controller.run(self.work, data={'kind': 'layout', 'layout': {'items': [title], 'background_regions': []}})
        background = response['jobs'][0]
        self.begin(background)
        response = self.submit(background, self.bg)
        task = response['jobs'][0]
        self.assertEqual(task['role'], 'wordart')
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertIn('source_detail', saved)
        detail = saved['source_detail']
        self.assertEqual(task['references'], [str(self.work / detail['path']), str(self.work / 'input/working.png')])
        self.assertEqual(detail['crop_box'], [0, 0, 1200, 270])
        self.assertEqual(detail['sha256'], sha256(task['references'][0]))
        self.assertEqual(task['begin_template']['request']['references'], task['references'])

    def test_old_default_start_has_no_reading_files_or_new_source_metadata(self):
        self.start()
        state = read_state(self.work)
        self.assertNotIn('reading_images', state['source'])
        self.assertEqual({path.name for path in (self.work / 'input').iterdir()}, {'original.png', 'working.png', 'ruler.png'})


class RealR15SourceDetail(unittest.TestCase):
    def test_actual_source_crop_keeps_old_missed_brush_tail_without_changing_job(self):
        configured = os.environ.get('PRK_R15_REVIEW_JOB')
        if not configured:
            self.skipTest('Set PRK_R15_REVIEW_JOB to the preserved real R15 diagnostic job.')
        job = Path(configured)
        before = {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()}
        with tempfile.TemporaryDirectory(prefix='prk-real-source-detail-') as temporary:
            work = Path(temporary) / 'job'
            (work / 'input').mkdir(parents=True)
            shutil.copyfile(job / 'input/working.png', work / 'input/working.png')
            prepared = {'working': 'input/working.png',
                        'working_sha256': sha256(job / 'input/working.png')}
            detail = source.prepare_wordart_source_crop(work, prepared, {'box': [118, 10, 945, 96]})
            self.assertEqual(detail['crop_box'], [23, 0, 1158, 201])
            self.assertGreaterEqual(detail['crop_box'][3], 153)
            original_pixels = Image.open(job / 'input/working.png')
            self.assertEqual(Image.open(work / detail['path']).tobytes(), original_pixels.crop((23, 0, 1158, 201)).tobytes())
        after = {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
