"""Capture tests use invented jobs, never paid model outputs or user originals."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

try:
    from engineering.capture_fixture import capture_fixture
    from engineering.snapshot_baseline import snapshot_baseline
except ImportError:
    capture_fixture = snapshot_baseline = None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, bytes) else json.dumps(content).encode()
    path.write_bytes(data)
    return digest(data)


class FixtureCaptureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(capture_fixture, 'capture_fixture must implement stable read-only capture')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = self.root / 'source-job'
        self.job.mkdir()
        src = {'original': 'input/original.jpeg', 'working': 'input/working.png',
               'guide': 'input/ruler.png', 'input_path': str(self.root / 'not-available.jpeg')}
        for field, key in [('original', 'byte_sha256'), ('working', 'working_sha256'), ('guide', 'guide_sha256')]:
            src[key] = put(self.job, src[field], field.encode())
        brief_hash = put(self.job, 'briefs/bg.txt', b'Keep the complete background.')
        reference_hash = put(self.job, 'boards/reference.png', b'reference')
        old_hash = put(self.job, 'returned/bg-0.bin', b'unreadable-original')
        result_hash = put(self.job, 'returned/bg-1.png', b'actual-raw-return')
        normalized_hash = put(self.job, 'layers/background.png', b'normalized')
        task = {'id': 'bg', 'role': 'background', 'status': 'recorded', 'issued': True,
                'attempt': 1, 'brief': 'briefs/bg.txt', 'brief_sha256': brief_hash,
                'references': {'boards/reference.png': reference_hash, 'input/working.png': src['working_sha256']},
                'attempts': [{'attempt': 0, 'outcome': 'unreadable', 'raw': 'returned/bg-0.bin', 'sha256': old_hash,
                              'reason': 'decoder rejected'},
                             {'attempt': 1, 'outcome': 'recorded', 'raw': 'returned/bg-1.png', 'sha256': result_hash}],
                'result': {'raw': 'returned/bg-1.png', 'sha256': result_hash,
                           'normalized': 'layers/background.png', 'normalized_sha256': normalized_hash}}
        fg = {'id': 'fg', 'role': 'foreground', 'status': 'failed', 'issued': True,
              'attempt': 0, 'brief': 'briefs/fg.txt', 'brief_sha256': put(self.job, 'briefs/fg.txt', b'Foreground.'),
              'references': {'input/working.png': src['working_sha256']},
              'attempts': [{'attempt': 0, 'outcome': 'failed', 'reason': 'explicit failure'}], 'failure': 'explicit failure'}
        self.state = {'format': 'newpath.job/1', 'phase': 'layers', 'source': src,
                      'tasks': {'bg': task, 'fg': fg}, 'layout': None, 'assets': [],
                      'events': [{'event': 'explicit_retry'}]}
        self.save()

    def save(self):
        put(self.job, 'state.json', self.state)

    def capture(self, **kw):
        out = kw.pop('output', self.root / 'captured')
        capture_fixture(self.job, out, **kw)
        return out, json.loads((out / 'fixture.json').read_text(encoding='utf-8'))

    def test_preserves_entire_job_and_ordered_attempts_without_done_or_pptx(self):
        before = {p.relative_to(self.job).as_posix(): p.read_bytes() for p in self.job.rglob('*') if p.is_file()}
        out, value = self.capture(origin='synthetic')
        self.assertEqual(value['format'], 'ppt-rebuilder.fixture/1')
        self.assertEqual(value['origin'], 'synthetic')
        self.assertEqual(value['source'], 'job/input/original.jpeg')
        self.assertIsNone(value['layout'])
        self.assertEqual([(t['id'], t['attempt'], t['outcome']) for t in value['tasks']],
                         [('bg', 0, 'unreadable'), ('bg', 1, 'recorded'), ('fg', 0, 'failed')])
        self.assertEqual([r['path'] for r in value['tasks'][0]['references']],
                         ['job/boards/reference.png', 'job/input/working.png'])
        self.assertEqual(value['tasks'][0]['result'], 'job/returned/bg-0.bin')
        self.assertTrue(all(not task['transparent_background'] for task in value['tasks']))
        self.assertTrue(all('background_mode' not in task for task in value['tasks']))
        for name, content in before.items():
            self.assertEqual((out / 'job' / name).read_bytes(), content)
            self.assertEqual((self.job / name).read_bytes(), content)
        self.assertEqual(len(value['files']), len(before))

    def test_native_material_and_wordart_contract_metadata_are_preserved(self):
        material_mode = 'native-transparent-materials/1'
        self.state['material_background_mode'] = material_mode
        foreground = self.state['tasks']['fg']
        foreground['background_mode'] = material_mode
        board = {**foreground, 'id': 'board', 'role': 'board',
                 'board': {'size': [2048, 1024], 'key': None,
                           'background_mode': material_mode, 'members': []}}
        title = {**foreground, 'id': 'title', 'role': 'wordart',
                 'background_mode': 'standalone-native-wordart/2'}
        self.state['tasks'].update(board=board, title=title)
        self.save()
        _, value = self.capture(origin='synthetic')
        captured = {task['id']: task for task in value['tasks']}
        for identity, mode, size in [('fg', material_mode, [1920, 1080]),
                                     ('board', material_mode, [2048, 1024]),
                                     ('title', 'standalone-native-wordart/2', [1536, 1024])]:
            with self.subTest(role=identity):
                self.assertTrue(captured[identity]['transparent_background'])
                self.assertEqual(captured[identity]['background_mode'], mode)
                self.assertEqual(captured[identity]['size'], size)
        self.assertIsNone(captured['board']['key'])
        self.assertFalse(captured['bg']['transparent_background'])
        self.assertNotIn('background_mode', captured['bg'])

    def test_legacy_board_keeps_keyed_contract_and_still_requires_a_key(self):
        board = {**self.state['tasks']['fg'], 'id': 'board', 'role': 'board',
                 'board': {'size': [1024, 1024], 'key': 'magenta', 'members': []}}
        self.state['tasks']['board'] = board
        self.save()
        _, value = self.capture(origin='synthetic')
        captured = next(task for task in value['tasks'] if task['id'] == 'board')
        self.assertEqual(captured['size'], [1024, 1024])
        self.assertEqual(captured['key'], 'magenta')
        self.assertFalse(captured['transparent_background'])
        self.assertNotIn('background_mode', captured)
        board['board']['key'] = None
        self.save()
        with self.assertRaisesRegex(ValueError, 'size/key'):
            self.capture(output=self.root / 'missing-legacy-key')

    def test_unknown_provenance_is_not_invented(self):
        _, value = self.capture()
        for task in value['tasks']:
            for key in ('tool', 'invocation_count', 'tool_call_id', 'tool_output_id', 'executed_prompt'):
                self.assertIsNone(task[key])
            self.assertEqual(task['parameter_provenance'], 'derived_from_current_public_task_contract')
        self.assertEqual(value['human_review']['status'], 'unknown')
        self.assertIn('external_instructions_not_supplied', value['missing'])
        self.assertIn('original_layout_not_supplied', value['missing'])
        self.assertIn('model_runtime_unknown', value['missing'])

    def test_rejects_recorded_observation_conflicting_with_immutable_result(self):
        alternate = put(self.job, 'returned/other.png', b'different-valid-file')
        current = self.state['tasks']['bg']['attempts'][-1]
        current.update(raw='returned/other.png', sha256=alternate)
        self.save()
        with self.assertRaises(ValueError):
            self.capture()
        self.assertFalse((self.root / 'captured' / 'fixture.json').exists())

    def test_captures_retry_authorization_only_for_the_recorded_attempt(self):
        self.state['tasks']['bg']['retry_authorizations']=[{
            'attempt':1,'authorization':'Synthetic user: retry this failed background once.',
            'provenance':'caller_reported_user_request_not_verified'}]
        self.save()
        _,value=self.capture()
        records=[task for task in value['tasks'] if task['id']=='bg']
        self.assertNotIn('retry_authorization',records[0])
        self.assertEqual(records[1]['retry_authorization'],'Synthetic user: retry this failed background once.')
        self.assertTrue(all('retry_authorization' not in task for task in value['tasks'] if task['id']=='fg'))

    def test_rejects_null_or_invalid_recorded_reference_hash(self):
        for index, invalid in enumerate((None, '', 'not-a-sha256', 0)):
            self.state['tasks']['fg']['references']['input/working.png'] = invalid
            self.save()
            with self.subTest(hash=invalid), self.assertRaises(ValueError):
                self.capture(output=self.root / ('bad-hash-' + str(index)))

    def test_rejects_unknown_phase_even_when_a_task_failed(self):
        self.state['phase'] = 'invented-phase'
        self.save()
        with self.assertRaises(ValueError):
            self.capture()

    def test_preserves_same_attempt_failed_and_unreadable_then_corrected_result(self):
        task = self.state['tasks']['bg']
        task['attempts'][0]['attempt'] = 1
        task['attempts'].insert(0, {'attempt': 1, 'outcome': 'failed', 'reason': 'original service failure'})
        self.save()
        _, value = self.capture()
        bg = [item for item in value['tasks'] if item['id'] == 'bg']
        self.assertEqual([(item['attempt'], item['outcome']) for item in bg],
                         [(1, 'failed'), (1, 'unreadable'), (1, 'recorded')])
        self.assertEqual([item['result'] for item in bg],
                         [None, 'job/returned/bg-0.bin', 'job/returned/bg-1.png'])

    def test_actual_controller_same_attempt_failure_correction_remains_capturable(self):
        import sys
        sys.dont_write_bytecode = True
        runtime = Path(__file__).resolve().parents[2] / 'compat-runtime'
        if str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        from PIL import Image
        from newpath.controller import run
        source = self.root / 'synthetic-white.png'
        Image.new('RGB', (16, 9), 'white').save(source)
        bad = self.root / 'unreadable.bin'
        bad.write_bytes(b'not-an-image')
        for failure in ('failed', 'unreadable'):
            with self.subTest(failure=failure):
                job = self.root / ('actual-' + failure)
                dispatched = run(job, source=source)['jobs']
                bg = next(task for task in dispatched if task['role'] == 'background')
                fg = next(task for task in dispatched if task['role'] == 'foreground')
                base = {'task_id': bg['id'], 'role': 'background', 'attempt': 0}
                if failure == 'failed':
                    run(job, data={**base, 'outcome': 'failed', 'reason': 'synthetic failure'})
                else:
                    with self.assertRaises(ValueError):
                        run(job, data={**base, 'file': str(bad)})
                run(job, data={**base, 'file': str(source)})
                run(job, data={'task_id': fg['id'], 'role': 'foreground', 'attempt': 0,
                               'outcome': 'failed', 'reason': 'leave the synthetic job paused'})
                value = capture_fixture(job, self.root / ('actual-fixture-' + failure), origin='synthetic')
                bg_records = [item for item in value['tasks'] if item['id'] == bg['id']]
                self.assertEqual([(item['attempt'], item['outcome']) for item in bg_records],
                                 [(0, failure), (0, 'recorded')])

    def test_rejects_two_conflicting_recorded_observations_for_one_attempt(self):
        task = self.state['tasks']['bg']
        alternate_hash = put(self.job, 'returned/contradictory.png', b'different-frozen-result')
        task['attempts'].append({'attempt': 1, 'outcome': 'recorded',
                                 'raw': 'returned/contradictory.png', 'sha256': alternate_hash})
        self.save()
        with self.assertRaises(ValueError):
            self.capture()

    def test_explicit_inputs_preserve_original_layout_and_review(self):
        layout = self.root / 'submitted.json'
        layout.write_bytes(b'{"items":[]}\n')
        instruction = self.root / 'external.txt'
        instruction.write_bytes(b'Actual external instruction.')
        _, value = self.capture(raw_layout=layout, instructions=[instruction], human_review={'status': 'not_reviewed'})
        self.assertNotIn('original_layout_not_supplied', value['missing'])
        self.assertEqual(value['human_review']['status'], 'not_reviewed')
        self.assertEqual(value['original_layout']['sha256'], digest(layout.read_bytes()))
        self.assertEqual(value['instructions'][0]['sha256'], digest(instruction.read_bytes()))

    def test_rejects_output_overlap_and_existing_output(self):
        for out in (self.job / 'fixture', self.job, self.root):
            with self.subTest(out=out), self.assertRaises(ValueError):
                capture_fixture(self.job, out)
        self.assertFalse((self.job / 'fixture').exists())

    def test_rejects_active_or_unrecognized_phase(self):
        self.state['tasks']['fg']['status'] = 'waiting'
        self.state['tasks']['fg']['attempts'] = []
        self.save()
        with self.assertRaises(ValueError):
            self.capture()
        self.assertFalse((self.root / 'captured' / 'fixture.json').exists())

    def test_rejects_changed_hash_without_publishing_fixture(self):
        (self.job / 'returned/bg-0.bin').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            self.capture()
        self.assertFalse((self.root / 'captured' / 'fixture.json').exists())

    def test_rejects_missing_referenced_original_without_using_external_input(self):
        (self.job / 'input/original.jpeg').unlink()
        with self.assertRaises(ValueError):
            self.capture()

    def test_rejects_temporary_writes(self):
        put(self.job, 'state.json.write.tmp', b'in flight')
        with self.assertRaises(ValueError):
            self.capture()

    def test_rejects_symlink_traversal(self):
        try:
            (self.job / 'linked').symlink_to(self.root, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('symlink creation unavailable for this account')
            import _winapi
            _winapi.CreateJunction(str(self.root), str(self.job / 'linked'))
        with self.assertRaises(ValueError):
            self.capture()

    def test_existing_unheld_lock_is_captured_without_changing_bytes(self):
        put(self.job, '.lock', b'1')
        _, value = self.capture()
        self.assertEqual((self.job / '.lock').read_bytes(), b'1')
        self.assertEqual(value['capture_consistency']['lock'], 'existing_lock_read_only_shared_lock')

    def test_held_controller_lock_refuses_capture_without_changing_source(self):
        put(self.job, '.lock', b'1')
        with (self.job / '.lock').open('r+b') as stream:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(ValueError):
                    self.capture()
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        self.assertEqual((self.job / '.lock').read_bytes(), b'1')
        self.assertFalse((self.root / 'captured').exists())

    def test_rejects_done_state_with_failed_task(self):
        pptx_hash = put(self.job, 'output/result.pptx', b'opaque-container')
        self.state['phase'] = 'done'
        self.state['done'] = {'pptx': str(self.job / 'output/result.pptx'), 'sha256': pptx_hash}
        self.save()
        with self.assertRaises(ValueError):
            self.capture()

    def test_rejects_missing_recorded_result_and_missing_normalized_hash(self):
        for problem in ('missing_result', 'missing_normalized_hash'):
            task = self.state['tasks']['bg']
            if problem == 'missing_result':
                saved = task.pop('result')
            else:
                task['result'] = saved
                task['result'].pop('normalized_sha256')
            self.save()
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                self.capture(output=self.root / problem)

    def test_rejects_missing_brief_instead_of_raising_unhandled_type_error(self):
        self.state['tasks']['fg'].pop('brief')
        self.save()
        with self.assertRaises(ValueError):
            self.capture()

    def test_rejects_absolute_or_traversing_relative_references(self):
        for path in ('../escape.png', str(self.root / 'outside.png')):
            self.state['tasks']['fg']['references'] = {path: '0' * 64}
            self.save()
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.capture()

    def test_detects_mutation_during_copy_and_leaves_no_valid_fixture(self):
        import engineering.capture_fixture as module
        original_copy = module.shutil.copyfile
        def mutate(src, dst, *args, **kwargs):
            result = original_copy(src, dst, *args, **kwargs)
            if Path(src).name == 'bg-0.bin':
                (self.job / 'input/working.png').write_bytes(b'concurrent change')
            return result
        with mock.patch.object(module.shutil, 'copyfile', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.capture()
        self.assertFalse((self.root / 'captured' / 'fixture.json').exists())

    def test_rejects_inconsistent_normalized_layout(self):
        self.state['layout'] = {'items': []}
        put(self.job, 'layout.json', {'items': [{'id': 'changed'}]})
        self.save()
        with self.assertRaises(ValueError):
            self.capture()


class BaselineSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(snapshot_baseline, 'snapshot_baseline must implement verified archive fingerprinting')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'original.zip'

    def archive_with(self, bad_hash=False, extra=None):
        files = {'SKILL.md': b'original skill', 'references/scene.md': b'original scene',
                 'package.json': b'{"dependencies":{"example":"1.2.3"}}',
                 'node_modules/example/package.json': b'{"name":"example","version":"1.2.3"}'}
        manifest = {'files': [{'path': name, 'sha256': '0' * 64 if bad_hash else digest(data), 'size': len(data)}
                              for name, data in files.items()]}
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)
            z.writestr('MANIFEST.json', json.dumps(manifest))
            if extra:
                z.writestr(*extra)

    def snapshot(self, **kw):
        out = self.root / 'baseline'
        snapshot_baseline(self.archive, out, **kw)
        return out

    def test_records_verified_archive_files_instructions_and_current_environment(self):
        self.archive_with()
        original = self.archive.read_bytes()
        out = self.snapshot()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual((out / 'archive.sha256').read_text().split()[0], digest(original))
        files = json.loads((out / 'files.json').read_text())
        self.assertTrue(files['manifest_verification']['passed'])
        self.assertEqual(len(files['files']), 5)
        env = json.loads((out / 'environment.json').read_text())
        self.assertEqual(env['scope'], 'current_capture_machine')
        self.assertEqual(env['bundled_node_packages']['example'], '1.2.3')
        self.assertIsNone(env['external_model_runtime'])
        fingerprints = json.loads((out / 'instruction-fingerprints.json').read_text())
        self.assertEqual([f['path'] for f in fingerprints['archive_instructions']], ['SKILL.md', 'references/scene.md'])
        self.assertEqual(fingerprints['external_instructions'], [])
        self.assertIn('unknown', (out / 'limitations.md').read_text().lower())
        self.assertFalse((out / 'archive-original.zip').exists())

    def test_rejects_manifest_tampering_before_creating_record(self):
        self.archive_with(bad_hash=True)
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse((self.root / 'baseline').exists())

    def test_rejects_archive_traversal_and_unlisted_members(self):
        for name in ('../escape.txt', '/absolute.txt', 'C:/absolute.txt', 'unlisted.txt'):
            self.archive_with(extra=(name, b'bad'))
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.snapshot()

    def test_supplied_successful_environment_has_explicit_provenance(self):
        self.archive_with()
        out = self.snapshot(successful_environment={'python': 'claimed-version'})
        env = json.loads((out / 'environment.json').read_text())
        self.assertEqual(env['successful_environment']['provenance'], 'operator_supplied_not_independently_verified')
        self.assertEqual(env['successful_environment']['value']['python'], 'claimed-version')


if __name__ == '__main__':
    unittest.main()
