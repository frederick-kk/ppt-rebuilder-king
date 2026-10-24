"""Read-only inspector behavior, using only local synthetic records."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[2]
SCRIPT = PACKAGE / "engineering" / "inspect_job.py"
SPEC = importlib.util.spec_from_file_location("inspect_job_under_test", SCRIPT)


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "job"
        self.job.mkdir()

    def save(self, name, value):
        path = self.job / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def run_inspector(self, output=None, fmt="json"):
        output = output or self.root / ("report." + ("json" if fmt == "json" else "md"))
        command = [sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--job", str(self.job), "--output", str(output)]
        if fmt is not None:
            command += ["--format", fmt]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        return result, output

    def report(self):
        result, output = self.run_inspector()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_missing_records_are_unknown_and_never_all_clear(self):
        report = self.report()
        self.assertEqual(report["records"]["state"]["status"], "missing")
        self.assertEqual(report["records"]["compiled_report"]["status"], "missing")
        self.assertEqual(report["assessment"]["evidence_status"], "incomplete")
        self.assertIsNone(report["assessment"]["visual_acceptance"])
        self.assertIsNone(report["workflow"]["phase"])
        self.assertIn("未记录", report["assessment"]["explanation"])

    def test_workflow_and_caller_reported_unknowns_remain_distinct(self):
        self.save("state.json", {"phase": "boards", "tasks": {"a": {
            "id": "a", "role": "board", "status": "waiting", "attempt": 1, "issued": True,
            "attempts": [{"attempt": 0, "invocation_count": 2, "tool": "reported-tool"}],
            "extraction": {"cell_mapping": {"status": "ambiguous", "unsafe_members": ["art"]}}
        }}})
        self.save("receipt.json", {"quality": "not_evaluated", "caller_reported_invocations": None})
        report = self.report()
        task = report["workflow"]["tasks"][0]
        self.assertEqual((task["status"], task["attempt"], task["issued"]), ("waiting", 1, True))
        self.assertEqual(task["attempts"][0]["invocation_count"], 2)
        self.assertEqual(task["extraction"]["cell_mapping"]["unsafe_members"], ["art"])
        self.assertIsNone(report["workflow"]["caller_reported_invocations"])
        self.assertEqual(report["workflow"]["quality"], "not_evaluated")
        self.assertIn("调用方", report["workflow"]["invocation_explanation"])

    def test_known_warning_codes_are_explained_without_inventing_failure(self):
        codes = ["source_matte_collision_unresolved", "touch_slot_edge", "safe_area_cross", "cross_slot", "ambiguous_member_omitted"]
        self.save("state.json", {"phase": "done", "tasks": {}, "warnings": [{"code": code, "id": "art"} for code in codes]})
        report = self.report()
        warnings = {warning["code"]: warning for warning in report["warnings"]}
        self.assertTrue(set(codes).issubset(warnings))
        for code in codes:
            self.assertTrue(warnings[code]["explanation"])
            self.assertEqual(warnings[code]["severity"], "risk")
        self.assertNotEqual(report["assessment"]["evidence_status"], "failed")
        self.assertIsNone(report["assessment"]["visual_acceptance"])

    def test_mixed_measurement_and_table_lines_preserve_known_overflow(self):
        self.save("build/compiled.report.json", {"objects": [
            {"id": "text", "kind": "text", "measurement": {"status": "measured", "remaining_overflow_px": 4.5}},
            {"id": "table", "kind": "table", "cell_lines": [{"row": 0, "column": 1, "line": 0, "measurement": {"status": "estimated", "remaining_overflow_px": 2}}]},
            {"id": "eq", "kind": "formula", "measurement": {"status": "native_math_unmeasured", "literal_fallback": {"status": "measured", "remaining_overflow_px": 7}}}
        ]})
        report = self.report()
        summary = report["rendering"]["text_measurement"]
        self.assertEqual(summary["status_counts"], {"measured": 1, "estimated": 1, "native_math_unmeasured": 1})
        self.assertEqual(len(summary["known_overflow"]), 2)
        self.assertEqual(summary["known_overflow"][0]["remaining_overflow_px"], 4.5)
        self.assertEqual(summary["literal_fallback_overflow"][0]["remaining_overflow_px"], 7)
        self.assertEqual(summary["entries"][1]["column"], 1)

    def test_native_counts_equations_checks_and_skipped_are_reused(self):
        counts = {"text_frames": 3, "shapes": 2, "lines": 1, "table_groups": 1, "table_cells": 4, "foreground_images": 2}
        equations = {"embedded": ["eq"], "fallback": [], "warnings": []}
        validation = {"zip_valid": True, "xml_well_formed": True, "office_render_checked": False}
        self.save("state.json", {"phase": "done", "done": {"kind": "done", "quality": "not_evaluated"}, "tasks": {}})
        self.save("receipt.json", {"kind": "done", "quality": "not_evaluated", "skipped": [{"id": "art", "reason": "missing_asset"}]})
        self.save("build/compiled.report.json", {"objects": [], "native_counts": counts, "equations": equations, "validation": validation})
        report = self.report()
        self.assertEqual(report["rendering"]["native_counts"], counts)
        self.assertEqual(report["rendering"]["equations"], equations)
        self.assertEqual(report["rendering"]["validation"], validation)
        self.assertEqual(report["workflow"]["done"], {"kind": "done", "quality": "not_evaluated"})
        self.assertEqual(report["skipped"][0]["record"]["reason"], "missing_asset")
        self.assertIn("Office", report["rendering"]["validation_explanation"])

    def test_source_bytes_and_timestamps_remain_unchanged(self):
        self.save("state.json", {"phase": "layers", "tasks": {}})
        self.save("receipt.json", {"quality": "not_evaluated"})
        (self.job / "unrelated.bin").write_bytes(b"immutable bytes")
        before = {p.relative_to(self.job).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in self.job.rglob("*") if p.is_file()}
        report = self.report()
        after = {p.relative_to(self.job).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in self.job.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(report["source_integrity"]["unchanged"])
        self.assertEqual(report["source_integrity"]["before"], report["source_integrity"]["after"])

    def test_refuses_output_inside_job(self):
        result, output = self.run_inspector(self.job / "report.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_refuses_output_inside_runtime_without_creating_it(self):
        result, output = self.run_inspector(PACKAGE / "compat-runtime" / "inspector-test-must-not-exist.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_refuses_existing_output_and_preserves_contents(self):
        output = self.root / "report.json"
        output.write_text("keep me", encoding="utf-8")
        result, _ = self.run_inspector(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep me")

    def test_corrupt_and_wrong_shape_records_do_not_masquerade_as_empty_pass(self):
        (self.job / "state.json").write_text("{broken", encoding="utf-8")
        self.save("receipt.json", [])
        self.save("build/compiled.report.json", {"objects": "bad", "validation": "bad"})
        report = self.report()
        self.assertEqual(report["records"]["state"]["status"], "invalid")
        self.assertEqual(report["records"]["receipt"]["status"], "invalid")
        self.assertTrue(report["data_issues"])
        self.assertIsNone(report["rendering"]["objects"])
        self.assertEqual(report["assessment"]["evidence_status"], "incomplete")

    def test_partial_nested_report_is_incomplete_even_when_documents_exist(self):
        self.save("state.json", {"phase": "done", "tasks": {}})
        self.save("receipt.json", {"quality": "not_evaluated"})
        self.save("build/compiled.report.json", {"objects": [], "native_counts": {}, "equations": {}, "validation": {}})
        report = self.report()
        self.assertEqual(report["assessment"]["evidence_status"], "incomplete")
        sources = [issue["source"] for issue in report["data_issues"]]
        self.assertIn("compiled_report.validation.office_render_checked", sources)
        self.assertIn("compiled_report.native_counts.text_frames", sources)

    def test_malformed_done_assets_and_attempt_elements_stay_raw_but_incomplete(self):
        self.save("state.json", {"phase": "done", "tasks": {"a": {
            "id": "a", "role": "background", "status": "recorded", "attempt": 0, "issued": True,
            "attempts": ["malformed"]}}, "done": ["malformed"], "assets": "malformed"})
        self.save("receipt.json", {"quality": "not_evaluated"})
        self.save("build/compiled.report.json", {
            "objects": [], "native_counts": {"text_frames": 0, "shapes": 0, "lines": 0,
                "table_groups": 0, "table_cells": 0, "foreground_images": 0},
            "equations": {"embedded": [], "fallback": [], "warnings": []},
            "validation": {"zip_valid": True, "xml_well_formed": True, "background_picture_fill": True,
                "office_render_checked": False, "required_parts": []}})
        report = self.report()
        self.assertEqual(report["assessment"]["evidence_status"], "incomplete")
        sources = [issue["source"] for issue in report["data_issues"]]
        self.assertIn("state.done", sources)
        self.assertIn("state.assets", sources)
        self.assertIn("state.tasks.a.attempts[0]", sources)
        self.assertEqual(report["workflow"]["done"], ["malformed"])
        self.assertEqual(report["extraction"]["assets"], "malformed")
        self.assertEqual(report["workflow"]["tasks"][0]["attempts"], ["malformed"])
        self.assertEqual(report["workflow"]["quality"], "not_evaluated")

    def test_malformed_assets_members_and_attempt_container_are_preserved(self):
        self.save("state.json", {"phase": "boards", "assets": ["malformed"], "tasks": {"a": {
            "id": "a", "role": "board", "status": "failed", "attempt": 0, "issued": True,
            "attempts": "malformed"}}})
        report = self.report()
        sources = [issue["source"] for issue in report["data_issues"]]
        self.assertIn("state.assets[0]", sources)
        self.assertIn("state.tasks.a.attempts", sources)
        self.assertEqual(report["extraction"]["assets"], ["malformed"])
        self.assertEqual(report["workflow"]["tasks"][0]["attempts"], "malformed")

    def test_unhashable_malformed_fields_are_reported_without_crashing(self):
        self.save("state.json", {"phase": [], "tasks": {"a": {"status": {}, "attempt": "bad", "issued": "bad"}}, "warnings": [{"code": []}]})
        self.save("build/compiled.report.json", {"objects": [{"id": "t", "kind": "text", "measurement": {"status": []}}]})
        report = self.report()
        self.assertEqual(report["assessment"]["evidence_status"], "incomplete")
        self.assertTrue(report["warnings"])

    def test_changed_source_aborts_before_report_publication(self):
        inspector = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(inspector)
        output = self.root / "aborted.json"
        with mock.patch.object(inspector, "_inventory", side_effect=[{}, {"changed.txt": {"kind": "file"}}]):
            with self.assertRaisesRegex(ValueError, "changed"):
                inspector.inspect_job(self.job, output, "json")
        self.assertFalse(output.exists())

    def test_linked_report_is_not_followed_and_output_alias_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.save("state.json", {"phase": "layers", "tasks": {}})
        secret_record = outside / "receipt.json"
        secret_record.write_text('{"quality":"do-not-read"}', encoding="utf-8")
        try:
            (self.job / "receipt.json").symlink_to(secret_record)
            alias = self.root / "alias"
            alias.symlink_to(self.job, target_is_directory=True)
        except OSError as error:
            self.skipTest("Local symlink permission unavailable: " + type(error).__name__)
        report = self.report()
        self.assertEqual(report["records"]["receipt"]["status"], "unsafe")
        self.assertIsNone(report["workflow"]["quality"])
        result, output = self.run_inspector(alias / "report.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_default_is_markdown_and_unknowns_visible(self):
        result, output = self.run_inspector(self.root / "report.md", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = output.read_text(encoding="utf-8")
        self.assertIn("#", content)
        self.assertIn("未记录", content)
        self.assertIn("quality", content)

    def test_missing_job_refused_without_output(self):
        self.job.rmdir()
        result, output = self.run_inspector()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
