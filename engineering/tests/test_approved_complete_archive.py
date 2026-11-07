"""Approved complete archives retain their bytes and validated context."""
from pathlib import Path
from unittest import mock
from zipfile import ZipFile
import hashlib
import json
import tempfile
import unittest

from tools import repack_runtime as repack
from tools import build_release
from tools.verify_release import verify


class ApprovedCompleteArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-complete-archive-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.baseline = self.root / 'approved.zip'
        self.identity = repack.ARCHIVE_ROOT
        self.runtime = {
            'LICENSE': b'Synthetic MIT fixture\n',
            'SKILL.md': b'Synthetic workflow fixture\n',
            'newpath/version.py': b'PACKAGE_ID = "synthetic"\n',
            'node_modules/example/LICENSE': b'Synthetic dependency license\n',
        }
        rows = repack.inventory(self.runtime)
        snapshot = repack.sha(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode('utf-8'))
        runtime_manifest = repack.encoded({
            'format': 'newpath.package-manifest/1', 'package_id': self.identity,
            'archive_name': repack.RUNTIME_NAME, 'files': rows,
            'source_file_count': len(rows), 'source_snapshot_sha256': snapshot,
        })
        self.runtime['MANIFEST.json'] = runtime_manifest
        self.payload = {'compat-runtime/' + name: body for name, body in self.runtime.items()}
        self.payload.update({
            'PROJECT_INSTRUCTIONS.txt': b'Synthetic project instructions\n',
            'README.zh-CN.md': b'Synthetic user readme\n',
            'engineering/context.txt': b'Synthetic engineering context\n',
            'verification/record.json': b'{"synthetic": true}\n',
            'baseline-record/context.txt': b'Synthetic local baseline context\n',
        })
        self.payload['PACKAGE_MANIFEST.json'] = repack.encoded({
            'format': 'ppt-rebuilder.quality-test/1', 'package_id': self.identity,
            'archive_name': repack.RUNTIME_NAME,
            'runtime_manifest_sha256': repack.sha(runtime_manifest),
            'files': repack.inventory(self.payload),
        })
        repack.pack(self.baseline, self.identity, self.payload, 1793836800)
        pins = {
            'BASELINE_ROOT': self.identity,
            'BASELINE_SHA256': repack.sha(self.baseline.read_bytes()),
            'RUNTIME_MANIFEST_SHA256': repack.sha(runtime_manifest),
            'RUNTIME_SNAPSHOT_SHA256': snapshot,
            'RUNTIME_COUNT': len(self.runtime),
            'COMPLETE_ARCHIVE_SHA256': repack.sha(self.baseline.read_bytes()),
            'COMPLETE_ARCHIVE_ROOT': self.identity,
            'COMPLETE_ARCHIVE_NAME': repack.RUNTIME_NAME,
            'COMPLETE_ARCHIVE_MEMBERS': len(self.payload),
            'COMPLETE_RUNTIME_MANIFEST_SHA256': repack.sha(runtime_manifest),
            'COMPLETE_RUNTIME_SNAPSHOT_SHA256': snapshot,
            'COMPLETE_RUNTIME_COUNT': len(self.runtime),
        }
        for name, value in pins.items():
            patcher = mock.patch.object(repack, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in repack.RUNTIME_DOCS | repack.RUNTIME_LEGAL_FILES:
            self.write(name, b'Synthetic public document\n')
        for name, body in self.runtime.items():
            if name != 'MANIFEST.json' and not name.startswith('node_modules/'):
                self.write('compat-runtime/' + name, body)
        self.write('PROJECT_INSTRUCTIONS.txt', self.payload['PROJECT_INSTRUCTIONS.txt'])
        self.meta = {
            'version': 'synthetic', 'distribution_mode': 'approved-complete-archive',
            'package_id': self.identity, 'archive_root': self.identity,
            'archive_name': repack.RUNTIME_NAME, 'source_archive_name': repack.SOURCE_NAME,
            'source_archive_root': repack.ARCHIVE_ROOT + '-source',
            'source_date_epoch': 1793836800,
            'project_instructions_name': 'PROJECT-INSTRUCTIONS.txt',
            'sha256sums_name': 'SHA256SUMS.txt',
            'runtime': {'files': len(self.runtime),
                        'manifest_sha256': repack.sha(runtime_manifest),
                        'source_snapshot_sha256': snapshot},
            'approved_artifact': {'sha256': repack.sha(self.baseline.read_bytes()),
                                  'bytes': self.baseline.stat().st_size,
                                  'members': len(self.payload), 'archive_root': self.identity},
        }
        self.write('RELEASE.json', repack.encoded(self.meta))

    def write(self, name, body):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def build(self, name='out'):
        return repack.build(self.baseline, self.source, self.root / name)

    def test_approved_zip_bytes_and_all_context_members_are_retained(self):
        result = self.build()
        actual = Path(result['runtime'])
        self.assertEqual(actual.read_bytes(), self.baseline.read_bytes())
        with ZipFile(actual) as archive:
            expected_names = {self.identity + '/' + name for name in self.payload}
            self.assertEqual(set(archive.namelist()), expected_names)
            self.assertEqual(archive.read(self.identity + '/verification/record.json'),
                             self.payload['verification/record.json'])

    def test_complete_runtime_and_allowlisted_source_verify_separately(self):
        result = self.build()
        runtime_check = verify(Path(result['runtime']))
        self.assertTrue(runtime_check['valid'])
        self.assertEqual(runtime_check['files'], len(self.payload))
        self.assertTrue(repack.verify_source(Path(result['source']))['valid'])
        with ZipFile(result['source']) as archive:
            self.assertFalse(any('/verification/' in name for name in archive.namelist()))
            self.assertFalse(any('/node_modules/' in name for name in archive.namelist()))

    def test_source_code_must_equal_the_approved_runtime(self):
        self.write('compat-runtime/newpath/version.py', b'PACKAGE_ID = "different"\n')
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_companion_instructions_must_equal_the_approved_zip(self):
        self.write('PROJECT_INSTRUCTIONS.txt', b'Different instructions\n')
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_changed_complete_archive_is_rejected_even_with_valid_zip_crc(self):
        self.baseline.write_bytes(self.baseline.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            self.build()

    def test_unknown_quality_archive_cannot_bypass_the_public_verifier(self):
        changed = self.root / 'unknown.zip'
        changed.write_bytes(self.baseline.read_bytes() + b'unknown')
        with self.assertRaises(ValueError):
            verify(changed)

    def test_approved_complete_files_still_reject_private_machine_paths(self):
        private_path = 'C:/' + 'Users/PrivateOwner/Desktop/private.py'
        self.payload['verification/record.json'] = repack.encoded({'path': private_path})
        manifest = json.loads(self.payload['PACKAGE_MANIFEST.json'])
        manifest['files'] = repack.inventory({name: body for name, body in self.payload.items()
                                            if name != 'PACKAGE_MANIFEST.json'})
        self.payload['PACKAGE_MANIFEST.json'] = repack.encoded(manifest)
        revised = self.root / 'private-path.zip'
        repack.pack(revised, self.identity, self.payload, 1793836800)
        with mock.patch.object(repack, 'COMPLETE_ARCHIVE_SHA256', repack.sha(revised.read_bytes())):
            with self.assertRaisesRegex(ValueError, 'private user path'):
                verify(revised)

    def test_generic_builder_rejects_complete_distribution_before_creating_output(self):
        self.meta['archive_root'] = 'Approved Complete Archive'
        self.write('RELEASE.json', repack.encoded(self.meta))
        self.write('compat-runtime/newpath/version.py', (
            "PACKAGE_ID = '" + self.identity + "'\nARCHIVE_NAME = '" +
            repack.RUNTIME_NAME + "'\n").encode('utf-8'))
        lockfile = b'{}\n'
        dependency = b'Synthetic dependency license\n'
        self.write('compat-runtime/package-lock.json', lockfile)
        self.write('compat-runtime/node_modules/example/LICENSE', dependency)
        self.write('dependency/runtime-files.json', repack.encoded({
            'format': 'ppt-rebuilder.runtime-dependencies/1',
            'package_lock_sha256': repack.sha(lockfile),
            'files': [{'path': 'example/LICENSE', 'size': len(dependency),
                       'sha256': repack.sha(dependency)}],
        }))
        output = self.root / 'generic-out'
        with self.assertRaises(ValueError):
            build_release.build(self.source, output)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
