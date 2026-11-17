"""Expected member IDs clarify registration format without certifying pixels."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from test_result_role_binding import ReviewedOutputFixture
from newpath import binding, controller, __main__ as cli
from newpath.storage import read_state, sha256


class BoardReviewRosterTemplate(ReviewedOutputFixture):
    def board_output(self):
        path = self.work / 'board-fixture-return.png'
        image = Image.new('RGBA', (220, 220), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((45, 45, 170, 170), fill='red')
        image.save(path)
        return path

    def task(self):
        return {'role': 'board', 'board': {'members': [
            {'id': identity} for identity in
            ['o005', 'o006', 'o007', 'o008', 'o009', 'o020', 'o057', 'o059']]}}

    def test_complete_board_prefills_only_the_expected_id_roster(self):
        task = self.task()
        review = binding.review_template(task)
        self.assertEqual(review['matched_members'], [member['id'] for member in task['board']['members']])
        self.assertIsNone(review['looked_at_return'])
        self.assertIsNone(review['matches_task'])
        self.assertIsNone(review['ordinary_text_removed'])
        self.assertEqual(review['observed'], '')
        self.assertEqual(review['output_sha256'], '')

    def test_roster_is_a_fresh_list_and_does_not_mutate_frozen_members(self):
        task = self.task()
        before = copy.deepcopy(task)
        first = binding.review_template(task)
        second = binding.review_template(task)
        self.assertIsInstance(first['matched_members'], list)
        self.assertIsNot(first['matched_members'], second['matched_members'])
        first['matched_members'].append('wrong')
        self.assertEqual(task, before)
        self.assertEqual(second['matched_members'], [member['id'] for member in task['board']['members']])

    def test_sparse_legacy_template_retains_unknown_roster(self):
        review = binding.review_template({'role': 'board'})
        self.assertIsNone(review['matched_members'])
        self.assertIsNone(review['ordinary_text_removed'])
        self.assertIsNone(review['looked_at_return'])
        self.assertIsNone(review['matches_task'])

    def test_other_role_templates_keep_their_exact_existing_shape(self):
        checks = {'background': {'foreground_text_absent': None},
                  'foreground': {'page_elements_match': None},
                  'wordart': {'lettering_matches': None, 'only_assigned_title': None}}
        for role, extra in checks.items():
            with self.subTest(role=role):
                self.assertEqual(binding.review_template({'role': role}), {
                    'role': role, 'output_sha256': '', 'looked_at_return': None,
                    'matches_task': None, 'observed': '', **extra})

    def test_prefilled_roster_does_not_register_unobserved_output(self):
        task, _ = self.board()
        output = self.board_output()
        review = copy.deepcopy(self.permits[task['id']]['role_review_template'])
        review['output_sha256'] = sha256(output)
        with self.assertRaisesRegex(ValueError, 'review|role|subject'):
            self.submit(task, output, role_review=review)
        saved = read_state(self.work)['tasks'][task['id']]
        self.assertNotIn('result', saved)
        self.assertEqual(saved['attempt'], 0)

    def test_boolean_missing_unknown_duplicate_and_partial_rosters_still_reject(self):
        task, _ = self.board()
        output = self.board_output()
        for roster in [True, None, [], ['unknown'], ['photo', 'photo'], ['photo', 'unknown']]:
            with self.subTest(roster=roster):
                with self.assertRaisesRegex(ValueError, 'member|review'):
                    self.submit(task, output, role_review=self.review(task, output, matched_members=roster))
                saved = read_state(self.work)['tasks'][task['id']]
                self.assertNotIn('result', saved)
                self.assertEqual(saved['attempt'], 0)

    def test_prefilled_roster_cannot_reverse_a_viewed_false(self):
        task, _ = self.board()
        output = self.board_output()
        with self.assertRaisesRegex(ValueError, 'role|subject'):
            self.submit(task, output, role_review=self.review(task, output, matches_task=False))
        with self.assertRaisesRegex(ValueError, 'rejected|role|subject'):
            self.submit(task, output)
        self.assertNotIn('result', read_state(self.work)['tasks'][task['id']])

    def test_surplus_template_keeps_existing_false_or_partial_declarations(self):
        task, _ = self.board()
        output = self.board_output()
        internal = read_state(self.work)['tasks'][task['id']]
        original_members = copy.deepcopy(internal['board']['members'])
        for declared in [{'matched_members': ['photo'], 'matches_task': False},
                         {'matched_members': [], 'looked_at_return': True},
                         {'matched_members': True}]:
            with self.subTest(declared=declared):
                internal['surplus_registration'] = {'output_count': 1, 'returns': [{
                    'sha256': sha256(output), 'raw': output.name,
                    'tool_output_id': 'existing-output', 'declared_role_review': declared}]}
                response = controller._registration_task(self.work, internal)
                review = response['result_template']['returned_outputs'][0]['role_review']
                for key, value in declared.items():
                    self.assertEqual(review[key], value)
                self.assertEqual(internal['board']['members'], original_members)


@unittest.skipUnless(os.environ.get('PRK_R13_REVIEW_JOB'), 'actual R13 continuation job not supplied')
class ActualR13BoardRosterCLI(ReviewedOutputFixture):
    """Use real saved pixels through the CLI with test-only runtime identity."""
    def invoke(self, *, source=None, payload=None):
        arguments = ['--work', str(self.work)]
        if source is not None:
            arguments.extend(['--source', str(source)])
        if payload is not None:
            path = self.root / 'cli-input.json'
            path.write_text(json.dumps(payload, ensure_ascii=False), 'utf-8')
            arguments.extend(['--input', str(path)])
        output = io.StringIO()
        with mock.patch.object(controller, '_runtime'), mock.patch('sys.stdout', output):
            code = cli.main(arguments)
        response = json.loads(output.getvalue())
        self.assertEqual(code, 0, response)
        return response

    def test_actual_eight_member_template_completes_same_attempt_without_new_image_call(self):
        actual = Path(os.environ['PRK_R13_REVIEW_JOB'])
        state = json.loads((actual / 'state.json').read_text('utf-8'))
        expected_ids = ['o005', 'o006', 'o007', 'o008', 'o009', 'o020', 'o057', 'o059']
        actual_board = next(task for task in state['tasks'].values()
                            if task['role'] == 'board'
                            and [member['id'] for member in task['board']['members']] == expected_ids)
        shutil.copyfile(actual / state['source']['original'], self.source)
        tasks = self.invoke(source=self.source)['jobs']
        for role in ('background', 'foreground'):
            public = next(task for task in tasks if task['role'] == role)
            data = copy.deepcopy(public['begin_template'])
            data['request']['tool'] = 'offline-existing-fixture-no-provider'
            permit = self.invoke(payload=data)
            source_task = next(task for task in state['tasks'].values() if task['role'] == role)
            image = actual / source_task['result']['raw']
            review = copy.deepcopy(source_task['result']['role_review'])
            result = self.invoke(payload={**permit['result_template'], 'file': str(image),
                'tool': data['request']['tool'], 'invocation_count': 1, 'output_count': 1,
                'role_review': review})
        self.assertEqual(result['kind'], 'describe')
        result = self.invoke(payload={'kind': 'layout', 'layout': state['layout']})
        board = next(task for task in result['jobs'] if task['role'] == 'board'
                     and [member['id'] for member in task['members']] == expected_ids)
        begin = copy.deepcopy(board['begin_template'])
        begin['request']['tool'] = 'offline-existing-fixture-no-provider'
        permit = self.invoke(payload=begin)
        self.assertEqual(permit['role_review_template']['matched_members'], expected_ids)
        saved_before = read_state(self.work)
        task_before = saved_before['tasks'][board['id']]
        request_before = (self.work / task_before['execution']['request_file']).read_bytes()
        image = actual / actual_board['result']['raw']
        original_sha = sha256(image)
        observed = copy.deepcopy(permit['role_review_template'])
        observed.update(output_sha256=original_sha, looked_at_return=True, matches_task=True,
            ordinary_text_removed=True,
            observed=actual_board['result']['role_review']['observed'])
        payload = {**permit['result_template'], 'file': str(image),
            'tool': begin['request']['tool'], 'invocation_count': 1, 'output_count': 1,
            'role_review': observed}
        invalid_payload = copy.deepcopy(payload)
        invalid_payload['role_review']['matched_members'] = True
        pending = self.invoke(payload=invalid_payload)
        self.assertEqual(pending['kind'], 'registration_required')
        registration = next(task for task in pending['pending_results'] if task['id'] == board['id'])
        self.assertEqual(registration['role_review_template']['matched_members'], expected_ids)
        corrected = copy.deepcopy(registration['role_review_template'])
        corrected.update(looked_at_return=True, matches_task=True, ordinary_text_removed=True,
                         observed=observed['observed'])
        payload['role_review'] = corrected
        self.invoke(payload=payload)
        saved = read_state(self.work)
        recorded = saved['tasks'][board['id']]
        self.assertEqual(recorded['status'], 'recorded')
        self.assertEqual(recorded['attempt'], 0)
        self.assertEqual(recorded['result']['sha256'], original_sha)
        self.assertEqual(recorded['result']['role_review']['matched_members'], expected_ids)
        self.assertEqual((self.work / recorded['execution']['request_file']).read_bytes(), request_before)
        self.assertEqual(saved['recovery']['used'], saved_before['recovery']['used'])
        self.assertEqual(saved['recovery']['used'], 0)
        before_repeat = (self.work / 'state.json').read_bytes()
        self.invoke(payload=payload)
        self.assertEqual((self.work / 'state.json').read_bytes(), before_repeat)
        self.assertEqual(sha256(image), original_sha)
        self.assertFalse(list(self.work.rglob('*.pptx')))
        self.assertFalse(any(task.get('retry_authorizations') for task in saved['tasks'].values()))
        self.assertEqual([entry['outcome'] for entry in recorded['attempts']],
                         ['registration_required', 'recorded'])
        self.assertTrue(all(entry['attempt'] == 0 for entry in recorded['attempts']))


if __name__ == '__main__':
    unittest.main()
