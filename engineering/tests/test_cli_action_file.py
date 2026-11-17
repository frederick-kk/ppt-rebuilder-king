"""Safe optional action output avoids redirecting responses over job records."""
import contextlib
import copy
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
from zipfile import ZipFile

from test_result_role_binding import ReviewedOutputFixture
from newpath import __main__ as entry
from newpath import controller, diagnostics, storage

PACKAGE = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


class ActionFileCLI(ReviewedOutputFixture):
    def feature(self):
        self.assertIn('--action-file', inspect.getsource(entry.main))

    def invoke(self, arguments=(), *, action=None, module=entry):
        if action is not None:
            self.feature()
        args = ['--work', str(self.work), *map(str, arguments)]
        if action is not None:
            args.extend(['--action-file', str(action)])
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(controller, '_runtime'):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = module.main(args)
        return code, output.getvalue(), errors.getvalue()

    def assert_saved(self, action, output):
        value = json.loads(output)
        self.assertEqual(json.loads(action.read_text('utf-8')), value)
        self.assertEqual(value['action_file'], str(action.resolve()))
        return value

    def prepare_done(self):
        self.work.mkdir()
        output = self.work / 'output.pptx'
        with ZipFile(output, 'w') as archive:
            archive.writestr('ppt/presentation.xml', '<presentation/>')
            archive.writestr('ppt/slides/slide1.xml', '<slide/>')
        receipt = {'kind': 'done', 'terminal': True, 'pptx': str(output),
                   'sha256': storage.sha256(output), 'quality': 'not_evaluated',
                   'warnings': [], 'skipped': [], 'within_600_seconds': None,
                   'post_return_prompt_audit': {'unknown': [None]}}
        storage.write_json(self.work / 'receipt.json', receipt)
        storage.write_json(self.work / 'state.json', {
            'format': 'newpath.job/1', 'phase': 'done', 'tasks': {}, 'done': receipt})
        (self.work / '.lock').write_bytes(b'1')
        return receipt

    def job_hashes(self):
        return {path.relative_to(self.work).as_posix(): storage.sha256(path)
                for path in self.work.rglob('*') if path.is_file()}

    def reject_before_controller(self, target, arguments=()):
        self.feature()
        before = target.read_bytes() if target.is_file() else None
        with mock.patch.object(controller, 'run', return_value={'kind': 'unexpected'}) as run:
            with mock.patch.object(diagnostics, 'export_diagnostics') as export:
                code, output, _ = self.invoke(arguments, action=target)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['kind'], 'error')
        run.assert_not_called()
        export.assert_not_called()
        if before is not None:
            self.assertEqual(target.read_bytes(), before)
        else:
            self.assertFalse(target.exists())

    def test_external_action_file_handles_repeated_normal_source_first_actions_without_source_damage(self):
        action = self.root / 'actions' / 'current.json'
        source_before = self.source.read_bytes()
        code, output, _ = self.invoke(['--source', self.source, '--source-first', '--compact'], action=action)
        self.assertEqual(code, 0)
        self.assertEqual(self.assert_saved(action, output)['kind'], 'describe')
        first_state = (self.work / 'state.json').read_bytes()
        code, output, _ = self.invoke(['--compact'], action=action)
        self.assertEqual(code, 0)
        self.assertEqual(self.assert_saved(action, output)['kind'], 'describe')
        self.assertEqual((self.work / 'state.json').read_bytes(), first_state)
        layout_input = self.root / 'layout-input.json'
        storage.write_json(layout_input, {'kind': 'layout', 'layout': {'items': [], 'background_regions': []}})
        code, output, _ = self.invoke(['--input', layout_input, '--compact'], action=action)
        self.assertEqual(code, 0)
        self.assertEqual(self.assert_saved(action, output)['kind'], 'generate')
        state = storage.read_state(self.work)
        self.assertTrue(all(task['execution']['state'] == 'not_started' for task in state['tasks'].values()))
        self.assertTrue(all(not task['attempts'] for task in state['tasks'].values()))
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertFalse(list(self.work.rglob('*.pptx')))

    def test_valid_external_path_saves_error_receipt_without_entering_controller(self):
        malformed = self.root / 'malformed.json'
        malformed.write_text('{', 'utf-8')
        action = self.root / 'error-action.json'
        with mock.patch.object(controller, 'run') as run:
            code, output, _ = self.invoke(['--input', malformed], action=action)
        self.assertEqual(code, 2)
        self.assertEqual(self.assert_saved(action, output)['kind'], 'error')
        run.assert_not_called()
        self.assertEqual(malformed.read_text('utf-8'), '{')
        self.assertFalse(self.work.exists())

    def test_work_and_runtime_paths_reject_without_writes_or_controller_entry(self):
        targets = [self.work / 'state.json', self.work / 'input' / 'reply.json',
                   PACKAGE / 'compat-runtime/newpath/__main__.py',
                   PACKAGE / 'compat-runtime/new-response.json']
        for target in targets:
            with self.subTest(target=target):
                self.reject_before_controller(target)
        self.assertFalse(self.work.exists())

    def test_source_and_input_paths_reject_including_hardlink_alias(self):
        for flag, original in [('--source', self.source), ('--input', self.root / 'input.json')]:
            if flag == '--input':
                original.write_text('{}', 'utf-8')
            with self.subTest(flag=flag):
                self.reject_before_controller(original, [flag, original])
                alias = self.root / ('source-alias.json' if flag == '--source' else 'input-alias.json')
                os.link(original, alias)
                self.reject_before_controller(alias, [flag, original])
                self.assertEqual(alias.read_bytes(), original.read_bytes())

    def test_resume_also_protects_the_original_source_recorded_in_initialization(self):
        self.feature()
        code, _, _ = self.invoke(['--source', self.source, '--source-first'])
        self.assertEqual(code, 0)
        before = self.job_hashes()
        self.reject_before_controller(self.source)
        self.assertEqual(self.job_hashes(), before)

    def test_diagnostics_output_collision_rejects_before_export(self):
        destination = self.root / 'diagnostic.zip'
        destination.write_bytes(b'existing-zip-sentinel')
        self.reject_before_controller(destination, ['--diagnostics', destination])

    def test_symbolic_aliases_of_source_runtime_work_and_diagnostics_are_rejected(self):
        self.feature()
        link = self.root / 'source-link.json'
        try:
            link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest('Symbolic links are unavailable for this Windows account')
        self.reject_before_controller(link, ['--source', self.source])
        runtime_link = self.root / 'runtime-link.json'
        runtime_link.symlink_to(PACKAGE / 'compat-runtime/newpath/__main__.py')
        self.reject_before_controller(runtime_link)
        self.work.mkdir()
        work_link = self.root / 'work-link'
        work_link.symlink_to(self.work, target_is_directory=True)
        self.reject_before_controller(work_link / 'new.json')
        destination = self.root / 'diagnostic.zip'
        destination.write_bytes(b'zip-sentinel')
        diagnostic_link = self.root / 'diagnostic-link.json'
        diagnostic_link.symlink_to(destination)
        self.reject_before_controller(diagnostic_link, ['--diagnostics', destination])

    def test_existing_external_action_is_atomically_replaced_and_no_temp_file_remains(self):
        action = self.root / 'current-action.json'
        action.write_text('previous action', 'utf-8')
        self.feature()
        original = {'kind': 'describe', 'unknown': None, 'metadata': {'names': ['x']}}
        replace = os.replace
        checks = []

        def verify_replace(temporary, destination):
            if Path(destination) == action.resolve():
                self.assertEqual(action.read_text('utf-8'), 'previous action')
                value = json.loads(Path(temporary).read_text('utf-8'))
                self.assertEqual(value['kind'], 'describe')
                checks.append(True)
            return replace(temporary, destination)

        with mock.patch.object(controller, 'run', return_value=copy.deepcopy(original)):
            with mock.patch.object(storage.os, 'replace', side_effect=verify_replace):
                code, output, _ = self.invoke(action=action)
        self.assertEqual(code, 0)
        self.assert_saved(action, output)
        self.assertEqual(checks, [True])
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_action_write_failure_preserves_previous_file_and_does_not_repeat_business_operation(self):
        action = self.root / 'current-action.json'
        action.write_text('keep previous action', 'utf-8')
        self.feature()
        with mock.patch.object(controller, 'run', return_value={'kind': 'generate'}) as run:
            with mock.patch.object(storage.os, 'replace', side_effect=OSError('fixture destination unavailable')):
                code, output, _ = self.invoke(action=action)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['kind'], 'error')
        run.assert_called_once()
        self.assertEqual(action.read_text('utf-8'), 'keep previous action')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_done_compact_action_does_not_change_internal_receipt_state_or_PPT(self):
        receipt = self.prepare_done()
        before = self.job_hashes()
        action = self.root / 'done-action.json'
        code, output, _ = self.invoke(['--compact'], action=action)
        self.assertEqual(code, 0)
        value = self.assert_saved(action, output)
        self.assertEqual(value['pptx'], receipt['pptx'])
        self.assertEqual(value['quality'], 'not_evaluated')
        self.assertIsNone(value['within_600_seconds'])
        self.assertEqual(self.job_hashes(), before)

    def test_actual_diagnostics_keeps_job_bytes_and_saves_its_complete_action(self):
        self.feature()
        with mock.patch.object(controller, '_runtime'):
            controller.run(self.work, source=self.source)
        before = self.job_hashes()
        destination = self.root / 'diagnostic.zip'
        action = self.root / 'diagnostic-action.json'
        code, output, _ = self.invoke(['--diagnostics', destination, '--compact'], action=action)
        self.assertEqual(code, 0)
        self.assert_saved(action, output)
        self.assertTrue(destination.is_file())
        with ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
        self.assertEqual(self.job_hashes(), before)

    def test_real_job_lock_error_is_saved_without_changing_existing_job(self):
        self.feature()
        self.prepare_done()
        before = self.job_hashes()
        action = self.root / 'locked-action.json'
        with storage.job_lock(self.work):
            process = subprocess.run([sys.executable, '-B', '-m', 'newpath', '--work', str(self.work),
                                      '--action-file', str(action)],
                cwd=PACKAGE / 'compat-runtime', capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(process.returncode, 2, process.stderr)
        value = self.assert_saved(action, process.stdout)
        self.assertEqual(value['kind'], 'error')
        self.assertRegex(value['message'], 'process|job|temporarily unavailable|Permission denied')
        self.assertEqual(self.job_hashes(), before)

    def test_input_mutual_exclusion_and_diagnostics_retry_guard_still_stop_before_writes(self):
        action = self.root / 'invalid-action.json'
        for arguments in [('--source', self.source, '--input', self.root / 'input.json'),
                          ('--diagnostics', self.root / 'diag.zip', '--retry-authorization', 'not allowed')]:
            with self.subTest(arguments=arguments):
                self.feature()
                with self.assertRaises(SystemExit) as error:
                    self.invoke(arguments, action=action)
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(action.exists())
                self.assertFalse(self.work.exists())

    def test_without_flag_stdout_fields_and_error_format_equal_R15(self):
        old_path = PACKAGE.parent / 'source-r15/compat-runtime/newpath/__main__.py'
        if not old_path.is_file():
            self.skipTest('Retained R15 source unavailable outside this checkout')
        spec = importlib.util.spec_from_file_location('newpath.r15_action_output_fixture', old_path)
        prior = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prior)
        for kind in ('describe', 'generate', 'invoke_once', 'registration_required', 'paused', 'done'):
            result = {'kind': kind, 'unknown': None, 'warnings': [], 'skipped': [],
                      'nested': {'known_false': False}, 'instructions': 'Existing action.'}
            for compact in ([], ['--compact']):
                with self.subTest(kind=kind, compact=compact):
                    with mock.patch.object(controller, 'run', return_value=copy.deepcopy(result)):
                        self.assertEqual(self.invoke(compact), self.invoke(compact, module=prior))
        with mock.patch.object(controller, 'run', side_effect=ValueError('Existing failure')):
            self.assertEqual(self.invoke(), self.invoke(module=prior))

    def test_pre_start_shell_style_stdout_redirection_can_still_corrupt_a_temporary_state(self):
        self.prepare_done()
        original = (self.work / 'state.json').read_bytes()
        with (self.work / 'state.json').open('wb') as redirected:
            process = subprocess.run([sys.executable, '-B', '-m', 'newpath', '--work', str(self.work)],
                cwd=PACKAGE / 'compat-runtime', stdout=redirected, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(process.returncode, 2)
        current = (self.work / 'state.json').read_bytes()
        self.assertNotEqual(current, original)
        self.assertEqual(json.loads(current)['kind'], 'error')
        self.assertRegex(json.loads(current)['message'], 'Expecting value')


if __name__ == '__main__':
    unittest.main()
