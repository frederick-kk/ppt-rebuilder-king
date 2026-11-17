"""Correcting a pixel/file hash mix-up preserves the later real recovery budget."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from PIL import Image
from newpath import binding, controller
from newpath.storage import read_state, sha256
from test_result_role_binding import ReviewedOutputFixture


class ReviewHashRegistrationRecovery(ReviewedOutputFixture):
    def initialize(self, *, recovery=True):
        options = {'recovery_policy': 'bounded-recovery/1'} if recovery else {}
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', post_return_prompt_policy='audit-existing-return/1',
                delivery_policy='reviewed-existing-output/1', source_layout_policy='source-layout-first/1',
                source_board_reference_policy='direct-source-board-reference/1', **options)

    def start_background(self, *, recovery=True):
        self.initialize(recovery=recovery)
        layout = {'items': [{'id': 'footer', 'kind': 'art', 'box': [385, 972, 1223, 78],
                            'lettering': 'TEST TITLE', 'description': 'One standalone written group.'}],
                  'background_regions': [], 'primary_wordart_id': 'footer'}
        action = controller.run(self.work, data={'kind': 'layout', 'layout': layout})
        background = action['jobs'][0]
        self.begin(background)
        return background

    def pixel_hash_payload(self, task, path, **review_fields):
        review = self.review(task, path, output_sha256=binding.pixel_digest(path), **review_fields)
        return self.payload(task, path, role_review=review)

    def incomplete(self, task, path):
        try:
            response = controller.run(self.work, data=self.pixel_hash_payload(task, path))
        except ValueError as error:
            self.fail('A proven positive review with the actual pixel digest needs same-file registration correction: ' + str(error))
        self.assertEqual(response['kind'], 'registration_required')
        return response

    def reject_wordart(self, task, path, *, count=1, **extra):
        return self.submit(task, path, invocation_count=count, role_review=self.review(
            task, path, matches_task=False, only_assigned_title=False,
            observed='The saved synthetic output is not the assigned standalone title.'), **extra)

    def test_pixel_hash_mistake_is_pending_registration_with_preserved_actual_bytes(self):
        background = self.start_background()
        response = self.incomplete(background, self.bg)
        state = read_state(self.work)
        task = state['tasks'][background['id']]
        self.assertEqual(task['status'], 'waiting')
        self.assertFalse(task.get('hard_binding_failure', False))
        self.assertEqual(state['recovery']['used'], 0)
        self.assertEqual(task['attempts'][-1]['outcome'], 'registration_required')
        self.assertEqual(task['registration']['error_kind'], 'review_output_sha256_is_pixel_digest')
        self.assertEqual(task['registration']['sha256'], sha256(self.bg))
        self.assertEqual((self.work / task['registration']['raw']).read_bytes(), self.bg.read_bytes())
        self.assertEqual(response['pending_results'][0]['role_review_template']['output_sha256'], sha256(self.bg))

    def test_same_file_correction_then_real_wordart_failure_automatically_gets_the_only_repair(self):
        background = self.start_background()
        self.incomplete(background, self.bg)
        action = self.submit(background, self.bg)
        before = read_state(self.work)
        fixed = before['tasks'][background['id']]
        self.assertEqual(fixed['status'], 'recorded')
        self.assertEqual(fixed['attempt'], 0)
        self.assertEqual([entry['outcome'] for entry in fixed['attempts']], ['registration_required', 'recorded'])
        self.assertEqual(fixed['attempts'][0]['role_review']['output_sha256'], binding.pixel_digest(self.bg))
        self.assertEqual(fixed['result']['sha256'], sha256(self.bg))
        title = next(job for job in action['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        repair = self.reject_wordart(title, self.fg)
        self.assertEqual(repair['kind'], 'generate')
        self.assertEqual([(job['id'], job['attempt']) for job in repair['jobs']], [(title['id'], 1)])
        after = read_state(self.work)
        self.assertEqual(after['recovery']['used'], 1)
        self.assertEqual(after['recovery']['entry']['authorization_scope'], 'frozen_job_policy_not_a_new_user_request')
        self.assertEqual(after['tasks'][background['id']], fixed)
        self.assertNotIn('retry_authorizations', after['tasks'][title['id']])

    def test_unresolved_metadata_error_is_corrected_before_another_task_uses_repair(self):
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                recovery_policy='bounded-recovery/1')
        jobs = {job['role']: job for job in action['jobs']}
        self.begin(jobs['background'])
        self.begin(jobs['foreground'])
        self.incomplete(jobs['background'], self.bg)
        response = self.submit(jobs['foreground'], self.fg, role_review=self.review(
            jobs['foreground'], self.fg, matches_task=False, page_elements_match=False,
            observed='The actual synthetic foreground is missing its assigned subjects.'))
        self.assertEqual(response['kind'], 'registration_required')
        self.assertEqual(response['pending_results'][0]['id'], jobs['background']['id'])
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(read_state(self.work)['tasks'][jobs['foreground']['id']]['status'], 'failed')
        repair = self.submit(jobs['background'], self.bg)
        self.assertEqual(repair['kind'], 'generate')
        self.assertEqual([(job['id'], job['attempt']) for job in repair['jobs']],
                         [(jobs['foreground']['id'], 1)])
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)

    def test_pending_pixel_hash_error_cannot_switch_to_different_file_bytes(self):
        background = self.start_background()
        self.incomplete(background, self.bg)
        changed = self.root / 'same-pixels-new-bytes.png'
        with Image.open(self.bg) as image:
            image.save(changed, compress_level=0)
        self.assertEqual(binding.pixel_digest(changed), binding.pixel_digest(self.bg))
        self.assertNotEqual(sha256(changed), sha256(self.bg))
        with self.assertRaises(binding.BindingViolation):
            self.submit(background, changed)
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertTrue(state['tasks'][background['id']]['hard_binding_failure'])
        self.assertEqual(controller.run(self.work)['kind'], 'paused')

    def test_incomplete_followup_keeps_the_original_same_file_requirement(self):
        background = self.start_background()
        self.incomplete(background, self.bg)
        self.assertEqual(self.submit(background, self.bg, role_review=None)['kind'], 'registration_required')
        changed = self.root / 'reencoded-after-incomplete-followup.png'
        with Image.open(self.bg) as image:
            image.save(changed, compress_level=0)
        self.assertNotEqual(sha256(changed), sha256(self.bg))
        with self.assertRaises(binding.BindingViolation):
            self.submit(background, changed)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_other_wrong_review_hash_stays_a_hard_binding_error(self):
        background = self.start_background()
        with self.assertRaises(binding.BindingViolation):
            self.submit(background, self.bg, role_review=self.review(background, self.bg, output_sha256='f' * 64))
        self.assertTrue(read_state(self.work)['tasks'][background['id']]['hard_binding_failure'])
        self.assertEqual(controller.run(self.work)['kind'], 'paused')

    def test_wrong_role_with_pixel_hash_stays_a_hard_binding_error(self):
        background = self.start_background()
        with self.assertRaises(binding.BindingViolation):
            controller.run(self.work, data=self.pixel_hash_payload(background, self.bg, role='wordart'))
        self.assertTrue(read_state(self.work)['tasks'][background['id']]['hard_binding_failure'])

    def test_false_or_unobserved_checks_cannot_be_reclassified_as_a_positive_metadata_mistake(self):
        background = self.start_background()
        for fields in ({'matches_task': False}, {'looked_at_return': False}, {'foreground_text_absent': False}):
            with self.subTest(fields=fields), self.assertRaises(binding.BindingViolation):
                controller.run(self.work, data=self.pixel_hash_payload(background, self.bg, **fields))
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_source_reference_reuse_remains_hard_even_with_a_pixel_hash_declaration(self):
        background = self.start_background()
        reference = Path(background['references'][0])
        with self.assertRaises(binding.BindingViolation):
            controller.run(self.work, data=self.pixel_hash_payload(background, reference))
        self.assertTrue(read_state(self.work)['tasks'][background['id']]['hard_binding_failure'])

    def test_other_historical_binding_conflict_still_blocks_later_role_failure(self):
        background = self.start_background()
        with self.assertRaises(binding.BindingViolation):
            self.submit(background, self.bg, request_sha256='wrong-request')
        title = next(job for job in self.submit(background, self.bg)['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        response = self.reject_wordart(title, self.fg)
        self.assertEqual(response['kind'], 'paused')
        self.assertIn('recovery_blocker', response)
        self.assertEqual(response['recovery_blocker']['code'], 'binding_conflict')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertNotIn('only after the user requests another generation', response['instructions'])

    def test_unknown_call_count_keeps_protection_and_requests_existing_registration(self):
        background = self.start_background()
        title = next(job for job in self.submit(background, self.bg)['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        response = self.reject_wordart(title, self.fg, count=None)
        self.assertEqual(response['kind'], 'paused')
        self.assertIn('recovery_blocker', response)
        self.assertEqual(response['recovery_blocker']['code'], 'invocation_count_unknown')
        self.assertIn('existing', response['instructions'])
        self.assertNotIn('only after the user requests another generation', response['instructions'])
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_user_hold_still_prevents_recovery(self):
        background = self.start_background()
        title = next(job for job in self.submit(background, self.bg)['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        response = self.reject_wordart(title, self.fg, stop_remaining_generation=True,
                                      violation_authorization='Synthetic test user stops remaining generation.')
        self.assertEqual(response['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertTrue(read_state(self.work)['execution_hold'])

    def test_extra_calls_still_pause_without_spending_repair(self):
        background = self.start_background()
        title = next(job for job in self.submit(background, self.bg)['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        response = self.reject_wordart(title, self.fg, count=2)
        self.assertEqual(response['kind'], 'paused')
        self.assertEqual(response['reason'], 'generation_budget_exceeded')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_saved_legacy_job_without_recovery_keeps_its_existing_hash_guard(self):
        background = self.start_background(recovery=False)
        with self.assertRaises(ValueError):
            controller.run(self.work, data=self.pixel_hash_payload(background, self.bg))
        self.assertNotIn('recovery', read_state(self.work))

    def test_same_file_correction_can_preserve_a_later_true_role_failure(self):
        background = self.start_background()
        self.incomplete(background, self.bg)
        response = self.submit(background, self.bg, role_review=self.review(
            background, self.bg, matches_task=False, foreground_text_absent=False,
            observed='The same actual synthetic return is now explicitly judged wrong for the assigned role.'))
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(response['jobs'][0]['attempt'], 1)
        failed = read_state(self.work)['tasks'][background['id']]
        self.assertEqual(failed['attempts'][-1]['outcome'], 'role_mismatch')
        self.assertEqual(len(failed['role_review_rejections']), 1)

    def test_actual_background_hash_correction_and_footer_failure_use_real_saved_bytes(self):
        fixture = os.environ.get('PRK_R23B_MEDIUM_JOB')
        if not fixture:
            self.skipTest('Set PRK_R23B_MEDIUM_JOB for the retained actual 5.6 standard job')
        original = Path(fixture)
        before = {str(path.relative_to(original)): sha256(path) for path in original.rglob('*') if path.is_file()}
        saved = read_state(original)
        bg_saved = next(task for task in saved['tasks'].values() if task['role'] == 'background')
        art_saved = next(task for task in saved['tasks'].values() if task['role'] == 'wordart')
        bg_file = original / bg_saved['result']['raw']
        art_file = original / art_saved['attempts'][-1]['raw']
        self.source = original / 'input/original.png'
        background = self.start_background()
        self.assertEqual(sha256(bg_file), 'e792d0bf152971108283049c528f3a9a87db23113462c501e3a91b4ec2682215')
        self.assertEqual(binding.pixel_digest(bg_file), '55d0ebd64141bac313c3283ec170fa940fd6923fd01611027bc3661f38935f7a')
        self.incomplete(background, bg_file)
        title = next(job for job in self.submit(background, bg_file)['jobs'] if job['role'] == 'wordart')
        self.begin(title)
        response = self.reject_wordart(title, art_file)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(response['jobs'][0]['attempt'], 1)
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)
        self.assertEqual({str(path.relative_to(original)): sha256(path)
                          for path in original.rglob('*') if path.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
