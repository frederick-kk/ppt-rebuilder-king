"""Source-first foreground references consume board artwork only, with old jobs intact."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, execution, prompts
from newpath.storage import fingerprint, read_state, save_state, write_json
from test_result_role_binding import ReviewedOutputFixture

POLICY = 'source-first-board-reference/1'


def old_module(name):
    path = PACKAGE.parent / ('source-r16/compat-runtime/newpath/' + name + '.py')
    if not path.is_file():
        raise unittest.SkipTest('Retained R16 source fixture unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r16_scope_' + name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sample_layout():
    return {'items': [
        {'id': 'primary', 'kind': 'art', 'box': [100, 15, 1000, 140],
         'lettering': '完整主标题', 'description': 'Title with its own complete ink arc.'},
        {'id': 'photo', 'kind': 'art', 'box': [400, 300, 180, 180],
         'description': 'Independent photograph with its internal writing.', 'embedded_image': True},
        {'id': 'label', 'kind': 'art', 'box': [100, 500, 240, 60],
         'lettering': '源字样', 'description': 'Complete red plaque and painted backing.'},
        {'id': 'body', 'kind': 'text', 'box': [100, 720, 600, 40],
         'text': 'Ordinary source text.', 'size': 24},
    ], 'background_regions': [
        {'id': 'scenery', 'box': [0, 400, 300, 250], 'description': 'Integrated scene with a person.'}]}


class SourceLayerReferenceScopeController(ReviewedOutputFixture):
    def initialize(self):
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', post_return_prompt_policy='audit-existing-return/1',
                delivery_policy='reviewed-existing-output/1', source_layout_policy='source-layout-first/1')

    def freeze(self, layout=None):
        self.initialize()
        response = controller.run(self.work, data={'kind': 'layout', 'layout': layout or sample_layout()})
        self.jobs = {task['role']: task for task in response['jobs']}
        return response

    def test_new_scope_is_frozen_from_layout_without_removing_primary_from_full_map(self):
        self.freeze()
        state = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        expected = {'policy': POLICY, 'board_member_ids': ['photo', 'label'], 'primary_wordart_id': 'primary'}
        self.assertEqual(marker.get('source_layer_scope_policy'), POLICY)
        self.assertEqual(state.get('source_layer_scope_policy'), POLICY)
        self.assertEqual(state.get('source_layer_scope'), expected)
        self.assertEqual([i['id'] for i in state['scene_allocation']['foreground_regions']],
                         ['primary', 'photo', 'label'])
        for task in state['tasks'].values():
            self.assertEqual(task.get('source_layer_scope'), expected)
            self.assertEqual(task['scene_allocation'], state['scene_allocation'])
        scope_copy = copy.deepcopy(state['source_layer_scope'])
        next(iter(state['tasks'].values()))['source_layer_scope']['board_member_ids'].append('body')
        self.assertEqual(state['source_layer_scope'], scope_copy)

    def test_public_permit_and_pending_scope_use_component_reference_observations(self):
        self.freeze()
        task = self.jobs['foreground']
        text = task['role_review_scope']
        for phrase in ('component-layout references', 'glyph differences alone',
                       'Missing or wrong subjects', 'severe displacement', 'outside this foreground task'):
            self.assertIn(phrase, text)
        self.assertIsNone(task['role_review_template']['matches_task'])
        self.assertIsNone(task['role_review_template']['page_elements_match'])
        permit = self.begin(task)
        self.assertEqual(permit['role_review_scope'], text)
        pending = controller.run(self.work)
        card = next(t for t in pending['pending_results'] if t['id'] == task['id'])
        self.assertEqual(card['role_review_scope'], text)
        self.assertIsNone(permit['result_template'].get('executed_prompt'))

    def test_explicit_false_and_missing_actual_review_still_reject_same_saved_return(self):
        self.freeze()
        self.begin(self.jobs['background'])
        self.submit(self.jobs['background'], self.bg)
        task = self.jobs['foreground']
        self.begin(task)
        with self.assertRaisesRegex(ValueError, 'review|role|subject'):
            self.submit(task, self.fg, role_review=None)
        with self.assertRaisesRegex(ValueError, 'review|role|subject'):
            self.submit(task, self.fg, role_review=self.review(task, self.fg,
                matches_task=False, page_elements_match=False,
                observed='A required board member is absent from its assigned ROI.'))
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'], 'failed')
        self.assertNotIn('done', read_state(self.work))

    def test_minor_reference_glyph_observation_is_preserved_with_a_real_true_review(self):
        self.freeze()
        background = self.jobs['background']
        self.begin(background)
        self.submit(background, self.bg)
        foreground = self.jobs['foreground']
        self.begin(foreground)
        observed = 'Required reference subjects are visible; reference lettering differs from the source.'
        result = self.submit(foreground, self.fg,
            role_review=self.review(foreground, self.fg, observed=observed))
        self.assertEqual(result['kind'], 'generate')
        state = read_state(self.work)
        self.assertEqual(state['tasks'][foreground['id']]['result']['role_review']['observed'], observed)
        self.assertEqual(state['tasks'][foreground['id']]['attempt'], 0)
        self.assertEqual({t['role'] for t in state['tasks'].values()},
                         {'background', 'foreground', 'wordart', 'board'})

    def test_scope_mutation_or_removal_is_rejected_before_begin(self):
        self.freeze()
        original = read_state(self.work)
        for location, change in (
                ('state', 'primary'), ('task', 'primary'), ('state', 'remove'), ('task', 'remove')):
            with self.subTest(location=location, change=change):
                state = copy.deepcopy(original)
                target = state if location == 'state' else next(iter(state['tasks'].values()))
                if change == 'remove':
                    target.pop('source_layer_scope')
                else:
                    target['source_layer_scope']['board_member_ids'] = ['primary', 'photo', 'label']
                save_state(self.work, state, 'private_scope_mutation')
                with self.assertRaisesRegex(ValueError, 'scope|frozen'):
                    controller.run(self.work)
        save_state(self.work, original, 'private_restore')

    def test_older_source_first_initialized_without_scope_policy_does_not_migrate(self):
        self.initialize()
        state = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        state.pop('source_layer_scope_policy', None)
        marker.pop('source_layer_scope_policy', None)
        state.pop('board_reference_policy', None)
        marker.pop('board_reference_policy', None)
        save_state(self.work, state, 'simulate_original_r16_source_first')
        write_json(self.work / 'initialization.json', marker)
        response = controller.run(self.work, data={'kind': 'layout', 'layout': sample_layout()})
        saved = read_state(self.work)
        self.assertNotIn('source_layer_scope', saved)
        previous = old_module('prompts')
        for public in response['jobs']:
            task = saved['tasks'][public['id']]
            self.assertNotIn('source_layer_scope', task)
            mode = {'background_mode': prompts.MATERIAL_MODE} if task['role'] == 'foreground' else {}
            expected = previous.layer_brief(task['role'], saved['source']['transform'],
                decorative_background=True, scene_allocation=saved['scene_allocation'], **mode)
            self.assertEqual((self.work / task['brief']).read_text('utf-8'), expected)

    def test_no_art_or_only_primary_still_creates_no_foreground(self):
        self.initialize()
        description = sample_layout()
        description['items'] = description['items'][:1]
        result = controller.run(self.work, data={'kind': 'layout', 'layout': description})
        self.assertEqual([t['role'] for t in result['jobs']], ['background'])
        self.assertEqual(read_state(self.work).get('source_layer_scope'), {
            'policy': POLICY, 'board_member_ids': [], 'primary_wordart_id': 'primary'})

    def test_non_source_first_initialization_has_no_new_scope_metadata(self):
        self.start()
        self.assertNotIn('source_layer_scope_policy', read_state(self.work))
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertNotIn('source_layer_scope_policy', marker)

    def test_current_source_first_action_exposes_optional_observed_bounds_and_parts(self):
        action = self.initialize()
        text = action['instructions']
        for phrase in ('source_bounds=[left,top,right,bottom]', 'source_parts',
                       'optional name', 'program unions', 'omit target', 'Existing box input remains accepted'):
            self.assertIn(phrase, text)
        before = read_state(self.work)
        correction = controller.run(self.work, data={'kind': 'layout', 'layout': {
            'items': [{'id': 'narrow-line', 'kind': 'text', 'box': [100, 100, 20, 40],
                       'text': 'A line that cannot fit this box.', 'size': 30}],
            'background_regions': []}})
        self.assertEqual(correction['kind'], 'describe')
        self.assertFalse(correction['generate_more'])
        self.assertIn('source_parts', correction['instructions'])
        self.assertEqual(read_state(self.work), before)

    def test_older_source_first_action_instructions_remain_byte_exact_to_r16(self):
        self.initialize()
        state = read_state(self.work)
        state.pop('source_layer_scope_policy')
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        marker.pop('source_layer_scope_policy')
        state.pop('board_reference_policy', None)
        marker.pop('board_reference_policy', None)
        save_state(self.work, state, 'simulate_old_description_action')
        write_json(self.work / 'initialization.json', marker)
        expected = old_module('controller')._source_description_action(self.work, state)['instructions']
        self.assertEqual(controller.run(self.work)['instructions'], expected)
        self.assertNotIn('source_parts', expected)

    def test_interrupted_initialization_keeps_new_scope_policy_and_old_marker_stays_legacy(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture interruption')):
            with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker.get('source_layer_scope_policy'), POLICY)
        with mock.patch.object(controller, '_runtime'):
            response = controller.run(self.work)
        self.assertEqual(response['kind'], 'describe')
        self.assertEqual(read_state(self.work)['source_layer_scope_policy'], POLICY)
        state = read_state(self.work)
        state.pop('source_layer_scope_policy')
        marker.pop('source_layer_scope_policy')
        state.pop('board_reference_policy', None)
        marker.pop('board_reference_policy', None)
        save_state(self.work, state, 'simulate_pre_scope_state')
        write_json(self.work / 'initialization.json', marker)
        self.assertEqual(controller.run(self.work)['kind'], 'describe')
        self.assertNotIn('source_layer_scope_policy', read_state(self.work))

    def test_interrupted_marker_with_unknown_scope_policy_is_rejected_without_creating_state(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture interruption')):
            with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        marker['source_layer_scope_policy'] = 'unsupported-scope'
        write_json(self.work / 'initialization.json', marker)
        with mock.patch.object(controller, '_runtime'), self.assertRaisesRegex(ValueError, 'scope'):
            controller.run(self.work)
        self.assertFalse((self.work / 'state.json').exists())

    def test_empty_art_has_empty_scope_and_no_foreground(self):
        self.initialize()
        description = sample_layout()
        description['items'] = [i for i in description['items'] if i['kind'] != 'art']
        result = controller.run(self.work, data={'kind': 'layout', 'layout': description})
        self.assertEqual([t['role'] for t in result['jobs']], ['background'])
        self.assertEqual(read_state(self.work)['source_layer_scope'], {
            'policy': POLICY, 'board_member_ids': [], 'primary_wordart_id': None})

    def test_new_task_identity_contains_scope_and_original_reference_binding(self):
        self.freeze()
        state = read_state(self.work)
        for task in state['tasks'].values():
            metadata = {key: copy.deepcopy(task[key]) for key in
                        ('background_mode', 'decorative_background_policy', 'scene_allocation', 'source_layer_scope')
                        if key in task}
            identity = {'source_pixel_sha256': state['source']['pixel_sha256'], **metadata,
                        'source_layout_policy': 'source-layout-first/1'}
            text = (self.work / task['brief']).read_text('utf-8')
            self.assertEqual(task['id'], 'task-' + fingerprint([task['role'], identity, text])[:20])
            self.assertEqual(list(task['references']), ['input/working.png'])

    def test_scope_policy_cannot_be_removed_from_only_state_or_only_marker(self):
        self.initialize()
        state = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        for where in ('state', 'marker'):
            altered_state, altered_marker = copy.deepcopy(state), copy.deepcopy(marker)
            (altered_state if where == 'state' else altered_marker).pop('source_layer_scope_policy')
            save_state(self.work, altered_state, 'private_scope_policy_mutation')
            write_json(self.work / 'initialization.json', altered_marker)
            with self.subTest(where=where), self.assertRaisesRegex(ValueError, 'scope'):
                controller.run(self.work)
        save_state(self.work, state, 'private_restore')
        write_json(self.work / 'initialization.json', marker)


class SourceLayerReferenceScopePrompts(unittest.TestCase):
    def setUp(self):
        self.transform = {'offset': [0, 0], 'resized': [1919, 1080]}
        self.allocation = {
            'background_regions': sample_layout()['background_regions'],
            'foreground_regions': [{k: copy.deepcopy(item[k]) for k in
                ('id', 'box', 'description', 'lettering', 'embedded_image') if k in item}
                for item in sample_layout()['items'] if item['kind'] == 'art'],
            'source_pixel_sha256': 'real-frozen-source-identity'}
        self.scope = {'policy': POLICY, 'board_member_ids': ['photo', 'label'],
                      'primary_wordart_id': 'primary'}

    def brief(self, role, **options):
        return prompts.layer_brief(role, self.transform, scene_allocation=self.allocation,
                                   source_layer_scope=self.scope, **options)

    def test_none_scope_with_or_without_map_is_byte_identical_to_r16(self):
        previous = old_module('prompts')
        for role in ('background', 'foreground'):
            for mode in (None, prompts.MATERIAL_MODE):
                for containers in (False, True):
                    for allocation in (None, self.allocation):
                        options = {'background_mode': mode, 'decorative_background': containers,
                                   'scene_allocation': allocation}
                        with self.subTest(role=role, options=options):
                            expected = previous.layer_brief(role, self.transform, **options)
                            self.assertEqual(prompts.layer_brief(role, self.transform, **options), expected)
                            self.assertEqual(prompts.layer_brief(role, self.transform,
                                source_layer_scope=None, **options), expected)

    def test_new_foreground_only_generates_board_members_without_contradictory_old_requirements(self):
        text = self.brief('foreground', background_mode=prompts.MATERIAL_MODE, decorative_background=True)
        for phrase in ('component-layout reference', 'only board_member_ids',
                       'Do not generate the standalone primary title', 'ordinary page text',
                       'complete rectangular images', 'including their image-internal writing',
                       'glyph differences alone do not require another foreground generation'):
            self.assertIn(phrase, text)
        for old in ('Keep ordinary page text', 'Retain every foreground_regions item',
                    'retain their reference appearance in the foreground'):
            self.assertNotIn(old, text)
        self.assertIn('alpha 0', text)
        self.assertIn('one transparent PNG', text)

    def test_background_clears_complete_assigned_backings_while_retaining_unassigned_empty_containers(self):
        text = self.brief('background', decorative_background=True)
        for phrase in ('Remove every foreground_regions item', 'red plaques',
                       'brush grounds', 'badge backplates', 'even when the lettering is removed',
                       'not assigned to foreground artwork', 'native geometry', 'table content'):
            self.assertIn(phrase, text)
        self.assertIn('one opaque PNG', text)
        self.assertIn('primary', text)
        self.assertIn('photo', text)
        self.assertIn('label', text)

    def test_new_scope_retains_alpha_chroma_geometry_and_actual_source_binding(self):
        for containers in (False, True):
            native = self.brief('foreground', background_mode=prompts.MATERIAL_MODE,
                                decorative_background=containers)
            chroma = self.brief('foreground', decorative_background=containers)
            self.assertIn('one transparent PNG', native)
            self.assertIn('alpha 0', native)
            self.assertNotIn('solid magenta', native)
            self.assertIn('uniform RGB(255,0,255)', chroma)
            self.assertIn('one opaque PNG', chroma)
            self.assertIn('real-frozen-source-identity', native)
            self.assertIn('content_size', native)
        before = copy.deepcopy((self.scope, self.allocation))
        first = self.brief('foreground')
        reordered = {k: copy.deepcopy(v) for k, v in reversed(list(self.scope.items()))}
        self.assertEqual(first, prompts.layer_brief('foreground', self.transform,
            scene_allocation=self.allocation, source_layer_scope=reordered))
        self.assertEqual((self.scope, self.allocation), before)

    def test_scope_rejects_wrong_role_or_inconsistent_program_member_ownership(self):
        for role in ('board', 'wordart', 'unknown'):
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.brief(role)
        for ids in (['primary', 'photo', 'label'], ['photo'], ['photo', 'label', 'label']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                prompts.layer_brief('foreground', self.transform, scene_allocation=self.allocation,
                    source_layer_scope={**self.scope, 'board_member_ids': ids})

    def test_short_goals_describe_only_current_consumption_and_complete_backing_removal(self):
        request = {'transparent_background': True, 'references': ['actual-working.png']}
        for role in ('background', 'foreground'):
            task = {'role': role, 'source_layer_scope': copy.deepcopy(self.scope),
                    'scene_allocation': copy.deepcopy(self.allocation),
                    'decorative_background_policy': prompts.BACKGROUND_CONTAINER_POLICY_ID}
            text = execution._invocation_instructions(task, request)
            with self.subTest(role=role):
                if role == 'foreground':
                    for phrase in ('仅生成组件板参考成员', '不生成独立主标题', '普通正文',
                                   '参考字形差异记入observed', '主体缺失', '严重错位', '图内文字'):
                        self.assertIn(phrase, text)
                    self.assertNotIn('保留完整图片、文字、艺术字', text)
                else:
                    self.assertIn('完整红牌', text)
                    self.assertIn('徽章背板', text)
                    self.assertIn('primary', text)
                self.assertIn('photo', text)
                self.assertIn('label', text)
                self.assertIn('原许可范围保持一次调用', text)

    def test_requests_states_and_other_once_fields_are_unchanged_by_scope_guidance(self):
        previous = old_module('execution')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory(prefix='prk-scope-request-') as folder:
            work = Path(folder)
            (work / 'briefs').mkdir()
            source = work / 'working.png'
            source.write_bytes(b'unchanged-reference')
            for role in ('background', 'foreground', 'wordart', 'board'):
                task = {'id': 'task-' + role, 'role': role, 'attempt': 0,
                        'brief': 'briefs/' + role + '.txt', 'source_layer_scope': copy.deepcopy(self.scope),
                        'scene_allocation': copy.deepcopy(self.allocation)}
                (work / task['brief']).write_text('Frozen canonical request.\n', 'utf-8')
                public = {'references': [str(source)], 'size': [1920, 1080],
                          'transparent_background': role != 'background',
                          'result_template': {'executed_prompt': None}, 'failure_template': {}}
                execution.enroll(task)
                request = {'tool': 'actual-unexposed-fixture',
                    'prompt': 'Frozen canonical request.\n', 'references': public['references'],
                    'size': public['size'], 'transparent_background': public['transparent_background'],
                    'requested_outputs': 1, 'tool_arguments': {
                    'transparent_background': public['transparent_background'], 'unexposed': None}}
                old_task, new_task = copy.deepcopy(task), copy.deepcopy(task)
                with mock.patch.object(previous, 'datetime', fixed):
                    old = previous.begin(work, old_task, public, request)
                old_bytes = Path(old['request_file']).read_bytes()
                with mock.patch.object(execution, 'datetime', fixed):
                    new = execution.begin(work, new_task, public, request)
                self.assertEqual(old_bytes, Path(new['request_file']).read_bytes())
                self.assertEqual(old_task, new_task)
                if role not in ('background', 'foreground'):
                    self.assertEqual(old['instructions'], new['instructions'])
                old.pop('instructions')
                new.pop('instructions')
                self.assertEqual(old, new)
                self.assertIsNone(new['result_template']['executed_prompt'])

    def test_actual_r16_layout_derives_24_board_members_and_preserves_original_job(self):
        path = PACKAGE.parent / 'heritage-20261112/evidence/r16-medium-done/job/state.json'
        if not path.exists():
            self.skipTest('Private R16 real job fixture unavailable')
        original = path.read_bytes()
        state = json.loads(original)
        actual = controller._source_layer_scope(state['layout'])
        self.assertEqual(actual['primary_wordart_id'], 'o001')
        self.assertEqual(len(actual['board_member_ids']), 24)
        self.assertNotIn('o001', actual['board_member_ids'])
        self.assertEqual(set(actual['board_member_ids']), {
            member['id'] for task in state['tasks'].values() if task['role'] == 'board'
            for member in task['board']['members']})
        self.assertEqual(len(state['scene_allocation']['foreground_regions']), 25)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
