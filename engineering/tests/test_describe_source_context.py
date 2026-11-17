"""Describe metadata contracts from real source preparation, no image calls."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from PIL import Image
import test_result_role_binding as binding_fixture
from newpath import controller
from newpath.storage import read_state


class DescribeSourceContext(binding_fixture.ReviewedOutputFixture):
    def setUp(self):
        super().setUp()
        # Portrait input exercises a real non-unit scale and nonzero padding.
        Image.new('RGB', (640, 800), 'white').save(self.source)

    def ready(self, production=True):
        options = {'execution_policy': 'explicit-start/1',
                   'post_return_prompt_policy': 'audit-existing-return/1',
                   'delivery_policy': 'reviewed-existing-output/1',
                   'recovery_policy': 'bounded-recovery/1'} if production else {}
        with mock.patch.object(controller, '_runtime'):
            first = controller.run(self.work, source=self.source, **options)
        self.jobs = {task['role']: task for task in first['jobs']}
        for role, path in [('background', self.bg), ('foreground', self.fg)]:
            task = self.jobs[role]
            if production:
                self.begin(task)
                response = self.submit(task, path)
            else:
                response = controller.run(self.work, data={
                    'task_id': task['id'], 'role': role, 'attempt': task['attempt'], 'file': str(path)})
        self.assertEqual(response['kind'], 'describe')
        return response

    @staticmethod
    def overflowing_layout():
        return {'kind': 'layout', 'layout': {'items': [{
            'id': 'line', 'kind': 'text', 'box': [100, 100, 20, 48],
            'text': '普通正文保持可编辑', 'size': 32}]}}

    def test_current_describe_exposes_real_transform_as_independent_metadata(self):
        response = self.ready()
        state = read_state(self.work)
        expected = {'scale': 1.35, 'original_size': [640, 800], 'resized': [864, 1080],
                    'offset': [528, 0], 'padding': [528, 0, 528, 0]}
        self.assertEqual(state['source']['transform'], expected)
        self.assertIn('source_transform', response)
        self.assertEqual(response['source_transform'], expected)
        self.assertEqual(response['reply_template'], {'kind': 'layout', 'layout': {'items': []}})
        direct = controller._drive(self.work, state)
        direct['source_transform']['offset'][0] = -1
        self.assertEqual(state['source']['transform'], expected)

    def test_actual_preflight_correction_keeps_same_transform_and_existing_job(self):
        response = self.ready()
        before = read_state(self.work)
        correction = controller.run(self.work, data=self.overflowing_layout())
        self.assertEqual(correction['reason'], 'native_text_requires_correction')
        self.assertFalse(correction['generate_more'])
        self.assertEqual(correction.get('source_transform'), before['source']['transform'])
        self.assertEqual(correction['source_transform'], response['source_transform'])
        self.assertEqual(read_state(self.work), before)
        self.assertFalse((self.work / 'layout.json').exists())
        self.assertEqual(len(before['tasks']), 2)
        self.assertNotIn('source_transform', correction['reply_template'])

    def test_current_guidance_reaches_normal_and_corrective_actions(self):
        response = self.ready()
        correction = controller.run(self.work, data=self.overflowing_layout())
        # These check prompt transmission only. Model measurements and wording
        # correctness still require a new real-page run.
        for action in [response, correction]:
            with self.subTest(action=action.get('reason', 'normal')):
                guidance = action['instructions']
                for phrase in ['original-image crop', 'working-image crop',
                               'source_xy = crop_origin + local_xy / zoom',
                               'working_xy = source_xy * scale + offset',
                               'working_xy = crop_origin + local_xy / zoom',
                               'complete artwork group', 'detached strokes',
                               'lettering', 'each label', 'panel']:
                    self.assertIn(phrase, guidance)

    def test_legacy_describe_keeps_frozen_r10_keys_and_instruction_bytes(self):
        response = self.ready(production=False)
        self.assertEqual(set(response), {'files', 'instructions', 'kind', 'reply_template', 'schema_file'})
        self.assertNotIn('source_transform', response)
        digest = hashlib.sha256(response['instructions'].encode('utf-8')).hexdigest()
        self.assertEqual(digest, 'c323581e9d4b41732616b4d6db8a9d9aa28de6d7b3877f1dbce62c3a4a6bd961')

    def test_full_group_dimensions_use_measured_edges_in_both_existing_actions(self):
        response = self.ready()
        before = read_state(self.work)
        correction = controller.run(self.work, data=self.overflowing_layout())
        for action in (response, correction):
            for phrase in ('right - left', 'bottom - top',
                           'do not substitute text size or glyph-line height',
                           'omit target to reuse the measured box'):
                self.assertIn(phrase, action['instructions'])
        self.assertEqual(read_state(self.work), before)
        self.assertEqual(response['reply_template'], {'kind': 'layout', 'layout': {'items': []}})

    def test_legacy_correction_keeps_frozen_r10_keys_and_instruction_bytes(self):
        self.ready(production=False)
        state = read_state(self.work)
        # Isolate the existing correction return for an older job without the
        # container policy; real text preflight still supplies the issues.
        with mock.patch.object(controller.recovery, 'enabled', return_value=True):
            response = controller._submit(self.work, state, copy.deepcopy(self.overflowing_layout()))
        self.assertEqual(set(response), {'files', 'generate_more', 'instructions', 'issues',
                                         'kind', 'reason', 'reply_template', 'schema_file'})
        self.assertNotIn('source_transform', response)
        digest = hashlib.sha256(response['instructions'].encode('utf-8')).hexdigest()
        self.assertEqual(digest, 'bffaed88548c922197e418f28160c2b2167d0b2bf48418007260a6b0d813f489')


if __name__ == '__main__':
    unittest.main()
