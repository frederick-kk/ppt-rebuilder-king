"""Current-role Chinese dispatch with unchanged requests, permits and state."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import execution
from newpath.prompts import BACKGROUND_CONTAINER_POLICY_ID


class SingleTaskInstructions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-single-task-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'briefs').mkdir()
        self.source = self.work / 'working.png'
        self.source.write_bytes(b'offline-source-reference')
        self.board = self.work / 'component-board.png'
        self.board.write_bytes(b'offline-layout-reference')

    def fixture(self, role, policy=None, transparent=None, form='sparse'):
        brief = 'Frozen full ' + role + ' prompt. Keep exact punctuation.\n'
        relative = 'briefs/' + role + '.txt'
        (self.work / relative).write_text(brief, encoding='utf-8')
        task = {'id': 'task-' + role + '-' + str(policy) + '-' + form,
                'role': role, 'attempt': 0, 'brief': relative}
        execution.enroll(task)
        if policy is not None:
            task['decorative_background_policy'] = policy
        references = [str(self.board), str(self.source)] if role == 'board' else [str(self.source)]
        alpha = role != 'background' if transparent is None else transparent
        request = {'tool': 'offline-tool', 'prompt': brief, 'references': references,
                   'size': [1920, 1080], 'transparent_background': alpha, 'requested_outputs': 1}
        if form == 'sparse':
            request['tool_arguments'] = {'transparent_background': alpha}
        elif form == 'explicit':
            request['tool_arguments'] = {'prompt': brief, 'referenced_image_paths': references,
                'size': [1920, 1080], 'transparent_background': alpha, 'num_outputs': 1}
        public = {'references': references, 'size': [1920, 1080], 'transparent_background': alpha,
                  'result_template': {'kind': 'image_result'}, 'failure_template': {'kind': 'image_failed'}}
        return task, public, request

    def dispatch(self, role, **options):
        task, public, request = self.fixture(role, **options)
        return execution.begin(self.work, task, public, request)

    def test_background_true_has_one_concrete_empty_scene_goal_and_all_container_levels(self):
        result = self.dispatch('background', policy=BACKGROUND_CONTAINER_POLICY_ID)
        text = result['instructions']
        self.assertIn('本轮直接调用当前background任务一次', text)
        self.assertIn('不透明PNG', text)
        self.assertIn('所有层级的空纸底、金框、卡片轮廓及分隔', text)
        self.assertIn('原位、比例和轮廓', text)
        self.assertIn('完整艺术字及所属圆弧、刷痕和衬底', text)
        self.assertNotIn('foreground', text)
        self.assertNotIn('wordart', text)
        self.assertNotIn('当前board', text)

    def test_background_missing_and_false_keep_paper_assigned_to_independent_content(self):
        for policy in (None, False):
            with self.subTest(policy=policy):
                text = self.dispatch('background', policy=policy)['instructions']
                self.assertIn('移除分配给独立层的纸框、卡片', text)
                self.assertNotIn('保留所有层级的空纸底', text)

    def test_foreground_container_allocation_follows_current_policy(self):
        current = self.dispatch('foreground', policy=BACKGROUND_CONTAINER_POLICY_ID)['instructions']
        self.assertIn('移除分配给背景的场景和固定容器', current)
        legacy = self.dispatch('foreground', policy=False)['instructions']
        self.assertIn('保留本任务分配的纸框、卡片及页面内容', legacy)

    def test_transparency_is_taken_from_actual_request_for_all_roles(self):
        for role in ('background', 'foreground', 'wordart', 'board'):
            for alpha in (True, False):
                with self.subTest(role=role, alpha=alpha):
                    task, public, request = self.fixture(role, form='mode-' + str(alpha), transparent=alpha)
                    text = execution.begin(self.work, task, public, request)['instructions']
                    if alpha:
                        self.assertIn('真实透明PNG', text)
                        self.assertIn('alpha 0', text)
                    else:
                        self.assertIn('不透明PNG', text)
                        self.assertNotIn('alpha 0', text)

    def test_board_has_current_grid_members_exact_lettering_and_reference_roles(self):
        task, public, request = self.fixture('board')
        task['board'] = {'grid': {'rows': 1, 'columns': 2}, 'members': [
            {'id': 'label-a', 'lettering': '艺术字甲！'}, {'id': 'icon-b'}]}
        text = execution.begin(self.work, task, public, request)['instructions']
        self.assertIn('1行2列', text)
        self.assertIn('label-a（写“艺术字甲！”）', text)
        self.assertIn('icon-b', text)
        self.assertIn('参考1', text)
        self.assertIn('格网布局', text)
        self.assertIn('参考2', text)
        self.assertIn('原图外观', text)
        self.assertNotIn('当前background', text)

    def test_wordart_has_only_the_exact_assigned_complete_title(self):
        task, public, request = self.fixture('wordart')
        task['member'] = {'lettering': '完整标题：甲乙！', 'box': [80, 20, 900, 130]}
        text = execution.begin(self.work, task, public, request)['instructions']
        self.assertIn('只生成指定完整标题“完整标题：甲乙！”', text)
        self.assertIn('自带圆弧、笔刷和衬底', text)
        self.assertNotIn('当前board', text)

    def test_direct_call_registration_and_unknown_boundaries_have_no_visible_message_step(self):
        text = self.dispatch('background')['instructions']
        for phrase in ('本轮直接调用', '保存', '实际查看', '登记', '下一动作', '锁定原文', '原参考顺序',
                       'unknown', '服务实际暴露', 'executed_prompt', '先登记', '并行'):
            self.assertIn(phrase, text)
        for old in ('先发', 'visible stage message', '普通可见', '等待用户继续'):
            self.assertNotIn(old, text)

    def test_requests_state_and_every_other_once_field_are_exactly_equal_to_r11(self):
        old_path = PACKAGE.parent / 'source-r11/compat-runtime/newpath/execution.py'
        if not old_path.exists():
            self.skipTest('Retained r11 fixture is unavailable outside this checkout')
        spec = importlib.util.spec_from_file_location('newpath.old_single_dispatch', old_path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 13, tzinfo=timezone.utc))
        for role in ('background', 'foreground', 'wordart', 'board'):
            for form in ('absent', 'sparse', 'explicit'):
                with self.subTest(role=role, form=form):
                    task, public, request = self.fixture(role, form=form)
                    prior_task = copy.deepcopy(task)
                    with mock.patch.object(old, 'datetime', fixed):
                        prior = old.begin(self.work, prior_task, public, request)
                    expected_bytes = Path(prior['request_file']).read_bytes()
                    current_task = copy.deepcopy(task)
                    with mock.patch.object(execution, 'datetime', fixed):
                        current = execution.begin(self.work, current_task, public, request)
                    self.assertEqual(Path(current['request_file']).read_bytes(), expected_bytes)
                    self.assertEqual(current_task, prior_task)
                    prior.pop('instructions')
                    current.pop('instructions')
                    self.assertEqual(current, prior)
                    self.assertIsNone(execution.begin(self.work, current_task, public, request))


if __name__ == '__main__':
    unittest.main()
