"""Role summaries preserve the existing native-object and title material rules."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import json

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import execution
from newpath.prompts import BACKGROUND_CONTAINER_POLICY_ID


class DispatchGoalCompleteness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-dispatch-goal-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'briefs').mkdir()
        self.source = self.work / 'working.png'
        self.source.write_bytes(b'offline-source')
        self.board = self.work / 'board.png'
        self.board.write_bytes(b'offline-grid')

    def fixture(self, role, policy=None, alpha=None, form='sparse'):
        task = {'id': 'task-' + role, 'role': role, 'attempt': 0, 'brief': 'briefs/' + role + '.txt'}
        if policy is not None:
            task['decorative_background_policy'] = policy
        if role == 'wordart':
            task['member'] = {'id': 'title', 'lettering': '完整艺术标题！'}
        if role == 'board':
            task['board'] = {'grid': {'rows': 1, 'columns': 2},
                             'members': [{'id': 'left', 'lettering': '甲！'}, {'id': 'right'}]}
        prompt = 'Exact locked ' + role + ' request.\n'
        (self.work / task['brief']).write_text(prompt, encoding='utf-8')
        references = [str(self.board), str(self.source)] if role == 'board' else [str(self.source)]
        alpha = role != 'background' if alpha is None else alpha
        request = {'tool': 'offline-tool', 'prompt': prompt, 'references': references,
                   'size': [1536, 1024], 'transparent_background': alpha, 'requested_outputs': 1}
        if form == 'sparse':
            request['tool_arguments'] = {'transparent_background': alpha}
        elif form == 'explicit':
            request['tool_arguments'] = {'prompt': prompt, 'referenced_image_paths': references,
                                         'size': [1536, 1024], 'transparent_background': alpha, 'n': 1}
        public = {'references': references, 'size': [1536, 1024], 'transparent_background': alpha,
                  'result_template': {'kind': 'image_result'}, 'failure_template': {'kind': 'image_failed'}}
        execution.enroll(task)
        return task, public, request

    def dispatch(self, role, **options):
        return execution.begin(self.work, *self.fixture(role, **options))

    def old_execution(self):
        old_path = PACKAGE.parent / 'source-r12/compat-runtime/newpath/execution.py'
        if not old_path.exists():
            self.skipTest('Retained r12 fixture unavailable outside this checkout')
        spec = importlib.util.spec_from_file_location('newpath.r12_dispatch_goals', old_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def assert_pair_equal_except_instruction(self, old, task, public, request):
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        prior_task = copy.deepcopy(task)
        with mock.patch.object(old, 'datetime', fixed):
            prior = old.begin(self.work, prior_task, public, request)
        prior_bytes = Path(prior['request_file']).read_bytes()
        current_task = copy.deepcopy(task)
        with mock.patch.object(execution, 'datetime', fixed):
            current = execution.begin(self.work, current_task, public, request)
        self.assertEqual(prior_bytes, Path(current['request_file']).read_bytes())
        self.assertEqual(prior_task, current_task)
        old_text = prior.pop('instructions')
        new_text = current.pop('instructions')
        self.assertEqual(prior, current)
        self.assertIsNone(execution.begin(self.work, current_task, public, request))
        return old_text, new_text

    def test_background_removes_native_geometry_for_true_false_and_missing_policy(self):
        for policy in (None, False, BACKGROUND_CONTAINER_POLICY_ID):
            with self.subTest(policy=policy):
                text = self.dispatch('background', policy=policy)['instructions']
                self.assertIn('简单原生几何、连接线、箭头及表格内容均清除', text)
                self.assertIn('已分配背景的装饰轮廓和分隔保留', text)
                if policy == BACKGROUND_CONTAINER_POLICY_ID:
                    self.assertIn('所有层级的空纸底、金框、卡片轮廓及分隔', text)
                else:
                    self.assertIn('移除分配给独立层的纸框、卡片', text)

    def test_foreground_keeps_native_reference_appearance_for_all_policies(self):
        for policy in (None, False, BACKGROUND_CONTAINER_POLICY_ID):
            with self.subTest(policy=policy):
                text = self.dispatch('foreground', policy=policy)['instructions']
                self.assertIn('保留简单原生几何、连接线、箭头及表格内容的参考外观', text)
                self.assertIn('供后续原生制作', text)
                if policy == BACKGROUND_CONTAINER_POLICY_ID:
                    self.assertIn('移除分配给背景的场景和固定容器', text)
                else:
                    self.assertIn('保留本任务分配的纸框、卡片及页面内容', text)

    def test_transparent_title_has_solid_ink_clear_exterior_and_four_edge_clearance(self):
        text = self.dispatch('wordart')['instructions']
        for phrase in ('完整艺术标题！', '实色笔画alpha 255', '主体之外alpha 0',
                       '四边保留透明空隙', '完整笔画不得触边', '自带圆弧、笔刷和衬底'):
            self.assertIn(phrase, text)
        self.assertNotIn('背景任务', text)

    def test_opaque_title_obeys_actual_parameter_without_transparent_clearance_claim(self):
        text = self.dispatch('wordart', alpha=False)['instructions']
        self.assertIn('不透明PNG', text)
        self.assertIn('主体实色保持实色', text)
        self.assertIn('四边保留锁定请求规定的底色空隙', text)
        self.assertNotIn('alpha 0', text)
        self.assertNotIn('透明空隙', text)

    def test_board_text_is_byte_identical_to_r12(self):
        task, public, request = self.fixture('board')
        old = self.old_execution()
        old_text, new_text = self.assert_pair_equal_except_instruction(old, task, public, request)
        self.assertEqual(old_text, new_text)

    def test_all_request_bytes_states_and_other_once_fields_remain_equal_to_r12(self):
        old = self.old_execution()
        for role in ('background', 'foreground', 'wordart', 'board'):
            for policy in (None, False, BACKGROUND_CONTAINER_POLICY_ID):
                for form in ('absent', 'sparse', 'explicit'):
                    with self.subTest(role=role, policy=policy, form=form):
                        task, public, request = self.fixture(role, policy=policy, form=form)
                        (self.work / 'requests' / (task['id'] + '-0.json')).unlink(missing_ok=True)
                        self.assert_pair_equal_except_instruction(old, task, public, request)

    def test_direct_same_turn_and_unknown_boundaries_are_kept_for_all_roles(self):
        for role in ('background', 'foreground', 'wordart', 'board'):
            with self.subTest(role=role):
                text = self.dispatch(role)['instructions']
                for phrase in ('本轮直接调用当前' + role + '任务一次', '保存、实际查看并登记',
                               '锁定原文与原参考顺序', 'unknown', '服务实际暴露',
                               '原许可范围保持一次调用', 'waiting只等待、保存或登记已有回图'):
                    self.assertIn(phrase, text)
                self.assertNotIn('先发普通可见', text)

    def test_retained_real_r12_requests_only_change_the_expected_instruction_roles(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r12-medium-paused/job'
        if not job.is_dir():
            self.skipTest('Private real r12 fixture unavailable outside this checkout')
        old = self.old_execution()
        state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        observed = []
        for path in sorted((job / 'requests').glob('*.json')):
            record = json.loads(path.read_text(encoding='utf-8'))
            task = state['tasks'][record['task_id']]
            request = record['request']
            old_text = old._invocation_instructions(task, request)
            new_text = execution._invocation_instructions(task, request)
            self.assertNotEqual(old_text, new_text)
            self.assertIn('本轮直接调用当前' + task['role'] + '任务一次', new_text)
            if task['role'] == 'background':
                self.assertIn('箭头及表格内容均清除', new_text)
            elif task['role'] == 'foreground':
                self.assertIn('参考外观', new_text)
            elif task['role'] == 'wordart':
                self.assertIn('四边保留透明空隙', new_text)
            observed.append((task['role'], record['attempt']))
        self.assertCountEqual(observed, [('background', 0), ('background', 1),
                                         ('foreground', 0), ('wordart', 0)])


if __name__ == '__main__':
    unittest.main()
