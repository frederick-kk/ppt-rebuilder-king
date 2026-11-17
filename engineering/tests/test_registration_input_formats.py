"""Registration-only repairs must reuse saved pixels and keep real rejections."""
import copy
from pathlib import Path

import test_bounded_flow_recovery as recovery_tests
from test_result_role_binding import ReviewedOutputFixture
from newpath import binding, controller
from newpath.storage import read_state, save_state, sha256


class RegistrationSmoke(ReviewedOutputFixture):
    start = recovery_tests.BoundedFlowRecovery.start
    def test_single_return_array_registers_nested_review_without_new_call(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, role_review=None,
            returned_files=[str(self.bg)], returned_outputs=[
                {'file': str(self.bg), 'role_review': self.review(task, self.bg)}])
        saved = read_state(self.work)
        self.assertEqual(saved['tasks'][task['id']]['status'], 'recorded')
        self.assertEqual(saved['tasks'][task['id']]['result']['sha256'], sha256(self.bg))
        self.assertNotEqual(response['kind'], 'paused')
        self.assertEqual(saved['recovery']['used'], 0)

    def test_single_return_array_accepts_unknown_provider_output_count(self):
        task = self.start()['background']; self.begin(task)
        self.submit(task, self.bg, output_count=None,
            returned_outputs=[{'file': str(self.bg), 'role_review': self.review(task, self.bg)}])
        saved = read_state(self.work)
        self.assertEqual(saved['tasks'][task['id']]['status'], 'recorded')
        self.assertIsNone(saved['tasks'][task['id']]['result']['output_count'])
        self.assertEqual(saved['recovery']['used'], 0)

    def test_empty_return_array_cannot_register_or_authorize_repair(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, returned_outputs=[])
        saved = read_state(self.work)
        self.assertNotIn('result', saved['tasks'][task['id']])
        self.assertEqual(response['kind'], 'paused')
        self.assertEqual(saved['recovery']['used'], 0)

    def test_real_zero_output_failure_with_empty_return_arrays_uses_one_repair(self):
        task = self.start()['background']; permit = self.begin(task)
        payload = {**permit['failure_template'], 'reason': 'Actual tool call returned a sanitized error and no images.',
                   'invocation_count': 1, 'output_count': 0,
                   'returned_outputs': [], 'returned_files': []}
        response = controller.run(self.work, data=payload)
        saved = read_state(self.work)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(saved['tasks'][task['id']]['attempt'], 1)
        self.assertEqual(saved['recovery']['used'], 1)
        self.assertNotIn('generation_violations', saved['tasks'][task['id']])
        self.assertEqual(saved['tasks'][task['id']]['attempts'][0]['outcome'], 'generation_failure')
        self.assertEqual(saved['tasks'][task['id']]['attempts'][0]['output_count'], 0)

    def test_success_with_zero_output_and_empty_arrays_is_rejected(self):
        task = self.start()['background']; permit = self.begin(task)
        payload = {**permit['result_template'], 'invocation_count': 1, 'output_count': 0,
                   'returned_outputs': [], 'returned_files': []}
        with self.assertRaisesRegex(ValueError, 'output_count'):
            controller.run(self.work, data=payload)
        saved = read_state(self.work)
        self.assertNotIn('result', saved['tasks'][task['id']])
        self.assertEqual(saved['recovery']['used'], 0)

    def test_zero_output_failure_with_known_image_is_rejected(self):
        task = self.start()['background']; permit = self.begin(task)
        payload = {**permit['failure_template'], 'reason': 'Error report contradicts a known saved output.',
                   'invocation_count': 1, 'output_count': 0,
                   'returned_outputs': [{'file': str(self.bg), 'role_review': self.review(task, self.bg)}],
                   'returned_files': [str(self.bg)]}
        with self.assertRaisesRegex(ValueError, 'output_count'):
            controller.run(self.work, data=payload)
        saved = read_state(self.work)
        self.assertNotIn('result', saved['tasks'][task['id']])
        self.assertEqual(saved['recovery']['used'], 0)

    def test_conflicting_single_return_path_cannot_register(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, returned_outputs=[
            {'file': str(self.fg), 'role_review': self.review(task, self.fg)}])
        saved = read_state(self.work)
        self.assertNotIn('result', saved['tasks'][task['id']])
        self.assertEqual(response['kind'], 'paused')

    def test_conflicting_single_return_review_cannot_silently_override_top_review(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, returned_outputs=[
            {'file': str(self.bg), 'role_review': self.review(task, self.bg, matches_task=False)}])
        saved = read_state(self.work)
        self.assertNotIn('result', saved['tasks'][task['id']])
        self.assertEqual(response['kind'], 'paused')

    def test_review_template_unknowns_do_not_reject_unfilled_role_check(self):
        task = self.start()['background']; permit = self.begin(task)
        review = copy.deepcopy(permit['role_review_template'])
        self.assertIsNone(review['looked_at_return'])
        self.assertIsNone(review['matches_task'])
        self.assertIsNone(review['foreground_text_absent'])
        review.update(output_sha256=sha256(self.bg), looked_at_return=True,
                      matches_task=True, observed='Actual navy background inspected.')
        response = self.submit(task, self.bg, role_review=review)
        saved = read_state(self.work)
        self.assertEqual(response['kind'], 'registration_required')
        self.assertEqual(saved['tasks'][task['id']]['attempt'], 0)
        self.assertEqual(saved['recovery']['used'], 0)
        self.assertEqual((self.work / saved['tasks'][task['id']]['registration']['raw']).read_bytes(), self.bg.read_bytes())

    def test_every_unobserved_role_check_uses_null(self):
        for role, check in [('foreground', 'page_elements_match'),
                            ('wordart', 'lettering_matches'), ('board', 'ordinary_text_removed')]:
            template = binding.review_template({'role': role})
            self.assertIsNone(template[check])
            self.assertIsNone(template['matches_task'])

    def test_explicit_false_is_still_a_real_rejection(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg,
            role_review=self.review(task, self.bg, foreground_text_absent=False))
        saved = read_state(self.work)
        self.assertEqual(response['kind'], 'generate')
        self.assertEqual(saved['tasks'][task['id']]['attempt'], 1)
        self.assertEqual(saved['recovery']['used'], 1)
        self.assertEqual(saved['tasks'][task['id']]['role_review_rejections'][0]['explicit_false_checks'],
                         ['foreground_text_absent'])

    def test_placeholder_output_ids_across_two_tasks_do_not_collide(self):
        for index, placeholder in enumerate(['unknown', ' NULL ', 'None', 'n/a', '', '   ']):
            with self.subTest(placeholder=placeholder):
                self.work = self.root / f'placeholder-{index}'
                jobs = self.start()
                for role, path in [('background', self.bg), ('foreground', self.fg)]:
                    task = jobs[role]; self.begin(task)
                    self.submit(task, path, tool_output_id=placeholder)
                saved = read_state(self.work)
                for task in saved['tasks'].values():
                    result = task['result']
                    self.assertIsNone(result['tool_output_id'])
                    self.assertEqual(result['reported_tool_output_id'], placeholder)
                    self.assertEqual(task['return_bindings'][0]['reported_tool_output_id'], placeholder)
                self.assertEqual(saved['recovery']['used'], 0)

    def test_invalid_identifier_objects_require_registration_correction(self):
        for index, invalid in enumerate([{}, [], False, 0]):
            with self.subTest(invalid=invalid):
                self.work = self.root / f'invalid-id-{index}'
                task = self.start()['background']; self.begin(task)
                response = self.submit(task, self.bg, tool_output_id=invalid)
                saved = read_state(self.work)
                self.assertEqual(response['kind'], 'registration_required')
                self.assertEqual(saved['recovery']['used'], 0)
                self.assertNotIn('hard_binding_failure', saved['tasks'][task['id']])
                self.submit(task, self.bg, tool_output_id=None)
                self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'], 'recorded')

    def test_historical_placeholder_binding_does_not_collide_with_new_placeholder(self):
        jobs = self.start(); bg = jobs['background']; self.begin(bg)
        self.submit(bg, self.bg, tool_output_id='unknown')
        state = read_state(self.work)
        state['tasks'][bg['id']]['return_bindings'][0]['tool_output_id'] = 'unknown'
        state['tasks'][bg['id']]['result']['tool_output_id'] = 'unknown'
        save_state(self.work, state, 'older_literal_unknown_fixture')
        fg = jobs['foreground']; self.begin(fg)
        response = self.submit(fg, self.fg, tool_output_id='unknown')
        self.assertNotEqual(response['kind'], 'paused')
        self.assertEqual(read_state(self.work)['tasks'][fg['id']]['status'], 'recorded')

    def test_single_array_known_identifier_wins_over_unknown_and_preserves_both_reports(self):
        pairs = [('unknown', 'real-output-001'), ('real-output-001', 'unknown'),
                 (None, 'real-output-001'), ('real-output-001', None)]
        for index, (top_level, returned_output) in enumerate(pairs):
            with self.subTest(top_level=top_level, returned_output=returned_output):
                self.work = self.root / f'mixed-output-id-{index}'
                task = self.start()['background']; permit = self.begin(task)
                request_bytes = Path(permit['request_file']).read_bytes()
                self.submit(task, self.bg, tool_output_id=top_level, returned_outputs=[
                    {'file': str(self.bg), 'role_review': self.review(task, self.bg),
                     'tool_output_id': returned_output}])
                saved = read_state(self.work)
                self.assertEqual(saved['tasks'][task['id']]['status'], 'recorded')
                result = saved['tasks'][task['id']]['result']
                declarations = {'top_level': top_level, 'returned_output': returned_output}
                self.assertEqual(result['tool_output_id'], 'real-output-001')
                self.assertEqual(result['tool_output_id_declarations'], declarations)
                self.assertEqual(saved['tasks'][task['id']]['return_bindings'][0]['tool_output_id_declarations'], declarations)
                self.assertEqual(Path(permit['request_file']).read_bytes(), request_bytes)
                before = (self.work / 'state.json').read_bytes()
                self.submit(task, self.bg, tool_output_id=top_level, returned_outputs=[
                    {'file': str(self.bg), 'role_review': self.review(task, self.bg),
                     'tool_output_id': returned_output}])
                self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_single_array_conflicting_real_identifiers_are_rejected(self):
        task = self.start()['background']; self.begin(task)
        response = self.submit(task, self.bg, tool_output_id='real-output-A', returned_outputs=[
            {'file': str(self.bg), 'role_review': self.review(task, self.bg),
             'tool_output_id': 'real-output-B'}])
        self.assertEqual(response['kind'], 'paused')
        self.assertNotIn('result', read_state(self.work)['tasks'][task['id']])

    def test_two_positive_observations_can_differ_without_extra_calls(self):
        task = self.start()['background']; self.begin(task)
        first = self.review(task, self.bg, observed='Viewed a navy background without page lettering.')
        second = self.review(task, self.bg, observed='The saved image contains dark blue scenery and no text.')
        response = self.submit(task, self.bg, role_review=first, returned_outputs=[
            {'file': str(self.bg), 'role_review': second}])
        saved = read_state(self.work)
        self.assertNotEqual(response['kind'], 'paused')
        self.assertEqual(saved['tasks'][task['id']]['status'], 'recorded')
        declarations = {'top_level': first, 'returned_output': second}
        self.assertEqual(saved['tasks'][task['id']]['result']['role_review_declarations'], declarations)
        self.assertEqual(saved['tasks'][task['id']]['return_bindings'][0]['role_review_declarations'], declarations)
        self.assertEqual(saved['recovery']['used'], 0)
        before = (self.work / 'state.json').read_bytes()
        self.submit(task, self.bg, role_review=first, returned_outputs=[
            {'file': str(self.bg), 'role_review': second}])
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_board_member_order_is_semantically_compatible(self):
        first = {'role':'board', 'output_sha256':sha256(self.bg), 'looked_at_return':True,
                 'matches_task':True, 'ordinary_text_removed':True,
                 'observed':'Viewed both assigned members.', 'matched_members':['a','b']}
        second = {**first, 'matched_members':['b','a']}
        data = binding.normalize_known_returns({'file':str(self.bg), 'output_count':1,
            'role_review':first, 'returned_outputs':[{'file':str(self.bg), 'role_review':second}]})
        self.assertFalse(data['_returned_outputs_inconsistent'])
        self.assertEqual(set(data['role_review']['matched_members']), {'a','b'})
        self.assertEqual(data['role_review_declarations'], {'top_level':first, 'returned_output':second})

    def test_review_fields_only_complete_from_actual_other_declaration(self):
        for index, other_check in enumerate([True, None]):
            with self.subTest(other_check=other_check):
                self.work = self.root / f'complementary-review-{index}'
                task = self.start()['background']; self.begin(task)
                first = self.review(task, self.bg, foreground_text_absent=None)
                second = self.review(task, self.bg, foreground_text_absent=other_check)
                response = self.submit(task, self.bg, role_review=first, returned_outputs=[
                    {'file':str(self.bg), 'role_review':second}])
                saved = read_state(self.work)
                self.assertEqual(saved['recovery']['used'], 0)
                if other_check is True:
                    self.assertEqual(saved['tasks'][task['id']]['status'], 'recorded')
                else:
                    self.assertEqual(response['kind'], 'registration_required')
                    self.assertNotIn('result', saved['tasks'][task['id']])

    def test_review_role_hash_and_boolean_contradictions_still_reject(self):
        changes = [{'role':'wordart'}, {'output_sha256':sha256(self.fg)},
                   {'foreground_text_absent':False}]
        for index, changeset in enumerate(changes):
            with self.subTest(changeset=changeset):
                self.work = self.root / f'conflicting-review-{index}'
                task = self.start()['background']; self.begin(task)
                first = self.review(task, self.bg)
                second = {**first, **changeset}
                response = self.submit(task, self.bg, role_review=first, returned_outputs=[
                    {'file':str(self.bg), 'role_review':second}])
                saved = read_state(self.work)
                self.assertEqual(response['kind'], 'paused')
                self.assertNotIn('result', saved['tasks'][task['id']])
                self.assertEqual(saved['recovery']['used'], 0)

    def test_real_duplicate_identifier_remains_rejected(self):
        jobs = self.start(); bg = jobs['background']; self.begin(bg)
        self.submit(bg, self.bg, tool_output_id='actual-provider-output-001')
        fg = jobs['foreground']; self.begin(fg)
        with self.assertRaisesRegex(ValueError, 'identifier.*another task'):
            self.submit(fg, self.fg, tool_output_id='actual-provider-output-001')
        self.assertEqual(controller.run(self.work)['kind'], 'paused')


if __name__ == '__main__':
    import unittest
    unittest.main()
