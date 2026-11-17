"""Optional stdout projection retains complete, immutable controller actions."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from newpath import __main__ as entry
from newpath import controller


class FileBackedActions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-action-projection-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'
        self.action = self.root / 'actions' / 'current.json'

    def invoke(self, response, *, file_actions=False, action_file=True, compact=True):
        arguments = ['--work', str(self.work)]
        if action_file:
            arguments.extend(['--action-file', str(self.action)])
        if compact:
            arguments.append('--compact')
        if file_actions:
            arguments.append('--file-actions')
        output = io.StringIO()
        errors = io.StringIO()
        with mock.patch.object(controller, 'run', return_value=response):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                try:
                    code = entry.main(arguments)
                except SystemExit as error:
                    code = error.code
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_generate_keeps_full_templates_on_disk_and_member_details_in_stdout(self):
        response = {'kind': 'generate', 'instructions': 'Read the actual source.',
                    'jobs': [{'id': 'board-one', 'role': 'board',
                               'members': [{'id': 'a', 'slot': [10, 10, 50, 50]}],
                               'references': ['board.png', 'source.png'],
                               'begin_template': {'kind': 'begin', 'request': {'prompt': 'locked' * 4000}}}],
                    'future_field': {'preserved': True}}
        before = copy.deepcopy(response)
        code, output = self.invoke(response, file_actions=True)
        self.assertEqual(code, 0)
        saved = json.loads(self.action.read_text('utf-8'))
        self.assertEqual(saved, {**response, 'action_file': str(self.action.resolve())})
        self.assertFalse('begin_template' in output['jobs'][0], 'The real jobs entry still exposes its full template.')
        for field in ('id', 'role', 'members', 'references'):
            self.assertEqual(output['jobs'][0][field], response['jobs'][0][field])
        self.assertEqual(output['action_file'], str(self.action.resolve()))
        self.assertEqual(output['future_field'], response['future_field'])
        self.assertEqual(response, before)
        self.assertLess(len(json.dumps(output)), len(json.dumps(saved)) / 3)

    def test_permit_retains_hashes_review_and_goal_while_request_stays_in_full_file(self):
        response = {'kind': 'invoke_once', 'task_id': 'a', 'permit_id': 'real-permit',
                    'request_sha256': 'abc', 'request_file': 'actual-request.json',
                    'request': {'prompt': 'locked' * 4000, 'tool_arguments': {'prompt': 'locked' * 4000}},
                    'references': ['real.png'], 'instructions': 'Current scoped goal.',
                    'result_template': {'role_review': {'matches_task': None}}}
        code, output = self.invoke(response, file_actions=True)
        self.assertEqual(code, 0)
        self.assertNotIn('request', output)
        for field in ('permit_id', 'request_sha256', 'request_file', 'references', 'instructions', 'result_template'):
            self.assertEqual(output[field], response[field])
        self.assertEqual(json.loads(self.action.read_text('utf-8'))['request'], response['request'])

    def test_a_permit_without_request_file_retains_its_only_request_copy(self):
        response = {'kind': 'invoke_once', 'request': {'prompt': 'sole actual request'}}
        code, output = self.invoke(response, file_actions=True)
        self.assertEqual(code, 0)
        self.assertEqual(output['request'], response['request'])

    def test_existing_compact_and_no_action_file_behaviors_remain_exact(self):
        response = {'kind': 'invoke_once', 'request_file': 'actual.json', 'request': {'prompt': 'unchanged'}}
        code, output = self.invoke(response)
        self.assertEqual(code, 0)
        self.assertEqual(output, {**response, 'action_file': str(self.action.resolve())})
        code, output = self.invoke(response, file_actions=True, action_file=False)
        self.assertEqual(code, 0)
        self.assertEqual(output, response)

    def test_done_saves_full_action_and_receipt_details_before_stdout_projection(self):
        response = {'kind': 'done', 'pptx': 'actual.pptx', 'warnings': [], 'skipped': [],
                    'role_history': [{'actual': True}], 'delivery_summary': {'missing': []}}
        code, output = self.invoke(response, file_actions=True)
        self.assertEqual(code, 0)
        self.assertNotIn('role_history', output)
        self.assertEqual(json.loads(self.action.read_text('utf-8'))['role_history'], response['role_history'])
        self.assertEqual(output['delivery_summary'], response['delivery_summary'])

    def test_unknown_action_fields_are_preserved(self):
        response = {'kind': 'future_action', 'new_protocol': {'do_not_prune': ['real']}}
        code, output = self.invoke(response, file_actions=True)
        self.assertEqual(code, 0)
        self.assertEqual(output, {**response, 'action_file': str(self.action.resolve())})

    def test_actual_recorded_generate_actions_use_the_jobs_entry(self):
        fixture_root = Path(__file__).resolve().parents[3] / 'heritage-20261112/engineering/r19-action-volume'
        if not fixture_root.is_dir():
            self.skipTest('The private recorded-action fixture is unavailable.')
        for name in ('generate-layers.json', 'generate-title-and-two-boards.json'):
            response = json.loads((fixture_root / name).read_text('utf-8'))
            self.assertTrue(response['jobs'])
            code, output = self.invoke(response, file_actions=True)
            self.assertEqual(code, 0)
            saved = json.loads(self.action.read_text('utf-8'))
            self.assertEqual(saved, {**response, 'action_file': str(self.action.resolve())})
            self.assertEqual(len(output['jobs']), len(response['jobs']))
            for old, new in zip(response['jobs'], output['jobs']):
                self.assertFalse('begin_template' in new, 'The actual task template was duplicated in stdout.')
                self.assertEqual({k: v for k, v in new.items() if k != 'begin_template_file'},
                                 {k: v for k, v in old.items() if k != 'begin_template'})


if __name__ == '__main__':
    unittest.main()
