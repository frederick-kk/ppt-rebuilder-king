"""Exercise the shipped CLI with synthetic callbacks and no image-provider calls."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

from test_result_role_binding import ReviewedOutputFixture


PACKAGE = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HostRegistrationSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = PACKAGE / 'compat-runtime'
        if not shutil.which('node') and not shutil.which('nodejs'):
            raise unittest.SkipTest('An existing Node runtime is needed for CLI smoke tests.')
        if not (runtime / 'node_modules' / 'pptxgenjs' / 'package.json').is_file():
            raise unittest.SkipTest('The bundled runtime dependencies are needed for CLI smoke tests.')
        cls.temporary = tempfile.TemporaryDirectory(prefix='prk-cli-smoke-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.runtime = cls.root / 'runtime'
        shutil.copytree(runtime, cls.runtime, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

        # This test snapshot has its own manifest. The production snapshot and
        # its manifest are never rewritten by these tests.
        constants = {}
        syntax = ast.parse((cls.runtime / 'newpath' / 'version.py').read_text('utf-8'))
        for node in syntax.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in {'PACKAGE_ID', 'ARCHIVE_NAME'}:
                    constants[target.id] = ast.literal_eval(node.value)
        rows = []
        for path in sorted(cls.runtime.rglob('*')):
            if path.is_file() and path.name != 'MANIFEST.json':
                rows.append({'path': path.relative_to(cls.runtime).as_posix(),
                             'size': path.stat().st_size, 'sha256': digest(path)})
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        manifest = {'format': 'newpath.package-manifest/1', 'package_id': constants['PACKAGE_ID'],
                    'archive_name': constants['ARCHIVE_NAME'], 'files': rows,
                    'source_file_count': len(rows),
                    'source_snapshot_sha256': hashlib.sha256(canonical.encode('utf-8')).hexdigest()}
        (cls.runtime / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
        cls.package_id = constants['PACKAGE_ID']

    def setUp(self):
        self.case = self.root / self._testMethodName
        self.case.mkdir()
        self.work = self.case / 'job'
        self.source = self.case / 'source.png'
        Image.new('RGB', (160, 90), 'white').save(self.source)
        self.background = self.case / 'background.png'
        Image.new('RGB', (1920, 1080), 'navy').save(self.background)
        self.foreground = self.case / 'foreground.png'
        image = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((200, 200, 600, 600), fill='blue')
        image.save(self.foreground)
        self.sequence = 0

    def invoke(self, payload=None, source=None, expected_code=0):
        self.sequence += 1
        args = [sys.executable, '-m', 'newpath', '--work', str(self.work)]
        if source is not None:
            args += ['--source', str(source)]
        if payload is not None:
            submission = self.case / f'submission-{self.sequence}.json'
            submission.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            args += ['--input', str(submission)]
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.run(args, cwd=self.runtime, capture_output=True, env=environment, timeout=30)
        response = json.loads(process.stdout.decode('utf-8'))
        self.assertEqual(process.returncode, expected_code, response)
        return response

    def start(self):
        response = self.invoke(source=self.source)
        self.assertEqual(response['kind'], 'generate')
        return {task['role']: task for task in response['jobs']}

    def begin(self, task, actual_arguments=None):
        submission = copy.deepcopy(task['begin_template'])
        submission['request']['tool'] = 'synthetic-offline-fixture'
        if actual_arguments is not None:
            submission['request']['tool_arguments'] = actual_arguments
        permit = self.invoke(payload=submission)
        self.assertEqual(permit['kind'], 'invoke_once')
        return permit

    def result(self, permit, path):
        role = permit['role']
        review = {'role': role, 'output_sha256': digest(path), 'looked_at_return': True,
                  'matches_task': True,
                  'observed': 'Synthetic fixture is solid navy without foreground text.' if role == 'background'
                              else 'Synthetic fixture is a blue rectangle surrounded by transparent pixels.'}
        review['foreground_text_absent' if role == 'background' else 'page_elements_match'] = True
        return {**permit['result_template'], 'file': str(path), 'invocation_count': 1,
                'output_count': 1, 'role_review': review}

    def state(self):
        return json.loads((self.work / 'state.json').read_text('utf-8'))

    def test_new_cli_job_freezes_current_identity_without_issuing_image_calls(self):
        tasks = self.start()
        state = self.state()
        self.assertEqual(state['runtime_identity']['package_id'], self.package_id)
        self.assertEqual(state['recovery_policy'], 'bounded-recovery/1')
        self.assertEqual({task['execution']['state'] for task in state['tasks'].values()}, {'not_started'})
        self.assertEqual(set(tasks), {'background', 'foreground'})
        self.assertFalse((self.work / 'requests').exists())

    def test_unknown_provider_output_identifier_does_not_collide_across_roles(self):
        tasks = self.start()
        for role, path in [('background', self.background), ('foreground', self.foreground)]:
            permit = self.begin(tasks[role])
            payload = self.result(permit, path)
            payload['tool_output_id'] = 'unknown'
            payload['executed_prompt'] = None
            response = self.invoke(payload=payload)
        self.assertEqual(response['kind'], 'describe')
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_empty_provider_output_identifier_remains_unknown(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = self.result(permit, self.background)
        payload['tool_output_id'] = ''
        self.assertEqual(self.invoke(payload=payload)['kind'], 'generate')
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_absent_provider_prompt_ids_times_and_dimensions_do_not_block(self):
        tasks = self.start()
        for role, path in [('background', self.background), ('foreground', self.foreground)]:
            permit = self.begin(tasks[role])
            response = self.invoke(payload=self.result(permit, path))
        self.assertEqual(response['kind'], 'describe')
        statuses = [task['prompt_audits'][0]['status'] for task in self.state()['tasks'].values()]
        self.assertEqual(statuses, ['unknown', 'unknown'])

    def test_one_returned_output_list_is_a_single_existing_result(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = self.result(permit, self.background)
        payload['returned_outputs'] = [{'file': payload['file'], 'role_review': payload['role_review']}]
        payload['returned_files'] = [payload['file']]
        response = self.invoke(payload=payload)
        self.assertEqual(response['kind'], 'generate')
        state = self.state()
        self.assertEqual(state['recovery']['used'], 0)
        self.assertFalse(any(task.get('generation_violations') for task in state['tasks'].values()))

    def test_delayed_review_reuses_saved_file_and_original_request(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        request = Path(permit['request_file']).read_bytes()
        payload = self.result(permit, self.background)
        payload.pop('role_review')
        self.assertEqual(self.invoke(payload=payload)['kind'], 'registration_required')
        self.assertEqual(self.invoke()['kind'], 'registration_required')
        self.assertEqual(self.invoke(payload=self.result(permit, self.background))['kind'], 'generate')
        self.assertEqual(Path(permit['request_file']).read_bytes(), request)
        self.assertEqual(len(list((self.work / 'requests').glob('*.json'))), 1)
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_wrong_request_hash_stays_a_hard_binding_failure(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = self.result(permit, self.background)
        payload['request_sha256'] = '0' * 64
        self.invoke(payload=payload, expected_code=2)
        saved = self.state()['tasks'][tasks['background']['id']]
        self.assertTrue(saved['hard_binding_failure'])
        self.assertEqual(self.invoke()['kind'], 'paused')

    def test_known_service_identifier_reuse_still_fails_for_distinct_files(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = self.result(permit, self.background)
        payload['tool_output_id'] = 'real-service-result-001'
        self.invoke(payload=payload)
        permit = self.begin(tasks['foreground'])
        payload = self.result(permit, self.foreground)
        payload['tool_output_id'] = 'real-service-result-001'
        response = self.invoke(payload=payload, expected_code=2)
        self.assertIn('identifier is already bound', response['message'])
        self.assertEqual(self.invoke()['kind'], 'paused')

    def test_source_input_cannot_be_registered_as_new_background(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        response = self.invoke(payload=self.result(permit, self.source), expected_code=2)
        self.assertIn('source/reference', response['message'])
        self.assertEqual(self.invoke()['kind'], 'paused')

    def test_shortened_prompt_is_correctable_before_begin(self):
        tasks = self.start()
        payload = copy.deepcopy(tasks['background']['begin_template'])
        payload['request']['tool'] = 'synthetic-offline-fixture'
        payload['request']['prompt'] = 'shortened prompt'
        self.invoke(payload=payload, expected_code=2)
        saved = self.state()['tasks'][tasks['background']['id']]
        self.assertEqual(saved['execution']['state'], 'not_started')
        self.begin(tasks['background'])

    def test_tool_without_a_size_parameter_can_record_its_available_arguments(self):
        tasks = self.start()
        request = tasks['background']['begin_template']['request']
        actual = {'prompt': request['prompt'], 'referenced_image_paths': request['references'],
                  'transparent_background': request['transparent_background']}
        permit = self.begin(tasks['background'], actual_arguments=actual)
        self.assertNotIn('size', permit['request']['tool_arguments'])
        self.assertEqual(permit['request']['size'], [1920, 1080])

    def test_failed_call_with_zero_outputs_can_use_the_frozen_repair(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = {**permit['failure_template'], 'reason': 'Synthetic provider failure before any image returned.',
                   'invocation_count': 1, 'output_count': 0}
        response = self.invoke(payload=payload)
        self.assertEqual(response['kind'], 'generate')
        repair = next(task for task in response['jobs'] if task['role'] == 'background')
        self.assertEqual(repair['attempt'], 1)
        self.assertEqual(self.state()['recovery']['used'], 1)

    def test_foreground_can_be_saved_before_background_without_describing_early(self):
        tasks = self.start()
        foreground_permit = self.begin(tasks['foreground'])
        response = self.invoke(payload=self.result(foreground_permit, self.foreground))
        self.assertNotEqual(response['kind'], 'describe')
        state = self.state()
        self.assertEqual(state['phase'], 'layers')
        self.assertEqual(state['tasks'][tasks['foreground']['id']]['status'], 'recorded')
        self.assertEqual(state['tasks'][tasks['background']['id']]['status'], 'waiting')
        request_bytes = Path(foreground_permit['request_file']).read_bytes()
        raw = self.work / state['tasks'][tasks['foreground']['id']]['result']['raw']
        self.assertEqual(raw.read_bytes(), self.foreground.read_bytes())
        background_permit = self.begin(tasks['background'])
        response = self.invoke(payload=self.result(background_permit, self.background))
        self.assertEqual(response['kind'], 'describe')
        self.assertEqual(Path(foreground_permit['request_file']).read_bytes(), request_bytes)
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_zero_output_count_with_a_saved_result_is_rejected_before_registration(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        payload = self.result(permit, self.background)
        payload['output_count'] = 0
        self.invoke(payload=payload, expected_code=2)
        saved = self.state()['tasks'][tasks['background']['id']]
        self.assertNotIn('result', saved)
        self.assertEqual(saved['execution']['state'], 'invoking')

    def test_zero_output_failure_cannot_omit_an_existing_return(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        for field, value in [('file', str(self.background)),
                             ('returned_files', [str(self.background)]),
                             ('returned_outputs', [{'file': str(self.background)}])]:
            with self.subTest(field=field):
                payload = {**permit['failure_template'], 'reason': 'Synthetic error with an actual return.',
                           'invocation_count': 1, 'output_count': 0, field: value}
                self.invoke(payload=payload, expected_code=2)
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_invalid_output_counts_remain_correctable_input_errors(self):
        tasks = self.start()
        permit = self.begin(tasks['background'])
        for count in [-1, True, 0.0]:
            with self.subTest(count=count):
                payload = {**permit['failure_template'], 'reason': 'Synthetic failure.',
                           'invocation_count': 1, 'output_count': count}
                self.invoke(payload=payload, expected_code=2)
        self.assertEqual(self.state()['recovery']['used'], 0)

    def test_foreground_first_still_requires_a_truthful_role_review(self):
        tasks = self.start()
        permit = self.begin(tasks['foreground'])
        payload = self.result(permit, self.foreground)
        payload['role_review'].update(matches_task=False, page_elements_match=False,
                                      observed='Synthetic negative test explicitly rejects this foreground role.')
        response = self.invoke(payload=payload)
        self.assertEqual(response['kind'], 'generate')
        state = self.state()
        saved = state['tasks'][tasks['foreground']['id']]
        self.assertNotIn('result', saved)
        self.assertEqual(saved['attempt'], 1)
        self.assertEqual(saved['attempts'][0]['outcome'], 'role_mismatch')
        self.assertEqual(state['phase'], 'layers')

    def test_foreground_first_rejects_source_input(self):
        tasks = self.start()
        permit = self.begin(tasks['foreground'])
        response = self.invoke(payload=self.result(permit, self.source), expected_code=2)
        self.assertIn('source/reference', response['message'])
        state = self.state()
        self.assertTrue(state['tasks'][tasks['foreground']['id']]['hard_binding_failure'])
        self.assertEqual(state['tasks'][tasks['background']['id']]['status'], 'waiting')


class LegacyForegroundOrderSmoke(ReviewedOutputFixture):
    def test_legacy_reviewed_job_keeps_background_first_registration(self):
        tasks = self.start()
        self.begin(tasks['foreground'])
        with self.assertRaisesRegex(ValueError, 'register the background first'):
            self.submit(tasks['foreground'], self.fg)
        from newpath.storage import read_state
        self.assertNotIn('result', read_state(self.work)['tasks'][tasks['foreground']['id']])


if __name__ == '__main__':
    unittest.main()
