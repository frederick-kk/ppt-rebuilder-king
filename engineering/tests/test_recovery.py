"""Queue, integrity and local recovery contracts for the frozen controller.

Every returned image is synthetic and already on disk. Fault injection patches
only local extract/build/publish boundaries; it never authorizes generation.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "compat-runtime"))

from PIL import Image, ImageDraw
from newpath import controller as c
from newpath.storage import read_state, sha256

MATERIAL_MODE = 'native-transparent-materials/1'


class RecoveryContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="prk-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.png"
        Image.new("RGB", (160, 90), (240, 240, 240)).save(self.source)
        self.background = self.root / "background.png"
        Image.new("RGB", (160, 90), "white").save(self.background)
        self.foreground = self.root / "foreground.png"
        image = Image.new("RGBA", (160, 90), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((20, 20, 100, 70), fill="blue")
        image.save(self.foreground)
        self.work = self.root / "job"
        self.first = c.run(self.work, source=self.source)

    def state(self): return read_state(self.work)

    def task(self, role):
        return next(t for t in self.state()["tasks"].values() if t["role"] == role)

    def payload(self, role, file=None, **extra):
        task = self.task(role)
        return {"task_id": task["id"], "role": role, "attempt": task["attempt"],
                "file": str(file or (self.background if role == "background" else self.foreground)), **extra}

    def layers(self):
        c.run(self.work, data=self.payload("background"))
        result = c.run(self.work, data=self.payload("foreground"))
        self.assertEqual(result["kind"], "describe")

    def ready(self, board=False):
        self.layers()
        items = [{"id": "art", "kind": "art", "box": [240, 240, 600, 500], "description": "synthetic blue"}] if board else []
        state = self.state()
        c._submit(self.work, state, {"kind": "layout", "layout": {"items": items}})
        if board:
            result = c.run(self.work)
            self.assertEqual(result["kind"], "generate")
            state = self.state()
            task = next(t for t in state["tasks"].values() if t["role"] == "board")
            # Independent synthetic output; never adopt the input helper board.
            board = task['board']
            image = Image.new('RGBA', tuple(board['size']), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            for member in board['members']:
                x, y, width, height = member['slot']
                inset = max(10, int(min(width, height) * .2))
                draw.rectangle((x + inset, y + inset, x + width - inset - 1,
                                y + height - inset - 1), fill='blue')
            returned = self.root / (task['id'] + '-synthetic-output.png')
            image.save(returned)
            c._submit(self.work, state, {"task_id": task["id"], "role": "board", "attempt": 0, "file": str(returned)})
        return self.state()

    @staticmethod
    def minimal_build(layout, background, assets, output):
        # Intentionally minimal container accepted by the controller's existing
        # recovery gate. Full renderer validation is tested in the other file.
        with ZipFile(output, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<presentation/>")
            archive.writestr("ppt/slides/slide1.xml", "<slide/>")
        return {"warnings": [], "skipped": [], "synthetic_test_backend": True}

    def finish_stub(self):
        with mock.patch.object(c, "build_presentation", side_effect=self.minimal_build):
            return c.run(self.work)

    def assert_no_new_invocations(self, before):
        after = self.state()
        self.assertEqual(set(before["tasks"]), set(after["tasks"]))
        self.assertEqual({i: (t["attempt"], t["attempts"], t["issued"]) for i, t in before["tasks"].items()},
                         {i: (t["attempt"], t["attempts"], t["issued"]) for i, t in after["tasks"].items()})

    def test_first_dispatch_exact_contract_and_repeated_run_waits(self):
        self.assertEqual(self.first["kind"], "generate")
        self.assertEqual([t["role"] for t in self.first["jobs"]], ["background", "foreground"])
        for task in self.first["jobs"]:
            self.assertEqual(task["requested_outputs"], 1)
            self.assertEqual(task["transparent_background"], task["role"] == "foreground")
            self.assertEqual(task["size"], [1920, 1080])
            self.assertEqual(task["attempt"], 0)
            self.assertEqual(task["references"], [str(self.work / "input" / "working.png")])
        before = self.state()
        again = c.run(self.work)
        self.assertEqual(again["kind"], "await_results")
        self.assertFalse(again["generate_more"])
        self.assertEqual([t["id"] for t in again["pending_results"]], [t["id"] for t in self.first["jobs"]])
        self.assert_no_new_invocations(before)

    def test_foreground_requires_background_and_preserves_input(self):
        before = self.foreground.read_bytes()
        with self.assertRaisesRegex(ValueError, "background first"):
            c.run(self.work, data=self.payload("foreground"))
        self.assertEqual(self.foreground.read_bytes(), before)
        self.assertEqual(self.task("foreground")["status"], "waiting")
        self.assertEqual(self.task("foreground")["attempts"], [])

    def test_duplicate_record_is_idempotent_and_keeps_first_provenance(self):
        payload = self.payload("background", tool="synthetic", invocation_count=1)
        c.run(self.work, data=payload)
        before = self.task("background")
        c.run(self.work, data={**payload, "tool": "different", "invocation_count": 1})
        self.assertEqual(self.task("background"), before)
        self.assertEqual(len(before["attempts"]), 1)
        self.assertEqual(before["result"]["tool"], "synthetic")

    def test_first_registered_output_cannot_be_replaced_or_failed(self):
        c.run(self.work, data=self.payload("background"))
        other = self.root / "other.png"; Image.new("RGB", (160, 90), "blue").save(other)
        with self.assertRaisesRegex(ValueError, "immutable"):
            c.run(self.work, data=self.payload("background", other))
        with self.assertRaisesRegex(ValueError, "cannot be replaced"):
            c.run(self.work, data=self.payload("background", outcome="failed", reason="failure"))

    def test_wrong_role_attempt_and_bad_invocation_metadata_rejected(self):
        for change in [{"role": "foreground"}, {"attempt": 1}, {"invocation_count": 0},
                       {"invocation_count": True}, {"invocation_count": "1"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                c.run(self.work, data={**self.payload("background"), **change})
        self.assertEqual(self.task("background")["attempts"], [])

    def test_source_original_working_and_ruler_tamper_detection(self):
        for field in ["original", "working", "guide"]:
            with self.subTest(field=field):
                path = self.work / self.state()["source"][field]
                original = path.read_bytes(); path.write_bytes(original + b"tamper")
                with self.assertRaisesRegex(ValueError, "source file changed"): c.run(self.work)
                path.write_bytes(original)

    def test_brief_tamper_detection(self):
        path = self.work / self.task("background")["brief"]
        path.write_text(path.read_text(encoding="utf-8") + "changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "recorded task file changed"): c.run(self.work)

    def test_registered_raw_and_normalized_result_tamper_detection(self):
        c.run(self.work, data=self.payload("background"))
        result = self.task("background")["result"]
        for field in ["raw", "normalized"]:
            with self.subTest(field=field):
                path = self.work / result[field]
                original = path.read_bytes(); path.write_bytes(original + b"tamper")
                with self.assertRaisesRegex(ValueError, "recorded task file changed"): c.run(self.work)
                path.write_bytes(original)

    def test_layout_and_board_reference_tamper_detection(self):
        self.ready(board=True)
        layout_path = self.work / "layout.json"
        original = layout_path.read_bytes(); layout_path.write_text('{"items":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "page description changed"): c.run(self.work)
        layout_path.write_bytes(original)
        board = self.task("board")["board"]
        path = Path(board["image"]); path.write_bytes(path.read_bytes() + b"changed reference")
        with self.assertRaisesRegex(ValueError, "recorded task file changed"): c.run(self.work)

    def test_partially_recorded_three_plus_two_does_not_dispatch_next_batch(self):
        self.layers()
        boards = [{"id": "board-%s" % i, "signature": "signature-%s" % i,
                   "image": str(self.work / "layers" / "foreground.png"), "size": [1024, 1024],
                   "background_mode": MATERIAL_MODE, "key": None,
                   "members": [{"id": "art-%s" % i, "description": "synthetic", "slot": [24, 24, 976, 976],
                                "box": [0, 0, 100, 100], "target": [0, 0, 100, 100]}]} for i in range(5)]
        with mock.patch.object(c, "prepare_boards", return_value=boards):
            first = c.run(self.work, data={"kind": "layout", "layout": {"items": []}})
        self.assertEqual(first["kind"], "generate"); self.assertEqual(len(first["jobs"]), 3)
        for job in first["jobs"][:2]:
            result = c.run(self.work, data={"task_id": job["id"], "role": "board", "attempt": 0, "file": str(self.foreground)})
            self.assertEqual(result["kind"], "await_results")
            self.assertFalse(result["generate_more"])
        tasks = [t for t in self.state()["tasks"].values() if t["role"] == "board"]
        self.assertEqual([t["issued"] for t in tasks], [True, True, True, False, False])
        last = first["jobs"][2]
        second = c.run(self.work, data={"task_id": last["id"], "role": "board", "attempt": 0, "file": str(self.foreground)})
        self.assertEqual(second["kind"], "generate"); self.assertEqual(len(second["jobs"]), 2)
        self.assertEqual([j["id"] for j in second["jobs"]], [t["id"] for t in tasks[3:]])
        expected_refs = [str(self.work / "layers" / "foreground.png"), str(self.work / "input" / "working.png")]
        self.assertEqual(first["jobs"][0]["references"], expected_refs)

    def test_unreadable_transparent_and_key_only_pause_and_preserve_bytes(self):
        c.run(self.work, data=self.payload("background"))
        bad_files = []
        bad = self.root / "unreadable.png"; bad.write_bytes(b"not a PNG"); bad_files.append(bad)
        for name, color in [("transparent.png", (0, 0, 0, 0)), ("key-only.png", (255, 0, 255, 255))]:
            path = self.root / name; Image.new("RGBA", (20, 20), color).save(path); bad_files.append(path)
        for index, path in enumerate(bad_files):
            with self.subTest(path=path):
                if index: c.run(self.work, retry_task=self.task("foreground")["id"], retry_authorization="Synthetic user: retry failed foreground once.")
                with self.assertRaisesRegex(ValueError, "no automatic generation"):
                    c.run(self.work, data=self.payload("foreground", path))
                task = self.task("foreground")
                self.assertEqual(task["status"], "failed")
                observation = task["attempts"][-1]
                self.assertEqual(observation["outcome"], "unreadable")
                self.assertEqual((self.work / observation["raw"]).read_bytes(), path.read_bytes())
                paused = c.run(self.work)
                self.assertEqual(paused["kind"], "paused"); self.assertFalse(paused["generate_more"])
                self.assertEqual(self.task("background")["attempt"], 0)
        self.assertEqual(len(self.task("foreground")["attempts"]), 3)

    def test_explicit_failure_is_idempotent_retry_reopens_only_unfinished(self):
        c.run(self.work, data=self.payload("background"))
        failure = self.payload("foreground", outcome="failed", reason="synthetic service failure")
        c.run(self.work, data=failure); before = self.task("foreground")
        c.run(self.work, data=failure)
        self.assertEqual(self.task("foreground"), before)
        with self.assertRaisesRegex(ValueError, "unfinished"): c.run(self.work, retry_task=self.task("background")["id"])
        retry = c.run(self.work, retry_task=before["id"], retry_authorization="Synthetic user: retry this failed foreground once.")
        self.assertEqual(len(retry["jobs"]), 1)
        self.assertEqual(retry["jobs"][0]["attempt"], 1)
        self.assertEqual(self.task("background")["attempt"], 0)
        self.assertEqual(len(self.task("foreground")["attempts"]), 1)
        with self.assertRaisesRegex(ValueError, "attempt differs"):
            c.run(self.work, data={**self.payload("foreground"), "attempt": 0})

    def test_waiting_task_cannot_be_explicitly_retried(self):
        task = self.task("background")
        with self.assertRaisesRegex(ValueError, "failed"):
            c.run(self.work, retry_task=task["id"], retry_authorization="Synthetic user: retry once.")
        self.assertEqual(self.task("background")["attempt"], 0)
        self.assertEqual(self.task("foreground")["attempt"], 0)

    def test_unissued_board_cannot_be_registered_or_retried(self):
        self.layers()
        boards = [{"id": str(i), "signature": str(i), "image": str(self.work / "layers" / "foreground.png"), "size": [1024, 1024],
                   "background_mode": MATERIAL_MODE, "key": None, "members": []} for i in range(4)]
        with mock.patch.object(c, "prepare_boards", return_value=boards):
            c.run(self.work, data={"kind": "layout", "layout": {"items": []}})
        unissued = next(t for t in self.state()["tasks"].values() if not t["issued"])
        with self.assertRaisesRegex(ValueError, "not been dispatched"):
            c.run(self.work, data={"task_id": unissued["id"], "role": "board", "attempt": 0, "file": str(self.foreground)})
        with self.assertRaisesRegex(ValueError, "active batch"): c.run(self.work, retry_task=unissued["id"])

    def test_extract_interruption_reuses_registered_images_and_local_files(self):
        before = self.ready(board=True)
        real_extract = c.extract_assets
        def interrupted(*args):
            real_extract(*args)
            raise RuntimeError("injected after extraction before state save")
        with mock.patch.object(c, "extract_assets", side_effect=interrupted), mock.patch.object(c, "build_presentation") as builder:
            with self.assertRaisesRegex(RuntimeError, "injected"): c.run(self.work)
            builder.assert_not_called()
        self.assertTrue(list((self.work / "assets").rglob("*.png")))
        self.assertFalse(self.task("board").get("processed", False))
        result = self.finish_stub()
        self.assertEqual(result["kind"], "done")
        self.assertTrue(self.task("board")["processed"])
        self.assertEqual(len(self.state()["assets"]), 1)
        self.assert_no_new_invocations(before)

    def test_compile_interruption_before_container_retries_local_build_only(self):
        before = self.ready()
        with mock.patch.object(c, "build_presentation", side_effect=RuntimeError("injected compile failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"): c.run(self.work)
        self.assertFalse((self.work / "build" / "compiled.pptx").exists())
        result = self.finish_stub()
        self.assertEqual(result["kind"], "done")
        self.assert_no_new_invocations(before)

    def test_extracted_asset_tamper_is_detected_before_compile_retry(self):
        self.ready(board=True)
        with mock.patch.object(c, "build_presentation", side_effect=RuntimeError("injected compile failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"): c.run(self.work)
        self.assertTrue(self.task("board")["processed"])
        asset = Path(self.state()["assets"][0]["path"])
        asset.write_bytes(asset.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "extracted asset changed"): c.run(self.work)

    def test_real_compile_interruption_after_container_recovers_without_rerender(self):
        before = self.ready()
        real_build = c.build_presentation
        def interrupted(*args):
            real_build(*args)
            raise RuntimeError("injected after real container before report")
        with mock.patch.object(c, "build_presentation", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "injected"): c.run(self.work)
        compiled = self.work / "build" / "compiled.pptx"
        digest = sha256(compiled)
        with mock.patch.object(c, "build_presentation") as builder:
            result = c.run(self.work); builder.assert_not_called()
        report = json.loads((self.work / "build" / "compiled.report.json").read_text(encoding="utf-8"))
        self.assertTrue(report["recovered_existing_container"])
        self.assertEqual(report["object_inspection"], "not_recorded_before_interruption")
        self.assertEqual(result["sha256"], digest)
        self.assert_no_new_invocations(before)

    def test_publish_interruption_before_link_recovers_without_rerender(self):
        before = self.ready()
        with mock.patch.object(c, "build_presentation", side_effect=self.minimal_build), mock.patch.object(c.os, "link", side_effect=OSError("injected publish failure")):
            with self.assertRaisesRegex(OSError, "injected"): c.run(self.work)
        self.assertTrue((self.work / "output" / "presentation.tmp").exists())
        with mock.patch.object(c, "build_presentation") as builder:
            result = c.run(self.work); builder.assert_not_called()
        self.assertEqual(result["kind"], "done")
        self.assertFalse((self.work / "output" / "presentation.tmp").exists())
        self.assert_no_new_invocations(before)

    def test_publish_interruption_after_link_accepts_same_output(self):
        before = self.ready()
        real_link = c.os.link
        def interrupted(source, destination):
            real_link(source, destination)
            raise OSError("injected after publish link")
        with mock.patch.object(c, "build_presentation", side_effect=self.minimal_build), mock.patch.object(c.os, "link", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "injected"): c.run(self.work)
        final = self.work / "output" / "PPT Rebuilder King.pptx"
        before_hash = sha256(final)
        with mock.patch.object(c, "build_presentation") as builder:
            result = c.run(self.work); builder.assert_not_called()
        self.assertEqual(result["sha256"], before_hash)
        self.assert_no_new_invocations(before)
        # Current recovery leaves the old same-content temporary hard link.
        self.assertTrue((self.work / "output" / "presentation.tmp").exists())

    def test_unrelated_final_output_is_never_overwritten(self):
        before = self.ready()
        output = self.work / "output"; output.mkdir()
        final = output / "PPT Rebuilder King.pptx"; final.write_bytes(b"unrelated user file")
        with mock.patch.object(c, "build_presentation", side_effect=self.minimal_build):
            with self.assertRaisesRegex(ValueError, "unrelated output"): c.run(self.work)
        self.assertEqual(final.read_bytes(), b"unrelated user file")
        self.assertNotEqual(self.state()["phase"], "done")
        self.assert_no_new_invocations(before)

    def test_compiled_hash_mismatch_and_incomplete_container_fail_closed(self):
        self.ready()
        build = self.work / "build"; build.mkdir()
        compiled = build / "compiled.pptx"
        self.minimal_build(None, None, None, compiled)
        (build / "compiled.report.json").write_text('{"compiled_sha256":"wrong"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "compiled presentation changed"): c.run(self.work)
        (build / "compiled.report.json").unlink()
        with ZipFile(compiled, "w") as archive: archive.writestr("not-pptx.xml", "<x/>")
        with self.assertRaisesRegex(ValueError, "incomplete"): c.run(self.work)

    def test_done_terminal_only_checks_final_hash_and_does_not_reopen(self):
        self.ready(); done = self.finish_stub(); before = self.state()
        self.assertEqual(done["quality"], "not_evaluated")
        self.assertIsNone(done["user_end_to_end_seconds"])
        self.assertIsNone(done["within_600_seconds"])
        self.assertIsNone(done["caller_reported_invocations"])
        brief = self.work / self.task("background")["brief"]; brief.write_text("tampered after done", encoding="utf-8")
        self.assertEqual(c.run(self.work, retry_task=self.task("foreground")["id"]), done)
        self.assertEqual(self.state(), before)
        final = Path(done["pptx"]); final.write_bytes(final.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "not reopened"): c.run(self.work)
        final.unlink()
        with self.assertRaisesRegex(ValueError, "not reopened"): c.run(self.work)
        self.assertEqual(self.state()["phase"], "done")


if __name__ == "__main__":
    unittest.main()
