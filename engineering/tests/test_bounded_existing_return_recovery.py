"""Bounded recovery and saved-return review through the real controller entry."""
import copy
import inspect
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from test_result_role_binding import ReviewedOutputFixture
from newpath import controller as c, recovery
from newpath.storage import read_state, sha256

MODE_V2 = 'bounded-existing-return-recovery/2'


class BoundedExistingReturnRecovery(ReviewedOutputFixture):
    def start_new(self):
        try:
            with mock.patch.object(c, '_runtime'):
                action = c.run(self.work, source=self.source, execution_policy='explicit-start/1',
                    post_return_prompt_policy='audit-existing-return/1',
                    delivery_policy='reviewed-existing-output/1', recovery_policy=MODE_V2,
                    layer_generation_order='background-then-foreground/1')
        except ValueError as error:
            self.fail('The new explicit recovery policy must initialize: ' + str(error))
        self.assertEqual([task['role'] for task in action['jobs']], ['background'])
        return action['jobs'][0]

    def colour(self, name, colour):
        path = self.root / name
        Image.new('RGB', (1920, 1080), colour).save(path)
        return path

    def rejected(self, task, path):
        return self.payload(task, path, role_review=self.review(task, path, matches_task=False,
            observed='Viewed synthetic return is missing its assigned scene subject.'))

    def many(self, task, paths, reviews, invocations=2):
        return self.payload(task, paths[0], invocation_count=invocations, output_count=len(paths),
            returned_files=[str(path) for path in paths],
            returned_outputs=[{'file': str(path), 'role_review': review}
                              for path, review in zip(paths, reviews)])

    def test_background_and_foreground_can_each_receive_one_repair(self):
        bg = self.start_new()
        self.begin(bg)
        repaired = c.run(self.work, data=self.rejected(bg, self.bg))['jobs'][0]
        self.begin(repaired)
        fg = self.submit(repaired, self.colour('bg-fixed.png', 'green'))['jobs'][0]
        self.begin(fg)
        action = self.submit(fg, self.colour('fg-opaque.png', 'red'))
        self.assertEqual(action['kind'], 'generate')
        repaired_fg = action['jobs'][0]
        self.assertEqual(repaired_fg['attempt'], 1)
        self.assertEqual(read_state(self.work)['recovery']['used'], 2)
        self.begin(repaired_fg)
        self.assertEqual(self.submit(repaired_fg, self.fg)['kind'], 'describe')
        self.assertEqual(len(read_state(self.work)['recovery']['entries']), 2)

    def test_one_task_cannot_receive_a_second_automatic_repair(self):
        bg = self.start_new()
        self.begin(bg)
        repaired = c.run(self.work, data=self.rejected(bg, self.bg))['jobs'][0]
        self.begin(repaired)
        failed = self.colour('second-failure.png', 'green')
        action = c.run(self.work, data=self.rejected(repaired, failed))
        self.assertEqual(action['kind'], 'paused')
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)
        self.assertEqual(read_state(self.work)['tasks'][bg['id']]['attempt'], 1)
        before = (self.work / 'state.json').read_bytes()
        self.assertEqual(c.run(self.work)['kind'], 'paused')
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_background_repair_edits_preserved_png_before_original_source(self):
        bg = self.start_new()
        permit = self.begin(bg)
        original_request = Path(permit['request_file']).read_bytes()
        original_refs = copy.deepcopy(read_state(self.work)['tasks'][bg['id']]['references'])
        original_brief = Path(bg['prompt_file']).read_bytes()
        repaired = c.run(self.work, data=self.rejected(bg, self.bg))['jobs'][0]
        saved = read_state(self.work)['tasks'][bg['id']]
        self.assertEqual(len(repaired['references']), len(bg['references']) + 1)
        self.assertEqual(Path(repaired['references'][0]).read_bytes(), self.bg.read_bytes())
        self.assertEqual(repaired['references'][1:], bg['references'])
        self.assertEqual(saved['references_history'], [original_refs])
        self.assertEqual(Path(permit['request_file']).read_bytes(), original_request)
        self.assertEqual(Path(bg['prompt_file']).read_bytes(), original_brief)
        self.assertIn('Image 1 is the saved failed background', Path(repaired['prompt_file']).read_text('utf-8'))
        self.assertIn('Keep already cleared regions empty', Path(repaired['prompt_file']).read_text('utf-8'))
        self.begin(repaired)

    def test_missing_review_keeps_the_same_file_without_spending_recovery(self):
        bg = self.start_new()
        self.begin(bg)
        action = self.submit(bg, self.bg, role_review=None)
        self.assertEqual(action['kind'], 'registration_required')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        self.assertEqual(c.run(self.work)['kind'], 'registration_required')
        self.assertEqual(self.submit(bg, self.bg)['jobs'][0]['role'], 'foreground')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_multiple_reported_invocations_adopt_first_valid_existing_return(self):
        bg = self.start_new()
        self.begin(bg)
        second = self.colour('second.png', 'green')
        data = self.many(bg, [self.bg, second], [
            self.review(bg, self.bg, matches_task=False, observed='Saved first return lacks the scene.'),
            self.review(bg, second)])
        action = c.run(self.work, data=data)
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual(action['jobs'][0]['role'], 'foreground')
        state = read_state(self.work)
        task = state['tasks'][bg['id']]
        self.assertEqual(task['result']['sha256'], sha256(second))
        self.assertEqual(task['surplus_resolution']['selected_index'], 1)
        self.assertEqual(task['surplus_resolution']['invocation_count'], 2)
        self.assertEqual(task['generation_violations'][0]['invocation_count'], 2)
        self.assertTrue(task['generation_violations'][0]['acknowledged'])
        self.assertEqual(state['recovery']['used'], 0)
        self.assertTrue(state['warnings'])
        before = (self.work / 'state.json').read_bytes()
        c.run(self.work, data=data)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_multiple_existing_returns_request_real_reviews_without_calling_again(self):
        bg = self.start_new()
        self.begin(bg)
        second = self.colour('second.png', 'green')
        data = self.many(bg, [self.bg, second], [None, self.review(bg, second)])
        action = c.run(self.work, data=data)
        self.assertEqual(action['kind'], 'registration_required')
        template = action['pending_results'][0]['result_template']
        self.assertEqual(template['invocation_count'], 2)
        self.assertEqual(c.run(self.work)['kind'], 'registration_required')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)
        template['tool'] = 'synthetic-image-tool'
        template['returned_outputs'][0]['role_review'] = self.review(bg, self.bg)
        action = c.run(self.work, data=template)
        self.assertEqual(action['jobs'][0]['role'], 'foreground')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_all_existing_returns_wrong_role_remain_failures_and_use_one_repair(self):
        bg = self.start_new()
        self.begin(bg)
        second = self.colour('second.png', 'green')
        data = self.many(bg, [self.bg, second], [
            self.review(bg, path, matches_task=False, observed='Saved return has the wrong role.')
            for path in [self.bg, second]])
        action = c.run(self.work, data=data)
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual(action['jobs'][0]['role'], 'background')
        self.assertEqual(action['jobs'][0]['attempt'], 1)
        task = read_state(self.work)['tasks'][bg['id']]
        self.assertEqual(len(task['role_review_rejections']), 2)
        self.assertNotIn('result', task)
        self.assertEqual(read_state(self.work)['recovery']['used'], 1)

    def test_unknown_invocation_count_keeps_unknown_when_existing_return_is_usable(self):
        bg = self.start_new()
        self.begin(bg)
        second = self.colour('second.png', 'green')
        data = self.many(bg, [self.bg, second], [self.review(bg, self.bg), self.review(bg, second)],
                         invocations=None)
        action = c.run(self.work, data=data)
        self.assertEqual(action['jobs'][0]['role'], 'foreground')
        task = read_state(self.work)['tasks'][bg['id']]
        self.assertIsNone(task['surplus_resolution']['invocation_count'])
        self.assertIsNone(task['generation_violations'][0]['invocation_count'])
        self.assertEqual(read_state(self.work)['warnings'][-1]['invocation_count_status'], 'unknown')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_unknown_invocations_with_all_wrong_returns_never_dispatch_a_repair(self):
        bg = self.start_new()
        self.begin(bg)
        second = self.colour('second.png', 'green')
        reviews = [self.review(bg, path, matches_task=False, observed='Actual saved return has the wrong role.')
                   for path in [self.bg, second]]
        action = c.run(self.work, data=self.many(bg, [self.bg, second], reviews, invocations=None))
        self.assertEqual(action['kind'], 'paused')
        self.assertEqual(action['recovery_blocker']['code'], 'invocation_count_unknown')
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_source_substitution_in_one_return_cannot_be_ignored(self):
        bg = self.start_new()
        self.begin(bg)
        source = Path(bg['references'][0])
        data = self.many(bg, [source, self.bg], [self.review(bg, source), self.review(bg, self.bg)])
        self.assertEqual(c.run(self.work, data=data)['kind'], 'paused')
        task = read_state(self.work)['tasks'][bg['id']]
        self.assertTrue(task['hard_binding_failure'])
        self.assertNotIn('result', task)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_reported_two_calls_with_one_real_output_can_adopt_that_output(self):
        bg = self.start_new()
        self.begin(bg)
        action = self.submit(bg, self.bg, invocation_count=2, output_count=1)
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual(action['jobs'][0]['role'], 'foreground')
        task = read_state(self.work)['tasks'][bg['id']]
        self.assertEqual(task['surplus_resolution']['invocation_count'], 2)
        self.assertEqual(task['surplus_resolution']['output_count'], 1)
        self.assertTrue(task['generation_violations'][0]['acknowledged'])

    def test_background_edit_reference_cannot_be_replaced_after_preparation(self):
        bg = self.start_new()
        self.begin(bg)
        repaired = c.run(self.work, data=self.rejected(bg, self.bg))['jobs'][0]
        Path(repaired['references'][0]).write_bytes(self.fg.read_bytes())
        with self.assertRaisesRegex(ValueError, 'changed|reference'):
            c.run(self.work)

    def test_new_budget_is_frozen_and_old_policy_is_not_migrated(self):
        self.start_new()
        state = read_state(self.work)
        self.assertEqual(state['recovery'], {'used': 0, 'limit': 2, 'per_task_limit': 1, 'entries': []})
        self.assertEqual(c.run(self.work)['recovery_policy'], MODE_V2)
        with self.assertRaisesRegex(ValueError, 'policy|migrat'):
            c.run(self.work, recovery_policy=recovery.MODE)

    def test_saved_wrong_backgrounds_stop_before_foreground(self):
        configured = os.environ.get('PRK_BACKGROUND_FAILURE_JOB')
        if not configured:
            self.skipTest('Set PRK_BACKGROUND_FAILURE_JOB for a private background diagnostic')
        job = Path(configured)
        if not (job / 'state.json').is_file():
            self.skipTest('Private background diagnostic is unavailable')
        original_hashes = {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()}
        original = json.loads((job / 'state.json').read_text('utf-8'))
        old_bg = next(task for task in original['tasks'].values() if task['role'] == 'background')
        first = job / old_bg['attempts'][0]['raw']
        later = [job / entry['raw'] for entry in old_bg['generation_violations'][0]['preserved_files']]
        self.source = job / original['source']['original']
        bg = self.start_new()
        self.begin(bg)
        observed = old_bg['attempts'][0]['role_review']['observed']
        data = self.payload(bg, first, role_review=self.review(bg, first, matches_task=False, observed=observed))
        repaired = c.run(self.work, data=data)['jobs'][0]
        self.assertEqual(Path(repaired['references'][0]).read_bytes(), first.read_bytes())
        self.begin(repaired)
        reviews = [self.review(repaired, path, matches_task=False,
                    observed='Actual saved background still contains page titles, body text, icons and independent photos.')
                   for path in later]
        action = c.run(self.work, data=self.many(repaired, later, reviews))
        self.assertEqual(action['kind'], 'paused')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 1)
        self.assertEqual(state['tasks'][bg['id']]['attempt'], 1)
        self.assertNotIn('result', state['tasks'][bg['id']])
        fg = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
        self.assertFalse(fg['issued'])
        self.assertEqual(len(list((self.work / 'requests').glob('*.json'))), 2)
        self.assertEqual(original_hashes,
                         {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
