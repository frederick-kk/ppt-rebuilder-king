"""Optional terminal stdout projection leaves production artifacts unchanged."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import __main__ as entry
from newpath import controller, diagnostics


class CompactCliDone(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-compact-cli-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def receipt(self):
        return {'kind': 'done', 'terminal': True, 'pptx': str(self.work / 'output.pptx'),
                'sha256': 'actual-content-hash', 'quality': 'not_evaluated',
                'expected_generation_tasks': 6, 'warnings': [], 'skipped': [],
                'controller_wall_seconds': 735.125, 'user_end_to_end_seconds': None,
                'within_600_seconds': None, 'caller_reported_invocations': None,
                'caller_reported_known_invocations': 0,
                'instructions': 'Deliver the existing PowerPoint and disclose actual omissions.',
                'post_return_prompt_audit': {'per_task': [{'unknown': None}]},
                'output_role_binding': {'claims': [{'matches_task': False}]},
                'observed_task_attempt_file_counts': [{'task_id': 't1', 'unique_known_files': 1}]}

    def invoke_cli(self, arguments):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = entry.main(['--work', str(self.work), *arguments])
        return code, stream.getvalue()

    def snapshot(self):
        return {path.relative_to(self.work).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.work.rglob('*') if path.is_file()}

    def prepare_done_job(self, receipt):
        presentation = self.work / 'output.pptx'
        with ZipFile(presentation, 'w') as archive:
            archive.writestr('ppt/presentation.xml', '<presentation/>')
            archive.writestr('ppt/slides/slide1.xml', '<slide/>')
        receipt = copy.deepcopy(receipt)
        receipt['pptx'] = str(presentation)
        receipt['sha256'] = hashlib.sha256(presentation.read_bytes()).hexdigest()
        state = {'format': 'newpath.job/1', 'phase': 'done', 'tasks': {}, 'done': receipt}
        for filename, value in [('state.json', state), ('receipt.json', receipt)]:
            (self.work / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.work / '.lock').write_bytes(b'1')
        return receipt

    def test_projection_keeps_terminal_unknowns_scalars_and_absolute_receipt_pointer(self):
        original = self.receipt()
        original['known_false'] = False
        original['known_zero'] = 0
        result = entry._compact_done(original, self.work)
        for name, value in original.items():
            if value is None or isinstance(value, (str, bool, int, float)):
                self.assertEqual(result[name], value)
        self.assertEqual(result['skipped'], [])
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['warning_count'], 0)
        self.assertEqual(result['receipt_file'], str((self.work / 'receipt.json').resolve()))
        for name in ('post_return_prompt_audit', 'output_role_binding', 'observed_task_attempt_file_counts'):
            self.assertNotIn(name, result)
        self.assertEqual(original, self.receipt() | {'known_false': False, 'known_zero': 0})

    def test_all_missing_items_and_delivery_summary_are_preserved_without_truncation(self):
        original = self.receipt()
        missing = [{'id': 'missing-' + str(index), 'name': '完整素材名称' * 30,
                    'reason': 'actual_skip', 'source_box': [0, 0, 100, 100]}
                   for index in range(250)]
        original['skipped'] = copy.deepcopy(missing)
        original['delivery_summary'] = {'priority': 'missing_content', 'missing_count': len(missing),
                                        'missing_items': copy.deepcopy(missing), 'message': '实际缺项逐项披露'}
        before = copy.deepcopy(original)
        result = entry._compact_done(original, self.work)
        self.assertEqual(result['skipped'], missing)
        self.assertEqual(result['delivery_summary'], before['delivery_summary'])
        self.assertEqual(original, before)

    def test_warning_flat_scalar_fields_and_true_count_survive_stable_deduplication(self):
        first = {'id': 'o1', 'reason': 'weak_geometry', 'message': '完整实有警告' * 200,
                 'actual': False, 'unknown': None, 'count': 0, 'ratio': 0.5,
                 'box': [0, 0, 100, 100], 'details': {'pixels': [{'x': 1}]}}
        second = {**first, 'id': 'o2'}
        original = self.receipt()
        original['warnings'] = [first, copy.deepcopy(first), second, 'actual simple warning', 'actual simple warning']
        before = copy.deepcopy(original)
        result = entry._compact_done(original, self.work)
        self.assertEqual(result['warning_count'], 5)
        self.assertEqual(len(result['warnings']), 3)
        self.assertEqual(result['warnings'][0], {key: value for key, value in first.items()
                                               if key not in ('box', 'details')})
        self.assertEqual(result['warnings'][1]['id'], 'o2')
        self.assertEqual(result['warnings'][2], 'actual simple warning')
        self.assertEqual(original, before)

    def test_unknown_warning_collection_is_not_rewritten_as_zero(self):
        original = self.receipt()
        original['warnings'] = None
        result = entry._compact_done(original, self.work)
        self.assertIsNone(result['warnings'])
        self.assertIsNone(result['warning_count'])

    def test_every_non_done_action_passes_through_without_new_fields(self):
        for kind in ('generate', 'invoke_once', 'await_results', 'registration_required',
                     'describe', 'paused', 'error', 'future_action'):
            original = {'kind': kind, 'unknown': None, 'metadata': {'source': [1, 2]}}
            with self.subTest(kind=kind):
                self.assertEqual(entry._compact_done(original, self.work), original)

    def test_default_cli_and_direct_run_preserve_complete_terminal_response(self):
        receipt = self.prepare_done_job(self.receipt())
        before = self.snapshot()
        self.assertEqual(controller.run(self.work), receipt)
        code, output = self.invoke_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(output, json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        self.assertEqual(self.snapshot(), before)

    def test_normal_subprocess_cli_compacts_existing_done_state_without_artifact_writes(self):
        receipt = self.prepare_done_job(self.receipt())
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', '-m', 'newpath', '--work', str(self.work), '--compact'],
                                cwd=PACKAGE / 'compat-runtime', capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), entry._compact_done(receipt, self.work))
        self.assertEqual(self.snapshot(), before)

    def test_compact_non_done_cli_response_is_identical_to_default(self):
        original = {'kind': 'paused', 'generate_more': False, 'failures': [{'reason': 'actual_failure'}],
                    'instructions': 'Keep the failed real return.'}
        with mock.patch.object(controller, 'run', return_value=copy.deepcopy(original)):
            plain = self.invoke_cli([])
            compact = self.invoke_cli(['--compact'])
        self.assertEqual(compact, plain)
        self.assertEqual(json.loads(compact[1]), original)

    def test_errors_remain_identical_with_compact_flag(self):
        with mock.patch.object(controller, 'run', side_effect=ValueError('Actual source failure')):
            plain = self.invoke_cli([])
            compact = self.invoke_cli(['--compact'])
        self.assertEqual(compact, plain)
        self.assertEqual(compact[0], 2)
        self.assertEqual(json.loads(compact[1])['message'], 'Actual source failure')

    def test_diagnostics_response_is_unchanged_and_not_projected(self):
        response = {'kind': 'done', 'diagnostics': {'files': [{'sha256': 'actual'}]}, 'unknown': None}
        destination = str(self.work.parent / 'external-diagnostic.zip')
        with mock.patch.object(diagnostics, 'export_diagnostics', return_value=response) as exporter:
            plain = self.invoke_cli(['--diagnostics', destination])
            compact = self.invoke_cli(['--diagnostics', destination, '--compact'])
        self.assertEqual(compact, plain)
        self.assertEqual(json.loads(compact[1]), response)
        self.assertEqual(exporter.call_count, 2)

    def test_real_r13_receipt_projection_keeps_artifacts_and_all_warning_scalar_fields(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r13-continuation-run/job'
        if not job.is_dir():
            self.skipTest('Private R13 receipt unavailable outside this checkout')
        files = [job / 'receipt.json', job / 'state.json', job / 'output/PPT Rebuilder King.pptx']
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        raw = files[0].read_bytes()
        receipt = json.loads(raw)
        self.assertEqual(len(raw), 68792)
        result = entry._compact_done(receipt, job)
        self.assertEqual(result['pptx'], receipt['pptx'])
        self.assertEqual(result['sha256'], '0770fd62153b818af07c109592af69fe377dc49dca42cea48449d7441d91ba53')
        self.assertEqual(result['skipped'], receipt['skipped'])
        self.assertEqual(result.get('delivery_summary'), receipt.get('delivery_summary'))
        self.assertEqual(result['warning_count'], len(receipt['warnings']))
        for warning in receipt['warnings']:
            flat = ({key: value for key, value in warning.items()
                     if value is None or isinstance(value, (str, bool, int, float))}
                    if isinstance(warning, dict) else warning)
            self.assertIn(flat, result['warnings'])
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode('utf-8')), len(raw))
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


if __name__ == '__main__':
    unittest.main()
