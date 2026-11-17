"""Explicit direct-source planning leaves existing source-first callers intact."""
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
from newpath import controller, version


class DirectSourceCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-direct-source-cli-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'

    def invoke(self, extra):
        output = io.StringIO()
        errors = io.StringIO()
        with mock.patch.object(controller, 'run', return_value={'kind': 'describe'}) as run:
            with mock.patch.object(version, 'validate_work_directory'):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    try:
                        code = entry.main(['--work', str(self.work), *extra])
                    except SystemExit as error:
                        code = error.code
        return code, output.getvalue(), run

    def test_explicit_direct_source_choice_forwards_the_frozen_policy(self):
        code, output, run = self.invoke(['--source', str(self.root / 'real.png'),
                                         '--source-first', '--direct-source-boards'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['source_board_reference_policy'], 'direct-source-board-reference/1')
        self.assertEqual(run.call_args.kwargs['source_layout_policy'], 'source-layout-first/1')
        self.assertEqual(json.loads(output), {'kind': 'describe'})

    def test_existing_source_first_keeps_its_existing_reference_strategy(self):
        code, _, run = self.invoke(['--source-first'])
        self.assertEqual(code, 0)
        self.assertNotIn('source_board_reference_policy', run.call_args.kwargs)

    def test_direct_source_requires_source_first_before_entering_the_controller(self):
        code, _, run = self.invoke(['--direct-source-boards'])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_repeated_flag_is_forwarded_for_saved_policy_validation(self):
        self.work.mkdir()
        (self.work / 'state.json').write_text('{}', encoding='utf-8')
        code, _, run = self.invoke(['--source-first', '--direct-source-boards'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['source_board_reference_policy'], 'direct-source-board-reference/1')


if __name__ == '__main__':
    unittest.main()
