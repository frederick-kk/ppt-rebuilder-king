"""Public release packaging contracts; no image providers."""
from pathlib import Path
import json,sys,tempfile,unittest,hashlib
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.build_release import build
from tools.verify_release import verify

class ReleaseBuilder(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.src=self.root/'src';self.src.mkdir()
        meta={'package_id':'PPT Rebuilder King-test-beta','archive_name':'runtime.zip','source_archive_name':'source.zip','version':'test-beta','release_date':'2026-10-24','source_date_epoch':1792800000}
        self.write('RELEASE.json',json.dumps(meta));self.write('LICENSE','MIT fixture')
        self.write('NOTICE.md','Synthetic test');self.write('THIRD_PARTY_LICENSES.md','Fixture notices')
        self.write('PROJECT_INSTRUCTIONS.txt','PPT Rebuilder King-test-beta runtime.zip')
        self.write('README.zh-CN.md','source usage');self.write('KNOWN_ISSUES.md','beta')
        self.write('compat-runtime/newpath/version.py',"PACKAGE_ID = 'PPT Rebuilder King-test-beta'\nARCHIVE_NAME = 'runtime.zip'\n")
        self.write('compat-runtime/SKILL.md','fixture skill');self.write('compat-runtime/package.json','{}')
        self.write('compat-runtime/package-lock.json','{}');self.write('compat-runtime/node_modules/jszip/LICENSE','test license')
        self.write('compat-runtime/node_modules/jszip/dist/main.js','module.exports = {};')
        self.write('compat-runtime/node_modules/https/package.json','{"license":"ISC"}')
        modules=self.src/'compat-runtime/node_modules'
        self.write('dependency/runtime-files.json',json.dumps({'format':'ppt-rebuilder.runtime-dependencies/1','package_lock_sha256':hashlib.sha256((self.src/'compat-runtime/package-lock.json').read_bytes()).hexdigest(),'files':[
            {'path':p.relative_to(modules).as_posix(),'size':len(p.read_bytes()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(modules.rglob('*')) if p.is_file() and not p.is_relative_to(modules/'https')]}))
        self.write('docs/USAGE.zh-CN.md','Usage');self.write('verification/private.txt','do not ship')
        self.write('baseline-record/private.txt','do not ship');self.write('.env','do not ship')
    def write(self,name,text):
        p=self.src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def test_build_twice_is_byte_reproducible(self):
        before={p.relative_to(self.src):p.read_bytes() for p in self.src.rglob('*') if p.is_file()}
        a=build(self.src,self.root/'a');b=build(self.src,self.root/'b')
        for key in ['runtime','source']:
            self.assertEqual(Path(a[key]).read_bytes(),Path(b[key]).read_bytes())
        self.assertEqual(before,{p.relative_to(self.src):p.read_bytes() for p in self.src.rglob('*') if p.is_file()})
    def test_runtime_and_source_are_separated_and_both_manifests_verify(self):
        out=build(self.src,self.root/'out')
        self.assertTrue(verify(Path(out['runtime']))['valid'])
        self.assertTrue(verify(Path(out['source']))['valid'])
        with ZipFile(out['runtime']) as z:
            names=z.namelist();self.assertFalse(any('verification/' in n or 'baseline-record/' in n or 'engineering/' in n for n in names))
            self.assertFalse(any('node_modules/https/' in n for n in names))
            self.assertTrue(any('node_modules/jszip/' in n for n in names))
            self.assertTrue(any(n.endswith('/node_modules/jszip/dist/main.js') for n in names))
        with ZipFile(out['source']) as z:self.assertFalse(any('/node_modules/' in n or n.endswith('.env') for n in z.namelist()))
    def test_does_not_overwrite_source_or_output(self):
        with self.assertRaises(ValueError):build(self.src,self.src/'dist')
        build(self.src,self.root/'out')
        with self.assertRaises(FileExistsError):build(self.src,self.root/'out')
    def test_real_user_path_in_public_doc_is_rejected(self):
        self.write('docs/leak.md','C:/'+'Users/'+'RealPrivatePerson/Documents/internal.txt')
        with self.assertRaisesRegex(ValueError,'privacy|private'):build(self.src,self.root/'out')
    def test_changed_archive_member_fails_manifest_verification(self):
        out=build(self.src,self.root/'out');target=self.root/'tampered.zip'
        with ZipFile(out['runtime']) as old,ZipFile(target,'w') as new:
            for n in old.namelist():new.writestr(n,b'changed' if n.endswith('/SKILL.md') else old.read(n))
        with self.assertRaises(ValueError):verify(target)

    def test_traversal_archive_is_rejected_before_content_is_read(self):
        target=self.root/'traversal.zip'
        with ZipFile(target,'w') as z:z.writestr('root/../outside.txt','malicious')
        with self.assertRaisesRegex(ValueError,'Unsafe'):verify(target)

    def test_unlisted_member_is_not_silently_accepted(self):
        out=build(self.src,self.root/'out');target=self.root/'unlisted.zip'
        with ZipFile(out['runtime']) as old,ZipFile(target,'w') as new:
            for n in old.namelist():new.writestr(n,old.read(n))
            new.writestr('PPT Rebuilder King-test-beta/unlisted.txt','unexpected')
        with self.assertRaisesRegex(ValueError,'inventory'):verify(target)

    def test_identity_disagreement_does_not_create_output(self):
        self.write('compat-runtime/newpath/version.py',"PACKAGE_ID = 'wrong'\nARCHIVE_NAME = 'runtime.zip'\n")
        with self.assertRaisesRegex(ValueError,'identity'):build(self.src,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_clean_install_extras_do_not_change_either_archive(self):
        original=build(self.src,self.root/'original')
        self.write('compat-runtime/node_modules/.bin/image-size.cmd','installer wrapper')
        self.write('compat-runtime/node_modules/jszip/.github/workflows/pr.yaml','upstream development only')
        self.write('compat-runtime/node_modules/.package-lock.json','installer metadata')
        reinstalled=build(self.src,self.root/'reinstalled')
        for key in ['runtime','source']:
            self.assertEqual(Path(original[key]).read_bytes(),Path(reinstalled[key]).read_bytes())

    def test_missing_pinned_dependency_is_rejected_before_output(self):
        (self.src/'compat-runtime/node_modules/jszip/dist/main.js').unlink()
        with self.assertRaisesRegex(ValueError,'dependency'):build(self.src,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_changed_pinned_dependency_is_rejected_before_output(self):
        self.write('compat-runtime/node_modules/jszip/dist/main.js','different module')
        with self.assertRaisesRegex(ValueError,'dependency'):build(self.src,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_dependency_allowlist_cannot_escape_node_modules(self):
        self.write('dependency/runtime-files.json',json.dumps({'format':'ppt-rebuilder.runtime-dependencies/1','package_lock_sha256':hashlib.sha256((self.src/'compat-runtime/package-lock.json').read_bytes()).hexdigest(),'files':[
            {'path':'../package.json','size':2,'sha256':hashlib.sha256(b'{}').hexdigest()}]}))
        with self.assertRaisesRegex(ValueError,'dependency'):build(self.src,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_dependency_allowlist_requires_the_approved_lockfile(self):
        self.write('compat-runtime/package-lock.json','{"different":"lockfile"}')
        with self.assertRaisesRegex(ValueError,'dependency'):build(self.src,self.root/'out')
        self.assertFalse((self.root/'out').exists())
