"""Production ZIP policy tests; all builds use disposable source fixtures."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
if SCRIPT.is_file():
    spec = importlib.util.spec_from_file_location("newpath_package_tool", SCRIPT)
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
else:
    packager = None

ROOT_VERSIONS = {"pptxgenjs": "4.0.1", "jszip": "3.10.1"}


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(packager, "independent package tool is missing")
        self.temporary = tempfile.TemporaryDirectory(prefix="newpath-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "explicit-source"
        self.root.mkdir()
        for name in ("SKILL.md", "README.md", "LICENSE", "requirements.txt", "pyproject.toml"):
            self.write(name, f"independent fixture for {name}\n".encode())
        self.write("newpath/core.py", b"VALUE = 'own source'\n")
        self.write("newpath/render.cjs", b"'use strict';\n")
        self.write("references/scene.md", b"finite fixture contract\n")
        self.write("package.json", json.dumps({"dependencies": ROOT_VERSIONS}).encode())
        self.write("package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {
            "": {"dependencies": ROOT_VERSIONS},
            **{"node_modules/" + name: {"version": version} for name, version in ROOT_VERSIONS.items()},
        }}).encode())
        dependency_files = {}
        for name, version in ROOT_VERSIONS.items():
            for filename, contents in {
                "package.json": json.dumps({"name": name, "version": version}).encode(),
                "dist/runtime.cjs": b"module.exports = {}; // owned inert fixture\n",
            }.items():
                relative = f"node_modules/{name}/{filename}"
                self.write(relative, contents)
                dependency_files[relative] = hashlib.sha256(contents).hexdigest()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(packager, "LOCKED_DEPENDENCY_FILES", dependency_files))

    def write(self, relative, contents):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    def build(self, **kwargs):
        return packager.build_package(self.root, **kwargs)

    def test_exact_production_allowlist_and_generated_manifest(self):
        result = self.build()
        archive = Path(result["archive"])
        self.assertEqual(archive.name, "新路径-ChatGPT-20261018-r1.zip")
        self.assertEqual(archive.parent, self.root / "dist")
        with ZipFile(archive) as zipfile:
            names = zipfile.namelist()
            self.assertIsNone(zipfile.testzip())
            self.assertEqual(set(names), {
                "SKILL.md", "README.md", "LICENSE", "requirements.txt", "pyproject.toml",
                "package.json", "package-lock.json", "newpath/core.py", "newpath/render.cjs",
                "references/scene.md", "MANIFEST.json", *packager.LOCKED_DEPENDENCY_FILES,
            })
            manifest_bytes = zipfile.read("MANIFEST.json")
            manifest = json.loads(manifest_bytes)
            self.assertEqual(result["manifest_sha256"], hashlib.sha256(manifest_bytes).hexdigest())
            self.assertEqual(manifest["dependency_versions"], ROOT_VERSIONS)
            self.assertEqual(manifest["dependency_file_count"], 4)
            for entry in manifest["files"]:
                contents = zipfile.read(entry["path"])
                self.assertEqual(entry["sha256"], hashlib.sha256(contents).hexdigest())
                self.assertEqual(entry["size"], len(contents))
        checked = packager.verify_package(archive, expected_manifest_sha256=result["manifest_sha256"])
        self.assertTrue(checked["zip_crc_valid"])
        self.assertTrue(checked["manifest_valid"])
        self.assertEqual(result["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertTrue(result["source_unchanged"])

    def test_unknown_and_malicious_directories_never_enter_zip(self):
        for relative in [".dev/secret.txt", "tests/private.py", "scripts/install.py", "jobs/input.png",
                         "dist/stale.zip", "old-task/work.pptx", "reference-third-party/source.py",
                         "newpath/__pycache__/private.pyc", "newpath/nested/hidden.py", "newpath/input.png",
                         "references/old-architecture.md", "node_modules/evil/postinstall.js",
                         "node_modules/pptxgenjs/scripts/postinstall.js", "node_modules/pptxgenjs/unlocked.cjs"]:
            self.write(relative, b"must never ship")
        result = self.build()
        with ZipFile(result["archive"]) as archive:
            names = archive.namelist()
        self.assertFalse(any("secret" in name or "old-" in name or "postinstall" in name for name in names))
        self.assertEqual(len(names), 15)

    def test_source_mutation_during_staging_refuses_publication(self):
        original_write = packager._write_archive

        def mutate_after_write(*args, **kwargs):
            original_write(*args, **kwargs)
            self.write("newpath/core.py", b"changed during package staging\n")

        with patch.object(packager, "_write_archive", side_effect=mutate_after_write):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.build()
        self.assertEqual(list((self.root / "dist").glob("*.zip")), [])
        self.assertEqual(list((self.root / "dist").glob(".newpath-package-*")), [])

    def test_late_added_allowed_source_is_detected(self):
        original_write = packager._write_archive

        def mutate_after_write(*args, **kwargs):
            original_write(*args, **kwargs)
            self.write("newpath/late.py", b"late addition\n")

        with patch.object(packager, "_write_archive", side_effect=mutate_after_write):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.build()
        self.assertEqual(list((self.root / "dist").glob("*.zip")), [])

    def test_locked_dependency_changes_are_refused(self):
        self.write("node_modules/pptxgenjs/dist/runtime.cjs", b"unapproved dependency replacement")
        with self.assertRaisesRegex(ValueError, "dependency"):
            self.build()
        self.assertFalse((self.root / "dist" / packager.ARCHIVE_NAME).exists())

    def test_tested_snapshot_binds_packaging_to_the_same_production_inputs(self):
        self.assertTrue(hasattr(packager, "freeze_source"), "source freeze API is missing")
        frozen = packager.freeze_source(self.root)
        self.assertEqual(frozen["format"], "newpath.source-snapshot/1")
        result = self.build(tested_snapshot=json.loads(json.dumps(frozen)))
        self.assertEqual(result["source_snapshot_sha256"], frozen["source_snapshot_sha256"])
        self.assertTrue(result["tested_snapshot_verified"])

    def test_source_change_after_tested_snapshot_is_refused_before_build(self):
        self.assertTrue(hasattr(packager, "freeze_source"), "source freeze API is missing")
        frozen = packager.freeze_source(self.root)
        self.write("README.md", b"changed after verification tests")
        with self.assertRaisesRegex(ValueError, "tested snapshot"):
            self.build(tested_snapshot=frozen)
        self.assertFalse((self.root / "dist" / packager.ARCHIVE_NAME).exists())

    def test_excluded_reference_files_do_not_change_production_snapshot(self):
        self.assertTrue(hasattr(packager, "freeze_source"), "source freeze API is missing")
        frozen = packager.freeze_source(self.root)
        self.write("新路径-项目指令.txt", b"outside runtime zip")
        self.write("tests/local-test.py", b"separately frozen test source")
        self.assertEqual(packager.freeze_source(self.root), frozen)

    def test_existing_output_race_never_overwrites_other_writer(self):
        import_name = "rename" if os.name == "nt" else "link"
        original = getattr(packager.os, import_name)

        def raced_publish(source, destination):
            Path(destination).write_bytes(b"another writer")
            return original(source, destination)

        with patch.object(packager.os, import_name, side_effect=raced_publish):
            with self.assertRaises(FileExistsError):
                self.build()
        self.assertEqual((self.root / "dist" / packager.ARCHIVE_NAME).read_bytes(), b"another writer")

    def test_missing_required_file_or_dependency_is_an_error(self):
        (self.root / "README.md").unlink()
        with self.assertRaisesRegex(ValueError, "README"):
            self.build()
        self.write("README.md", b"restored")
        (self.root / "node_modules/jszip/package.json").unlink()
        with self.assertRaisesRegex(ValueError, "jszip"):
            self.build()

    def test_wrong_direct_versions_fail_without_any_install_or_download(self):
        self.write("package.json", json.dumps({"dependencies": {**ROOT_VERSIONS, "jszip": "^3.10.1"}}).encode())
        with patch("subprocess.run", side_effect=AssertionError("No external commands")):
            with patch("socket.create_connection", side_effect=AssertionError("No network")):
                with self.assertRaisesRegex(ValueError, "version|dependencies"):
                    self.build()

    def test_success_never_runs_install_or_network(self):
        with patch("subprocess.run", side_effect=AssertionError("No external commands")):
            with patch("socket.create_connection", side_effect=AssertionError("No network")):
                result = self.build()
        self.assertTrue(Path(result["archive"]).is_file())

    def test_existing_archive_is_not_overwritten(self):
        destination = self.root / "dist" / packager.ARCHIVE_NAME
        destination.parent.mkdir()
        destination.write_bytes(b"user archive")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(destination.read_bytes(), b"user archive")

    def test_archive_is_deterministic_and_does_not_record_absolute_source(self):
        first = self.build()
        second = self.build(output_dir=self.root / "second-output")
        self.assertEqual(first["sha256"], second["sha256"])
        with ZipFile(first["archive"]) as archive:
            self.assertNotIn(str(self.root).encode(), archive.read("MANIFEST.json"))

    def test_verifier_rejects_changed_member_and_unlisted_member(self):
        result = self.build()
        path = Path(result["archive"])
        with ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members["newpath/core.py"] = b"tampered after packaging"
        with ZipFile(path, "w") as archive:
            for name, contents in members.items():
                archive.writestr(name, contents)
        with self.assertRaisesRegex(ValueError, "hash|SHA|size"):
            packager.verify_package(path)
        members["newpath/core.py"] = b"VALUE = 'own source'\n"
        members["jobs/hidden.png"] = b"injected"
        with ZipFile(path, "w") as archive:
            for name, contents in members.items():
                archive.writestr(name, contents)
        with self.assertRaisesRegex(ValueError, "member|declared|whitelist"):
            packager.verify_package(path)

    def test_verifier_checks_manifest_own_sha_when_supplied(self):
        result = self.build()
        with self.assertRaisesRegex(ValueError, "manifest"):
            packager.verify_package(Path(result["archive"]), expected_manifest_sha256="0" * 64)

    def test_verifier_checks_zip_crc_before_trusting_manifest(self):
        damaged = self.root / "damaged.zip"
        with ZipFile(damaged, "w", ZIP_STORED) as archive:
            archive.writestr("MANIFEST.json", b"this must get a CRC check")
        data = bytearray(damaged.read_bytes())
        name_len, extra_len = struct.unpack_from("<HH", data, 26)
        data[30 + name_len + extra_len] ^= 1
        damaged.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "ZIP|CRC"):
            packager.verify_package(damaged)

    def test_selected_symlink_is_refused(self):
        outside = Path(self.temporary.name) / "outside.py"
        outside.write_bytes(b"outside input")
        link = self.root / "newpath" / "linked.py"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink privilege is not available on this Windows host")
        with self.assertRaisesRegex(ValueError, "link|reparse"):
            self.build()


class FrozenMetadataTests(unittest.TestCase):
    def test_real_dependency_inventory_is_explicit_and_frozen(self):
        self.assertIsNotNone(packager, "independent package tool is missing")
        self.assertEqual(len(packager.LOCKED_DEPENDENCY_FILES), 394)
        self.assertIn("node_modules/pptxgenjs/dist/pptxgen.cjs.js", packager.LOCKED_DEPENDENCY_FILES)
        self.assertIn("node_modules/jszip/package.json", packager.LOCKED_DEPENDENCY_FILES)
        for name, digest in packager.LOCKED_DEPENDENCY_FILES.items():
            self.assertTrue(name.startswith("node_modules/"))
            self.assertEqual(len(digest), 64)

    def test_real_394_dependency_payloads_validate_without_importing_or_installing(self):
        self.assertIsNotNone(packager, "independent package tool is missing")
        import shutil
        source = SCRIPT.parents[1]
        with tempfile.TemporaryDirectory(prefix="newpath-full-dependency-fixture-") as temporary:
            root = Path(temporary)
            for name in ("SKILL.md", "README.md", "LICENSE", "requirements.txt", "pyproject.toml"):
                (root / name).write_text("owned temporary fixture\n", encoding="utf-8")
            (root / "newpath").mkdir()
            (root / "newpath" / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "references").mkdir()
            (root / "references" / "scene.md").write_text("owned temporary fixture\n", encoding="utf-8")
            for name in ("package.json", "package-lock.json", *packager.LOCKED_DEPENDENCY_FILES):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target)
            with patch("subprocess.run", side_effect=AssertionError("No dependency execution")):
                with patch("socket.create_connection", side_effect=AssertionError("No network")):
                    result = packager.build_package(root)
            self.assertEqual(result["dependency_file_count"], 394)
            checked = packager.verify_package(Path(result["archive"]))
            self.assertEqual(checked["dependency_versions"], ROOT_VERSIONS)

    def test_packager_imports_only_standard_library(self):
        import ast
        import sys
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imports.add(node.module.split(".")[0])
        self.assertTrue(imports.issubset(sys.stdlib_module_names), imports - sys.stdlib_module_names)


if __name__ == "__main__":
    unittest.main()
