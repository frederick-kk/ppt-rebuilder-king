"""Exact-directory diagnostics exports, using local synthetic records only."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

PACKAGE = Path(__file__).resolve().parents[2]
RUNTIME = PACKAGE / 'compat-runtime'
sys.dont_write_bytecode = True
sys.path.insert(0, str(RUNTIME))


class JobDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-diagnostics-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = self.root / 'exact-job'
        self.job.mkdir()
        self.output = self.root / 'diagnostics.zip'
        self.state = {'format': 'newpath.job/1', 'phase': 'done',
                      'source': {'original': 'input/original.png', 'byte_sha256': 'recorded-source-hash',
                                 'pixel_sha256': 'recorded-pixel-hash', 'working': 'input/missing-working.png'},
                      'tasks': {'task-one': {'id': 'task-one', 'role': 'foreground', 'status': 'failed',
                                'attempt': 0, 'brief': 'briefs/task-one.txt',
                                'references': {'input/missing-reference.png': 'recorded-reference-hash'},
                                'execution': {'state': 'failed', 'request_file': 'requests/task-one.json'},
                                'attempts': [{'raw': 'returned/missing.png'}]}},
                      'events': [{'event': 'source_prepared', 'at_utc': '2026-01-01T00:00:00+00:00'}]}
        self.write('state.json', json.dumps(self.state, ensure_ascii=False).encode('utf-8'))
        self.write('input/original.png', b'original image bytes\x00\xff')
        self.write('returned/saved.png', b'first actual returned bytes\x00\xff')
        self.write('briefs/task-one.txt', '保持原始中文描述\r\n'.encode('utf-8'))
        self.write('requests/task-one.json', b'{"request":{"tool":"synthetic","prompt":"frozen","tool_arguments":{"quality":"low"}}}')
        self.write('build/compiled.report.json', b'{"validation":{"office_render_checked":false}}')

    def write(self, name, data):
        path = self.job / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def snapshot(self):
        return {p.relative_to(self.job).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.job.rglob('*') if p.is_file()}

    def cli(self, *arguments, work=None, output=None):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(RUNTIME)
        return subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'newpath', '--work',
                               str(work or self.job), '--diagnostics', str(output or self.output), *arguments],
                              capture_output=True, text=True, encoding='utf-8', env=env)

    def diagnostics(self):
        return importlib.import_module('newpath.diagnostics')

    def test_export_retains_every_original_byte_and_exact_job_binding_without_job_mutation(self):
        before = self.snapshot()
        sibling = self.root / 'newer-job'
        sibling.mkdir()
        (sibling / 'state.json').write_text('{"phase":"different"}', encoding='utf-8')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with ZipFile(self.output) as archive:
            index = json.loads(archive.read('index.json'))
            self.assertEqual(index['work'], str(self.job.resolve()))
            self.assertTrue(index['read_only'])
            self.assertEqual(index['workflow']['phase'], 'done')
            self.assertEqual(index['workflow']['tasks']['task-one']['status'], 'failed')
            self.assertEqual(index['source']['byte_sha256'], 'recorded-source-hash')
            for name, (data, _) in before.items():
                self.assertEqual(archive.read('job/' + name), data)
                row = next(row for row in index['files'] if row['archive_path'] == 'job/' + name)
                self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(row['timestamp_provenance'], 'filesystem_mtime_not_execution_time')
            self.assertIn('runtime/newpath/controller.py', archive.namelist())
            self.assertIn('runtime/SKILL.md', archive.namelist())
            self.assertIn('runtime/MANIFEST.json', archive.namelist())
            self.assertIn('project/PROJECT_INSTRUCTIONS.txt', archive.namelist())
            self.assertFalse(any('newer-job' in name or 'node_modules/' in name or '__pycache__/' in name for name in archive.namelist()))
            missing = {row['path'] for row in index['missing_files']}
            self.assertTrue({'receipt.json', 'initialization.json', 'input/missing-working.png',
                             'input/missing-reference.png', 'returned/missing.png'} <= missing)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.job / '.lock').exists())

    def test_diagnostics_routes_before_controller_import(self):
        from newpath import __main__ as cli
        original_import = __import__
        def guarded_import(name, *args, **kwargs):
            if name == 'controller' or name == 'newpath.controller':
                raise AssertionError('diagnostics imported the advancing controller')
            return original_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=guarded_import), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(cli.main(['--work', str(self.job), '--diagnostics', str(self.output)]), 0)
        self.assertTrue(self.output.is_file())

    def test_missing_selected_job_does_not_search_siblings_or_create_directory(self):
        missing = self.root / 'absent-job'
        result = self.cli(work=missing)
        self.assertEqual(result.returncode, 2)
        self.assertIn('existing', result.stdout.lower())
        self.assertFalse(missing.exists())
        self.assertFalse(self.output.exists())

    def test_output_inside_job_or_runtime_is_rejected_before_creation(self):
        for destination in (self.job / 'diagnostics.zip', RUNTIME / 'diagnostics-must-not-exist.zip'):
            with self.subTest(destination=destination):
                result = self.cli(output=destination)
                self.assertEqual(result.returncode, 2)
                self.assertIn('inside', result.stdout.lower())
                self.assertFalse(destination.exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'existing output')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('existing output', result.stdout.lower())
        self.assertEqual(self.output.read_bytes(), b'existing output')

    def test_corrupt_state_bytes_are_retained_with_explicit_issue(self):
        self.write('state.json', b'{broken json')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.read('job/state.json'), b'{broken json')
            index = json.loads(archive.read('index.json'))
        self.assertIsNone(index['workflow']['phase'])
        self.assertTrue(index['record_issues'])

    def test_external_recorded_input_is_not_followed_and_is_listed(self):
        outside = self.root / 'outside-sensitive.txt'
        outside.write_bytes(b'unrelated external bytes')
        self.state['source']['input_path'] = str(outside)
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with ZipFile(self.output) as archive:
            index = json.loads(archive.read('index.json'))
            self.assertFalse(any(archive.read(name) == outside.read_bytes() for name in archive.namelist()))
        self.assertIn(str(outside), {row['path'] for row in index['uncollected_external_references']})

    def test_mutually_exclusive_submission_and_retry_arguments_have_real_error_exit(self):
        for arguments in (('--source', str(self.root / 'source.png')),
                          ('--input', str(self.root / 'registration.json')),
                          ('--retry-task', 'task-one'), ('--retry-authorization', 'user quote')):
            with self.subTest(arguments=arguments):
                before = self.snapshot()
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.output.exists())
                self.assertEqual(before, self.snapshot())

    def test_credentials_abort_export_without_leaking_or_changing_job(self):
        for text in ('{"api_key":"do-not-export-secret"}', 'Authorization: Bearer abcdefghijklmnop',
                     'password=private-value', 'https://service.invalid/?access_token=private-value'):
            with self.subTest(text=text):
                self.write('requests/unsafe.txt', text.encode('utf-8'))
                before = self.snapshot()
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertIn('credential', result.stdout.lower())
                self.assertNotIn('private-value', result.stdout)
                self.assertNotIn('do-not-export-secret', result.stdout)
                self.assertFalse(self.output.exists())
                self.assertEqual(before, self.snapshot())

    def test_safe_request_checker_rejects_forbidden_tool_arguments(self):
        self.write('requests/task-one.json', b'{"request":{"tool_arguments":{"headers":{"x-custom":"private-value"}}}}')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('credential', result.stdout.lower())
        self.assertFalse(self.output.exists())

    def test_unicode_escaped_credentials_in_json_abort_export(self):
        self.write('receipt.json', b'{"api\\u005fkey":"private-value"}')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('credential', result.stdout.lower())
        self.assertFalse(self.output.exists())

    def test_existing_exclusive_job_lock_is_rejected_without_job_mutation(self):
        from newpath.storage import job_lock
        with job_lock(self.job):
            pass
        before = self.snapshot()
        with job_lock(self.job):
            result = self.cli()
            self.assertEqual(result.returncode, 2)
            self.assertIn('updating', result.stdout.lower())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output.exists())

    def test_existing_unlocked_lock_file_is_preserved_byte_for_byte(self):
        self.write('.lock', b'1')
        before = self.snapshot()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_state_change_rejects_archive_and_cleans_temporary_output(self):
        diagnostics = self.diagnostics()
        original = ZipFile.writestr
        changed = False
        def write_then_change(archive, *args, **kwargs):
            nonlocal changed
            result = original(archive, *args, **kwargs)
            if not changed:
                changed = True
                self.write('state.json', b'{"phase":"concurrent-update"}')
            return result
        with mock.patch.object(ZipFile, 'writestr', write_then_change):
            with self.assertRaisesRegex(ValueError, 'changed'):
                diagnostics.export_diagnostics(self.job, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.diagnostics.zip.*.tmp')))

    def test_concurrent_destination_creation_is_never_overwritten(self):
        diagnostics = self.diagnostics()
        original = ZipFile.writestr
        def write_then_reserve(archive, *args, **kwargs):
            result = original(archive, *args, **kwargs)
            if not self.output.exists():
                self.output.write_bytes(b'concurrent owner')
            return result
        before = self.snapshot()
        with mock.patch.object(ZipFile, 'writestr', write_then_reserve):
            with self.assertRaises(OSError):
                diagnostics.export_diagnostics(self.job, self.output)
        self.assertEqual(self.output.read_bytes(), b'concurrent owner')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.root.glob('.diagnostics.zip.*.tmp')))

    def test_linked_job_contents_are_rejected_without_following_target(self):
        outside = self.root / 'unrelated'
        outside.mkdir()
        (outside / 'sensitive.txt').write_bytes(b'external bytes')
        link = self.job / 'linked'
        if os.name == 'nt':
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                     capture_output=True)
            if created.returncode:
                self.skipTest('junction creation unavailable')
        else:
            link.symlink_to(outside, target_is_directory=True)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('linked', result.stdout.lower())
        self.assertFalse(self.output.exists())
        self.assertEqual((outside / 'sensitive.txt').read_bytes(), b'external bytes')

    def test_interrupted_job_exports_recorded_source_hash_from_initialization(self):
        (self.job / 'state.json').unlink()
        self.write('initialization.json', b'{"format":"newpath.start/1","byte_sha256":"frozen-byte-hash","pixel_sha256":"frozen-pixel-hash","snapshot":"input/original.png"}')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with ZipFile(self.output) as archive:
            index = json.loads(archive.read('index.json'))
        self.assertEqual(index['source']['byte_sha256'], 'frozen-byte-hash')
        self.assertEqual(index['source_provenance'], 'initialization.json')
        self.assertIsNone(index['workflow']['phase'])

    def test_recorded_relative_path_traversal_is_rejected(self):
        self.state['tasks']['task-one']['brief'] = '../outside.txt'
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsafe', result.stdout.lower())
        self.assertFalse(self.output.exists())

    def test_unsafe_reference_in_saved_request_is_rejected_before_export(self):
        self.write('requests/task-one.json', b'{"request":{"references":["../outside.png"]}}')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsafe', result.stdout.lower())
        self.assertFalse(self.output.exists())

    def test_cli_sets_recovery_policy_only_on_brand_new_jobs(self):
        from newpath import __main__ as cli, controller
        source = self.root / 'source.png'
        fresh = self.root / 'fresh-job'
        cases = [(fresh, ['--source', str(source)], True),
                 (self.job, [], False)]
        interrupted = self.root / 'interrupted-job'
        interrupted.mkdir()
        (interrupted / 'initialization.json').write_text('{}', encoding='utf-8')
        cases.append((interrupted, ['--source', str(source)], False))
        for work, arguments, enroll in cases:
            with self.subTest(work=work), mock.patch.object(controller, 'run', return_value={'kind': 'test'}) as run, mock.patch('sys.stdout', io.StringIO()):
                self.assertEqual(cli.main(['--work', str(work), *arguments]), 0)
                if enroll:
                    self.assertEqual(run.call_args.kwargs.get('recovery_policy'), 'bounded-recovery/1')
                else:
                    self.assertNotIn('recovery_policy', run.call_args.kwargs)

    def test_cli_selected_job_summary_distinguishes_unstarted_recorded_and_unknown_requests(self):
        cases = [
            ({'phase': 'layers', 'tasks': {
                'background': {'status': 'waiting', 'execution': {'state': 'not_started'}},
                'foreground': {'status': 'waiting', 'execution': {'state': 'not_started'}}}},
             [], ['background', 'foreground'], 0, ['selected_job_has_no_recorded_image_invocation']),
            ({'phase': 'boards', 'tasks': {
                'board': {'status': 'failed', 'execution': {'state': 'failed', 'request_file': 'requests/task-one.json'},
                          'execution_history': [{'state': 'failed', 'request_file': 'requests/previous.json'},
                                                {'state': 'failed', 'request_file': 'requests/task-one.json'}]},
                'headline': {'status': 'waiting', 'execution': {'state': 'not_started'}}}},
             ['board'], ['headline'], 2, []),
            ({'phase': 'layers', 'tasks': {'legacy': {'status': 'failed'}}},
             ['legacy'], [], None, []),
            ({'phase': 'layers', 'tasks': {}},
             [], [], 0, ['selected_job_has_no_recorded_image_invocation']),
            ({'phase': 'unknown'}, None, None, None, []),
        ]
        for number, (state, failed, unstarted, count, warnings) in enumerate(cases):
            with self.subTest(number=number):
                self.write('state.json', json.dumps(state).encode('utf-8'))
                before = self.snapshot()
                output = self.root / ('summary-' + str(number) + '.zip')
                result = self.cli(output=output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                receipt = json.loads(result.stdout)
                self.assertIn('selected_job_summary', receipt)
                summary = receipt['selected_job_summary']
                self.assertEqual(summary['phase'], state['phase'])
                self.assertEqual(summary['failed_task_ids'], failed)
                self.assertEqual(summary['not_started_task_ids'], unstarted)
                self.assertEqual(summary['recorded_request_count'], count)
                self.assertEqual(summary['warnings'], warnings)
                with ZipFile(output) as archive:
                    self.assertEqual(json.loads(archive.read('index.json'))['selected_job_summary'], summary)
                self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
