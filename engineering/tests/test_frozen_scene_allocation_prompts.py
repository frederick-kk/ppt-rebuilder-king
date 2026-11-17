"""Shared source allocation adds concrete roles without altering legacy calls."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import execution, prompts


class FrozenSceneAllocationPrompts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-frozen-layer-map-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'briefs').mkdir()
        self.source = self.work / 'working.png'
        self.source.write_bytes(b'unchanged-source')
        self.transform = {'offset': [0, 0], 'resized': [1919, 1080]}
        self.allocation = {
            'background_regions': [
                {'id': 'scene-left', 'box': [0, 410, 510, 320], 'description': '拱窗融合的人物场景及灯笼'},
                {'id': 'scene-right', 'box': [1480, 0, 439, 185], 'description': '页边融合的制陶双手与器物场景'}],
            'foreground_regions': [
                {'id': 'photo-center', 'box': [715, 251, 175, 192], 'description': '独立内容卡中的人物照片',
                 'presentation': 'rectangle', 'embedded_image': True},
                {'id': 'label-a', 'box': [500, 450, 180, 45], 'description': '独立艺术标签',
                 'presentation': 'cutout', 'lettering': '完整文字！'}],
            'source_pixel_sha256': 'source-pixel-identity',
        }

    def old_module(self, name):
        path = PACKAGE.parent / ('source-r14/compat-runtime/newpath/' + name + '.py')
        if not path.exists():
            self.skipTest('Retained R14 module unavailable outside this checkout')
        spec = importlib.util.spec_from_file_location('newpath.r14_scene_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def brief(self, role, **options):
        return prompts.layer_brief(role, self.transform, scene_allocation=self.allocation, **options)

    def task(self, role, allocation=True, form='sparse'):
        task = {'id': 'task-' + role, 'role': role, 'attempt': 0, 'brief': 'briefs/' + role + '.txt',
                'decorative_background_policy': prompts.BACKGROUND_CONTAINER_POLICY_ID}
        if allocation:
            task['scene_allocation'] = copy.deepcopy(self.allocation)
        task['member'] = {'lettering': '完整标题！'}
        task['board'] = {'grid': {'rows': 1, 'columns': 2}, 'members': [{'id': 'art1'}, {'id': 'art2'}]}
        text = 'Existing exact locked prompt.\n'
        (self.work / task['brief']).write_text(text, encoding='utf-8')
        alpha = role != 'background'
        request = {'tool': 'offline-fixture', 'prompt': text, 'references': [str(self.source)],
                   'size': [1920, 1080], 'transparent_background': alpha, 'requested_outputs': 1}
        if form == 'sparse':
            request['tool_arguments'] = {'transparent_background': alpha, 'provider_unexposed': None}
        elif form == 'explicit':
            request['tool_arguments'] = {'prompt': text, 'referenced_image_paths': [str(self.source)],
                                        'transparent_background': alpha, 'size': [1920, 1080], 'n': 1}
        public = {'references': [str(self.source)], 'size': [1920, 1080], 'transparent_background': alpha,
                  'result_template': {'kind': 'image_result', 'executed_prompt': None},
                  'failure_template': {'kind': 'image_failed'}}
        execution.enroll(task)
        return task, public, request

    def test_no_map_or_explicit_none_keeps_eight_legacy_layer_payloads_exact(self):
        old = self.old_module('prompts')
        for role in ('background', 'foreground'):
            for mode in (None, prompts.MATERIAL_MODE):
                for containers in (False, True):
                    with self.subTest(role=role, mode=mode, containers=containers):
                        options = {'background_mode': mode, 'decorative_background': containers}
                        before = old.layer_brief(role, self.transform, **options)
                        self.assertEqual(prompts.layer_brief(role, self.transform, **options), before)
                        self.assertEqual(prompts.layer_brief(role, self.transform, scene_allocation=None, **options), before)

    def test_new_background_roles_retain_whole_scene_subjects_and_remove_complete_foreground(self):
        text = self.brief('background', decorative_background=True)
        self.assertIn('Keep every background_regions scene', text)
        self.assertIn('including its people, objects and image-internal writing', text)
        self.assertIn('Remove every foreground_regions item', text)
        self.assertIn('complete rectangles', text)
        self.assertIn('without deciding their roles again', text)
        for region in self.allocation['background_regions'] + self.allocation['foreground_regions']:
            self.assertIn(region['description'], text)

    def test_new_foreground_roles_retain_assigned_material_and_clear_same_background_regions(self):
        text = self.brief('foreground', background_mode=prompts.MATERIAL_MODE, decorative_background=True)
        self.assertIn('Retain every foreground_regions item', text)
        self.assertIn('Clear every background_regions scene', text)
        self.assertIn('including its people, objects and image-internal writing', text)
        self.assertIn('native geometry and table reference appearance', text)
        self.assertIn('fully transparent with alpha 0', text)

    def test_map_payload_keeps_all_fields_source_binding_and_presentation(self):
        for role in ('background', 'foreground'):
            with self.subTest(role=role):
                text = self.brief(role)
                actual = json.loads(text.split('Frozen source layer allocation follows.\n', 1)[1])
                self.assertEqual(actual, self.allocation)
                self.assertEqual(actual['foreground_regions'][0]['presentation'], 'rectangle')
                self.assertTrue(actual['foreground_regions'][0]['embedded_image'])
                self.assertEqual(actual['foreground_regions'][1]['lettering'], '完整文字！')

    def test_input_map_and_nested_fields_are_unchanged_and_canonical_key_order_is_stable(self):
        before = copy.deepcopy(self.allocation)
        first = self.brief('background')

        def reverse_keys(value):
            if isinstance(value, dict):
                return {key: reverse_keys(item) for key, item in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse_keys(item) for item in value]
            return value

        reordered = reverse_keys(self.allocation)
        second = prompts.layer_brief('background', self.transform, scene_allocation=reordered)
        self.assertEqual(first, second)
        self.assertEqual(self.allocation, before)
        self.allocation['foreground_regions'][0]['box'][0] += 99
        self.assertNotEqual(first, self.brief('background'))
        self.assertIn('715', first)

    def test_map_adds_no_changes_to_existing_alpha_container_geometry_rules(self):
        old = self.old_module('prompts')
        for role in ('background', 'foreground'):
            for mode in (None, prompts.MATERIAL_MODE):
                for containers in (False, True):
                    with self.subTest(role=role, mode=mode, containers=containers):
                        options = {'background_mode': mode, 'decorative_background': containers}
                        base = old.layer_brief(role, self.transform, **options)
                        text = self.brief(role, **options)
                        self.assertTrue(text.startswith(base + '\n\n'))
                        self.assertEqual(text.count(base), 1)

    def test_invalid_layer_role_is_rejected_only_when_map_is_supplied(self):
        old = self.old_module('prompts')
        for role in ('wordart', 'board', 'unknown-role'):
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    self.brief(role)
                self.assertEqual(prompts.layer_brief(role, self.transform), old.layer_brief(role, self.transform))

    def test_background_and_foreground_short_goals_name_same_frozen_scene_and_content_regions(self):
        for role in ('background', 'foreground'):
            task, _, request = self.task(role)
            before = copy.deepcopy(task)
            text = execution._invocation_instructions(task, request)
            with self.subTest(role=role):
                self.assertIn('冻结分层清单', text)
                self.assertIn('融景保留' if role == 'background' else '融景清空', text)
                self.assertIn('独立内容移除' if role == 'background' else '独立内容保留', text)
                self.assertIn('独立图片完整矩形及图内全部内容' +
                              ('一并移除并恢复下方背景' if role == 'background' else '一并保留'), text)
                for region in self.allocation['background_regions'] + self.allocation['foreground_regions']:
                    self.assertIn(region['id'], text)
                    self.assertIn(region['description'], text)
                    self.assertIn(json.dumps(region['box'], separators=(',', ':')), text)
                self.assertEqual(task, before)

    def test_no_map_and_non_layer_short_goals_are_byte_identical_to_r14(self):
        old = self.old_module('execution')
        for role in ('background', 'foreground', 'wordart', 'board', 'unknown-role'):
            for allocation in (False, True):
                if allocation and role in ('background', 'foreground'):
                    continue
                with self.subTest(role=role, allocation=allocation):
                    task, _, request = self.task(role, allocation=allocation)
                    self.assertEqual(execution._invocation_instructions(task, request),
                                     old._invocation_instructions(task, request))

    def test_requests_other_once_fields_states_and_service_unknowns_are_unmodified(self):
        old = self.old_module('execution')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        for role in ('background', 'foreground', 'wordart', 'board'):
            for allocation in (False, True):
                for form in ('absent', 'sparse', 'explicit'):
                    with self.subTest(role=role, allocation=allocation, form=form):
                        task, public, request = self.task(role, allocation=allocation, form=form)
                        (self.work / 'requests' / (task['id'] + '-0.json')).unlink(missing_ok=True)
                        old_task = copy.deepcopy(task)
                        with mock.patch.object(old, 'datetime', fixed):
                            old_result = old.begin(self.work, old_task, public, request)
                        old_bytes = Path(old_result['request_file']).read_bytes()
                        new_task = copy.deepcopy(task)
                        with mock.patch.object(execution, 'datetime', fixed):
                            new_result = execution.begin(self.work, new_task, public, request)
                        self.assertEqual(old_bytes, Path(new_result['request_file']).read_bytes())
                        self.assertEqual(old_task, new_task)
                        old_result.pop('instructions')
                        new_result.pop('instructions')
                        self.assertEqual(old_result, new_result)
                        self.assertIsNone(new_result['result_template']['executed_prompt'])
                        self.assertIsNone(execution.begin(self.work, new_task, public, request))

    def test_private_r13_source_map_distinguishes_integrated_person_and_content_photo(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r13-continuation-run/job'
        if not job.is_dir():
            self.skipTest('Private R13 source fixture unavailable outside this checkout')
        state_path = job / 'state.json'
        state_bytes = state_path.read_bytes()
        state = json.loads(state_bytes)
        source_path = job / state['source']['original']
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        self.assertEqual(source_hash, '36718a9eabe1b1866b290eaf2e58fb4418edcfeacf595e037392f8ca63018e2c')
        allocation = copy.deepcopy(self.allocation)
        allocation['source_pixel_sha256'] = state['source']['pixel_sha256']
        photo = next(item for item in state['layout']['items'] if item['id'] == 'o015')
        allocation['foreground_regions'] = [{key: copy.deepcopy(photo[key])
                                             for key in ('id', 'box', 'description', 'presentation', 'embedded_image')}]
        for role in ('background', 'foreground'):
            text = prompts.layer_brief(role, state['source']['transform'],
                decorative_background=True, background_mode=prompts.MATERIAL_MODE, scene_allocation=allocation)
            self.assertIn('拱窗融合的人物场景', text)
            self.assertIn('京剧旦角人物近景照片', text)
            self.assertIn('o015', text)
            self.assertIn(state['source']['pixel_sha256'], text)
        self.assertEqual(state_path.read_bytes(), state_bytes)
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)


if __name__ == '__main__':
    unittest.main()
