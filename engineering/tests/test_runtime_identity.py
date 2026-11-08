"""Local runtime identity and the supported CLI's new-job policy."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE/'compat-runtime'))
from newpath import __main__ as cli
from newpath import controller
try:
    from newpath import version
except ImportError:
    version = None

PACKAGE_ID = 'PPT Rebuilder King-20261108-flow-r1'
ARCHIVE_NAME = 'PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip'
EXECUTION_MODE = 'explicit-start/1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RuntimeIdentityContracts(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(version, 'Runtime identity guard is missing')
        temporary = tempfile.TemporaryDirectory(prefix='prk-version-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('newpath/version.py', 'newpath/controller.py', 'newpath/__main__.py',
                     'newpath/execution.py', 'README.md'):
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('fixture '+name+'\n').encode())
        self.rows = [
            {'path': p.relative_to(self.root).as_posix(), 'size': p.stat().st_size,
             'sha256': digest(p.read_bytes())}
            for p in sorted(self.root.rglob('*')) if p.is_file()]
        self.manifest = {'package_id': PACKAGE_ID, 'archive_name': ARCHIVE_NAME,
                         'source_file_count': len(self.rows), 'files': self.rows}
        self.rewrite()
        self.patch = mock.patch.object(version, 'RUNTIME_ROOT', self.root)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def rewrite(self, *, snapshot=True):
        if snapshot:
            self.manifest['source_snapshot_sha256'] = digest(json.dumps(
                self.manifest['files'], ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8'))
        (self.root/'MANIFEST.json').write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8')

    def test_valid_identity_records_local_provenance_and_verifies_every_file(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        result = version.runtime_identity(PACKAGE_ID)
        self.assertEqual(result['package_id'], PACKAGE_ID)
        self.assertEqual(result['archive_name'], ARCHIVE_NAME)
        self.assertEqual(result['manifest_sha256'], digest((self.root/'MANIFEST.json').read_bytes()))
        self.assertEqual(result['source_snapshot_sha256'], self.manifest['source_snapshot_sha256'])
        self.assertEqual(result['verified_files'], len(self.rows))
        self.assertEqual(result['identity_provenance'], 'local_manifest_and_file_hashes_not_provider_verified')
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes()
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_expected_package_mismatch_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'package|version'):
            version.runtime_identity('PPT Rebuilder King-20261022-tuned')

    def test_old_package_header_is_refused_even_if_expected_omitted(self):
        self.manifest['package_id'] = 'PPT Rebuilder King-20261022-tuned'; self.rewrite()
        with self.assertRaisesRegex(ValueError, 'package|version'):
            version.runtime_identity()

    def test_archive_identity_mismatch_is_refused(self):
        self.manifest['archive_name'] = 'old.zip'; self.rewrite()
        with self.assertRaisesRegex(ValueError, 'archive|package|version'):
            version.runtime_identity()

    def test_changed_code_is_refused(self):
        (self.root/'newpath/controller.py').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed|hash|size|integrity'):
            version.runtime_identity()

    def test_snapshot_digest_cannot_be_forged_without_record_update(self):
        self.manifest['source_snapshot_sha256'] = '0'*64; self.rewrite(snapshot=False)
        with self.assertRaisesRegex(ValueError, 'snapshot|manifest'):
            version.runtime_identity()

    def test_parent_escape_is_refused(self):
        self.manifest['files'][0]['path'] = '../outside.py'; self.rewrite()
        with self.assertRaisesRegex(ValueError, 'path|manifest'):
            version.runtime_identity()

    def test_case_aliased_duplicate_paths_are_refused(self):
        copied = dict(self.rows[0]); copied['path'] = copied['path'].upper()
        self.manifest['files'].append(copied)
        self.manifest['source_file_count'] += 1; self.rewrite()
        with self.assertRaisesRegex(ValueError, 'duplicate|manifest'):
            version.runtime_identity()

    def test_manifest_cannot_omit_critical_execution_code(self):
        self.manifest['files'] = [r for r in self.rows if r['path']!='newpath/execution.py']
        self.manifest['source_file_count'] -= 1; self.rewrite()
        with self.assertRaisesRegex(ValueError, 'missing|unlisted|manifest'):
            version.runtime_identity()

    def test_unlisted_runtime_code_is_refused(self):
        (self.root/'newpath/unlisted.py').write_text('unexpected', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unlisted|manifest'):
            version.runtime_identity()

    def test_derived_bytecode_does_not_change_identity(self):
        (self.root/'newpath/__pycache__').mkdir()
        (self.root/'newpath/__pycache__/version.pyc').write_bytes(b'derived cache')
        self.assertEqual(version.runtime_identity()['verified_files'], len(self.rows))

    def test_declared_nested_manifest_is_an_ordinary_checked_runtime_file(self):
        path = self.root/'newpath/support/MANIFEST.json'; path.parent.mkdir()
        path.write_bytes(b'{}')
        self.manifest['files'].append({'path':'newpath/support/MANIFEST.json',
                                       'size':2,'sha256':digest(b'{}')})
        self.manifest['source_file_count'] += 1; self.rewrite()
        try:
            result = version.runtime_identity()
        except ValueError as error:
            self.fail('A declared nested manifest is an ordinary runtime file: '+str(error))
        self.assertEqual(result['verified_files'], len(self.rows))

    def test_new_work_directory_cannot_be_inside_the_verified_runtime(self):
        self.assertTrue(hasattr(version, 'validate_work_directory'), 'New job directory guard is missing')
        with self.assertRaisesRegex(ValueError, 'outside|runtime|directory'):
            version.validate_work_directory(self.root/'jobs/current')
        self.assertIsNone(version.validate_work_directory(self.root.parent/'separate-job'))


class CliExecutionPolicyContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-cli-policy-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)

    def invoke(self, extra):
        output = io.StringIO()
        with (mock.patch.object(controller, 'run', return_value={'kind':'test-only'}) as run,
              mock.patch.dict(sys.modules, {'newpath.execution':types.SimpleNamespace(MODE=EXECUTION_MODE,POST_RETURN_PROMPT_POLICY='audit-existing-return/1')}),
              contextlib.redirect_stdout(output)):
            code = cli.main(['--work', str(self.root/'job'), *extra])
        self.assertEqual(code, 0, output.getvalue())
        return run.call_args.kwargs

    def test_supported_new_source_cli_enables_explicit_start_and_current_identity(self):
        kwargs = self.invoke(['--source', str(self.root/'source.png')])
        self.assertEqual(kwargs.get('execution_policy'), EXECUTION_MODE)
        self.assertEqual(kwargs.get('expected_package'), PACKAGE_ID)
        self.assertEqual(kwargs.get('post_return_prompt_policy'), 'audit-existing-return/1')

    def test_existing_source_cli_never_migrates_a_saved_job_implicitly(self):
        job = self.root/'job'; job.mkdir(); (job/'state.json').write_text('{}', encoding='utf-8')
        kwargs = self.invoke(['--source', str(self.root/'source.png')])
        self.assertNotIn('execution_policy', kwargs)
        self.assertNotIn('expected_package', kwargs)
        self.assertNotIn('post_return_prompt_policy', kwargs)

    def test_plain_resume_keeps_existing_policy_and_identity(self):
        kwargs = self.invoke([])
        self.assertNotIn('execution_policy', kwargs)
        self.assertNotIn('expected_package', kwargs)

    def test_explicit_expected_package_is_forwarded_on_resume(self):
        kwargs = self.invoke(['--expected-package', PACKAGE_ID])
        self.assertEqual(kwargs.get('expected_package'), PACKAGE_ID)
        self.assertNotIn('execution_policy', kwargs)

    def test_explicit_wrong_expected_package_is_not_silently_replaced_on_new_job(self):
        kwargs = self.invoke(['--source', str(self.root/'source.png'),
                              '--expected-package','PPT Rebuilder King-20261022-tuned'])
        self.assertEqual(kwargs.get('expected_package'), 'PPT Rebuilder King-20261022-tuned')
        self.assertEqual(kwargs.get('execution_policy'), EXECUTION_MODE)

    def test_cli_refuses_new_job_inside_runtime_before_controller_runs(self):
        self.assertIsNotNone(version, 'Runtime identity guard is missing')
        output = io.StringIO()
        with (mock.patch.object(controller, 'run', return_value={'kind':'test-only'}) as run,
              mock.patch.object(version, 'RUNTIME_ROOT', self.root),
              mock.patch.dict(sys.modules, {'newpath.execution':types.SimpleNamespace(MODE=EXECUTION_MODE,POST_RETURN_PROMPT_POLICY='audit-existing-return/1')}),
              contextlib.redirect_stdout(output)):
            code = cli.main(['--work',str(self.root/'job'),'--source',str(self.root/'source.png')])
        self.assertEqual(code, 2, 'New work inside runtime should be rejected before source preparation')
        self.assertFalse(run.called)
        self.assertFalse((self.root/'job').exists())
        self.assertIn('outside', output.getvalue())


if __name__=='__main__':
    unittest.main()
