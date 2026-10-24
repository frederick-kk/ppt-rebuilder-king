import json
from pathlib import Path
import shutil
import tempfile
import unittest

from replay_support import make_job, RUNTIME


class OfflineReplayIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from engineering.capture_fixture import capture_fixture
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        job, raw = make_job(cls.root / 'recorded')
        cls.fixture = cls.root / 'fixture'
        capture_fixture(job, cls.fixture, raw_layout=raw, origin='synthetic')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_01_baseline_against_itself_from_empty_jobs(self):
        from engineering.replay_compare import compare
        fixture = json.loads((self.fixture / 'fixture.json').read_text(encoding='utf-8'))
        materials = [task for task in fixture['tasks'] if task['role'] in {'foreground', 'board'}]
        self.assertTrue(materials)
        self.assertTrue(all(task.get('background_mode') == 'native-transparent-materials/1' for task in materials))
        self.assertTrue(all(task['transparent_background'] for task in materials))
        self.assertTrue(all(task['key'] is None for task in materials if task['role'] == 'board'))
        report = compare(RUNTIME, RUNTIME, self.fixture, self.root / 'self-compare')
        self.assertTrue(report['passed'], json.dumps(report, ensure_ascii=False)[:3000])
        self.assertEqual(report['replay_image_requests_issued'], 0)
        self.assertIsNone(report['real_generation_calls'])
        run = json.loads((self.root / 'self-compare/baseline.json').read_text(encoding='utf-8'))
        self.assertTrue(run['fresh_work_directory'])
        self.assertTrue(run['snapshot']['render_manifests'])
        self.assertTrue(run['snapshot']['requests'])

    def candidate(self, name, relative, before, after):
        candidate = self.root / name
        shutil.copytree(RUNTIME, candidate)
        file = candidate / relative
        data = file.read_text(encoding='utf-8')
        self.assertIn(before, data)
        file.write_text(data.replace(before, after, 1), encoding='utf-8')
        return candidate

    def test_02_changed_prompt_must_fail_closed(self):
        from engineering.replay_compare import compare
        candidate = self.candidate('prompt-runtime', 'newpath/prompts.py', 'Background task: Keep', 'Background task: Discard')
        report = compare(RUNTIME, candidate, self.fixture, self.root / 'prompt-compare')
        self.assertFalse(report['passed'])
        self.assertIn('No captured result', report['errors']['candidate'])

    def test_03_deleted_native_character_is_detected(self):
        from engineering.replay_compare import compare
        before = 'item = deepcopy(original)'
        after = before + '\n        if item["kind"] == "text": item["text"] = item["text"][:-1]'
        candidate = self.candidate('text-runtime', 'newpath/renderer.py', before, after)
        report = compare(RUNTIME, candidate, self.fixture, self.root / 'text-compare')
        self.assertTrue(report['replay_complete'])
        self.assertFalse(report['passed'])
        self.assertTrue(any('render_manifests' in diff['path'] or 'slide1.xml' in diff['path'] for diff in report['differences']))

    def test_04_slot_change_is_detected_before_service_use(self):
        from engineering.replay_compare import compare
        before = "placements=plan['placements']"
        after = before + "\n        placements[0]['slot'][0] += 1"
        candidate = self.candidate('slot-runtime', 'newpath/artwork.py', before, after)
        report = compare(RUNTIME, candidate, self.fixture, self.root / 'slot-compare')
        self.assertFalse(report['passed'])
        self.assertFalse(report['replay_complete'])
        self.assertEqual(report['replay_image_requests_issued'], 0)

    def test_05_replaced_fixture_image_is_rejected(self):
        from engineering.replay_compare import verify_fixture
        fixture = self.root / 'tampered-fixture'
        shutil.copytree(self.fixture, fixture)
        info = json.loads((fixture / 'fixture.json').read_text(encoding='utf-8'))
        path = fixture / next(task['result'] for task in info['tasks'] if task.get('result'))
        path.write_bytes(b'wrong PNG')
        with self.assertRaisesRegex(ValueError, 'Fixture content changed'):
            verify_fixture(fixture)

    def test_06_recorded_failed_background_and_explicit_retry_replay(self):
        from engineering.capture_fixture import capture_fixture
        from engineering.replay_compare import compare
        for failure in ('failed', 'unreadable', 'failed_same_attempt', 'unreadable_same_attempt'):
            with self.subTest(failure=failure):
                job, raw = make_job(self.root / ('recorded-' + failure), failure=failure)
                fixture = self.root / ('fixture-' + failure)
                capture_fixture(job, fixture, raw_layout=raw, origin='synthetic')
                report = compare(RUNTIME, RUNTIME, fixture, self.root / ('compare-' + failure))
                self.assertTrue(report['passed'], str(report)[:2500])

    def test_07_worker_cannot_overwrite_a_source_or_existing_report(self):
        from engineering.replay_compare import worker
        output = self.root / 'existing-report.json'; output.write_text('keep', encoding='utf-8')
        with self.assertRaises(ValueError):
            worker(RUNTIME, self.fixture, self.root / 'new-work', output)
        self.assertEqual(output.read_text(encoding='utf-8'), 'keep')
        with self.assertRaises(ValueError):
            worker(RUNTIME, self.fixture, self.fixture / 'new-work', self.root / 'new-report.json')
        with self.assertRaises(ValueError):
            worker(RUNTIME, self.fixture, self.root / 'safe-work', RUNTIME / 'new-report.json')

    def test_08_output_cannot_modify_original_captured_job(self):
        from engineering.replay_compare import compare
        job = self.root / 'recorded/job'
        with self.assertRaises(ValueError):
            compare(RUNTIME, RUNTIME, self.fixture, job / 'new-output')

    def test_09_two_import_failures_never_claim_equivalence(self):
        from engineering.replay_compare import compare
        candidate = self.candidate('broken-runtime', 'newpath/controller.py',
                                   'import copy', "raise RuntimeError('synthetic runtime import failure')\nimport copy")
        report = compare(candidate, candidate, self.fixture, self.root / 'broken-compare')
        self.assertFalse(report['passed'])
        self.assertFalse(report['equivalent'])
        self.assertFalse(report['replay_complete'])
        self.assertTrue(report['partial_snapshots_equal'])

    def test_10_missing_retry_authorization_is_not_invented_for_new_jobs(self):
        from engineering.capture_fixture import capture_fixture
        from engineering.replay_compare import compare
        job,raw=make_job(self.root/'recorded-missing-authorization',failure='failed')
        fixture=self.root/'fixture-missing-authorization'
        record=capture_fixture(job,fixture,raw_layout=raw,origin='synthetic')
        self.assertTrue(any(task.get('retry_authorization') for task in record['tasks']))
        for task in record['tasks']:task.pop('retry_authorization',None)
        (fixture/'fixture.json').write_text(json.dumps(record),encoding='utf-8')
        report=compare(RUNTIME,RUNTIME,fixture,self.root/'compare-missing-authorization')
        self.assertFalse(report['passed']);self.assertFalse(report['replay_complete'])
        self.assertIn('no recorded user authorization',report['errors']['baseline'])

    def test_11_material_mode_mismatch_is_rejected_before_replay_acceptance(self):
        from engineering.replay_compare import compare
        fixture = self.root / 'fixture-wrong-material-mode'
        shutil.copytree(self.fixture, fixture)
        record = json.loads((fixture / 'fixture.json').read_text(encoding='utf-8'))
        foreground = next(task for task in record['tasks'] if task['role'] == 'foreground')
        foreground['background_mode'] = 'synthetic-wrong-material-mode'
        (fixture / 'fixture.json').write_text(json.dumps(record), encoding='utf-8')
        report = compare(RUNTIME, RUNTIME, fixture, self.root / 'compare-wrong-material-mode')
        self.assertFalse(report['passed'])
        self.assertFalse(report['replay_complete'])
        self.assertIn('background_mode', report['errors']['baseline'])


if __name__ == '__main__': unittest.main()
