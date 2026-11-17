"""Keep an actual nullable tool prompt unknown while locking the canonical brief."""
import contextlib
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import execution, __main__ as cli
from newpath.storage import read_state, sha256
import test_host_context_dispatch as host_fixture
import test_result_role_binding as role_fixture


class NullableToolPrompt(unittest.TestCase):
    def setUp(self):
        self.host = host_fixture.HostContextDispatch(
            'test_hidden_fields_get_current_role_direct_action_in_same_permitted_call')
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.work = self.host.work

    def fixture(self, role='wordart', form='sparse'):
        task, public, request = self.host.fixture(role, form)
        public['result_template']['executed_prompt'] = None
        return task, public, request

    def null_fixture(self):
        task, public, request = self.fixture()
        request['tool_arguments']['prompt'] = None
        return task, public, request

    def test_actual_null_is_preserved_and_explicitly_unverified(self):
        task, public, request = self.null_fixture()
        before = copy.deepcopy((task, public, request))
        normalized = execution.normalize_request(self.work, task, public, request)
        self.assertIsNone(normalized['tool_arguments']['prompt'])
        self.assertEqual(normalized['prompt'], request['prompt'])
        self.assertEqual(normalized['unverified_tool_argument_fields'], ['prompt'])
        self.assertEqual((task, public, request), before)

    def test_null_prompt_with_unknown_reference_ids_keeps_the_raw_actual_fields(self):
        task, public, request = self.null_fixture()
        request['tool_arguments']['referenced_image_ids'] = None
        request['tool_arguments']['host_context'] = {'exposed': False}
        normalized = execution.normalize_request(self.work, task, public, request)
        self.assertEqual(normalized['tool_arguments'], request['tool_arguments'])
        self.assertEqual(normalized['unverified_tool_argument_fields'],
                         ['referenced_image_ids', 'host_context', 'prompt'])

    def test_canonical_prompt_still_requires_the_exact_full_frozen_text(self):
        task, public, request = self.null_fixture()
        for wrong in (None, '', 'shortened canonical request', False, 0):
            altered = copy.deepcopy(request)
            altered['prompt'] = wrong
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'exactly match'):
                execution.normalize_request(self.work, task, public, altered)

    def test_every_nonnull_wrong_or_empty_actual_prompt_stays_rejected(self):
        task, public, request = self.null_fixture()
        before = copy.deepcopy(task)
        for wrong in ('shortened tool prompt', '', 'unknown', False, True, 0, 1, [], {}):
            altered = copy.deepcopy(request)
            altered['tool_arguments']['prompt'] = wrong
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'contradicts'):
                execution.begin(self.work, task, public, altered)
        self.assertEqual(task, before)
        self.assertFalse((self.work / 'requests').exists())

    def test_null_prompt_preserves_other_actual_parameter_guards(self):
        task, public, request = self.null_fixture()
        for key, wrong in [('n', 2), ('transparent_background', False), ('size', [1, 1]),
                           ('references', ['/wrong/reference.png']),
                           ('referenced_image_paths', ['/wrong/reference.png'])]:
            changed = copy.deepcopy(request)
            changed['tool_arguments'][key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                execution.normalize_request(self.work, task, public, changed)

    def test_begin_saves_null_and_allows_only_one_original_permit(self):
        task, public, request = self.null_fixture()
        permit = execution.begin(self.work, task, public, request)
        self.assertEqual(permit['kind'], 'invoke_once')
        saved_bytes = Path(permit['request_file']).read_bytes()
        record = json.loads(saved_bytes)
        self.assertIsNone(record['request']['tool_arguments']['prompt'])
        self.assertEqual(record['request']['prompt'], request['prompt'])
        self.assertEqual(record['request']['unverified_tool_argument_fields'], ['prompt'])
        self.assertEqual(permit['request_sha256'], sha256(permit['request_file']))
        self.assertIsNone(permit['result_template']['executed_prompt'])
        original = copy.deepcopy(task)
        self.assertIsNone(execution.begin(self.work, task, public, request))
        self.assertEqual(task, original)
        self.assertEqual(Path(permit['request_file']).read_bytes(), saved_bytes)

    def test_changing_null_to_an_exact_value_after_begin_cannot_replace_the_request(self):
        task, public, request = self.null_fixture()
        permit = execution.begin(self.work, task, public, request)
        original = Path(permit['request_file']).read_bytes()
        changed = copy.deepcopy(request)
        changed['tool_arguments']['prompt'] = request['prompt']
        with self.assertRaisesRegex(ValueError, 'immutable'):
            execution.begin(self.work, task, public, changed)
        self.assertEqual(Path(permit['request_file']).read_bytes(), original)

    def test_null_never_fills_the_executed_prompt_or_claims_an_audit_match(self):
        task, public, request = self.null_fixture()
        execution.begin(self.work, task, public, request)
        audit = execution.audit_returned_prompt(self.work, task, {'executed_prompt': None}, 'a' * 64)
        self.assertEqual(audit['status'], 'unknown')
        self.assertIsNone(audit['reported_prompt'])
        self.assertIsNone(audit['reported_prompt_sha256'])
        self.assertEqual(audit['expected_prompt'], request['prompt'])
        summary = execution.prompt_audit_summary({'tasks': {task['id']: task}})
        self.assertEqual(summary['unknown_count'], 1)
        self.assertEqual(summary['match_count'], 0)

    def test_nullable_request_still_has_real_integrity_hash_protection(self):
        task, public, request = self.null_fixture()
        permit = execution.begin(self.work, task, public, request)
        state = {'execution_policy': execution.MODE, 'tasks': {task['id']: task}}
        execution.integrity(self.work, state)
        path = Path(permit['request_file'])
        record = json.loads(path.read_text('utf-8'))
        record['request']['tool_arguments']['prompt'] = request['prompt']
        path.write_text(json.dumps(record), 'utf-8')
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            execution.integrity(self.work, state)

    def test_every_old_legal_request_and_entire_once_reply_match_frozen_r20_bytes(self):
        path = PACKAGE.parent / 'source-r20/compat-runtime/newpath/execution.py'
        if not path.is_file():
            self.skipTest('Retained frozen R20 fixture is unavailable')
        spec = importlib.util.spec_from_file_location('newpath.r20_nullable_compat', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        for role in ('background', 'foreground', 'board', 'wordart'):
            for form in ('absent', 'sparse', 'explicit'):
                with self.subTest(role=role, form=form):
                    task, public, request = self.fixture(role, form)
                    old_task = copy.deepcopy(task)
                    (self.work / 'requests' / (task['id'] + '-0.json')).unlink(missing_ok=True)
                    with mock.patch.object(old, 'datetime', fixed):
                        previous = old.begin(self.work, old_task, public, request)
                    previous_bytes = Path(previous['request_file']).read_bytes()
                    new_task = copy.deepcopy(task)
                    with mock.patch.object(execution, 'datetime', fixed):
                        current = execution.begin(self.work, new_task, public, request)
                    self.assertEqual(current, previous)
                    self.assertEqual(new_task, old_task)
                    self.assertEqual(Path(current['request_file']).read_bytes(), previous_bytes)


class NullableToolPromptNormalCLI(unittest.TestCase):
    def test_actual_null_records_through_existing_cli_without_any_generation(self):
        fixture = role_fixture.ReviewedOutputFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        public = fixture.start()['background']
        begin = copy.deepcopy(public['begin_template'])
        begin['request']['tool'] = 'offline-context-tool'
        begin['request']['tool_arguments'] = {'prompt': None, 'transparent_background': False}
        path = fixture.root / 'begin-actual-null.json'
        path.write_text(json.dumps(begin), 'utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = cli.main(['--work', str(fixture.work), '--input', str(path)])
        self.assertEqual(code, 0)
        permit = json.loads(stdout.getvalue())
        self.assertEqual(permit['kind'], 'invoke_once')
        self.assertIsNone(permit['request']['tool_arguments']['prompt'])
        saved = read_state(fixture.work)
        self.assertEqual(saved['tasks'][public['id']]['execution']['state'], 'invoking')
        self.assertEqual(saved['tasks'][public['id']]['attempt'], 0)
        original_request = Path(permit['request_file']).read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as repeated:
            second_code = cli.main(['--work', str(fixture.work), '--input', str(path)])
        self.assertEqual(second_code, 0)
        self.assertNotEqual(json.loads(repeated.getvalue())['kind'], 'invoke_once')
        self.assertEqual(Path(permit['request_file']).read_bytes(), original_request)


if __name__ == '__main__':
    unittest.main()
