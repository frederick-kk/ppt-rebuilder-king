"""Optional true-source detail references preserve old wordart requests."""
import copy
from datetime import datetime, timezone
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


class WordartSourceDetailPrompts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-title-source-detail-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'briefs').mkdir()
        self.crop = self.work / 'title-source.png'
        self.full = self.work / 'working.png'
        self.crop.write_bytes(b'actual-source-pixels-local-fixture')
        self.full.write_bytes(b'actual-source-pixels-full-fixture')
        self.member = {'id': 'title', 'box': [118, 10, 945, 96], 'lettering': '完整标题！',
                       'description': 'Original red and black ink, complete arc and white original outlines.'}
        self.detail = {'path': 'title-source.png', 'crop_box': [20, 0, 1160, 220],
                       'member_box': [118, 10, 945, 96], 'zoom': 1,
                       'source_sha256': 'working-file-hash', 'sha256': 'crop-file-hash'}

    def old_module(self, name):
        path = PACKAGE.parent / ('source-r15/compat-runtime/newpath/' + name + '.py')
        if not path.is_file():
            self.skipTest('Retained R15 source fixture unavailable outside this checkout')
        spec = importlib.util.spec_from_file_location('newpath.r15_detail_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def task(self, role='wordart', detail=True, form='sparse'):
        task = {'id': 'task-' + role, 'role': role, 'attempt': 0, 'brief': 'briefs/' + role + '.txt',
                'member': copy.deepcopy(self.member)}
        if detail:
            task['source_detail'] = copy.deepcopy(self.detail)
        references = [str(self.crop), str(self.full)] if role == 'wordart' and detail else [str(self.full)]
        text = 'Exact frozen request.\n'
        (self.work / task['brief']).write_text(text, encoding='utf-8')
        alpha = role != 'background'
        request = {'tool': 'offline-tool', 'prompt': text, 'references': references,
                   'size': [1536, 1024], 'transparent_background': alpha, 'requested_outputs': 1}
        if form == 'sparse':
            request['tool_arguments'] = {'transparent_background': alpha, 'unknown_provider_field': None}
        elif form == 'explicit':
            request['tool_arguments'] = {'prompt': text, 'referenced_image_paths': references,
                                        'transparent_background': alpha, 'size': [1536, 1024], 'n': 1}
        public = {'references': references, 'size': [1536, 1024], 'transparent_background': alpha,
                  'result_template': {'kind': 'image_result', 'executed_prompt': None},
                  'failure_template': {'kind': 'image_failed'}}
        execution.enroll(task)
        return task, public, request

    def test_default_and_none_briefs_remain_exactly_equal_to_r15(self):
        old = self.old_module('prompts')
        members = [self.member,
                   {**self.member, 'lettering': '第一行\n第二行'},
                   {**self.member, 'box': [0.5, 9.5, 500.25, 140.5], 'description': 'Detached white strokes.'}]
        for member in members:
            with self.subTest(member=member):
                expected = old.wordart_brief(member)
                self.assertEqual(prompts.wordart_brief(member), expected)
                self.assertEqual(prompts.wordart_brief(member, source_detail=None), expected)

    def test_new_brief_assigns_actual_crop_and_full_source_distinct_roles(self):
        text = prompts.wordart_brief(self.member, source_detail=self.detail)
        for phrase in ('Image 1 is the actual unchanged-source detail crop',
                       'Image 2 is the complete unchanged working source',
                       'source_box_pixels locates the assigned group in image 2',
                       'characters, letter shapes, complete writing-to-arc proportions',
                       'only the assigned complete writing group'):
            self.assertIn(phrase, text)
        self.assertIn(self.member['lettering'], text)
        self.assertNotIn('group in that image; use the source', text)

    def test_source_crop_context_does_not_add_neighboring_writing_or_force_multiline_into_one_line(self):
        text = prompts.wordart_brief(self.member, source_detail=self.detail)
        self.assertIn('The crop may include nearby ordinary captions', text)
        self.assertIn('source line count and horizontal arrangement', text)
        self.assertIn('source is a single line, keep one line', text)
        self.assertIn('only the assigned complete writing group', text)

    def test_existing_alpha_original_outlines_and_no_added_glow_rules_remain(self):
        text = prompts.wordart_brief(self.member, source_detail=self.detail)
        for phrase in ('main painted ink and genuine opaque title materials at alpha 255',
                       'every area outside the complete subject must have alpha 0',
                       'Genuine source white writing and white outlines',
                       'a white sticker border, coloured fringe, haze, mist, glow',
                       'fully transparent clearance on all four outer edges'):
            self.assertIn(phrase, text)

    def test_detail_payload_is_order_stable_complete_and_does_not_mutate_inputs(self):
        member_before, detail_before = copy.deepcopy(self.member), copy.deepcopy(self.detail)
        first = prompts.wordart_brief(self.member, source_detail=self.detail)
        reversed_keys = {key: value for key, value in reversed(list(self.detail.items()))}
        self.assertEqual(first, prompts.wordart_brief(self.member, source_detail=reversed_keys))
        actual = json.loads(first.split('True-source detail reference follows.\n', 1)[1])
        self.assertEqual(actual, self.detail)
        self.assertEqual(self.member, member_before)
        self.assertEqual(self.detail, detail_before)
        self.detail['crop_box'][0] += 10
        self.assertNotEqual(first, prompts.wordart_brief(self.member, source_detail=self.detail))

    def test_wordart_goal_requests_actual_task_reference_view_and_source_proportions(self):
        task, _, request = self.task()
        text = execution._invocation_instructions(task, request)
        for phrase in ('调用前实际查看本任务references', '第一图是真实原图局部',
                       '第二图是完整工作原图', '完整组的字样、字形及字与圆弧笔刷比例',
                       '源为单行时保持单行横排', '只使用本任务references作为生成参考',
                       '原有白描边', '不添加宽白边或光晕'):
            self.assertIn(phrase, text)
        self.assertIn('参考1（title-source.png）提供真实源局部外观和完整组比例', text)
        self.assertIn('参考2（working.png）提供完整原图身份和位置', text)
        self.assertIn('executed_prompt仅记服务实际暴露', text)

    def test_no_detail_and_other_role_instructions_remain_byte_identical(self):
        old = self.old_module('execution')
        for role in ('background', 'foreground', 'wordart', 'board', 'future-role'):
            for detail in (False, True):
                if role == 'wordart' and detail:
                    continue
                with self.subTest(role=role, detail=detail):
                    task, _, request = self.task(role, detail)
                    self.assertEqual(execution._invocation_instructions(task, request),
                                     old._invocation_instructions(task, request))
        task, _, request = self.task(detail=False)
        task['source_detail'] = None
        self.assertEqual(execution._invocation_instructions(task, request), old._invocation_instructions(task, request))

    def test_request_bytes_state_once_fields_and_unknown_provider_values_remain_exact(self):
        old = self.old_module('execution')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        for role in ('background', 'foreground', 'wordart', 'board'):
            for detail in (False, True):
                for form in ('absent', 'sparse', 'explicit'):
                    with self.subTest(role=role, detail=detail, form=form):
                        task, public, request = self.task(role, detail, form)
                        (self.work / 'requests' / (task['id'] + '-0.json')).unlink(missing_ok=True)
                        prior_task = copy.deepcopy(task)
                        with mock.patch.object(old, 'datetime', fixed):
                            prior = old.begin(self.work, prior_task, public, request)
                        prior_bytes = Path(prior['request_file']).read_bytes()
                        current_task = copy.deepcopy(task)
                        with mock.patch.object(execution, 'datetime', fixed):
                            current = execution.begin(self.work, current_task, public, request)
                        self.assertEqual(Path(current['request_file']).read_bytes(), prior_bytes)
                        self.assertEqual(current_task, prior_task)
                        prior.pop('instructions')
                        current.pop('instructions')
                        self.assertEqual(current, prior)
                        self.assertIsNone(current['result_template']['executed_prompt'])
                        self.assertIsNone(execution.begin(self.work, current_task, public, request))


if __name__ == '__main__':
    unittest.main()
