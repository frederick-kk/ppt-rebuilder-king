"""Bounded queue recovery using real saved image fixtures, without image calls."""
import copy
import inspect
import json
from pathlib import Path
import unittest
from unittest import mock
from zipfile import ZipFile

from test_result_role_binding import ReviewedOutputFixture
from newpath import controller as c
from newpath.storage import read_state, save_state, sha256

MODE = 'bounded-recovery/1'


class BoundedFlowRecovery(ReviewedOutputFixture):
    def start(self, policy=MODE):
        kwargs = {'execution_policy': 'explicit-start/1',
                  'post_return_prompt_policy': 'audit-existing-return/1',
                  'delivery_policy': 'reviewed-existing-output/1'}
        if 'recovery_policy' in inspect.signature(c.run).parameters:
            kwargs['recovery_policy'] = policy
        with mock.patch.object(c, '_runtime'):
            response = c.run(self.work, source=self.source, **kwargs)
        self.jobs = {task['role']: task for task in response['jobs']}
        return self.jobs

    def rejected(self, task, path, **extra):
        return self.payload(task, path, role_review=self.review(
            task, path, matches_task=False,
            observed='Viewed output contains only a title and no assigned background scenery.'), **extra)

    def repair(self, response, role):
        self.assertEqual(response['kind'], 'generate')
        return next(task for task in response['jobs'] if task['role'] == role)

    def test_policy_is_frozen_in_initialization_and_state(self):
        self.start()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker.get('recovery_policy'), MODE)
        self.assertEqual(read_state(self.work).get('recovery_policy'), MODE)
        self.assertEqual(c.run(self.work).get('recovery_policy'), MODE)
        state = read_state(self.work)
        state.pop('recovery_policy')
        save_state(self.work, state, 'fixture-tampered-policy')
        with self.assertRaisesRegex(ValueError, 'policy'):
            c.run(self.work)

    def test_missing_review_keeps_real_output_pending_and_template_supplies_hashes(self):
        task = self.start()['background']; permit = self.begin(task)
        request_bytes = Path(permit['request_file']).read_bytes()
        response = self.submit(task, self.bg, role_review=None)
        self.assertEqual(response['kind'], 'registration_required')
        pending = response['pending_results'][0]
        self.assertEqual(pending['result_template']['request_sha256'], permit['request_sha256'])
        self.assertEqual(pending['role_review_template']['output_sha256'], sha256(self.bg))
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['status'], 'waiting')
        self.assertEqual(saved['attempt'], 0)
        self.assertNotIn('failure', saved)
        self.assertEqual(saved['attempts'][-1]['outcome'], 'registration_required')
        self.assertEqual((self.work / saved['attempts'][-1]['raw']).read_bytes(), self.bg.read_bytes())
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(c.run(self.work)['kind'], 'registration_required')
        self.submit(task, self.bg)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'], 'recorded')
        self.assertEqual(request_bytes, Path(permit['request_file']).read_bytes())

    def test_unviewed_default_false_is_incomplete_registration(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, role_review=self.review(
            task, self.bg, looked_at_return=False, matches_task=False, foreground_text_absent=False))
        self.assertEqual(response['kind'], 'registration_required')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.submit(task, self.bg)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'], 'recorded')

    def test_viewed_false_creates_one_repair_preserving_original_request_and_brief(self):
        task = self.start()['background']; permit = self.begin(task)
        original_prompt = Path(task['prompt_file']).read_bytes()
        original_request = Path(permit['request_file']).read_bytes()
        response = c.run(self.work, data=self.rejected(task, self.bg))
        repair = self.repair(response, 'background')
        self.assertEqual(repair['id'], task['id']); self.assertEqual(repair['attempt'], 1)
        self.assertEqual(repair['references'], task['references'])
        self.assertEqual(Path(task['prompt_file']).read_bytes(), original_prompt)
        self.assertEqual(Path(permit['request_file']).read_bytes(), original_request)
        self.assertIn('background scenery', Path(repair['prompt_file']).read_text('utf-8'))
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['attempts'][-1]['outcome'], 'role_mismatch')
        self.assertEqual((self.work / saved['attempts'][-1]['raw']).read_bytes(), self.bg.read_bytes())
        self.assertEqual(saved['execution']['state'], 'not_started')
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)
        self.assertNotIn('retry_authorizations', saved)

    def test_failed_repair_pauses_and_repeated_resume_cannot_spend_again(self):
        task = self.start()['background']; self.begin(task)
        repair = self.repair(c.run(self.work, data=self.rejected(task, self.bg)), 'background')
        permit = self.begin(repair)
        request = copy.deepcopy(repair['begin_template']); request['request']['tool'] = 'synthetic-image-tool'
        self.assertEqual(c.run(self.work, data=request)['kind'], 'await_results')
        response = c.run(self.work, data=self.rejected(repair, self.bg))
        self.assertEqual(response['kind'], 'paused')
        before = (self.work / 'state.json').read_bytes()
        for _ in range(3):
            self.assertEqual(c.run(self.work)['kind'], 'paused')
        self.assertEqual((self.work / 'state.json').read_bytes(), before)
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 1)
        self.assertEqual(state['tasks'][task['id']]['attempt'], 1)
        self.assertTrue(Path(permit['request_file']).is_file())

    def test_material_failure_preserves_completed_background(self):
        jobs = self.start(); self.begin(jobs['background']); self.submit(jobs['background'], self.bg)
        background = copy.deepcopy(read_state(self.work)['tasks'][jobs['background']['id']])
        self.begin(jobs['foreground'])
        opaque = self.root / 'opaque.png'
        from PIL import Image
        Image.new('RGB', (1920, 1080), 'red').save(opaque)
        repair = self.repair(self.submit(jobs['foreground'], opaque), 'foreground')
        self.assertEqual(repair['attempt'], 1)
        state = read_state(self.work)
        self.assertEqual(state['tasks'][jobs['foreground']['id']]['attempts'][-1]['outcome'], 'material')
        self.assertEqual(state['tasks'][jobs['background']['id']], background)

    def test_decoding_failure_is_distinct_from_role_review_failure(self):
        task = self.start()['background']; self.begin(task)
        bad = self.root / 'broken.png'; bad.write_bytes(b'not image bytes')
        response = self.submit(task, bad)
        self.repair(response, 'background')
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['attempts'][-1]['outcome'], 'decoding')
        self.assertEqual(saved.get('return_bindings', [{}])[0].get('output_sha256'), sha256(bad))

    def test_missing_request_hash_is_registration_but_mismatch_remains_binding_failure(self):
        task = self.start()['background']; self.begin(task)
        self.assertEqual(self.submit(task, self.bg, request_sha256=None)['kind'], 'registration_required')
        with self.assertRaisesRegex(ValueError, 'binding|request'):
            self.submit(task, self.bg, request_sha256='wrong')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertEqual(state['tasks'][task['id']]['attempts'][-1]['outcome'], 'binding')
        self.assertEqual(c.run(self.work)['kind'], 'paused')

    def test_source_substitution_cannot_be_hidden_by_missing_review(self):
        task = self.start()['background']; self.begin(task)
        with self.assertRaisesRegex(ValueError, 'source|reference|input'):
            self.submit(task, Path(task['references'][0]), role_review=None)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(c.run(self.work)['kind'], 'paused')

    def test_cross_task_reuse_cannot_consume_recovery(self):
        jobs = self.start(); self.begin(jobs['background']); self.submit(jobs['background'], self.bg)
        self.begin(jobs['foreground'])
        with self.assertRaisesRegex(ValueError, 'another task|cross.task'):
            self.submit(jobs['foreground'], self.bg, role_review=None)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(c.run(self.work)['kind'], 'paused')

    def test_generation_violation_never_consumes_recovery(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, invocation_count=2)
        self.assertEqual(response['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_user_stop_prevents_role_failure_recovery(self):
        task = self.start()['background']; self.begin(task)
        response = c.run(self.work, data=self.rejected(task, self.bg,
            stop_remaining_generation=True, violation_authorization='User said stop all remaining image generation.'))
        self.assertEqual(response['kind'], 'paused')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertEqual(state['tasks'][task['id']]['attempt'], 0)

    def test_real_started_generation_failure_allows_single_repair(self):
        task = self.start()['background']; permit = self.begin(task)
        response = c.run(self.work, data={**permit['failure_template'],
            'invocation_count': 1, 'reason': 'Provider returned a sanitized generation error.'})
        repair = self.repair(response, 'background')
        self.assertEqual(repair['attempt'], 1)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['attempts'][-1]['outcome'], 'generation_failure')

    def test_unstarted_waiting_task_continues_without_consuming_recovery(self):
        jobs = self.start(); self.begin(jobs['background']); self.submit(jobs['background'], self.bg)
        response = c.run(self.work)
        self.assertEqual([task['role'] for task in response['jobs']], ['foreground'])
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(response['jobs'][0]['attempt'], 0)

    def test_legacy_job_keeps_old_strict_failure_and_cannot_migrate(self):
        task = self.start(policy=None)['background']; self.begin(task)
        with self.assertRaises(ValueError):
            self.submit(task, self.bg, role_review=None)
        self.assertEqual(c.run(self.work)['kind'], 'paused')
        self.assertNotIn('recovery', read_state(self.work))
        if 'recovery_policy' not in inspect.signature(c.run).parameters:
            self.fail('Controller must reject migration through its policy parameter')
        with self.assertRaisesRegex(ValueError, 'policy|migrat'):
            c.run(self.work, recovery_policy=MODE)

    def test_explicit_user_retry_remains_available_after_repair_budget_exhaustion(self):
        task = self.start()['background']; self.begin(task)
        repair = self.repair(c.run(self.work, data=self.rejected(task, self.bg)), 'background'); self.begin(repair)
        c.run(self.work, data=self.rejected(repair, self.bg))
        response = c.run(self.work, retry_task=task['id'],
                         retry_authorization='User requests another background generation now.')
        self.assertEqual(response['jobs'][0]['attempt'], 2)
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['retry_authorizations'][-1]['attempt'], 2)

    def test_done_is_immutable_after_recovery(self):
        jobs = self.start(); self.begin(jobs['background'])
        repaired = self.repair(c.run(self.work, data=self.rejected(jobs['background'], self.bg)), 'background')
        self.begin(repaired)
        from PIL import Image
        good = self.root / 'good.png'; Image.new('RGB', (1920, 1080), 'green').save(good)
        self.submit(repaired, good)
        self.begin(jobs['foreground']); self.submit(jobs['foreground'], self.fg)
        def build(layout, background, assets, output, **kwargs):
            with ZipFile(output, 'w') as archive:
                archive.writestr('ppt/presentation.xml', '<presentation/>')
                archive.writestr('ppt/slides/slide1.xml', '<slide/>')
            return {'warnings': [], 'skipped': []}
        with mock.patch.object(c, 'build_presentation', side_effect=build):
            done = c.run(self.work, data={'kind': 'layout', 'layout': {'items': []}})
        self.assertEqual(done['kind'], 'done')
        before = (self.work / 'state.json').read_bytes()
        self.assertEqual(c.run(self.work, retry_task=repaired['id'],
                               retry_authorization='User asks for a retry.'), done)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_viewed_false_cannot_be_washed_by_omitting_review_binding_fields(self):
        task = self.start()['background']; self.begin(task)
        review = self.review(task, self.bg, matches_task=False,
                             observed='Only title lettering, no background scenery.')
        review.pop('output_sha256')
        response = self.submit(task, self.bg, role_review=review)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['attempts'][-1]['outcome'], 'role_mismatch')

    def test_missing_role_check_is_registration_without_consuming_recovery(self):
        task = self.start()['background']; self.begin(task)
        review = self.review(task, self.bg); review.pop('foreground_text_absent')
        self.assertEqual(self.submit(task, self.bg, role_review=review)['kind'], 'registration_required')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_second_task_failure_cannot_consume_second_job_repair(self):
        jobs = self.start(); self.begin(jobs['background'])
        repair = self.repair(c.run(self.work, data=self.rejected(jobs['background'], self.bg)), 'background')
        self.begin(repair)
        from PIL import Image
        good = self.root / 'fixed.png'; Image.new('RGB', (1920, 1080), 'green').save(good)
        self.submit(repair, good)
        self.begin(jobs['foreground'])
        response = c.run(self.work, data=self.rejected(jobs['foreground'], self.fg))
        self.assertEqual(response['kind'], 'paused')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 1)
        self.assertEqual(state['tasks'][jobs['foreground']['id']]['attempt'], 0)

    def test_unknown_invocation_count_pauses_without_repair(self):
        task = self.start()['background']; self.begin(task)
        response = c.run(self.work, data=self.rejected(task, self.bg, invocation_count=None))
        self.assertEqual(response['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_replay_of_original_failure_never_applies_to_new_attempt(self):
        task = self.start()['background']; self.begin(task)
        failure = self.rejected(task, self.bg)
        self.repair(c.run(self.work, data=failure), 'background')
        before = (self.work / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'attempt'):
            c.run(self.work, data=failure)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_prior_binding_failure_cannot_be_demoted_to_missing_registration(self):
        task = self.start()['background']; self.begin(task)
        with self.assertRaises(ValueError):
            self.submit(task, self.bg, request_sha256='wrong')
        response = self.submit(task, self.bg, role_review=None)
        self.assertEqual(response['kind'], 'paused')
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['failure_kind'], 'binding')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_begun_foreground_failure_can_be_registered_before_background(self):
        jobs = self.start(); self.begin(jobs['foreground'])
        permit = self.permits[jobs['foreground']['id']]
        response = c.run(self.work, data={**permit['failure_template'], 'invocation_count': 1,
            'reason': 'Sanitized real foreground generation failure.'})
        repair = self.repair(response, 'foreground')
        self.assertEqual(repair['attempt'], 1)

    def test_lock_contention_does_not_reserve_or_dispatch_repair(self):
        from newpath.storage import job_lock
        self.start()
        before = (self.work / 'state.json').read_bytes()
        with job_lock(self.work):
            with self.assertRaises((ValueError, PermissionError)):
                c.run(self.work)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_surplus_rejection_does_not_select_invalid_first_output(self):
        task = self.start()['background']; self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        response = self.submit(task, self.bg, output_count=2,
            returned_files=[str(self.bg), str(second)], returned_outputs=[
                {'file': str(self.bg), 'role_review': self.review(task, self.bg, matches_task=False)},
                {'file': str(second), 'role_review': self.review(task, second)}])
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'], sha256(second))
        self.assertEqual(saved['surplus_resolution']['selected_index'], 1)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_receipt_counts_registration_correction_once_and_repair_twice(self):
        jobs = self.start(); self.begin(jobs['background'])
        self.submit(jobs['background'], self.bg, role_review=None)
        self.submit(jobs['background'], self.bg)
        self.begin(jobs['foreground'])
        repair = self.repair(c.run(self.work, data=self.rejected(jobs['foreground'], self.fg)), 'foreground')
        self.begin(repair)
        from PIL import Image, ImageDraw
        good = self.root / 'foreground-fixed.png'
        im = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((100, 100, 600, 600), fill='red'); im.save(good)
        self.submit(repair, good)
        def build(layout, background, assets, output, **kwargs):
            with ZipFile(output, 'w') as archive:
                archive.writestr('ppt/presentation.xml', '<presentation/>')
                archive.writestr('ppt/slides/slide1.xml', '<slide/>')
            return {'warnings': [], 'skipped': []}
        with mock.patch.object(c, 'build_presentation', side_effect=build):
            done = c.run(self.work, data={'kind': 'layout', 'layout': {'items': []}})
        receipt = json.loads((self.work / 'receipt.json').read_text('utf-8'))
        self.assertEqual(receipt['caller_reported_invocations'], 3)
        self.assertEqual(receipt['caller_reported_outputs'], 3)
        self.assertEqual(receipt['recorded_invocation_starts'], 3)
        self.assertEqual(receipt['registered_outputs'], 2)
        self.assertEqual(receipt.get('recovery_policy'), MODE)
        self.assertEqual(receipt.get('recovery', {}).get('used'), 1)
        self.assertEqual(done['caller_reported_invocations'], 3)

    def test_surplus_missing_review_keeps_order_and_can_complete_without_new_call(self):
        task = self.start()['background']; self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        data = self.payload(task, self.bg, output_count=2, returned_files=[str(self.bg), str(second)],
            returned_outputs=[{'file': str(self.bg), 'role_review': None},
                              {'file': str(second), 'role_review': self.review(task, second)}])
        response = c.run(self.work, data=data)
        self.assertEqual(response['kind'], 'registration_required')
        template = response['pending_results'][0]['result_template']
        self.assertEqual([entry['role_review']['output_sha256'] for entry in template['returned_outputs']],
                         [sha256(self.bg), sha256(second)])
        self.assertEqual(c.run(self.work)['kind'], 'registration_required')
        data['returned_outputs'][0]['role_review'] = self.review(task, self.bg)
        c.run(self.work, data=data)
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'], sha256(self.bg))
        self.assertEqual(saved['surplus_resolution']['selected_index'], 0)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_user_stop_outranks_pending_surplus_registration(self):
        task = self.start()['background']; self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        data = self.payload(task, self.bg, output_count=2,
            returned_files=[str(self.bg), str(second)], returned_outputs=[
                {'file': str(self.bg), 'role_review': None},
                {'file': str(second), 'role_review': self.review(task, second)}])
        self.assertEqual(c.run(self.work, data=data)['kind'], 'registration_required')
        data.update(stop_remaining_generation=True,
                    violation_authorization='User stops every remaining image invocation.')
        self.assertEqual(c.run(self.work, data=data)['kind'], 'paused')
        self.assertEqual(c.run(self.work)['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_extra_invocations_outrank_pending_surplus_registration(self):
        task = self.start()['background']; self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        data = self.payload(task, self.bg, output_count=2,
            returned_files=[str(self.bg), str(second)], returned_outputs=[
                {'file': str(self.bg), 'role_review': None},
                {'file': str(second), 'role_review': self.review(task, second)}])
        self.assertEqual(c.run(self.work, data=data)['kind'], 'registration_required')
        data['invocation_count'] = 2
        self.assertEqual(c.run(self.work, data=data)['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_surplus_common_missing_request_hash_can_be_completed(self):
        task = self.start()['background']; permit = self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        data = self.payload(task, self.bg, request_sha256=None, output_count=2,
            returned_files=[str(self.bg), str(second)], returned_outputs=[
                {'file': str(self.bg), 'role_review': self.review(task, self.bg)},
                {'file': str(second), 'role_review': self.review(task, second)}])
        self.assertEqual(c.run(self.work, data=data)['kind'], 'registration_required')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        data['request_sha256'] = permit['request_sha256']
        c.run(self.work, data=data)
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'], sha256(self.bg))
        self.assertEqual(saved['surplus_resolution']['selected_index'], 0)

    def test_generation_failure_cannot_replace_prior_hard_binding_failure(self):
        task = self.start()['background']; permit = self.begin(task)
        with self.assertRaisesRegex(ValueError, 'source|reference|input'):
            self.submit(task, Path(task['references'][0]))
        response = c.run(self.work, data={**permit['failure_template'], 'invocation_count': 1,
            'reason': 'Later caller reports a generation error.'})
        self.assertEqual(response['kind'], 'paused')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertEqual(state['tasks'][task['id']]['attempt'], 0)
        self.assertEqual(state['tasks'][task['id']]['failure_kind'], 'binding')

    def test_later_failure_declaration_cannot_skip_review_of_existing_output(self):
        task = self.start()['background']; permit = self.begin(task)
        self.submit(task, self.bg, role_review=None)
        response = c.run(self.work, data={**permit['failure_template'], 'invocation_count': 1,
            'reason': 'Later caller reports a sanitized provider failure.'})
        self.assertEqual(response['kind'], 'registration_required')
        state = read_state(self.work)
        saved = state['tasks'][task['id']]
        self.assertEqual(saved['status'], 'waiting')
        self.assertEqual(saved['attempt'], 0)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertEqual((self.work / saved['registration']['raw']).read_bytes(), self.bg.read_bytes())
        self.submit(task, self.bg)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'], 'recorded')

    def test_later_failure_declaration_cannot_skip_pending_surplus_reviews(self):
        task = self.start()['background']; permit = self.begin(task)
        from PIL import Image
        second = self.root / 'second.png'; Image.new('RGB', (1920, 1080), 'red').save(second)
        data = self.payload(task, self.bg, output_count=2,
            returned_files=[str(self.bg), str(second)], returned_outputs=[
                {'file': str(self.bg), 'role_review': None},
                {'file': str(second), 'role_review': self.review(task, second)}])
        self.assertEqual(c.run(self.work, data=data)['kind'], 'registration_required')
        response = c.run(self.work, data={**permit['failure_template'], 'invocation_count': 1,
            'reason': 'Later caller reports a sanitized generation error.'})
        self.assertEqual(response['kind'], 'registration_required')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertNotIn('result', state['tasks'][task['id']])

    def test_missing_file_followups_cannot_discard_existing_return_before_failure(self):
        task = self.start()['background']; permit = self.begin(task)
        self.submit(task, self.bg, role_review=None)
        for missing in ['', str(self.root / 'does-not-exist.png')]:
            response = c.run(self.work, data={**permit['result_template'], 'file': missing})
            self.assertEqual(response['kind'], 'registration_required')
        response = c.run(self.work, data={**permit['failure_template'], 'invocation_count': 1,
            'reason': 'Later caller reports failure after losing the file field.'})
        self.assertEqual(response['kind'], 'registration_required')
        template = response['pending_results'][0]
        self.assertEqual(Path(template['result_template']['file']).read_bytes(), self.bg.read_bytes())
        self.assertEqual(template['role_review_template']['output_sha256'], sha256(self.bg))
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)


if __name__ == '__main__':
    unittest.main()
