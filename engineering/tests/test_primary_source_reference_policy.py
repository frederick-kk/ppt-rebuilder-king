"""A source-first title may use one explicitly isolated real-source reference."""
import copy
from datetime import datetime, timezone
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, execution, prompts
from newpath.storage import read_state, save_state, sha256, write_json
from test_result_role_binding import ReviewedOutputFixture

POLICY = 'source-parts-title-reference/1'


def title_layout():
    return {'items': [{'id': 'title', 'kind': 'art', 'box': [90, 10, 850, 170],
        'source_parts': [{'name': 'writing', 'bounds': [120, 20, 940, 130]},
                         {'name': 'own arc', 'bounds': [90, 10, 230, 180]}],
        'lettering': '完整标题甲乙', 'description': 'One red and black title with its original thin white outline and red arc.'}],
        'background_regions': []}


class PrimarySourceReferenceController(ReviewedOutputFixture):
    def initialize(self, *, policy=POLICY, direct=True):
        self.assertIn('primary_source_reference_policy', inspect.signature(controller.run).parameters)
        options = {'primary_source_reference_policy': policy} if policy is not None else {}
        if direct:
            options['source_board_reference_policy'] = 'direct-source-board-reference/1'
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', post_return_prompt_policy='audit-existing-return/1',
                delivery_policy='reviewed-existing-output/1', source_layout_policy='source-layout-first/1',
                **options)

    def plan_primary(self):
        layers = controller.run(self.work, data={'kind': 'layout', 'layout': title_layout()})
        self.assertEqual([task['role'] for task in layers['jobs']], ['background'])
        self.begin(layers['jobs'][0])
        return self.submit(layers['jobs'][0], self.bg)

    def test_policy_freezes_marker_state_and_resume_without_new_fields(self):
        action = self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker['primary_source_reference_policy'], POLICY)
        self.assertEqual(read_state(self.work)['primary_source_reference_policy'], POLICY)
        self.assertEqual(action['primary_source_reference_policy'], POLICY)
        self.assertIn('single source-parts reference', action['instructions'])
        self.assertEqual(controller.run(self.work), action)

    def test_new_primary_has_one_reference_and_bound_source_parts_descriptor(self):
        self.initialize()
        action = self.plan_primary()
        public = action['jobs'][0]
        self.assertEqual(public['role'], 'wordart')
        state = read_state(self.work)
        task = state['tasks'][public['id']]
        detail = task['source_detail']
        self.assertEqual(detail['reference_policy'], POLICY)
        self.assertEqual(list(task['references']), [detail['path']])
        self.assertEqual(public['references'], [str(self.work / detail['path'])])
        self.assertEqual(public['primary_source_reference_policy'], POLICY)
        self.assertEqual(public['source_detail'], detail)
        self.assertEqual(task['references'][detail['path']], detail['sha256'])
        self.assertEqual(detail['source_sha256'], state['source']['working_sha256'])
        self.assertNotIn(str(self.work / 'input/working.png'), public['references'])
        permit = self.begin(public)
        self.assertEqual(permit['request']['references'], public['references'])
        self.assertIn('唯一参考', permit['instructions'])
        self.assertNotIn('第二图', permit['instructions'])
        self.assertIsNone(permit['result_template'].get('executed_prompt'))

    def test_none_keeps_old_crop_full_references_and_omits_policy_fields(self):
        action = self.initialize(policy=None)
        self.assertNotIn('primary_source_reference_policy', action)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertNotIn('primary_source_reference_policy', marker)
        self.assertNotIn('primary_source_reference_policy', read_state(self.work))
        action = self.plan_primary()
        task = read_state(self.work)['tasks'][action['jobs'][0]['id']]
        self.assertEqual(list(task['references']), [task['source_detail']['path'], 'input/working.png'])
        self.assertNotIn('reference_policy', task['source_detail'])
        legacy = copy.deepcopy(task)
        legacy['source_detail'] = None
        public = controller._public_task(self.work, legacy)
        self.assertNotIn('primary_source_reference_policy', public)
        self.assertNotIn('source_detail', public)
        with self.assertRaisesRegex(ValueError, 'primary.source|migrat'):
            controller.run(self.work, primary_source_reference_policy=POLICY)

    def test_unknown_policy_or_missing_source_first_does_not_create_directory(self):
        self.assertIn('primary_source_reference_policy', inspect.signature(controller.run).parameters)
        for options in ({'primary_source_reference_policy': 'other', 'source_layout_policy': 'source-layout-first/1'},
                        {'primary_source_reference_policy': POLICY}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'primary.source|source.first|title.reference'):
                controller.run(self.work, source=self.source, **options)
        self.assertFalse(self.work.exists())

    def test_title_reference_policy_does_not_require_direct_source_boards(self):
        self.initialize(direct=False)
        layout = title_layout()
        layout['items'].append({'id': 'icon', 'kind': 'art', 'box': [300, 300, 100, 100],
                                'description': 'Independent blue icon'})
        action = controller.run(self.work, data={'kind': 'layout', 'layout': layout})
        layers = {job['role']: job for job in action['jobs']}
        self.assertEqual(set(layers), {'background', 'foreground'})
        self.begin(layers['background'])
        self.submit(layers['background'], self.bg)
        self.begin(layers['foreground'])
        action = self.submit(layers['foreground'], self.fg)
        title = next(job for job in action['jobs'] if job['role'] == 'wordart')
        self.assertEqual(len(title['references']), 1)
        self.assertEqual(title['primary_source_reference_policy'], POLICY)
        self.assertNotIn('source_board_reference_policy', read_state(self.work))

    def test_marker_or_state_policy_tamper_is_rejected(self):
        self.initialize()
        original_state = read_state(self.work)
        marker_path = self.work / 'initialization.json'
        original_marker = json.loads(marker_path.read_text('utf-8'))
        for location in ('state', 'marker'):
            for value in (None, 'other'):
                with self.subTest(location=location, value=value):
                    state, marker = copy.deepcopy(original_state), copy.deepcopy(original_marker)
                    target = state if location == 'state' else marker
                    if value is None:
                        target.pop('primary_source_reference_policy')
                    else:
                        target['primary_source_reference_policy'] = value
                    save_state(self.work, state, 'private_policy_tamper')
                    write_json(marker_path, marker)
                    with self.assertRaisesRegex(ValueError, 'primary.source|title.reference'):
                        controller.run(self.work)

    def test_interrupted_initialization_resumes_frozen_reference_choice(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.initialize()
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work)
        self.assertEqual(action['kind'], 'describe')
        self.assertEqual(read_state(self.work)['primary_source_reference_policy'], POLICY)

    def test_legacy_interrupted_marker_cannot_enable_new_reference(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.initialize(policy=None)
        with self.assertRaisesRegex(ValueError, 'primary.source|title.reference'):
            controller.run(self.work, primary_source_reference_policy=POLICY)
        self.assertFalse((self.work / 'state.json').exists())

    def test_saved_new_primary_cannot_add_full_source_or_remove_descriptor_policy(self):
        self.initialize()
        action = self.plan_primary()
        original = read_state(self.work)
        for mutation in ('policy', 'reference', 'member'):
            state = copy.deepcopy(original)
            task = state['tasks'][action['jobs'][0]['id']]
            if mutation == 'policy':
                task['source_detail'].pop('reference_policy')
            elif mutation == 'member':
                task['member']['box'][0] += 1
            else:
                task['references']['input/working.png'] = state['source']['working_sha256']
            save_state(self.work, state, 'private_task_tamper')
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'primary.source|title.reference'):
                controller.run(self.work)

    def test_actual_bootstrap_title_reference_uses_only_true_source_parts_pixels(self):
        fixture = os.environ.get('PRK_R19_BOOTSTRAP_JOB')
        if not fixture:
            self.skipTest('Set PRK_R19_BOOTSTRAP_JOB for the retained original title fixture')
        job = Path(fixture)
        before = {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()}
        original_state = read_state(job)
        title = copy.deepcopy(next(task['member'] for task in original_state['tasks'].values()
                                   if task['role'] == 'wordart'))
        self.source = job / original_state['source']['original']
        self.initialize()
        layers = controller.run(self.work, data={'kind': 'layout',
                                                'layout': {'items': [title], 'background_regions': []}})
        self.begin(layers['jobs'][0])
        action = self.submit(layers['jobs'][0], self.bg)
        public = action['jobs'][0]
        state = read_state(self.work)
        task = state['tasks'][public['id']]
        detail = task['source_detail']
        self.assertEqual(detail['reference_policy'], POLICY)
        self.assertEqual(public['references'], [str(self.work / detail['path'])])
        with Image.open(self.work / state['source']['working']) as working:
            original = np.asarray(working.convert('RGBA'))
        with Image.open(job / original_state['source']['working']) as baseline:
            self.assertTrue(np.array_equal(original, np.asarray(baseline.convert('RGBA'))))
        self.assertEqual(state['source']['pixel_sha256'], original_state['source']['pixel_sha256'])
        self.assertEqual(detail['source_sha256'], sha256(self.work / state['source']['working']))
        with Image.open(self.work / detail['path']) as opened:
            actual = np.asarray(opened.convert('RGBA'))
        left, top, right, bottom = detail['crop_box']
        crop = original[top:bottom, left:right]
        mask = np.zeros(crop.shape[:2], dtype=bool)
        import math
        for part in detail['source_parts']:
            x0, y0, x1, y1 = part['bounds']
            mask[max(0, math.floor(y0) - top):min(bottom - top, math.ceil(y1) - top),
                 max(0, math.floor(x0) - left):min(right - left, math.ceil(x1) - left)] = True
        self.assertTrue(np.array_equal(actual[mask], crop[mask]))
        self.assertTrue(np.all(actual[~mask] == 0))
        self.assertEqual(sha256(self.work / detail['path']), detail['sha256'])
        permit = self.begin(public)
        self.assertEqual(len(permit['request']['references']), 1)
        self.assertEqual(permit['request']['prompt'], (self.work / task['brief']).read_text('utf-8'))
        self.assertIsNone(permit['result_template'].get('executed_prompt'))
        self.assertEqual({str(path.relative_to(job)): sha256(path)
                          for path in job.rglob('*') if path.is_file()}, before)


class IsolatedTitlePromptContract(unittest.TestCase):
    def setUp(self):
        self.member = title_layout()['items'][0]
        self.detail = {'path': 'input/isolated-title.png', 'reference_policy': POLICY,
            'crop_box': [0, 0, 1025, 265], 'member_box': self.member['box'], 'zoom': 1,
            'source_sha256': 'working-file-hash', 'sha256': 'source-reference-file-hash',
            'source_parts': self.member['source_parts'],
            'reference_provenance': {'kind': 'masked-working-source', 'generated': False,
                'retained_rgba_unchanged': True, 'outside_parts_rgba_zero': True}}

    def prior(self, name):
        path = PACKAGE.parent / 'source-r19/compat-runtime/newpath' / (name + '.py')
        if not path.is_file():
            self.skipTest('Retained previous runtime is unavailable')
        spec = importlib.util.spec_from_file_location('newpath.r19_title_policy_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_single_source_reference_names_true_pixels_parts_and_remaining_paper(self):
        before = copy.deepcopy((self.member, self.detail))
        text = prompts.wordart_brief(self.member, source_detail=self.detail)
        self.assertIn('single attached source-parts title reference', text)
        self.assertIn('pixels inside the declared source parts', text)
        self.assertIn('Paper or decorative pixels', text)
        self.assertIn(self.member['lettering'], text)
        self.assertNotIn('Image 2', text)
        self.assertNotIn('two attached', text)
        self.assertIn('alpha 255', text)
        self.assertIn('alpha 0', text)
        self.assertIn('white outlines', text)
        self.assertIn('glow', text)
        self.assertEqual((self.member, self.detail), before)

    def test_default_none_and_old_detail_prompt_text_match_r19_exactly(self):
        old = self.prior('prompts')
        legacy_detail = {key: value for key, value in self.detail.items()
                         if key not in ('reference_policy', 'source_parts', 'reference_provenance')}
        for detail in (None, legacy_detail):
            with self.subTest(detail=detail):
                self.assertEqual(prompts.wordart_brief(self.member, source_detail=detail),
                                 old.wordart_brief(self.member, source_detail=detail))

    def test_unknown_descriptor_policy_is_rejected_and_key_order_is_stable(self):
        original = prompts.wordart_brief(self.member, source_detail=self.detail)
        reordered = dict(reversed(list(self.detail.items())))
        self.assertEqual(prompts.wordart_brief(self.member, source_detail=reordered), original)
        invalid = {**self.detail, 'reference_policy': 'other'}
        with self.assertRaisesRegex(ValueError, 'title.reference policy'):
            prompts.wordart_brief(self.member, source_detail=invalid)

    def test_single_reference_short_goal_keeps_source_paper_and_outline_boundary(self):
        task = {'role': 'wordart', 'member': self.member, 'source_detail': self.detail}
        request = {'transparent_background': True, 'references': ['/job/input/isolated-title.png']}
        text = execution._invocation_instructions(task, request)
        self.assertIn('唯一参考', text)
        self.assertIn('纸底或装饰', text)
        self.assertIn('原有白描边', text)
        self.assertIn('不添加宽白边或光晕', text)
        self.assertIn('完整组', text)
        self.assertNotIn('第二图', text)
        self.assertNotIn('普通可见', text)

    def test_all_other_once_fields_request_bytes_unknown_and_state_stay_identical_to_r19(self):
        old = self.prior('execution')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory(prefix='prk-isolated-title-contract-') as temporary:
            work = Path(temporary)
            (work / 'briefs').mkdir()
            reference = work / 'isolated-title.png'
            reference.write_bytes(b'private-real-source-placeholder')
            for detail in (None, {key: value for key, value in self.detail.items() if key != 'reference_policy'}, self.detail):
                for form in ('absent', 'sparse', 'explicit'):
                    task = {'id': 'task-title', 'role': 'wordart', 'attempt': 0,
                            'brief': 'briefs/title.txt', 'member': copy.deepcopy(self.member)}
                    if detail is not None:
                        task['source_detail'] = copy.deepcopy(detail)
                    (work / task['brief']).write_text('Unchanged locked canonical request.\n', 'utf-8')
                    prompt = (work / task['brief']).read_text('utf-8')
                    request = {'tool': 'offline-tool', 'prompt': prompt, 'references': [str(reference)],
                               'size': [1536, 1024], 'transparent_background': True, 'requested_outputs': 1}
                    if form == 'sparse':
                        request['tool_arguments'] = {'transparent_background': True, 'unknown_provider': None}
                    elif form == 'explicit':
                        request['tool_arguments'] = {'prompt': prompt, 'referenced_image_paths': [str(reference)],
                                                    'transparent_background': True}
                    public = {'references': [str(reference)], 'size': [1536, 1024], 'transparent_background': True,
                              'result_template': {'executed_prompt': None}, 'failure_template': {}}
                    execution.enroll(task)
                    previous_task = copy.deepcopy(task)
                    (work / 'requests/task-title-0.json').unlink(missing_ok=True)
                    with mock.patch.object(old, 'datetime', fixed):
                        previous = old.begin(work, previous_task, public, request)
                    previous_bytes = Path(previous['request_file']).read_bytes()
                    current_task = copy.deepcopy(task)
                    with mock.patch.object(execution, 'datetime', fixed):
                        current = execution.begin(work, current_task, public, request)
                    self.assertEqual(previous_bytes, Path(current['request_file']).read_bytes())
                    self.assertEqual(previous_task, current_task)
                    previous.pop('instructions')
                    current.pop('instructions')
                    self.assertEqual(previous, current)
                    self.assertIsNone(current['result_template']['executed_prompt'])
                    self.assertIsNone(execution.begin(work, current_task, public, request))


if __name__ == '__main__':
    unittest.main()
