"""Outgoing host adaptation and unchanged execution records; zero provider calls."""
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
from newpath import execution, prompts
from newpath.native import MODE as MATERIAL_MODE


class HostContextDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prior = PACKAGE.parent / 'source-r11/compat-runtime/newpath/execution.py'
        if not prior.is_file():
            cls.prior = None
            return
        spec = importlib.util.spec_from_file_location('newpath.dispatch_prior_execution', prior)
        cls.prior = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.prior)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-host-context-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / 'briefs').mkdir()
        self.reference = self.work / 'working.png'
        self.reference.write_bytes(b'synthetic-reference-identity-only')
        self.board = self.work / 'board.png'
        self.board.write_bytes(b'synthetic-layout-reference-identity-only')

    def fixture(self, role, form='sparse'):
        brief = ('Frozen ' + role + ' task. Preserve every exact character and source role.\n')
        path = self.work / 'briefs' / (role + '.txt')
        path.write_text(brief, encoding='utf-8')
        references = [str(self.board), str(self.reference)] if role == 'board' else [str(self.reference)]
        transparent = role != 'background'
        task = {'id': 'task-' + role + '-' + form, 'role': role, 'attempt': 0,
                'brief': 'briefs/' + role + '.txt', 'status': 'ready'}
        execution.enroll(task)
        public = {'references': references, 'size': [1920, 1080], 'transparent_background': transparent,
                  'result_template': {'kind': 'image_result', 'task_id': task['id'], 'role': role},
                  'failure_template': {'kind': 'image_failed', 'task_id': task['id'], 'role': role}}
        request = {'tool': 'synthetic-host-tool', 'prompt': brief, 'references': references,
                   'size': [1920, 1080], 'transparent_background': transparent, 'requested_outputs': 1}
        if form == 'sparse':
            request['tool_arguments'] = {'transparent_background': transparent}
        elif form == 'explicit':
            request['tool_arguments'] = {'prompt': brief, 'referenced_image_paths': references,
                'size': [1920, 1080], 'transparent_background': transparent, 'num_outputs': 1}
        return task, public, request

    def begin(self, role='background', form='sparse'):
        task, public, request = self.fixture(role, form)
        return execution.begin(self.work, task, public, request)

    def test_explicit_fields_preserve_original_prompt_and_reference_order(self):
        instructions = self.begin()['instructions']
        self.assertIn('显式prompt及参考字段', instructions)
        self.assertIn('锁定原文', instructions)
        self.assertIn('原参考顺序', instructions)

    def test_hidden_fields_get_current_role_direct_action_in_same_permitted_call(self):
        instructions = self.begin()['instructions']
        self.assertIn('本轮直接调用当前background任务一次', instructions)
        self.assertIn('只生成', instructions)
        self.assertIn('参考1', instructions)
        self.assertIn('宿主真实支持的机制', instructions)
        self.assertIn('实际查看并登记', instructions)
        self.assertNotIn('visible stage message', instructions)
        self.assertNotIn('先发', instructions)

    def test_unisolated_context_serializes_registration_and_explicit_binding_allows_parallel(self):
        instructions = self.begin('board')['instructions']
        self.assertIn('上下文无法隔离', instructions)
        self.assertIn('先登记再下一任务', instructions)
        self.assertIn('独立绑定且宿主支持时可并行', instructions)

    def test_provider_prompt_stays_unknown_and_current_permit_stays_single_call(self):
        instructions = self.begin()['instructions']
        self.assertIn('任务一次', instructions)
        self.assertIn('原许可范围', instructions)
        self.assertIn('unknown', instructions)
        self.assertIn('executed_prompt仅记服务实际暴露的原文', instructions)

    def test_r11_request_bytes_state_and_all_other_once_fields_are_exactly_unchanged(self):
        if self.prior is None:
            self.skipTest('The retained r11 workspace fixture is unavailable outside this audit checkout')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 13, tzinfo=timezone.utc))
        for role in ('background', 'foreground', 'wordart', 'board'):
            for form in ('absent', 'sparse', 'explicit'):
                with self.subTest(role=role, form=form):
                    task, public, request = self.fixture(role, form)
                    old_task = copy.deepcopy(task)
                    with mock.patch.object(self.prior, 'datetime', fixed):
                        old = self.prior.begin(self.work, old_task, public, request)
                    old_bytes = Path(old['request_file']).read_bytes()
                    new_task = copy.deepcopy(task)
                    with mock.patch.object(execution, 'datetime', fixed):
                        new = execution.begin(self.work, new_task, public, request)
                    self.assertEqual(Path(new['request_file']).read_bytes(), old_bytes)
                    self.assertEqual(new_task, old_task)
                    old.pop('instructions')
                    new.pop('instructions')
                    self.assertEqual(new, old)
                    before = Path(new['request_file']).read_bytes()
                    self.assertIsNone(execution.begin(self.work, new_task, public, request))
                    self.assertEqual(Path(new['request_file']).read_bytes(), before)

    def test_sparse_unknown_fields_remain_sparse_and_explicit_contradictions_still_fail(self):
        task, public, request = self.fixture('background')
        result = execution.begin(self.work, task, public, request)
        self.assertEqual(result['request']['tool_arguments'], {'transparent_background': False})
        task, public, request = self.fixture('foreground', 'explicit')
        request['tool_arguments']['prompt'] = 'wrong shortened prompt'
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            execution.begin(self.work, task, public, request)

    def test_documents_keep_unknown_fields_truthful_and_register_existing_returns(self):
        expected = {
            'compat-runtime/SKILL.md': (
                '首次invoke_once后调用图像工具一次，请求一张',
                '未知服务字段据实保留unknown',
                '多张已存在回图逐张复核',
                '如实保留实际数量',
                '登记未完成时先补现有回图，沿同一作业继续',
            ),
            'PROJECT_INSTRUCTIONS.txt': (
                '首次invoke_once后调用一次并请求一张',
                '未知信息保持未知',
                '已有多张回图逐张复核并保留实际数量',
                '合格结果沿用',
                '真实失败按控制器修复动作在同一作业继续',
            ),
        }
        for relative, phrases in expected.items():
            document = (PACKAGE / relative).read_text(encoding='utf-8')
            for phrase in phrases:
                with self.subTest(document=relative, phrase=phrase):
                    self.assertIn(phrase, document)

    def test_stage_context_preserves_paper_ownership_for_missing_false_and_true_policy(self):
        transform = {'offset': [0, 0], 'resized': [1920, 1080]}
        for policy in ('missing', 'false', 'true'):
            for role in ('background', 'foreground'):
                with self.subTest(policy=policy, role=role):
                    task, public, request = self.fixture(role, 'policy-' + policy)
                    options = {} if policy == 'missing' else {'decorative_background': policy == 'true'}
                    if policy == 'true':
                        task['decorative_background_policy'] = prompts.BACKGROUND_CONTAINER_POLICY_ID
                    brief = prompts.layer_brief(role, transform, background_mode=MATERIAL_MODE, **options)
                    (self.work / task['brief']).write_text(brief, encoding='utf-8')
                    request['prompt'] = brief
                    result = execution.begin(self.work, task, public, request)
                    self.assertEqual(result['request']['prompt'], brief)
                    if policy == 'true':
                        self.assertIn('Empty decorative paper bases', brief)
                        self.assertIn('remain in the background', brief)
                    else:
                        self.assertIn('Separate page elements belong to the foreground: artistic headings, paper or card panels', brief)
                    instructions = result['instructions']
                    if policy == 'true' and role == 'background':
                        self.assertIn('所有层级的空纸底、金框、卡片轮廓及分隔', instructions)
                    elif policy == 'true':
                        self.assertIn('移除分配给背景的场景和固定容器', instructions)
                    elif role == 'background':
                        self.assertIn('移除分配给独立层的纸框、卡片', instructions)
                    else:
                        self.assertIn('保留本任务分配的纸框、卡片及页面内容', instructions)

    def test_documents_keep_sequential_layers_and_true_transparent_final_boards(self):
        expected = {
            'compat-runtime/SKILL.md': (
                '--sequential-layers',
                '--foreground-components',
                '--keyed-materials',
                '首次启动只派发背景，背景登记成功后派发前景，两层登记后返回describe',
                '整页前景是采样参考',
                '指定纯色空区属于程序去底输入',
                '按当前任务生成真实透明PNG组件板',
            ),
            'PROJECT_INSTRUCTIONS.txt': (
                '先生成纯背景',
                '随后生成整页前景参考',
                '程序去底得到透明参考',
                '最终组件板生成真实透明PNG并由程序切分',
                '两层完成后一次描述页面',
            ),
        }
        for relative, phrases in expected.items():
            document = (PACKAGE / relative).read_text(encoding='utf-8')
            for phrase in phrases:
                with self.subTest(document=relative, phrase=phrase):
                    self.assertIn(phrase, document)


if __name__ == '__main__':
    unittest.main()
