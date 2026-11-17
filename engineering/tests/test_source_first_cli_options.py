"""Optional source-first CLI forwards explicit planning choices only."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))
from newpath import __main__ as entry
from newpath import controller,diagnostics,version


class SourceFirstCliOptions(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='prk-source-first-cli-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.work=self.root/'job'
        self.source=self.root/'current.png'
        self.source.write_bytes(b'only a mocked controller receives this input')

    def invoke(self,extra):
        output=io.StringIO()
        errors=io.StringIO()
        with mock.patch.object(controller,'run',return_value={'kind':'describe'}) as run:
            with mock.patch.object(version,'validate_work_directory'):
                with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                    try:
                        code=entry.main(['--work',str(self.work),*extra])
                    except SystemExit as error:
                        code=error.code
        return code,output.getvalue(),run

    def test_default_cli_keeps_existing_planning_choices(self):
        code,output,run=self.invoke(['--source',str(self.source)])
        self.assertEqual(code,0)
        self.assertNotIn('source_layout_policy',run.call_args.kwargs)
        self.assertNotIn('board_sampling_policy',run.call_args.kwargs)
        self.assertEqual(json.loads(output),{'kind':'describe'})

    def test_source_first_new_job_forwards_both_explicit_choices(self):
        code,output,run=self.invoke(['--source',str(self.source),'--source-first','--compact'])
        self.assertEqual(code,0)
        self.assertEqual(run.call_args.kwargs['source_layout_policy'],'source-layout-first/1')
        self.assertEqual(run.call_args.kwargs['board_sampling_policy'],'source-pixel-budget/1')
        self.assertEqual(json.loads(output),{'kind':'describe'})

    def test_repeated_source_first_flag_is_forwarded_for_saved_policy_validation(self):
        self.work.mkdir()
        (self.work/'state.json').write_text('{}',encoding='utf-8')
        code,_,run=self.invoke(['--source-first'])
        self.assertEqual(code,0)
        self.assertEqual(run.call_args.kwargs['source_layout_policy'],'source-layout-first/1')
        self.assertEqual(run.call_args.kwargs['board_sampling_policy'],'source-pixel-budget/1')

    def test_diagnostics_remains_read_only_and_keeps_complete_output(self):
        response={'kind':'done','files':[{'sha256':'actual'}]}
        with mock.patch.object(diagnostics,'export_diagnostics',return_value=response) as export:
            code,output,run=self.invoke(['--diagnostics',str(self.root/'diagnostic.zip'),
                                        '--source-first','--compact'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output),response)
        run.assert_not_called()
        export.assert_called_once()


if __name__=='__main__':
    unittest.main()
