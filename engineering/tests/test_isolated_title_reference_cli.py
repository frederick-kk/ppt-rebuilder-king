"""The isolated title reference is an explicit source-first CLI choice."""
import contextlib
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
from newpath import controller, source, version


class IsolatedTitleReferenceCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-title-reference-cli-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'

    def invoke(self, flags):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(controller, 'run', return_value={'kind': 'describe'}) as run:
            with mock.patch.object(version, 'validate_work_directory'):
                with mock.patch.object(source, 'TITLE_REFERENCE_POLICY', 'source-parts-title-reference/1', create=True):
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        try:
                            code = entry.main(['--work', str(self.work), *flags])
                        except SystemExit as error:
                            code = error.code
        return code, output.getvalue(), run

    def test_explicit_selection_forwards_the_title_policy(self):
        code, output, run = self.invoke(['--source', str(self.root / 'current.png'),
                                         '--source-first', '--direct-source-boards',
                                         '--isolated-title-reference'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['primary_source_reference_policy'],
                         'source-parts-title-reference/1')
        self.assertEqual(run.call_args.kwargs['source_board_reference_policy'],
                         'direct-source-board-reference/1')
        self.assertEqual(json.loads(output), {'kind': 'describe'})

    def test_source_first_without_direct_boards_can_select_title_reference(self):
        code, _, run = self.invoke(['--source-first', '--isolated-title-reference'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['primary_source_reference_policy'],
                         'source-parts-title-reference/1')
        self.assertNotIn('source_board_reference_policy', run.call_args.kwargs)

    def test_existing_flags_do_not_add_the_new_policy(self):
        for flags in ([], ['--source-first'], ['--source-first', '--direct-source-boards']):
            with self.subTest(flags=flags):
                code, _, run = self.invoke(flags)
                self.assertEqual(code, 0)
                self.assertNotIn('primary_source_reference_policy', run.call_args.kwargs)

    def test_dependency_error_precedes_controller_execution(self):
        code, _, run = self.invoke(['--isolated-title-reference'])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_resume_forwards_explicit_policy_for_frozen_validation(self):
        self.work.mkdir()
        (self.work / 'state.json').write_text('{}', encoding='utf-8')
        code, _, run = self.invoke(['--source-first', '--isolated-title-reference'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['primary_source_reference_policy'],
                         'source-parts-title-reference/1')


if __name__ == '__main__':
    unittest.main()
