"""An optional scene choice controls the existing primary-art route without loss."""
import ast
import copy
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, wordart
from newpath.storage import read_state, save_state, sha256
from test_result_role_binding import ReviewedOutputFixture


def scene():
    return {'items': [
        {'id': 'heading', 'kind': 'text', 'box': [100, 20, 1000, 90],
         'text': '普通字体标题', 'size': 56, 'bold': True},
        {'id': 'gold', 'kind': 'art', 'box': [100, 150, 400, 80],
         'description': 'Gold raised prefix with its original gradient and shadow', 'lettering': '示范前缀：'},
        {'id': 'small', 'kind': 'art', 'box': [100, 350, 180, 50],
         'description': 'Small original painted label', 'lettering': '小标签'},
        {'id': 'icon', 'kind': 'art', 'box': [600, 350, 80, 80],
         'description': 'Blue location icon'}], 'background_regions': []}


def retained_module(name):
    path = PACKAGE.parent / 'source-r21/compat-runtime/newpath' / (name + '.py')
    if not path.is_file():
        raise unittest.SkipTest('Retained frozen R21 fixture is unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r21_primary_override_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrimaryWordartSelection(unittest.TestCase):
    def arts(self):
        return [item for item in scene()['items'] if item['kind'] == 'art']

    def assert_optional_interface(self):
        self.assertIn('primary_id', inspect.signature(wordart.select_primary_wordart).parameters)

    def test_default_outputs_and_order_match_frozen_r21(self):
        old = retained_module('wordart')
        arts = self.arts()
        for sample in ([], arts, list(reversed(arts)), [arts[1], {**arts[0], 'target': [10, 10, 180, 50]}]):
            with self.subTest(sample=sample):
                self.assertEqual(wordart.select_primary_wordart(sample), old.select_primary_wordart(sample))

    def test_explicit_null_keeps_every_original_art_and_target_as_deep_copies(self):
        self.assert_optional_interface()
        arts = self.arts()
        original = copy.deepcopy(arts)
        primary, remaining = wordart.select_primary_wordart(arts, primary_id=None)
        self.assertIsNone(primary)
        self.assertEqual(remaining, original)
        remaining[0]['box'][0] += 1
        self.assertEqual(arts, original)

    def test_explicit_id_selects_nonlargest_without_reordering_other_art(self):
        self.assert_optional_interface()
        arts = self.arts()
        original = copy.deepcopy(arts)
        primary, remaining = wordart.select_primary_wordart(arts, primary_id='small')
        self.assertEqual(primary, original[1])
        self.assertEqual(remaining, [original[0], original[2]])
        primary['box'][0] += 1
        self.assertEqual(arts, original)

    def test_explicit_id_requires_a_real_eligible_written_art(self):
        self.assert_optional_interface()
        arts = self.arts() + [
            {'id': 'photo', 'box': [0, 0, 500, 500], 'embedded_image': True, 'lettering': 'internal'},
            {'id': 'blank', 'box': [0, 0, 100, 100], 'lettering': '  '}]
        for invalid in ('missing', 'icon', 'photo', 'blank', '', False, 0, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                wordart.select_primary_wordart(arts, primary_id=invalid)

    def test_all_geometry_alpha_and_extraction_function_asts_stay_frozen(self):
        old_path = PACKAGE.parent / 'source-r21/compat-runtime/newpath/wordart.py'
        if not old_path.is_file():
            self.skipTest('Retained frozen R21 fixture is unavailable')
        functions = lambda path: {node.name: ast.dump(node) for node in ast.parse(path.read_text('utf-8')).body
                                  if isinstance(node, ast.FunctionDef)}
        old, current = functions(old_path), functions(PACKAGE / 'compat-runtime/newpath/wordart.py')
        self.assertEqual({name: node for name, node in current.items() if name != 'select_primary_wordart'},
                         {name: node for name, node in old.items() if name != 'select_primary_wordart'})


class PrimaryWordartOverrideController(ReviewedOutputFixture):
    def initialize(self, *, direct=True):
        options = {'source_board_reference_policy': 'direct-source-board-reference/1'} if direct else {}
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                source_layout_policy='source-layout-first/1', **options)

    def plan(self, layout, *, bare=False):
        self.initialize()
        action = controller.run(self.work, data=layout if bare else {'kind': 'layout', 'layout': layout})
        self.assertEqual([job['role'] for job in action['jobs']], ['background'])
        self.begin(action['jobs'][0])
        return self.submit(action['jobs'][0], self.bg), read_state(self.work)

    def test_null_routes_all_art_including_gold_into_boards_without_wordart(self):
        description = {**scene(), 'primary_wordart_id': None}
        action, state = self.plan(description)
        self.assertIsNone(state['layout']['primary_wordart_id'])
        self.assertIsNone(state['source_layer_scope']['primary_wordart_id'])
        self.assertEqual(state['source_layer_scope']['board_member_ids'], ['gold', 'small', 'icon'])
        self.assertEqual({job['role'] for job in action['jobs']}, {'board'})
        self.assertFalse(any(task['role'] == 'wordart' for task in state['tasks'].values()))
        members = [member for task in state['tasks'].values() if task['role'] == 'board'
                   for member in task['board']['members']]
        self.assertEqual(sorted(member['id'] for member in members), ['gold', 'icon', 'small'])
        gold = next(member for member in members if member['id'] == 'gold')
        self.assertEqual(gold['lettering'], '示范前缀：')
        self.assertEqual(gold['target'], [100, 150, 400, 80])

    def test_bare_layout_with_null_uses_the_same_existing_description_entry(self):
        action, state = self.plan({**scene(), 'primary_wordart_id': None}, bare=True)
        self.assertIsNone(state['source_layer_scope']['primary_wordart_id'])
        self.assertEqual({job['role'] for job in action['jobs']}, {'board'})

    def test_explicit_nonlargest_choice_is_consistent_in_scope_primary_and_boards(self):
        action, state = self.plan({**scene(), 'primary_wordart_id': 'small'})
        self.assertEqual(state['source_layer_scope']['primary_wordart_id'], 'small')
        primary = next(task for task in state['tasks'].values() if task['role'] == 'wordart')
        self.assertEqual(primary['member']['id'], 'small')
        members = [member['id'] for task in state['tasks'].values() if task['role'] == 'board'
                   for member in task['board']['members']]
        self.assertEqual(members, state['source_layer_scope']['board_member_ids'])
        self.assertEqual(set(members), {'gold', 'icon'})

    def test_invalid_choice_keeps_same_describe_and_creates_no_tasks(self):
        self.initialize()
        for invalid in ('heading', 'icon', 'missing', False):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                controller.run(self.work, data={'kind': 'layout', 'layout': {**scene(), 'primary_wordart_id': invalid}})
            state = read_state(self.work)
            self.assertEqual(state['phase'], 'describe')
            self.assertEqual(state['tasks'], {})

    def test_null_with_only_native_heading_reaches_real_native_compilation(self):
        self.initialize()
        layout = {'items': [scene()['items'][0]], 'background_regions': [], 'primary_wordart_id': None}
        prepared = controller.run(self.work, data={'kind': 'layout', 'layout': layout})
        self.begin(prepared['jobs'][0])
        receipt = self.submit(prepared['jobs'][0], self.bg)
        self.assertEqual(receipt['kind'], 'done')
        self.assertEqual(receipt['skipped'], [])
        state = read_state(self.work)
        self.assertEqual([task['role'] for task in state['tasks'].values()], ['background'])
        self.assertEqual(state['assets'], [])

    def test_indirect_foreground_decision_also_honors_explicit_null(self):
        self.initialize(direct=False)
        layout = scene()
        layout['items'] = [layout['items'][1]]
        layout['primary_wordart_id'] = None
        prepared = controller.run(self.work, data={'kind': 'layout', 'layout': layout})
        self.assertEqual({job['role'] for job in prepared['jobs']}, {'background', 'foreground'})

    def test_default_task_identity_and_requests_match_frozen_r21_on_same_platform(self):
        old = retained_module('controller')
        self.initialize()
        controller.run(self.work, data={'kind': 'layout', 'layout': scene()})
        base = read_state(self.work)
        old_state, current_state = copy.deepcopy(base), copy.deepcopy(base)
        old_state['tasks'], current_state['tasks'] = {}, {}
        old._create_layer_tasks(self.work, old_state)
        controller._create_layer_tasks(self.work, current_state)
        self.assertEqual(current_state['tasks'], old_state['tasks'])
        self.assertEqual(controller._source_layer_scope(base['layout']), old._source_layer_scope(base['layout']))
        for state in (old_state, current_state):
            next(iter(state['tasks'].values()))['result'] = {'normalized': str(self.bg)}
        with mock.patch.object(old, 'save_state'), mock.patch.object(controller, 'save_state'):
            old._create_art_tasks(self.work, old_state, base['layout'])
            controller._create_art_tasks(self.work, current_state, base['layout'])
        self.assertEqual(current_state['tasks'], old_state['tasks'])
        self.assertNotIn('primary_wordart_id', current_state['layout'])
        for ident, task in current_state['tasks'].items():
            self.assertEqual(controller._public_task(self.work, task), old._public_task(self.work, old_state['tasks'][ident]))

    def test_descriptor_guidance_only_adds_optional_selection_and_keeps_frozen_choice(self):
        old = retained_module('controller')
        self.initialize()
        state = read_state(self.work)
        guidance = controller._source_description_action(self.work, state)['instructions']
        background_input_guidance = (
            'For each background_regions entry, prefer source_bounds=[left,top,right,bottom]. '
            'Existing box=[x,y,width,height] uses width = right - left and height = bottom - top. ')
        selection_guidance = (
            ' Ordinary headings expressible with supported native font styles remain native text. '
            'When a line has a separable ordinary-font section and a complex metallic or shaded section, '
            'register the ordinary section as native text and the complex section as artwork with its real effects. '
            'At the layout root primary_wordart_id is optional. null keeps every artwork on component boards. '
            'An eligible artwork ID selects that one standalone task. Omission keeps the largest written-art rule.')
        self.assertEqual(guidance.count(background_input_guidance), 1)
        self.assertEqual(guidance.count(selection_guidance), 1)
        normalized = guidance.replace(background_input_guidance, '').replace(selection_guidance, '')
        self.assertEqual(normalized, old._source_description_action(self.work, state)['instructions'])
        controller.run(self.work, data={'kind': 'layout', 'layout': {**scene(), 'primary_wordart_id': None}})
        frozen = read_state(self.work)
        frozen['layout']['primary_wordart_id'] = 'gold'
        save_state(self.work, frozen, 'private_tamper')
        with self.assertRaisesRegex(ValueError, 'frozen|changed'):
            controller.run(self.work)


class ActualUserPrimaryPlan(unittest.TestCase):
    def test_explicit_null_keeps_all_fifteen_real_art_members_and_the_gold_group(self):
        fixture = os.environ.get('PRK_R21_USER_JOB')
        if not fixture:
            self.skipTest('Set PRK_R21_USER_JOB for the retained actual layout planning replay')
        original = Path(fixture)
        before = {str(path.relative_to(original)): sha256(path) for path in original.rglob('*') if path.is_file()}
        state = read_state(original)
        with tempfile.TemporaryDirectory(prefix='prk-real-primary-null-') as temporary:
            work = Path(temporary) / 'plan'
            work.mkdir()
            shutil.copytree(original / 'input', work / 'input')
            state = copy.deepcopy(state)
            state['tasks'] = {}
            state['warnings'] = []
            state['layout']['primary_wordart_id'] = None
            controller._create_art_tasks(work, state, state['layout'])
            self.assertFalse(any(task['role'] == 'wordart' for task in state['tasks'].values()))
            boards = [task for task in state['tasks'].values() if task['role'] == 'board']
            members = [member for task in boards for member in task['board']['members']]
            original_art = [item for item in state['layout']['items'] if item['kind'] == 'art']
            self.assertEqual(set(member['id'] for member in members), set(item['id'] for item in original_art))
            gold = next(member for member in members if member['id'] == 'o004')
            self.assertEqual(gold['lettering'], '落地尝试一：')
            self.assertEqual(gold['box'], [238, 112, 344, 78])
            self.assertEqual(gold['target'], [238, 112, 344, 78])
            print(json.dumps({'offline_actual_user_plan': True, 'art_members': len(members),
                              'board_count': len(boards), 'standalone_wordart_tasks': 0,
                              'gold_group_retained': True, 'image_calls': 0}, ensure_ascii=True))
        self.assertEqual({str(path.relative_to(original)): sha256(path)
                          for path in original.rglob('*') if path.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
