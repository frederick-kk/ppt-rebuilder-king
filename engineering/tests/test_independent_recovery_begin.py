"""Real CLI smoke for per-task retry starts while another task remains failed."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
import test_host_registration_smoke as host_smoke


class IndependentRecoveryBegin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the isolated runtime/manifest fixture, not its test methods.
        host_smoke.HostRegistrationSmoke.setUpClass.__func__(cls)

    setUp = host_smoke.HostRegistrationSmoke.setUp
    invoke = host_smoke.HostRegistrationSmoke.invoke
    start = host_smoke.HostRegistrationSmoke.start
    begin = host_smoke.HostRegistrationSmoke.begin
    state = host_smoke.HostRegistrationSmoke.state

    def retry(self, task_id):
        command = [sys.executable, '-m', 'newpath', '--work', str(self.work),
                   '--retry-task', task_id, '--retry-authorization',
                   'User: retry only this named failed task once.']
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.run(command, cwd=self.runtime, capture_output=True,
                                 env=environment, timeout=30)
        response = json.loads(process.stdout.decode('utf-8'))
        self.assertEqual(process.returncode, 0, response)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(len(response['jobs']), 1)
        return response['jobs'][0]

    def fail_invocation(self, permit):
        return self.invoke(payload={**permit['failure_template'], 'outcome': 'failed',
                                    'reason': 'Synthetic service failure without any returned file.',
                                    'invocation_count': 1, 'output_count': 0})

    def prepared_recovery_and_failed_foreground(self):
        tasks = self.start()
        background = self.begin(tasks['background'])
        foreground = self.begin(tasks['foreground'])
        response = self.fail_invocation(background)
        repair = next(task for task in response['jobs'] if task['role'] == 'background')
        self.assertEqual(repair['attempt'], 1)
        self.fail_invocation(foreground)
        self.assertEqual(self.state()['tasks'][foreground['task_id']]['status'], 'failed')
        return repair

    def two_failed_tasks(self):
        tasks = self.start()
        background = self.begin(tasks['background'])
        foreground = self.begin(tasks['foreground'])
        response = self.fail_invocation(background)
        repair = next(task for task in response['jobs'] if task['role'] == 'background')
        self.fail_invocation(self.begin(repair))
        self.fail_invocation(foreground)
        state = self.state()
        self.assertEqual({task['status'] for task in state['tasks'].values()}, {'failed'})
        self.assertEqual(state['recovery']['used'], 1)
        return tasks

    def write_state(self, state):
        (self.work / 'state.json').write_text(json.dumps(state), 'utf-8')

    @staticmethod
    def begin_payload(task):
        payload = copy.deepcopy(task['begin_template'])
        payload['request']['tool'] = 'synthetic-offline-fixture'
        return payload

    def test_explicit_background_retry_begins_while_foreground_stays_failed(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        self.assertEqual(reopened['attempt'], 2)
        permit = self.begin(reopened)
        self.assertEqual(permit['kind'], 'invoke_once')
        state = self.state()
        self.assertEqual(state['tasks'][tasks['foreground']['id']]['status'], 'failed')
        self.assertEqual(state['recovery']['used'], 1)
        self.fail_invocation(permit)
        response = self.invoke(payload=self.begin_payload(reopened), expected_code=2)
        self.assertIn('failed task', response['message'])

    def test_prepared_bounded_recovery_begins_while_other_task_stays_failed(self):
        repair = self.prepared_recovery_and_failed_foreground()
        entry = copy.deepcopy(self.state()['recovery'])
        self.begin(repair)
        self.assertEqual(self.state()['recovery'], entry)

    def test_explicit_foreground_retry_begins_while_background_stays_failed(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['foreground']['id'])
        self.assertEqual(reopened['attempt'], 1)
        self.begin(reopened)
        state = self.state()
        self.assertEqual(state['tasks'][tasks['background']['id']]['status'], 'failed')
        self.assertEqual(state['recovery']['used'], 1)

    def test_duplicate_begin_never_reissues_retry_permit(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        self.begin(reopened)
        before = (self.work / 'state.json').read_bytes()
        count = len(list((self.work / 'requests').glob('*.json')))
        response = self.invoke(payload=self.begin_payload(reopened))
        self.assertEqual(response['kind'], 'await_results')
        self.assertFalse(response['generate_more'])
        self.assertEqual((self.work / 'state.json').read_bytes(), before)
        self.assertEqual(len(list((self.work / 'requests').glob('*.json'))), count)

    def test_two_authorized_retries_can_begin_in_reverse_submission_order(self):
        tasks = self.two_failed_tasks()
        background = self.retry(tasks['background']['id'])
        foreground = self.retry(tasks['foreground']['id'])
        self.begin(foreground)
        self.begin(background)
        state = self.state()
        self.assertEqual({task['execution']['state'] for task in state['tasks'].values()}, {'invoking'})
        self.assertEqual(state['recovery']['used'], 1)

    def test_unstarted_first_invocation_is_still_blocked_by_another_failed_task(self):
        tasks = self.start()
        background = self.begin(tasks['background'])
        response = self.fail_invocation(background)
        repair = next(task for task in response['jobs'] if task['role'] == 'background')
        self.fail_invocation(self.begin(repair))
        response = self.invoke(payload=self.begin_payload(tasks['foreground']), expected_code=2)
        self.assertIn('failed task', response['message'])
        self.assertEqual(self.state()['tasks'][tasks['foreground']['id']]['execution']['state'], 'not_started')

    def test_stale_blank_or_other_task_authorization_does_not_bypass_failure(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        baseline = self.state()
        for kind in ['stale', 'blank', 'other_task']:
            with self.subTest(kind=kind):
                state = copy.deepcopy(baseline)
                current = state['tasks'][reopened['id']]
                if kind == 'stale':
                    current['retry_authorizations'][-1]['attempt'] -= 1
                elif kind == 'blank':
                    current['retry_authorizations'][-1]['authorization'] = '  '
                else:
                    entry = current.pop('retry_authorizations')
                    state['tasks'][tasks['foreground']['id']]['retry_authorizations'] = entry
                self.write_state(state)
                response = self.invoke(payload=self.begin_payload(reopened), expected_code=2)
                self.assertIn('failed task', response['message'])

    def test_pending_violation_or_user_hold_still_blocks_authorized_retry(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        baseline = self.state()
        for kind in ['violation', 'hold']:
            with self.subTest(kind=kind):
                state = copy.deepcopy(baseline)
                if kind == 'violation':
                    state['tasks'][tasks['foreground']['id']]['generation_violations'] = [{'acknowledged': False}]
                else:
                    state['execution_hold'] = {'authorization': 'User: stop all remaining generation.'}
                self.write_state(state)
                response = self.invoke(payload=self.begin_payload(reopened), expected_code=2)
                self.assertIn('generation violation' if kind == 'violation' else 'paused by the user', response['message'])
                self.assertEqual(self.state()['recovery']['used'], 1)

    def test_wrong_attempt_role_or_request_remains_rejected(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        for field in ['attempt', 'role', 'request']:
            with self.subTest(field=field):
                payload = self.begin_payload(reopened)
                if field == 'attempt':
                    payload['attempt'] -= 1
                elif field == 'role':
                    payload['role'] = 'foreground'
                else:
                    payload['request']['prompt'] += '\nUnauthorized request variation.'
                self.invoke(payload=payload, expected_code=2)
                self.assertEqual(self.state()['tasks'][reopened['id']]['execution']['state'], 'not_started')

    def test_stale_or_other_task_bounded_entry_does_not_authorize_begin(self):
        repair = self.prepared_recovery_and_failed_foreground()
        baseline = self.state()
        for kind in ['stale', 'other_task', 'wrong_policy']:
            with self.subTest(kind=kind):
                state = copy.deepcopy(baseline)
                entry = state['tasks'][repair['id']]['bounded_recovery']
                if kind == 'stale':
                    entry['attempt'] = 0
                elif kind == 'other_task':
                    entry['task_id'] = 'a-different-task'
                else:
                    entry['policy'] = 'a-different-recovery-policy'
                state['recovery']['entry'] = copy.deepcopy(entry)
                self.write_state(state)
                response = self.invoke(payload=self.begin_payload(repair), expected_code=2)
                self.assertIn('failed task', response['message'])

    def test_current_authorization_does_not_bypass_active_batch(self):
        tasks = self.two_failed_tasks()
        reopened = self.retry(tasks['background']['id'])
        state = self.state()
        # A narrow queue fault injection verifies the later batch guard remains.
        state['tasks'][reopened['id']]['role'] = 'board'
        state['tasks'][reopened['id']]['background_mode'] = 'native-transparent-materials/1'
        state['tasks'][reopened['id']]['board'] = {'background_mode': 'native-transparent-materials/1'}
        state['active_batch'] = []
        self.write_state(state)
        payload = self.begin_payload(reopened)
        payload['role'] = 'board'
        response = self.invoke(payload=payload, expected_code=2)
        self.assertIn('active batch', response['message'])


if __name__ == '__main__':
    unittest.main()
