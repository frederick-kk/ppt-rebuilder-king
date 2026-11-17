"""The supported startup reaches background generation before page description."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


PACKAGE = Path(__file__).resolve().parents[2]


class SequentialLayersCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = tempfile.TemporaryDirectory(prefix='prk-sequential-cli-runtime-')
        cls.runtime = Path(cls.snapshot.name) / 'compat-runtime'
        shutil.copytree(PACKAGE / 'compat-runtime', cls.runtime,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        constants = {}
        for node in ast.parse((cls.runtime / 'newpath/version.py').read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
        cls.package_id = constants['PACKAGE_ID']
        rows = []
        for path in sorted(cls.runtime.rglob('*')):
            if path.is_file() and path.name != 'MANIFEST.json':
                data = path.read_bytes()
                rows.append({'path': path.relative_to(cls.runtime).as_posix(),
                             'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        snapshot = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        manifest = {'format': 'ppt-rebuilder.runtime-manifest/1',
                    'package_id': cls.package_id, 'archive_name': constants['ARCHIVE_NAME'],
                    'source_file_count': len(rows), 'source_snapshot_sha256': snapshot, 'files': rows}
        (cls.runtime / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.snapshot.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-sequential-cli-job-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'
        self.source = self.root / 'source.png'
        Image.new('RGB', (320, 180), '#CEE3F8').save(self.source)

    def invoke(self, extra=(), *, arguments=None):
        arguments = arguments if arguments is not None else ['--work', str(self.work), *extra]
        result = subprocess.run([sys.executable, '-B', '-m', 'newpath', *arguments],
            cwd=self.runtime, capture_output=True, timeout=60,
            env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        body = json.loads(result.stdout.decode('utf-8-sig')) if result.stdout else None
        return result, body

    def state(self):
        return json.loads((self.work / 'state.json').read_text(encoding='utf-8'))

    def test_sequential_flag_starts_with_only_background_and_no_description(self):
        result, action = self.invoke(['--source', str(self.source), '--sequential-layers'])
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual([task['role'] for task in action['jobs']], ['background'])
        state = self.state()
        self.assertEqual(state['phase'], 'layers')
        self.assertIsNone(state['layout'])
        self.assertNotIn('reading_images', state['source'])
        self.assertEqual(state['layer_generation_order'], 'background-then-foreground/1')
        foreground = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
        self.assertFalse(foreground['issued'])

    def test_published_skill_startup_selects_the_sequential_flow(self):
        skill = (self.runtime / 'SKILL.md').read_text(encoding='utf-8')
        command = re.search(r'```text\s*\n(python -m newpath[^\n]+)\n```', skill)
        self.assertIsNotNone(command)
        arguments = shlex.split(command.group(1))[3:]
        replacements = {'--work': str(self.work), '--source': str(self.source),
                        '--expected-package': self.package_id,
                        '--action-file': str(self.root / 'next-action.json')}
        for option, value in replacements.items():
            if option in arguments:
                arguments[arguments.index(option) + 1] = value
        result, action = self.invoke(arguments=arguments)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual([task['role'] for task in action['jobs']], ['background'])
        self.assertIsNone(self.state()['layout'])

    def test_plain_resume_keeps_order_and_does_not_dispatch_foreground(self):
        result, _ = self.invoke(['--source', str(self.source), '--sequential-layers'])
        self.assertEqual(result.returncode, 0)
        before = (self.work / 'state.json').read_bytes()
        resumed, action = self.invoke()
        self.assertEqual(resumed.returncode, 0)
        self.assertEqual([task['role'] for task in action['jobs']], ['background'])
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_existing_plain_job_cannot_silently_switch_order_on_resume(self):
        result, action = self.invoke(['--source', str(self.source)])
        self.assertEqual(result.returncode, 0)
        self.assertEqual([task['role'] for task in action['jobs']], ['background', 'foreground'])
        before = (self.work / 'state.json').read_bytes()
        resumed, error = self.invoke(['--sequential-layers'])
        self.assertEqual(resumed.returncode, 2)
        self.assertIsNotNone(error)
        self.assertEqual(error['kind'], 'error')
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_plain_cli_keeps_legacy_two_layer_dispatch(self):
        result, action = self.invoke(['--source', str(self.source)])
        self.assertEqual(result.returncode, 0)
        self.assertEqual([task['role'] for task in action['jobs']], ['background', 'foreground'])
        self.assertNotIn('layer_generation_order', self.state())

    def test_sequential_and_source_first_conflict_before_creating_a_job(self):
        result, _ = self.invoke(['--source', str(self.source), '--sequential-layers', '--source-first'])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.work.exists())

    def test_sequential_and_direct_boards_conflict_before_creating_a_job(self):
        result, _ = self.invoke(['--source', str(self.source), '--sequential-layers', '--direct-source-boards'])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.work.exists())

    def test_sequential_and_isolated_reference_conflict_before_creating_a_job(self):
        result, _ = self.invoke(['--source', str(self.source), '--sequential-layers', '--isolated-title-reference'])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.work.exists())


if __name__ == '__main__':
    unittest.main()
