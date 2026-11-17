"""Explicit direct-source references avoid a foreground generation task."""
import copy
import inspect
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
from PIL import Image, ImageDraw
import numpy as np

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, execution, prompts
from newpath.artwork import extract_assets
from newpath.storage import read_state, save_state, write_json, sha256
from test_result_role_binding import ReviewedOutputFixture
from test_source_layer_reference_scope import sample_layout

POLICY = 'direct-source-board-reference/1'


class DirectSourceBoardsController(ReviewedOutputFixture):
    def initialize(self, *, direct=True):
        self.assertIn('source_board_reference_policy', inspect.signature(controller.run).parameters)
        options = {'source_board_reference_policy': POLICY} if direct else {}
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                source_layout_policy='source-layout-first/1', execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1',
                delivery_policy='reviewed-existing-output/1', **options)

    def plan(self):
        self.initialize()
        result = controller.run(self.work, data={'kind': 'layout', 'layout': sample_layout()})
        self.assertEqual([job['role'] for job in result['jobs']], ['background'])
        background = result['jobs'][0]
        self.begin(background)
        result = self.submit(background, self.bg)
        return result, read_state(self.work)

    def test_explicit_policy_freezes_marker_state_and_requires_source_first(self):
        action = self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker.get('source_board_reference_policy'), POLICY)
        self.assertEqual(read_state(self.work).get('source_board_reference_policy'), POLICY)
        self.assertIn('unchanged source', action['instructions'])

    def test_background_ready_plans_source_wordart_and_boards_without_any_foreground_task(self):
        result, state = self.plan()
        self.assertEqual({task['role'] for task in state['tasks'].values()}, {'background', 'wordart', 'board'})
        self.assertEqual({job['role'] for job in result['jobs']}, {'wordart', 'board'})
        self.assertFalse((self.work / 'layers/foreground.png').exists())
        board_task = next(task for task in state['tasks'].values() if task['role'] == 'board')
        board = board_task['board']
        self.assertEqual(board['source_board_reference_policy'], POLICY)
        self.assertEqual(board['reference_provenance']['kind'], 'unchanged-working-source')
        prompt = (self.work / board_task['brief']).read_text('utf-8')
        self.assertIn('unchanged-source crops', prompt)
        self.assertNotIn('Every drawn reference pixel comes from a newly generated foreground', prompt)
        public = next(job for job in result['jobs'] if job['role'] == 'board')
        permit = self.begin(public)
        self.assertIn('真实原图裁片', permit['instructions'])
        self.assertIn('不能作为生成结果', permit['instructions'])
        primary = next(task for task in state['tasks'].values() if task['role'] == 'wordart')
        self.assertEqual(list(primary['references']), [primary['source_detail']['path'], 'input/working.png'])

    def test_default_source_first_keeps_both_layers_and_omits_direct_policy(self):
        self.initialize(direct=False)
        result = controller.run(self.work, data={'kind': 'layout', 'layout': sample_layout()})
        self.assertEqual({job['role'] for job in result['jobs']}, {'background', 'foreground'})
        self.assertNotIn('source_board_reference_policy', read_state(self.work))

    def test_policy_cannot_be_removed_changed_or_added_to_an_old_job(self):
        self.initialize()
        original = read_state(self.work)
        for value in (None, 'other'):
            changed = copy.deepcopy(original)
            if value is None:
                changed.pop('source_board_reference_policy')
            else:
                changed['source_board_reference_policy'] = value
            save_state(self.work, changed, 'private_policy_corruption')
            with self.assertRaisesRegex(ValueError, 'source.board|direct.source'):
                controller.run(self.work)
        save_state(self.work, original, 'restore')

    def test_old_marker_absence_stays_indirect_after_resuming_description(self):
        self.initialize(direct=False)
        with self.assertRaisesRegex(ValueError, 'source.board|migrat'):
            controller.run(self.work, source_board_reference_policy=POLICY)
        self.assertNotIn('source_board_reference_policy', read_state(self.work))

    def test_unknown_policy_and_missing_source_first_do_not_create_a_job(self):
        self.assertIn('source_board_reference_policy', inspect.signature(controller.run).parameters)
        for options in ({'source_board_reference_policy': 'other', 'source_layout_policy': 'source-layout-first/1'},
                        {'source_board_reference_policy': POLICY}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'source.board|source.first'):
                controller.run(self.work, source=self.source, **options)
        self.assertFalse(self.work.exists())

    def test_interrupted_initialization_resumes_the_same_explicit_policy(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.initialize()
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work)
        self.assertEqual(action['kind'], 'describe')
        self.assertEqual(read_state(self.work)['source_board_reference_policy'], POLICY)

    def test_only_primary_still_uses_background_and_source_without_unused_boards(self):
        self.initialize()
        description = sample_layout()
        description['items'] = description['items'][:1]
        result = controller.run(self.work, data={'kind': 'layout', 'layout': description})
        self.assertEqual([job['role'] for job in result['jobs']], ['background'])
        self.begin(result['jobs'][0])
        next_action = self.submit(result['jobs'][0], self.bg)
        self.assertEqual([job['role'] for job in next_action['jobs']], ['wordart'])

    def test_direct_reference_cannot_be_submitted_or_reencoded_as_generated_output(self):
        result, state = self.plan()
        public = next(job for job in result['jobs'] if job['role'] == 'board')
        board = state['tasks'][public['id']]['board']
        self.begin(public)
        reference = Path(board['image'])
        reencoded = self.root / 'reencoded-input.png'
        with Image.open(reference) as image:
            image.convert('RGBA').save(reencoded, compress_level=0)
        for path in (reference, reencoded):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'reference input'):
                self.submit(public, path)
        self.assertNotIn('result', read_state(self.work)['tasks'][public['id']])

    def test_frozen_direct_board_metadata_cannot_be_rewritten(self):
        _, state = self.plan()
        board = next(task['board'] for task in state['tasks'].values() if task['role'] == 'board')
        board['members'][0]['box'][0] += 1
        save_state(self.work, state, 'private_metadata_corruption')
        with self.assertRaisesRegex(ValueError, 'direct.source board'):
            controller.run(self.work)

    def test_normal_controller_compiles_permuted_board_without_moving_frozen_references(self):
        self.initialize()
        description = {'items': [
            {'id': 'a', 'kind': 'art', 'box': [100, 200, 100, 100], 'description': 'Blue circular icon'},
            {'id': 'b', 'kind': 'art', 'box': [400, 200, 100, 100], 'description': 'Green circular icon'}],
            'background_regions': []}
        action = controller.run(self.work, data={'kind': 'layout', 'layout': description})
        self.begin(action['jobs'][0])
        action = self.submit(action['jobs'][0], self.bg)
        public = action['jobs'][0]
        self.assertEqual(public['role'], 'board')
        state = read_state(self.work)
        frozen = copy.deepcopy(state['tasks'][public['id']]['board'])
        output = self.root / 'actual-permuted.png'
        image = Image.new('RGBA', tuple(frozen['size']), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        ordered = sorted(frozen['members'], key=lambda member: (member['slot'][1], member['slot'][0]))
        observed = [member['id'] for member in reversed(ordered)]
        for member, identity in zip(ordered, observed):
            x, y, width, height = member['slot']
            color = 'blue' if identity == 'a' else 'green'
            draw.ellipse((x + width*.3, y + height*.3, x + width*.7, y + height*.7), fill=color)
        image.save(output)
        self.begin(public)
        review = self.review(public, output, observed_cell_members=observed)
        receipt = self.submit(public, output, role_review=review)
        self.assertEqual(receipt['kind'], 'done')
        self.assertEqual(receipt['skipped'], [])
        self.assertEqual(receipt['quality'], 'not_evaluated')
        saved = read_state(self.work)
        task = saved['tasks'][public['id']]
        self.assertEqual(task['board'], frozen)
        self.assertEqual(task['extraction']['observed_cell_members'], observed)
        self.assertEqual(task['extraction']['request_sha256'], task['result']['request_sha256'])
        self.assertEqual(task['extraction']['output_sha256'], sha256(output))
        for asset in saved['assets']:
            original = next(member for member in frozen['members'] if member['id'] == asset['id'])
            self.assertEqual(asset['planned_slot'], original['slot'])
            self.assertEqual(asset.get('authored_target', asset['target']), original['target'])
            with Image.open(asset['path']) as cropped:
                pixels = np.asarray(cropped.convert('RGBA'))
                opaque = pixels[pixels[..., 3] > 0]
                expected = [0, 0, 255] if asset['id'] == 'a' else [0, 128, 0]
                self.assertTrue(np.all(opaque[:, :3] == expected))


class ObservedCellsProcessing(unittest.TestCase):
    def fixture(self):
        board = {'members': [
            {'id': 'a', 'slot': [10, 10, 100, 100], 'box': [100, 100, 50, 50],
             'target': [100, 100, 50, 50], 'context_exclusions': [{'id': 'b'}]},
            {'id': 'b', 'slot': [120, 10, 100, 100], 'box': [300, 100, 60, 60],
             'target': [300, 100, 60, 60]},
        ], 'image': 'unchanged-source-reference.png'}
        return {'role': 'board', 'board': board, 'result': {'role_review': {}}}

    def test_none_observation_uses_the_original_board_without_any_change(self):
        task = self.fixture()
        self.assertTrue(hasattr(controller, '_observed_board'))
        self.assertIs(controller._observed_board(task), task['board'])
        task['result']['role_review']['observed_cell_members'] = None
        self.assertIs(controller._observed_board(task), task['board'])

    def test_observed_permutation_changes_only_slots_and_keeps_source_reference_metadata(self):
        task = self.fixture()
        task['result']['role_review']['observed_cell_members'] = ['b', 'a']
        before = copy.deepcopy(task)
        self.assertTrue(hasattr(controller, '_observed_board'))
        effective = controller._observed_board(task)
        self.assertEqual([m['slot'] for m in effective['members']], [[120, 10, 100, 100], [10, 10, 100, 100]])
        for actual, original in zip(effective['members'], task['board']['members']):
            self.assertEqual({k:v for k,v in actual.items() if k != 'slot'},
                             {k:v for k,v in original.items() if k != 'slot'})
        self.assertEqual(effective['image'], task['board']['image'])
        self.assertEqual(task, before)

    def test_invalid_saved_map_is_rejected_before_extraction(self):
        for value in (True, [], ['a', 'a'], ['a', 'unknown'], ['a'], ['a', 1]):
            task = self.fixture()
            task['result']['role_review']['observed_cell_members'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'complete unique'):
                controller._observed_board(task)

    def test_default_and_none_board_briefs_and_goals_match_retained_r18_exactly(self):
        old_runtime = PACKAGE.parent / 'source-r18/compat-runtime/newpath'
        if not old_runtime.is_dir():
            self.skipTest('Retained previous runtime is unavailable in this development checkout')
        modules = {}
        for name in ('prompts', 'execution'):
            spec = importlib.util.spec_from_file_location('newpath.r18_direct_guard_' + name,
                                                         old_runtime / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
        board = self.fixture()['board']
        board.update(size=[240, 120], key='magenta')
        for member in board['members']:
            member['description'] = 'Assigned complete subject'
        for mode in (None, 'native-transparent-materials/1'):
            for owned in (None, prompts.BOARD_REFERENCE_POLICY):
                for null_field in (False, True):
                    sample = copy.deepcopy(board)
                    if mode is not None:
                        sample['background_mode'] = mode
                    if owned is not None:
                        sample['board_reference_policy'] = owned
                        sample['members'][0]['embedded_image'] = True
                    if null_field:
                        sample['source_board_reference_policy'] = None
                    with self.subTest(mode=mode, owned=owned, null_field=null_field):
                        self.assertEqual(prompts.board_brief(sample), modules['prompts'].board_brief(sample))
                        task = {'role': 'board', 'board': sample}
                        request = {'transparent_background': mode is not None,
                                   'references': ['board.png', 'input/working.png']}
                        before = copy.deepcopy((task, request))
                        self.assertEqual(execution._invocation_instructions(task, request),
                                         modules['execution']._invocation_instructions(task, request))
                        self.assertEqual((task, request), before)

    def test_actual_r18_permutation_recovers_same_physical_pixels_and_unsafe_board_stays_unsafe(self):
        fixture = os.environ.get('PRK_R18_BOARD_JOB')
        if not fixture:
            self.skipTest('Set PRK_R18_BOARD_JOB for the retained real-output replay')
        import tempfile
        job = Path(fixture)
        before = {str(path.relative_to(job)): sha256(path) for path in job.rglob('*') if path.is_file()}
        state = read_state(job)
        board_task = copy.deepcopy(next(task for task in state['tasks'].values()
                                       if len(task.get('board', {}).get('members', [])) == 15))
        board_task['board']['image'] = str(job / 'boards' / Path(board_task['board']['image']).name)
        board_task['result']['role_review']['observed_cell_members'] = [
            'o005', 'o010', 'o031', 'o015', 'o016', 'o017', 'o018', 'o032', 'o033',
            'o055', 'o056', 'o057', 'o058', 'o068', 'o070']
        original_owner = {tuple(member['slot']): member['id'] for member in board_task['board']['members']}
        with tempfile.TemporaryDirectory(prefix='prk-observed-cell-replay-') as temporary:
            effective = controller._observed_board(board_task)
            result = extract_assets(effective, job / board_task['result']['raw'], Path(temporary) / 'safe')
            self.assertEqual(len(result['assets']), 15)
            self.assertEqual(result['cell_mapping']['unsafe_members'], [])
            actual_slots = {member['id']: tuple(member['slot']) for member in effective['members']}
            for asset in result['assets']:
                previous_id = original_owner[actual_slots[asset['id']]]
                previous = next(item for item in state['assets'] if item['id'] == previous_id)
                old_path = job / 'assets' / Path(previous['path']).parent.name / Path(previous['path']).name
                with Image.open(asset['path']) as current, Image.open(old_path) as old:
                    self.assertEqual(current.convert('RGBA').tobytes(), old.convert('RGBA').tobytes())
            unsafe_task = copy.deepcopy(next(task for task in state['tasks'].values()
                                            if len(task.get('board', {}).get('members', [])) == 9))
            unsafe_task['board']['image'] = str(job / 'boards' / Path(unsafe_task['board']['image']).name)
            unsafe_task['result']['role_review']['observed_cell_members'] = [
                member['id'] for member in sorted(unsafe_task['board']['members'],
                                                  key=lambda member: (member['slot'][1], member['slot'][0]))]
            rejected = extract_assets(controller._observed_board(unsafe_task),
                                      job / unsafe_task['result']['raw'], Path(temporary) / 'unsafe')
            self.assertEqual(len(rejected['assets']), 0)
            self.assertEqual(len(rejected['cell_mapping']['unsafe_members']), 9)
        self.assertEqual({str(path.relative_to(job)): sha256(path)
                          for path in job.rglob('*') if path.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
