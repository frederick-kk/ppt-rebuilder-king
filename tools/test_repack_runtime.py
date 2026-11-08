"""Synthetic packaging contracts; no services, real jobs, or artwork."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import json
import tempfile
import unittest

try:
    from . import repack_runtime as repack
except ImportError:
    import repack_runtime as repack


class RepackRuntime(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.baseline = self.root / 'approved.zip'
        runtime = {
            'SKILL.md': b'Synthetic runtime instructions\n',
            'node_modules/https/package.json': b'{"name":"https","license":"ISC"}\n',
        }
        rows = repack.inventory(runtime)
        snapshot = repack.sha(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode())
        manifest = repack.encoded({
            'format': 'newpath.package-manifest/1', 'package_id': repack.BASELINE_ROOT,
            'source_file_count': len(rows), 'source_snapshot_sha256': snapshot,
            'files': rows,
        })
        runtime['MANIFEST.json'] = manifest
        self.expected = runtime
        baseline_files = {'compat-runtime/' + n: b for n, b in runtime.items()}
        baseline_files['engineering/private-note.txt'] = b'Internal synthetic fixture'
        repack.pack(self.baseline, repack.BASELINE_ROOT, baseline_files, 1793836800)
        for key, value in {
            'BASELINE_SHA256': repack.sha(self.baseline.read_bytes()),
            'RUNTIME_MANIFEST_SHA256': repack.sha(manifest),
            'RUNTIME_SNAPSHOT_SHA256': snapshot, 'RUNTIME_COUNT': len(runtime),
        }.items():
            active = patch.object(repack, key, value)
            active.start()
            self.addCleanup(active.stop)
        for name in repack.RUNTIME_DOCS | repack.RUNTIME_LEGAL_FILES:
            self.write(name, 'Synthetic public document\n')
        self.meta = {
            'version': 'synthetic', 'package_id': repack.BASELINE_ROOT,
            'archive_root': repack.ARCHIVE_ROOT,
            'archive_name': repack.RUNTIME_NAME, 'source_archive_name': repack.SOURCE_NAME,
            'source_date_epoch': 1793836800,
        }
        self.write('RELEASE.json', json.dumps(self.meta))
        self.write('tools/example.py', 'print("synthetic")\n')
        self.write('compat-runtime/newpath/render.cjs', 'module.exports = {};\n')
        self.write('compat-runtime/references/scene.md', 'Scene contract\n')
        self.write('compat-runtime/pyproject.toml', '[project]\nname="synthetic"\n')
        self.write('compat-runtime/node_modules/example/index.js', 'excluded\n')
        self.write('public-materials/note.md', 'excluded\n')
        self.write('verification/report.json', '{}\n')
        self.write('baseline-record/report.json', '{}\n')
        self.write('docs/RELEASE_VALIDATION.zh-CN.md', 'excluded\n')
        self.write('engineering/tests/__pycache__/test.pyc', 'excluded\n')
        self.write('engineering/tests/test_synthetic.py', 'pass\n')
        self.write('.env', 'excluded\n')

    def write(self, name, body):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')

    def build(self, name='out'):
        return repack.build(self.baseline, self.source, self.root / name)

    def rewrite(self, archive, change):
        data = repack.read_archive(archive)
        change(data)
        root = repack.ARCHIVE_ROOT + '/'
        manifest_name = root + 'PACKAGE_MANIFEST.json'
        manifest = json.loads(data[manifest_name])
        relative = {n[len(root):]: b for n, b in data.items() if n != manifest_name}
        manifest['files'] = repack.inventory(relative)
        data[manifest_name] = repack.encoded(manifest)
        target = self.root / 'changed.zip'
        with ZipFile(target, 'w') as output:
            for name, body in data.items():
                output.writestr(name, body)
        return target

    def test_runtime_bytes_and_old_manifest_are_preserved(self):
        before = {p.relative_to(self.source): p.read_bytes()
                  for p in self.source.rglob('*') if p.is_file()}
        result = self.build()
        files = repack.read_archive(result['runtime'])
        prefix = repack.ARCHIVE_ROOT + '/compat-runtime/'
        actual = {n[len(prefix):]: b for n, b in files.items() if n.startswith(prefix)}
        self.assertEqual(actual, self.expected)
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes()
                                  for p in self.source.rglob('*') if p.is_file()})
        self.assertTrue(result['runtime_check']['runtime_byte_identical_to_approved_download'])

    def test_outer_and_source_inventories_are_strict(self):
        result = self.build()
        runtime = repack.read_archive(result['runtime'])
        self.assertEqual(len(runtime), len(self.expected) + len(repack.RUNTIME_DOCS)
                         + len(repack.RUNTIME_LEGAL_FILES) + 1)
        source = repack.read_archive(result['source'])
        for name in source:
            self.assertFalse(any(p in repack.SKIP_DIRS for p in name.split('/')))
            self.assertFalse(name.endswith('RELEASE_VALIDATION.zh-CN.md'))
            self.assertFalse(name.endswith('.env'))
        self.assertTrue(any(n.endswith('/newpath/render.cjs') for n in source))
        self.assertTrue(any(n.endswith('/references/scene.md') for n in source))
        self.assertTrue(any(n.endswith('/pyproject.toml') for n in source))
        self.assertTrue(any(n.endswith('/test_synthetic.py') for n in source))

    def test_two_builds_are_byte_reproducible(self):
        a = self.build('a')
        b = self.build('b')
        for key in ('runtime', 'source', 'instructions', 'checksums'):
            self.assertEqual(Path(a[key]).read_bytes(), Path(b[key]).read_bytes())

    def test_clean_public_runtime_can_be_the_next_baseline(self):
        first = self.build('first')
        clean = Path(first['runtime'])
        second = repack.build(clean, self.source, self.root / 'second')
        self.assertEqual(repack.approved_runtime(clean), self.expected)
        self.assertTrue(second['runtime_check']['runtime_byte_identical_to_approved_download'])
        with ZipFile(second['runtime']) as archive:
            manifest = json.loads(archive.read(repack.ARCHIVE_ROOT + '/PACKAGE_MANIFEST.json'))
        self.assertEqual(manifest['baseline_archive_sha256'], repack.sha(clean.read_bytes()))
        self.assertEqual(manifest['runtime_origin_archive_sha256'], repack.BASELINE_SHA256)

    def test_tampered_clean_baseline_is_rejected_before_build(self):
        first = self.build('first')
        changed = self.rewrite(first['runtime'], lambda data: data.__setitem__(
            repack.ARCHIVE_ROOT + '/compat-runtime/SKILL.md', b'changed'))
        with self.assertRaises(ValueError):
            repack.build(changed, self.source, self.root / 'second')
        self.assertFalse((self.root / 'second').exists())

    def test_checksums_include_both_archives_and_exact_companion(self):
        result = self.build()
        self.assertEqual(Path(result['instructions']).read_bytes(),
                         (self.source / 'PROJECT_INSTRUCTIONS.txt').read_bytes())
        checksums = Path(result['checksums']).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(checksums), 3)
        for key in ('runtime', 'source', 'instructions'):
            path = Path(result[key])
            self.assertIn(repack.sha(path.read_bytes()) + '  ' + path.name, checksums)

    def test_changed_baseline_fails_before_output_creation(self):
        self.baseline.write_bytes(self.baseline.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_repaired_public_manifest_cannot_hide_runtime_tampering(self):
        result = self.build()
        changed = self.rewrite(result['runtime'], lambda data: data.__setitem__(
            repack.ARCHIVE_ROOT + '/compat-runtime/node_modules/https/package.json', b'changed'))
        with self.assertRaises(ValueError):
            repack.verify_runtime(self.baseline, changed)

    def test_extra_outer_member_is_rejected_even_with_repaired_manifest(self):
        result = self.build()
        changed = self.rewrite(result['runtime'], lambda data: data.__setitem__(
            repack.ARCHIVE_ROOT + '/engineering/note.txt', b'excluded'))
        with self.assertRaisesRegex(ValueError, 'outer inventory'):
            repack.verify_runtime(self.baseline, changed)

    def test_unsafe_and_case_duplicate_archive_paths_are_rejected(self):
        for names in [('root/../outside.txt',), ('root/File.txt', 'root/file.txt'),
                      ('root/./alias.txt',), ('root/space /file.txt',),
                      ('root/CON.txt',), ('root/question?.txt',)]:
            target = self.root / 'unsafe.zip'
            with ZipFile(target, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'synthetic')
            with self.assertRaises(ValueError):
                repack.read_archive(target)

    def test_existing_output_and_source_output_are_rejected(self):
        result = self.build()
        before = Path(result['runtime']).read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(before, Path(result['runtime']).read_bytes())
        with self.assertRaises(ValueError):
            repack.build(self.baseline, self.source, self.source / 'dist')

    def test_unsafe_companion_name_fails_before_output_creation(self):
        self.meta['project_instructions_name'] = '../outside.txt'
        self.write('RELEASE.json', json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_public_privacy_guard_runs_before_output_creation(self):
        self.write('README.zh-CN.md', 'C:/' + 'Users/' + 'PrivateExample/Documents/file.txt')
        with self.assertRaisesRegex(ValueError, 'privacy'):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_case_alias_companion_collision_fails_before_output_creation(self):
        self.meta['project_instructions_name'] = repack.RUNTIME_NAME.upper()
        self.write('RELEASE.json', json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError, 'collide'):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def prepare_complete_archive(self):
        runtime = self.expected
        data = {'compat-runtime/' + name: body for name, body in runtime.items()}
        data['PROJECT_INSTRUCTIONS.txt'] = (self.source / 'PROJECT_INSTRUCTIONS.txt').read_bytes()
        data['PACKAGE_MANIFEST.json'] = repack.encoded({
            'format': 'ppt-rebuilder.quality-test/1', 'package_id': repack.BASELINE_ROOT,
            'archive_name': 'approved-complete.zip',
            'runtime_manifest_sha256': repack.sha(runtime['MANIFEST.json']),
            'files': repack.inventory(data),
        })
        baseline = self.root / 'complete.zip'
        repack.pack(baseline, repack.BASELINE_ROOT, data, 1793836800)
        digest = repack.sha(baseline.read_bytes())
        snapshot = json.loads(runtime['MANIFEST.json'])['source_snapshot_sha256']
        for key, value in {
            'COMPLETE_ARCHIVE_SHA256': digest, 'COMPLETE_ARCHIVE_ROOT': repack.BASELINE_ROOT,
            'COMPLETE_ARCHIVE_NAME': 'approved-complete.zip',
            'COMPLETE_ARCHIVE_MEMBERS': len(data), 'COMPLETE_RUNTIME_COUNT': len(runtime),
            'COMPLETE_RUNTIME_MANIFEST_SHA256': repack.sha(runtime['MANIFEST.json']),
            'COMPLETE_RUNTIME_SNAPSHOT_SHA256': snapshot,
        }.items():
            active = patch.object(repack, key, value)
            active.start()
            self.addCleanup(active.stop)
        self.meta.update({
            'archive_name': 'approved-complete.zip', 'archive_root': repack.BASELINE_ROOT,
            'source_archive_root': repack.ARCHIVE_ROOT + '-source',
            'project_instructions_name': 'project-instructions.txt',
            'sha256sums_name': 'SHA256SUMS.txt',
            'distribution_mode': 'approved-complete-archive',
            'approved_artifact': {'sha256': digest, 'bytes': baseline.stat().st_size,
                                  'members': len(data), 'archive_root': repack.BASELINE_ROOT},
            'runtime': {'files': len(runtime), 'manifest_sha256': repack.sha(runtime['MANIFEST.json']),
                        'source_snapshot_sha256': snapshot},
        })
        (self.source / 'compat-runtime/SKILL.md').write_bytes(runtime['SKILL.md'])
        self.baseline = baseline

    def test_complete_release_exports_exact_optional_prompt_and_checksums(self):
        self.prepare_complete_archive()
        self.meta['prompt_name'] = 'restore-prompt.txt'
        (self.source / 'PROMPT.txt').write_bytes(b'Restore the supplied screenshot.\n')
        self.write('RELEASE.json', json.dumps(self.meta))
        result = self.build()
        self.assertIn('prompt', result)
        prompt = Path(result['prompt'])
        self.assertEqual(prompt.read_bytes(), b'Restore the supplied screenshot.\n')
        self.assertEqual(Path(result['runtime']).read_bytes(), self.baseline.read_bytes())
        source = repack.read_archive(result['source'])
        self.assertEqual(source[repack.ARCHIVE_ROOT + '-source/PROMPT.txt'], prompt.read_bytes())
        self.assertEqual(len(list(prompt.parent.iterdir())), 5)
        checksums = Path(result['checksums']).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(checksums), 4)
        self.assertIn(repack.sha(prompt.read_bytes()) + '  restore-prompt.txt', checksums)

    def test_complete_release_without_prompt_keeps_existing_companions(self):
        self.prepare_complete_archive()
        self.write('RELEASE.json', json.dumps(self.meta))
        result = self.build()
        self.assertNotIn('prompt', result)
        self.assertEqual(len(list(Path(result['checksums']).parent.iterdir())), 4)
        self.assertEqual(len(Path(result['checksums']).read_text(encoding='utf-8').splitlines()), 3)

    def test_invalid_complete_prompt_fails_before_output_creation(self):
        self.prepare_complete_archive()
        cases = [('restore-prompt.txt', False), ('../outside.txt', True),
                 ('PROJECT-INSTRUCTIONS.TXT', True)]
        for index, (name, create_prompt) in enumerate(cases):
            with self.subTest(name=name, create_prompt=create_prompt):
                if create_prompt:
                    self.write('PROMPT.txt', 'Restore the supplied screenshot.\n')
                self.meta['prompt_name'] = name
                self.write('RELEASE.json', json.dumps(self.meta))
                with self.assertRaises(ValueError):
                    self.build('invalid-' + str(index))
                self.assertFalse((self.root / ('invalid-' + str(index))).exists())


if __name__ == '__main__':
    unittest.main()
