"""Distinguish presentation modes and current project instructions from paths."""
from collections import Counter
import json
import os
from pathlib import Path
import unittest
from unittest import mock
from zipfile import ZipFile

import test_job_diagnostics as existing_tests


class DiagnosticPathFields(unittest.TestCase):
    setUp = existing_tests.JobDiagnosticsTests.setUp
    write = existing_tests.JobDiagnosticsTests.write
    snapshot = existing_tests.JobDiagnosticsTests.snapshot
    cli = existing_tests.JobDiagnosticsTests.cli
    diagnostics = existing_tests.JobDiagnosticsTests.diagnostics

    def exported_index(self):
        before = self.snapshot()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        with ZipFile(self.output) as archive:
            return json.loads(archive.read('index.json'))

    def test_presentation_modes_are_not_missing_files(self):
        self.state['layout'] = {'items': [
            {'id': 'icon', 'presentation': 'cutout'},
            {'id': 'photo', 'presentation': 'rectangle'}]}
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        index = self.exported_index()
        missing = {row['path'] for row in index['missing_files']}
        self.assertFalse({'cutout', 'rectangle'} & missing)
        self.assertIn('returned/missing.png', missing)

    def test_real_presentation_paths_keep_existing_and_missing_files_distinct(self):
        self.write('output/real.pptx', b'Existing presentation bytes')
        self.state['saved_results'] = [{'presentation': 'output/real.pptx'},
                                      {'presentation': 'output/actually-missing.pptx'}]
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        index = self.exported_index()
        missing = {row['path'] for row in index['missing_files']}
        self.assertIn('output/actually-missing.pptx', missing)
        self.assertNotIn('output/real.pptx', missing)
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.read('job/output/real.pptx'), b'Existing presentation bytes')

    def test_presentation_traversal_keeps_the_existing_safety_error(self):
        self.state['saved_result'] = {'presentation': '../outside.pptx'}
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        before = self.snapshot()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsafe', result.stdout.lower())
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_current_package_instruction_does_not_require_legacy_document(self):
        index = self.exported_index()
        missing = {row['path'] for row in index['missing_files']}
        self.assertNotIn('project/docs/CHATGPT_PROJECT_INSTRUCTIONS.txt', missing)
        with ZipFile(self.output) as archive:
            self.assertIn('project/PROJECT_INSTRUCTIONS.txt', archive.namelist())

    def runtime_fixture(self, root_text=None, legacy_text=None):
        package = self.root / 'package'
        runtime = package / 'compat-runtime'
        runtime.mkdir(parents=True)
        for filename in ['SKILL.md', 'MANIFEST.json', 'package.json', 'package-lock.json', 'requirements.txt']:
            (runtime / filename).write_text('Fixture runtime metadata', encoding='utf-8')
        for filename in ['RELEASE.json', 'LICENSE', 'NOTICE.md']:
            (package / filename).write_text('Fixture project metadata', encoding='utf-8')
        if root_text is not None:
            (package / 'PROJECT_INSTRUCTIONS.txt').write_text(root_text, encoding='utf-8')
        if legacy_text is not None:
            docs = package / 'docs'
            docs.mkdir()
            (docs / 'CHATGPT_PROJECT_INSTRUCTIONS.txt').write_text(legacy_text, encoding='utf-8')
        return runtime

    def export_with_runtime(self, runtime):
        diagnostics = self.diagnostics()
        before = self.snapshot()
        # Adapt only the package location. Inventory, safety checks, byte
        # collection, ZIP publication and read-only checks execute normally.
        with mock.patch.object(diagnostics, 'RUNTIME_ROOT', runtime):
            diagnostics.export_diagnostics(self.job, self.output)
        self.assertEqual(self.snapshot(), before)
        with ZipFile(self.output) as archive:
            return json.loads(archive.read('index.json')), archive.namelist()

    def test_legacy_instruction_is_selected_when_root_instruction_is_absent(self):
        runtime = self.runtime_fixture(legacy_text='Existing legacy project instruction')
        index, names = self.export_with_runtime(runtime)
        missing = {row['path'] for row in index['missing_files']}
        self.assertIn('project/docs/CHATGPT_PROJECT_INSTRUCTIONS.txt', names)
        self.assertNotIn('project/PROJECT_INSTRUCTIONS.txt', missing)

    def test_current_instruction_takes_priority_when_both_exist(self):
        runtime = self.runtime_fixture(root_text='Current instruction', legacy_text='Legacy instruction')
        index, names = self.export_with_runtime(runtime)
        self.assertIn('project/PROJECT_INSTRUCTIONS.txt', names)
        self.assertNotIn('project/docs/CHATGPT_PROJECT_INSTRUCTIONS.txt', names)
        self.assertFalse(any('INSTRUCTIONS' in row['path'] for row in index['missing_files']))

    def test_absent_both_instruction_locations_reports_current_path(self):
        runtime = self.runtime_fixture()
        index, _ = self.export_with_runtime(runtime)
        missing = [row['path'] for row in index['missing_files'] if 'INSTRUCTIONS' in row['path']]
        self.assertEqual(missing, ['project/PROJECT_INSTRUCTIONS.txt'])

    @unittest.skipUnless(os.environ.get('PRK_R5_HIGH_JOB'), 'Set private real r4 High job fixture')
    def test_actual_high_presentation_fields_and_real_missing_control(self):
        job = Path(os.environ['PRK_R5_HIGH_JOB'])
        actual = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        modes = []

        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'presentation':
                        modes.append(child)
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(actual)
        self.assertEqual(Counter(modes), {'cutout': 27, 'rectangle': 8})
        self.state['actual_high_presentation_fields'] = [{'presentation': mode} for mode in modes]
        self.state['missing_control'] = {'presentation': 'output/actually-missing.pptx'}
        self.write('state.json', json.dumps(self.state).encode('utf-8'))
        index = self.exported_index()
        missing = {row['path'] for row in index['missing_files']}
        self.assertFalse({'cutout', 'rectangle'} & missing)
        self.assertNotIn('project/docs/CHATGPT_PROJECT_INSTRUCTIONS.txt', missing)
        self.assertIn('output/actually-missing.pptx', missing)
        self.assertIn('input/missing-reference.png', missing)


if __name__ == '__main__':
    unittest.main()
