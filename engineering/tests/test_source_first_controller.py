"""Source-first queue contracts through real local images and ordinary run calls."""
import copy
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from PIL import Image, ImageDraw
from newpath import controller, layout
from newpath.storage import read_state, save_state, sha256, write_json
from test_result_role_binding import ReviewedOutputFixture

SOURCE_POLICY = 'source-layout-first/1'
BUDGET_POLICY = 'source-pixel-budget/1'


class SourceFirstController(ReviewedOutputFixture):
    def start_source_first(self, *, recovery=False, sampling=None):
        self.assertIn('source_layout_policy', inspect.signature(controller.run).parameters)
        options = {
            'execution_policy': 'explicit-start/1',
            'post_return_prompt_policy': 'audit-existing-return/1',
            'delivery_policy': 'reviewed-existing-output/1',
            'source_layout_policy': SOURCE_POLICY,
            'board_sampling_policy': sampling,
        }
        if recovery:
            options['recovery_policy'] = 'bounded-recovery/1'
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source, **options)

    @staticmethod
    def description(*, art=False, regions=True):
        items = [{'id': 'body', 'kind': 'text', 'box': [100, 720, 600, 40],
                  'text': 'Source wording.', 'size': 24}]
        if art:
            items.append({'id': 'content', 'kind': 'art', 'box': [100, 100, 500, 500],
                          'description': 'Independent blue square.', 'embedded_image': True})
        return {'items': items, 'background_regions': [
            {'box': [1200, 0, 720, 300], 'description': 'Continuous corner landscape.'}
        ] if regions else []}

    def freeze_description(self, *, art=False, regions=True, **options):
        action = self.start_source_first(**options)
        self.assertEqual(action['kind'], 'describe')
        layers = controller.run(self.work, data={
            'kind': 'layout', 'layout': self.description(art=art, regions=regions)})
        self.assertEqual(layers['kind'], 'generate')
        self.jobs = {task['role']: task for task in layers['jobs']}
        return layers

    def record_layers(self):
        for role, path in [('background', self.bg), ('foreground', self.fg)]:
            if role not in self.jobs:
                continue
            self.begin(self.jobs[role])
            response = self.submit(self.jobs[role], path)
        return response

    def test_source_is_described_before_any_image_task_exists(self):
        action = self.start_source_first()
        self.assertEqual(action['kind'], 'describe')
        self.assertEqual(action['reply_template'], {
            'kind': 'layout', 'layout': {'items': [], 'background_regions': []}})
        state = read_state(self.work)
        self.assertEqual(state['phase'], 'describe')
        self.assertEqual(state['tasks'], {})
        self.assertEqual(action['files'], [str(self.work / 'input/working.png'), str(self.work / 'input/ruler.png')])
        self.assertEqual(controller.run(self.work), action)

    def test_source_description_freezes_one_allocation_in_both_layer_tasks(self):
        self.freeze_description(art=True, sampling=BUDGET_POLICY)
        state = read_state(self.work)
        expected = {
            'background_regions': [{'id': 'bg001', 'box': [1200, 0, 720, 300],
                                    'description': 'Continuous corner landscape.'}],
            'foreground_regions': [{'id': 'content', 'box': [100, 100, 500, 500],
                                    'description': 'Independent blue square.',
                                    'presentation': 'rectangle', 'embedded_image': True}],
            'source_pixel_sha256': state['source']['pixel_sha256'],
        }
        self.assertEqual(state['scene_allocation'], expected)
        self.assertEqual(state['layout_source_sha256'], state['source']['working_sha256'])
        self.assertEqual(len(state['tasks']), 2)
        for task in state['tasks'].values():
            self.assertEqual(task['scene_allocation'], expected)
            self.assertIn('Continuous corner landscape.', (self.work / task['brief']).read_text('utf-8'))
        repeated = controller.run(self.work, data={'kind': 'layout', 'layout': self.description(art=True)})
        self.assertNotEqual(repeated['kind'], 'describe')
        self.assertEqual(len(read_state(self.work)['tasks']), 2)

    def test_empty_regions_and_no_art_generate_background_without_unused_foreground(self):
        self.freeze_description(regions=False)
        self.assertEqual(set(self.jobs), {'background'})
        response = self.record_layers()
        self.assertEqual(response['kind'], 'done')
        state = read_state(self.work)
        self.assertEqual(state['scene_allocation']['background_regions'], [])
        self.assertEqual(len(state['tasks']), 1)
        with ZipFile(response['pptx']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn(b'Source wording.', archive.read('ppt/slides/slide1.xml'))

    def test_frozen_art_goes_from_layers_to_board_without_second_description(self):
        self.freeze_description(art=True, sampling=BUDGET_POLICY)
        response = self.record_layers()
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual([task['role'] for task in response['jobs']], ['board'])
        task = response['jobs'][0]
        saved = read_state(self.work)['tasks'][task['id']]
        output = self.root / 'board-output.png'
        image = Image.new('RGBA', tuple(task['size']), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for member in saved['board']['members']:
            x, y, width, height = member['slot']
            draw.rectangle((x + 40, y + 40, x + width - 40, y + height - 40), fill='red')
        image.save(output)
        self.begin(task)
        done = self.submit(task, output)
        self.assertEqual(done['kind'], 'done')
        self.assertEqual(len(read_state(self.work)['assets']), 1)
        self.assertEqual(len(read_state(self.work)['tasks']), 3)

    def test_only_primary_wordart_uses_background_and_source_without_foreground(self):
        self.start_source_first()
        title = {'id': 'title', 'kind': 'art', 'box': [100, 20, 1000, 150],
                 'description': 'Independent red title.', 'lettering': 'TITLE'}
        response = controller.run(self.work, data={
            'kind': 'layout', 'layout': {'items': [title], 'background_regions': []}})
        self.jobs = {task['role']: task for task in response['jobs']}
        self.assertEqual(set(self.jobs), {'background'})
        response = self.record_layers()
        self.assertEqual([task['role'] for task in response['jobs']], ['wordart'])
        task = response['jobs'][0]
        source_detail = read_state(self.work)['tasks'][task['id']]['source_detail']
        self.assertEqual(task['references'], [str(self.work / source_detail['path']),
                                               str(self.work / 'input/working.png')])
        from PIL import ImageFont
        letters = Image.new('RGBA', (80, 20), (0, 0, 0, 0))
        ImageDraw.Draw(letters).text((3, 3), 'TITLE', fill='red', font=ImageFont.load_default())
        image = Image.new('RGBA', (1536, 1024), (0, 0, 0, 0))
        image.alpha_composite(letters.resize((800, 200), Image.Resampling.NEAREST), (350, 400))
        output = self.root / 'title-output.png'
        image.save(output)
        self.begin(task)
        done = self.submit(task, output)
        self.assertEqual(done['kind'], 'done')
        self.assertEqual({task['role'] for task in read_state(self.work)['tasks'].values()}, {'background', 'wordart'})

    def test_native_preflight_correction_keeps_source_first_and_creates_no_tasks(self):
        self.start_source_first(recovery=True)
        before = read_state(self.work)
        bad = {'items': [{'id': 'line', 'kind': 'text', 'box': [100, 100, 20, 48],
                          'text': '普通正文保持可编辑', 'size': 32}], 'background_regions': []}
        correction = controller.run(self.work, data={'kind': 'layout', 'layout': bad})
        self.assertEqual(correction['kind'], 'describe')
        self.assertEqual(correction['reason'], 'native_text_requires_correction')
        self.assertIn('background_regions', correction['reply_template']['layout'])
        self.assertEqual(read_state(self.work), before)
        self.assertFalse((self.work / 'layout.json').exists())

    def test_source_first_preflight_requires_no_recovery_or_execution_policy(self):
        with mock.patch.object(controller, '_runtime'):
            controller.run(self.work, source=self.source, source_layout_policy=SOURCE_POLICY)
        before = read_state(self.work)
        bad = {'items': [{'id': 'line', 'kind': 'text', 'box': [100, 100, 20, 48],
                          'text': '普通正文保持可编辑', 'size': 32}], 'background_regions': []}
        correction = controller.run(self.work, data={'kind': 'layout', 'layout': bad})
        self.assertEqual(correction['kind'], 'describe')
        self.assertEqual(correction['reason'], 'native_text_requires_correction')
        self.assertEqual(read_state(self.work), before)
        self.assertFalse((self.work / 'layout.json').exists())

    def test_new_policy_is_frozen_in_initialization_and_saved_state(self):
        self.start_source_first(sampling=BUDGET_POLICY)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        state = read_state(self.work)
        for key, expected in [('source_layout_policy', SOURCE_POLICY), ('board_sampling_policy', BUDGET_POLICY)]:
            self.assertEqual(marker[key], expected)
            self.assertEqual(state[key], expected)
        state['source_layout_policy'] = None
        save_state(self.work, state, 'fixture_corrupt_source_policy')
        with self.assertRaisesRegex(ValueError, 'source.layout|source-layout'):
            controller.run(self.work)

    def test_interrupted_initialization_resumes_frozen_source_first_strategy(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture stop')):
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                self.start_source_first(sampling=BUDGET_POLICY)
        action = controller.run(self.work)
        self.assertEqual(action['kind'], 'describe')
        self.assertEqual(read_state(self.work)['board_sampling_policy'], BUDGET_POLICY)
        self.assertEqual(read_state(self.work)['tasks'], {})

    def test_saved_legacy_job_cannot_be_migrated_by_source_first_argument(self):
        self.assertIn('source_layout_policy', inspect.signature(controller.run).parameters)
        self.start()
        with self.assertRaisesRegex(ValueError, 'source.layout|migrat'):
            controller.run(self.work, source_layout_policy=SOURCE_POLICY)

    def test_changed_source_is_rejected_before_or_after_description(self):
        self.start_source_first()
        other = self.root / 'other.png'
        Image.new('RGB', (160, 90), 'yellow').save(other)
        with self.assertRaisesRegex(ValueError, 'differs'):
            controller.run(self.work, source=other)

    def test_frozen_layout_or_allocation_mutation_is_rejected(self):
        self.freeze_description(art=True)
        state = read_state(self.work)
        altered = copy.deepcopy(state)
        altered['layout']['items'][0]['text'] = 'Changed wording.'
        write_json(self.work / 'layout.json', altered['layout'])
        save_state(self.work, altered, 'fixture_corrupt_layout')
        with self.assertRaisesRegex(ValueError, 'frozen|description|fingerprint'):
            controller.run(self.work)

    def test_layer_allocation_cannot_be_reassigned_after_freeze(self):
        self.freeze_description(art=True)
        state = read_state(self.work)
        next(iter(state['tasks'].values()))['scene_allocation']['foreground_regions'] = []
        save_state(self.work, state, 'fixture_corrupt_allocation')
        with self.assertRaisesRegex(ValueError, 'allocation|frozen'):
            controller.run(self.work)

    def test_false_role_keeps_existing_failure_rules(self):
        self.freeze_description()
        task = self.jobs['background']
        self.begin(task)
        with self.assertRaisesRegex(ValueError, 'role|review|subject'):
            self.submit(task, self.bg, role_review=self.review(task, self.bg, matches_task=False))
        state = read_state(self.work)
        self.assertEqual(state['tasks'][task['id']]['status'], 'failed')
        self.assertEqual(controller.run(self.work)['kind'], 'paused')
        self.assertNotIn('result', state['tasks'][task['id']])

    def test_viewed_false_keeps_one_bounded_repair_and_frozen_allocation(self):
        self.freeze_description(recovery=True)
        task = self.jobs['background']
        self.begin(task)
        response = self.submit(task, self.bg, role_review=self.review(
            task, self.bg, matches_task=False, foreground_text_absent=False,
            observed='An inspected title remained in the synthetic background.'))
        self.assertEqual(response['kind'], 'generate')
        state = read_state(self.work)
        saved = state['tasks'][task['id']]
        self.assertEqual(state['recovery']['used'], 1)
        self.assertEqual(saved['attempt'], 1)
        self.assertEqual(saved['scene_allocation'], state['scene_allocation'])
        self.assertTrue(any(attempt.get('outcome') == 'role_mismatch'
                            and attempt.get('role_review', {}).get('matches_task') is False
                            for attempt in saved['attempts']))
        self.assertNotIn('done', state)

    def test_known_extra_invocation_remains_paused(self):
        self.freeze_description(recovery=True)
        task = self.jobs['background']
        self.begin(task)
        response = self.submit(task, self.bg, invocation_count=2)
        self.assertEqual(response['kind'], 'paused')
        state = read_state(self.work)
        self.assertNotIn('result', state['tasks'][task['id']])
        self.assertNotIn('done', state)

    def test_same_call_multiple_returns_keep_order_and_actual_count(self):
        self.freeze_description(recovery=True)
        task = self.jobs['background']
        self.begin(task)
        extra = self.root / 'extra-background.png'
        Image.new('RGB', (1920, 1080), 'orange').save(extra)
        response = self.submit(task, self.bg, output_count=2,
            returned_files=[str(self.bg), str(extra)], returned_outputs=[
                {'file': str(self.bg), 'role_review': self.review(task, self.bg)},
                {'file': str(extra), 'role_review': self.review(task, extra)}])
        self.assertEqual(response['kind'], 'done')
        state = read_state(self.work)
        saved = state['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'], sha256(self.bg))
        self.assertEqual(saved['surplus_resolution']['selected_index'], 0)
        self.assertEqual(len(saved['surplus_resolution']['returns']), 2)
        self.assertEqual(saved['attempt'], 0)
        self.assertEqual(state['recovery']['used'], 0)

    def test_source_first_and_sampling_policies_reject_unknown_values(self):
        for options in [{'source_layout_policy': 'other'}, {'board_sampling_policy': 'other'}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                controller.run(self.work, source=self.source, **options)
        self.assertFalse(self.work.exists())

    def test_interrupted_legacy_marker_cannot_add_a_source_strategy(self):
        with mock.patch.object(controller, '_runtime', side_effect=RuntimeError('fixture stop')):
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                controller.run(self.work, source=self.source)
        with self.assertRaisesRegex(ValueError, 'source.layout|source-layout'):
            controller.run(self.work, source_layout_policy=SOURCE_POLICY)

    def test_sampling_state_cannot_differ_from_its_marker(self):
        self.start_source_first(sampling=BUDGET_POLICY)
        state = read_state(self.work)
        state.pop('board_sampling_policy')
        save_state(self.work, state, 'fixture_corrupt_sampling_policy')
        with self.assertRaisesRegex(ValueError, 'board sampling'):
            controller.run(self.work)

    def test_budget_policy_can_be_selected_without_source_first(self):
        self.assertIn('board_sampling_policy', inspect.signature(controller.run).parameters)
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work, source=self.source, board_sampling_policy=BUDGET_POLICY)
        self.assertEqual(action['kind'], 'generate')
        self.assertIsNone(read_state(self.work).get('source_layout_policy'))
        self.assertEqual(read_state(self.work)['board_sampling_policy'], BUDGET_POLICY)

    def test_source_only_api_preserves_its_existing_container_assignment(self):
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work, source=self.source, source_layout_policy=SOURCE_POLICY)
        self.assertIsNone(read_state(self.work).get('decorative_background_policy'))
        self.assertNotIn('Static decorative containers remain in the background', action['instructions'])
        dispatch = controller.run(self.work, data={
            'kind': 'layout', 'layout': self.description(art=True, regions=False)})
        foreground = next(task for task in dispatch['jobs'] if task['role'] == 'foreground')
        self.assertIn('paper or card panels', Path(foreground['prompt_file']).read_text('utf-8'))


class SourceFirstLayout(unittest.TestCase):
    def test_background_regions_are_normalized_only_for_selected_strategy(self):
        self.assertIn('source_layout_policy', inspect.signature(layout.normalize_layout).parameters)
        payload = {'items': [], 'background_regions': [
            {'box': [0, 0, 200, 100], 'description': 'Integrated corner scene.'}]}
        self.assertEqual(layout.normalize_layout(payload), {'format': 'newpath.scene/1', 'items': []})
        canonical = layout.normalize_layout(payload, source_layout_policy=SOURCE_POLICY)
        self.assertEqual(canonical['background_regions'], [
            {'id': 'bg001', 'box': [0, 0, 200, 100], 'description': 'Integrated corner scene.'}])
        self.assertEqual(layout.normalize_layout(canonical, source_layout_policy=SOURCE_POLICY), canonical)

    def test_invalid_region_dimensions_or_duplicate_ids_are_rejected(self):
        self.assertIn('source_layout_policy', inspect.signature(layout.normalize_layout).parameters)
        for regions in [None, [{'box': [0, 0, 0, 20], 'description': 'Invalid zero width.'}],
                        [{'id': 'same', 'box': [0, 0, 20, 20], 'description': 'One.'},
                         {'id': 'same', 'box': [20, 0, 20, 20], 'description': 'Two.'}]]:
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                layout.normalize_layout({'items': [], 'background_regions': regions}, source_layout_policy=SOURCE_POLICY)


if __name__ == '__main__':
    unittest.main()
