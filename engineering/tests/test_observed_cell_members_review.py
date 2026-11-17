"""Optional, pixel-observed board identities preserve exact ordered ownership."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

PACKAGE = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import binding, controller, __main__ as cli
from newpath.storage import read_state, sha256
from test_result_role_binding import ReviewedOutputFixture


class ObservedCellMemberReview(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-observed-cell-review-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.output = self.work / 'actual-output.png'
        self.reference = self.work / 'reference.png'
        Image.new('RGBA', (90, 90), 'red').save(self.output)
        Image.new('RGBA', (90, 90), 'blue').save(self.reference)
        self.request = self.work / 'request.json'
        self.request.write_text(json.dumps({'request': {'tool': 'offline-fixture',
            'prompt': 'The locked task prompt.'}}), 'utf-8')
        self.members = [
            {'id': 'b', 'slot': [110, 0, 90, 90]},
            {'id': 'c', 'slot': [0, 110, 90, 90]},
            {'id': 'a', 'slot': [0, 0, 90, 90]},
        ]
        self.task = {'id': 'board-task', 'role': 'board', 'attempt': 0,
            'board': {'members': copy.deepcopy(self.members)},
            'references': {'reference.png': sha256(self.reference)},
            'execution': {'request_file': 'request.json', 'request_sha256': sha256(self.request)}}
        self.state = {'source': {}, 'tasks': {'board-task': self.task}}
        self.review = {'role': 'board', 'output_sha256': sha256(self.output),
            'looked_at_return': True, 'matches_task': True,
            'ordinary_text_removed': True, 'matched_members': ['b', 'c', 'a'],
            'observed': 'Saved pixels show all three assigned subjects in the declared cell order.'}

    def data(self, **extra):
        return {'request_sha256': sha256(self.request), 'tool': 'offline-fixture',
            'role_review': {**copy.deepcopy(self.review), **extra}}

    def validate(self, data=None, *, incomplete=True):
        return binding.validate_return(self.work, self.state, self.task,
            self.data() if data is None else data, self.output, sha256(self.output),
            incomplete_registration=incomplete)

    def test_observed_permutation_is_saved_as_an_independent_ordered_copy(self):
        observed = ['c', 'a', 'b']
        data = self.data(observed_cell_members=observed)
        result = self.validate(data)
        self.assertEqual(result['role_review'].get('observed_cell_members'), observed)
        observed[0] = 'changed'
        self.assertEqual(result['role_review']['observed_cell_members'], ['c', 'a', 'b'])
        self.assertEqual(self.task['board']['members'], self.members)
        self.assertEqual(result['role_review']['matched_members'], ['b', 'c', 'a'])

    def test_invalid_order_maps_require_registration_without_rejecting_the_saved_pixels(self):
        invalid = [True, False, '', 'a,b,c', {}, 1, [], ['a'], ['a', 'b'],
                   ['a', 'b', 'c', 'd'], ['a', 'a', 'b'], ['a', 'b', 'unknown'],
                   ['a', 'b', 3], [['a'], 'b', 'c']]
        original = self.output.read_bytes()
        for incomplete in (False, True):
            for value in invalid:
                with self.subTest(value=value, incomplete=incomplete):
                    with self.assertRaisesRegex(binding.RegistrationRequired, 'observed_cell_members'):
                        self.validate(self.data(observed_cell_members=value), incomplete=incomplete)
                    self.assertNotIn('role_review_rejections', self.task)
                    self.assertEqual(self.output.read_bytes(), original)

    def test_absent_and_explicit_null_keep_the_exact_legacy_normalized_result(self):
        baseline = self.validate()
        explicit_null = self.validate(self.data(observed_cell_members=None))
        self.assertEqual(json.dumps(baseline), json.dumps(explicit_null))
        self.assertNotIn('observed_cell_members', baseline['role_review'])
        self.assertNotIn('observed_cell_members', binding.review_template(self.task))

    def test_hash_and_real_observation_checks_still_precede_optional_order(self):
        bad_request = self.data(observed_cell_members=['c', 'a', 'b'])
        bad_request['request_sha256'] = 'wrong'
        with self.assertRaises(binding.BindingViolation):
            self.validate(bad_request)
        bad_output = self.data(observed_cell_members=['c', 'a', 'b'], output_sha256='wrong')
        with self.assertRaises(binding.BindingViolation):
            self.validate(bad_output)
        for key in ('looked_at_return', 'observed'):
            value = False if key == 'looked_at_return' else ''
            with self.subTest(key=key):
                with self.assertRaises(binding.RegistrationRequired):
                    self.validate(self.data(observed_cell_members=['c', 'a', 'b'], **{key: value}))

    def test_order_map_cannot_erase_a_real_false_or_previous_output_rejection(self):
        with self.assertRaises(binding.RoleMismatch):
            self.validate(self.data(observed_cell_members=['c', 'a', 'b'], matches_task=False))
        with self.assertRaises(binding.RoleMismatch):
            self.validate(self.data(observed_cell_members=['c', 'a', 'b']))
        self.assertTrue(self.task['role_review_rejections'])

    def test_review_evidence_keeps_only_the_bounded_map_and_preserves_declared_false(self):
        source = {'observed_cell_members': ['c', 'a', 'b'], 'matches_task': False}
        evidence = binding.review_evidence(source)
        self.assertEqual(evidence, source)
        self.assertIsNot(evidence['observed_cell_members'], source['observed_cell_members'])
        for invalid in ([str(index) for index in range(101)], ['x' * 2001], [{}]):
            self.assertNotIn('observed_cell_members', binding.review_evidence({'observed_cell_members': invalid}))

    def test_null_optional_map_does_not_add_a_new_field_to_legacy_claim_evidence(self):
        previous = binding.review_evidence(self.review)
        explicit_null = binding.review_evidence({**self.review, 'observed_cell_members': None})
        self.assertEqual(json.dumps(explicit_null), json.dumps(previous))
        self.assertNotIn('observed_cell_members', explicit_null)

    def test_map_merge_is_order_exact_while_legacy_matched_members_remains_a_set(self):
        first = {'matched_members': ['a', 'b', 'c'], 'observed_cell_members': ['c', 'a', 'b']}
        compatible = {'matched_members': ['b', 'c', 'a'], 'observed_cell_members': ['c', 'a', 'b']}
        merged, valid = binding._merge_role_reviews(first, compatible)
        self.assertTrue(valid)
        self.assertEqual(merged, first)
        for altered in (['a', 'c', 'b'], ['c', 'a'], True):
            with self.subTest(altered=altered):
                _, valid = binding._merge_role_reviews(first, {'observed_cell_members': altered})
                self.assertFalse(valid)
        merged, valid = binding._merge_role_reviews({'observed_cell_members': None}, compatible)
        self.assertTrue(valid)
        self.assertEqual(merged['observed_cell_members'], compatible['observed_cell_members'])

    def test_top_and_nested_order_conflicts_remain_inconsistent_and_keep_original_claims(self):
        data = {'file': str(self.output), 'output_count': 1,
            'role_review': {'observed_cell_members': ['c', 'a', 'b']},
            'returned_outputs': [{'file': str(self.output),
                'role_review': {'observed_cell_members': ['a', 'c', 'b']}}]}
        result = binding.normalize_known_returns(data)
        self.assertTrue(result['_returned_outputs_inconsistent'])
        self.assertEqual(result['role_review_declarations'], {
            'top_level': data['role_review'],
            'returned_output': data['returned_outputs'][0]['role_review']})


class ObservedCellReviewNormalCLI(ReviewedOutputFixture):
    def start(self, policy=binding.DELIVERY_POLICY):
        with mock.patch.object(controller, '_runtime'):
            response = controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy=policy,
                recovery_policy='bounded-recovery/1')
        self.jobs = {task['role']: task for task in response['jobs']}
        return self.jobs

    def invoke(self, data):
        path = self.root / 'cli-result.json'
        path.write_text(json.dumps(data, ensure_ascii=False), 'utf-8')
        stdout = io.StringIO()
        with mock.patch('sys.stdout', stdout):
            code = cli.main(['--work', str(self.work), '--input', str(path)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, result)
        return result

    def test_bad_map_is_corrected_on_same_attempt_without_request_or_raw_changes(self):
        task, _ = self.board()
        output = self.root / 'real-existing-return.png'
        image = Image.new('RGBA', (220, 220), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((45, 45, 170, 170), fill='red')
        image.save(output)
        payload = self.payload(task, output)
        payload['role_review']['observed_cell_members'] = ['unknown']
        before = read_state(self.work)
        request = self.work / before['tasks'][task['id']]['execution']['request_file']
        request_bytes = request.read_bytes()
        output_bytes = output.read_bytes()
        pending = self.invoke(payload)
        self.assertEqual(pending['kind'], 'registration_required')
        current = read_state(self.work)
        rejected = current['tasks'][task['id']]
        self.assertEqual(rejected['attempt'], 0)
        self.assertEqual(current['recovery']['used'], 0)
        self.assertNotIn('result', rejected)
        self.assertEqual(rejected['attempts'][-1]['role_review']['observed_cell_members'], ['unknown'])
        payload['role_review']['observed_cell_members'] = ['photo']
        self.invoke(payload)
        saved = read_state(self.work)
        recorded = saved['tasks'][task['id']]
        self.assertEqual(recorded['status'], 'recorded')
        self.assertEqual(recorded['attempt'], 0)
        self.assertEqual(recorded['result']['role_review']['observed_cell_members'], ['photo'])
        self.assertEqual(saved['recovery']['used'], 0)
        self.assertEqual(request.read_bytes(), request_bytes)
        self.assertEqual(output.read_bytes(), output_bytes)
        self.assertEqual(len(recorded['return_bindings']), 1)
        self.assertEqual([entry['outcome'] for entry in recorded['attempts']], ['registration_required', 'recorded'])

    def test_repeated_valid_permutation_cannot_replace_an_already_accepted_map(self):
        layers = self.start()
        self.begin(layers['background'])
        self.submit(layers['background'], self.bg)
        self.begin(layers['foreground'])
        self.submit(layers['foreground'], self.fg)
        reference = self.work / 'two-member-reference.png'
        Image.new('RGBA', (440, 220), 'blue').save(reference)
        items = [{'id': identity, 'kind': 'art', 'box': [x, 200, 180, 180],
                  'description': identity + ' assigned subject'} for identity, x in [('left', 100), ('right', 400)]]
        board = {'id': 'two-member-board', 'signature': 'two-member-fixture',
            'image': str(reference), 'size': [440, 220], 'key': None,
            'background_mode': 'native-transparent-materials/1',
            'grid': {'rows': 1, 'columns': 2, 'margin': 10, 'gap': 20, 'cell_size': [200, 200]},
            'members': [{**item, 'target': item['box'], 'slot': [10 + index * 220, 10, 200, 200]}
                        for index, item in enumerate(items)]}
        with mock.patch.object(controller, 'prepare_boards', return_value=[board]):
            task = controller.run(self.work, data={'kind': 'layout', 'layout': {'items': items}})['jobs'][0]
        self.begin(task)
        output = self.root / 'two-member-existing-return.png'
        image = Image.new('RGBA', (440, 220), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 170, 170), fill='red')
        draw.rectangle((250, 30, 390, 170), fill='green')
        image.save(output)
        payload = self.payload(task, output)
        payload['role_review']['observed_cell_members'] = ['right', 'left']
        self.invoke(payload)
        saved_before = (self.work / 'state.json').read_bytes()
        recorded = read_state(self.work)['tasks'][task['id']]
        self.assertIn('result', recorded)
        result_before = recorded['result']
        self.assertEqual(result_before['role_review'].get('observed_cell_members'), ['right', 'left'])
        payload['role_review']['observed_cell_members'] = ['left', 'right']
        self.invoke(payload)
        self.assertEqual((self.work / 'state.json').read_bytes(), saved_before)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['result'], result_before)


@unittest.skipUnless(os.environ.get('PRK_R18_REVIEW_JOB'), 'actual R18 board job not supplied')
class ActualR18ObservedCellReview(unittest.TestCase):
    def test_actual_fifteen_member_permutation_keeps_original_job_and_raw_bytes(self):
        job = Path(os.environ['PRK_R18_REVIEW_JOB'])
        before = {str(path.relative_to(job)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in job.rglob('*') if path.is_file()}
        original_state = json.loads((job / 'state.json').read_text('utf-8'))
        state = copy.deepcopy(original_state)
        task = next(task for task in state['tasks'].values()
                    if task['role'] == 'board' and len(task['board']['members']) == 15)
        actual_order = ['o005', 'o010', 'o031', 'o015', 'o016', 'o017', 'o018',
                        'o032', 'o033', 'o055', 'o056', 'o057', 'o058', 'o068', 'o070']
        review = copy.deepcopy(task['result']['role_review'])
        review['observed_cell_members'] = actual_order
        output = job / task['result']['raw']
        result = binding.validate_return(job, state, task, {
            'request_sha256': task['execution']['request_sha256'],
            'tool': task['result']['binding_tool'],
            'tool_output_id': task['result']['tool_output_id'], 'role_review': review,
        }, output, sha256(output), incomplete_registration=True)
        self.assertEqual(result['role_review'].get('observed_cell_members'), actual_order)
        self.assertEqual(task['board'], original_state['tasks'][task['id']]['board'])
        after = {str(path.relative_to(job)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in job.rglob('*') if path.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
