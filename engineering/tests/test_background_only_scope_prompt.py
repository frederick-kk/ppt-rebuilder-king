"""Scoped background briefs expose only background detail and clearing geometry."""
import ast
import copy
from datetime import datetime, timezone
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import execution, prompts

MARKER = 'Frozen source layer allocation and component-reference scope follow.\n'


def retained(name):
    path = PACKAGE.parent / 'source-r22/compat-runtime/newpath' / (name + '.py')
    if not path.is_file():
        raise unittest.SkipTest('Retained frozen R22 module unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r22_background_focus_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackgroundOnlyScopePrompt(unittest.TestCase):
    def setUp(self):
        self.transform = {'offset': [0, 0], 'resized': [1919, 1080]}
        self.allocation = {
            'background_regions': [
                {'id': 'scene', 'box': [0, 0, 1920, 1080],
                 'description': 'Blue landscape and its intrinsic fixed brand ACME.'},
                {'id': 'flat-backplate', 'box': [40, 330, 100, 93],
                 'description': 'Empty pale blue flat icon backplate with its original shadow.'},
            ],
            'foreground_regions': [
                {'id': 'title', 'box': [238, 112, 344, 78], 'presentation': 'cutout',
                 'description': 'DELETE_DETAIL_METALLIC_GRADIENT_AND_SHADOW',
                 'lettering': 'DELETE_LETTERING_SOURCE_TITLE'},
                {'id': 'photo', 'box': [580, 230, 402, 215], 'presentation': 'rectangle',
                 'embedded_image': True, 'description': 'DELETE_DETAIL_MEETING_PHOTOGRAPH'},
                {'id': 'icon', 'box': [61, 350, 52, 55], 'presentation': 'cutout',
                 'description': 'DELETE_DETAIL_BLUE_ICON_OVER_EMPTY_BACKPLATE'},
            ],
            'source_pixel_sha256': 'actual-unchanged-source-sha',
        }
        self.scope = {'policy': prompts.SOURCE_LAYER_SCOPE_POLICY,
                      'board_member_ids': ['photo', 'icon'], 'primary_wordart_id': 'title'}

    def options(self, **extra):
        return {'scene_allocation': self.allocation, 'source_layer_scope': self.scope, **extra}

    def compact(self):
        return {
            'background_regions': copy.deepcopy(self.allocation['background_regions']),
            'foreground_regions': [
                {key: copy.deepcopy(region[key]) for key in ('id', 'box', 'presentation', 'embedded_image') if key in region}
                for region in self.allocation['foreground_regions']],
            'source_pixel_sha256': self.allocation['source_pixel_sha256'],
        }

    def test_background_single_goal_precedes_every_policy_and_payload(self):
        text = prompts.layer_brief('background', self.transform, **self.options(decorative_background=True))
        self.assertTrue(text.startswith('Background task: Generate only the empty background layer.'))
        self.assertNotIn(prompts.EMBEDDED_IMAGE_POLICY, text)
        self.assertNotIn('Preserve each as one complete rectangular image', text)
        for phrase in ('ordinary page text', 'independent content photos', 'complete group',
                       'one opaque PNG', '[x,y,width,height]', '1920x1080 working pixels'):
            self.assertIn(phrase, text)

    def test_background_payload_keeps_background_detail_scope_source_and_every_clear_box(self):
        before = copy.deepcopy((self.allocation, self.scope, self.transform))
        text = prompts.layer_brief('background', self.transform, **self.options())
        payload = json.loads(text.split(MARKER, 1)[1])
        self.assertEqual(payload, {'scene_allocation': self.compact(), 'source_layer_scope': self.scope})
        for region in self.allocation['foreground_regions']:
            actual = next(row for row in payload['scene_allocation']['foreground_regions'] if row['id'] == region['id'])
            self.assertEqual(actual['box'], region['box'])
            self.assertNotIn('description', actual)
            self.assertNotIn('lettering', actual)
        self.assertEqual((self.allocation, self.scope, self.transform), before)

    def test_cleared_art_appearance_and_lettering_are_absent_from_background_text(self):
        text = prompts.layer_brief('background', self.transform, **self.options())
        for region in self.allocation['foreground_regions']:
            self.assertNotIn(region['description'], text)
            if 'lettering' in region:
                self.assertNotIn(region['lettering'], text)
        for region in self.allocation['background_regions']:
            self.assertIn(region['description'], text)

    def test_fixed_background_backplate_and_brand_take_precedence_over_generic_geometry(self):
        text = prompts.layer_brief('background', self.transform, **self.options(decorative_background=True))
        for phrase in ('empty icon backplates', 'fixed brand marks',
                       'For background-assigned empty containers and native geometry, the background_regions assignment takes precedence',
                       'only when not assigned to background_regions'):
            self.assertIn(phrase, text)
        payload = json.loads(text.split(MARKER, 1)[1])['scene_allocation']
        self.assertEqual(payload['background_regions'][1], self.allocation['background_regions'][1])
        self.assertEqual(payload['foreground_regions'][-1]['id'], 'icon')

    def test_projection_is_independent_and_does_not_require_new_source_fields(self):
        helper = getattr(prompts, '_background_layer_allocation', None)
        self.assertTrue(callable(helper), 'A shared background-only allocation projection is required.')
        before = copy.deepcopy(self.allocation)
        compact = helper(self.allocation)
        compact['background_regions'][0]['box'][0] = 99
        compact['foreground_regions'][0]['box'][0] = 99
        self.assertEqual(self.allocation, before)
        legacy = {key: value for key, value in self.allocation.items() if key != 'source_pixel_sha256'}
        self.assertNotIn('source_pixel_sha256', helper(legacy))

    def test_all_unscoped_layers_and_scoped_foreground_outputs_equal_frozen_r22(self):
        old = retained('prompts')
        for mode in (None, prompts.MATERIAL_MODE):
            for containers in (False, True):
                for role in ('background', 'foreground'):
                    for allocation in (None, self.allocation):
                        options = {'background_mode': mode, 'decorative_background': containers,
                                   'scene_allocation': allocation}
                        self.assertEqual(prompts.layer_brief(role, self.transform, **options),
                                         old.layer_brief(role, self.transform, **options))
                options = self.options(background_mode=mode, decorative_background=containers)
                self.assertEqual(prompts.layer_brief('foreground', self.transform, **options),
                                 old.layer_brief('foreground', self.transform, **options))
        for mode in (None, prompts.MATERIAL_MODE):
            for embedded in (False, True):
                for owned in (False, True):
                    member = {'id': 'source-member', 'box': [100, 150, 180, 90],
                              'slot': [8, 8, 496, 240], 'inset_px': 8,
                              'description': 'Complete source artwork', 'lettering': 'SOURCE WORDS',
                              'embedded_image': embedded, 'presentation': 'rectangle' if embedded else 'cutout'}
                    board = {'size': [512, 256], 'key': 'magenta', 'background_mode': mode,
                             'members': [member]}
                    if owned:
                        board['board_reference_policy'] = prompts.BOARD_REFERENCE_POLICY
                    self.assertEqual(prompts.board_brief(board), old.board_brief(board))
        for name in ('wordart_brief', '_isolated_title_brief', '_decorative_layer_brief'):
            self.assertEqual(inspect.getsource(getattr(prompts, name)), inspect.getsource(getattr(old, name)))

    def test_scoped_background_invoke_uses_the_same_compact_projection_without_art_details(self):
        task = {'role': 'background', 'scene_allocation': self.allocation, 'source_layer_scope': self.scope,
                'decorative_background_policy': prompts.BACKGROUND_CONTAINER_POLICY_ID}
        request = {'transparent_background': False, 'references': ['current-working.png']}
        before = copy.deepcopy((task, request))
        text = execution._invocation_instructions(task, request)
        for region in self.allocation['foreground_regions']:
            self.assertNotIn(region['description'], text)
            self.assertNotIn(region.get('lettering', 'impossible-missing-value'), text)
        payload = {'scene_allocation': self.compact(), 'source_layer_scope': self.scope}
        self.assertIn(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')), text)
        for phrase in ('[x,y,width,height]', '1920×1080', '固定品牌标识',
                       '已分配的空底托、容器、分隔和原生装饰图形按背景清单保留',
                       '原许可范围保持一次调用', 'executed_prompt'):
            self.assertIn(phrase, text)
        self.assertEqual((task, request), before)

    def test_all_other_invoke_goal_outputs_are_byte_identical_to_r22(self):
        old = retained('execution')
        for role in ('background', 'foreground', 'wordart', 'board'):
            for scope in (None, self.scope):
                if role == 'background' and scope is not None:
                    continue
                for transparent in (False, True):
                    task = {'role': role, 'scene_allocation': self.allocation,
                            'source_layer_scope': scope, 'member': {'lettering': 'EXACT'},
                            'board': {'grid': {'rows': 1, 'columns': 2}, 'members': [{'id': 'a'}, {'id': 'b'}]},
                            'decorative_background_policy': prompts.BACKGROUND_CONTAINER_POLICY_ID}
                    request = {'transparent_background': transparent, 'references': ['a.png', 'b.png']}
                    self.assertEqual(execution._invocation_instructions(task, request),
                                     old._invocation_instructions(task, request))

    def test_begin_request_state_and_all_once_fields_except_background_instruction_are_unchanged(self):
        old = retained('execution')
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory(prefix='prk-bg-focus-permit-') as folder:
            work = Path(folder)
            (work / 'brief.txt').write_text('Existing canonical request.\n', 'utf-8')
            reference = work / 'source.png'
            reference.write_bytes(b'unchanged-reference-fixture')
            task = {'id': 'background', 'role': 'background', 'attempt': 0, 'brief': 'brief.txt',
                    'scene_allocation': self.allocation, 'source_layer_scope': self.scope}
            execution.enroll(task)
            public = {'references': [str(reference)], 'size': [1920, 1080], 'transparent_background': False,
                      'result_template': {'executed_prompt': None}, 'failure_template': {}}
            request = {'tool': 'offline-fixture', 'prompt': 'Existing canonical request.\n',
                       'references': [str(reference)], 'size': [1920, 1080], 'transparent_background': False,
                       'requested_outputs': 1, 'tool_arguments': {'prompt': None}}
            previous_task, current_task = copy.deepcopy(task), copy.deepcopy(task)
            with mock.patch.object(old, 'datetime', fixed):
                previous = old.begin(work, previous_task, public, request)
            old_bytes = Path(previous['request_file']).read_bytes()
            with mock.patch.object(execution, 'datetime', fixed):
                current = execution.begin(work, current_task, public, request)
            self.assertEqual(Path(current['request_file']).read_bytes(), old_bytes)
            self.assertEqual(current_task, previous_task)
            previous.pop('instructions')
            current.pop('instructions')
            self.assertEqual(current, previous)
            self.assertIsNone(current['result_template']['executed_prompt'])
            self.assertIsNone(execution.begin(work, current_task, public, request))

    def test_all_execution_functions_other_than_goal_formatter_keep_exact_ast(self):
        old_path = PACKAGE.parent / 'source-r22/compat-runtime/newpath/execution.py'
        if not old_path.is_file():
            self.skipTest('Retained frozen R22 unavailable')
        def functions(path):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(path.read_text('utf-8')).body
                    if isinstance(node, ast.FunctionDef) and node.name != '_invocation_instructions'}
        self.assertEqual(functions(PACKAGE / 'compat-runtime/newpath/execution.py'), functions(old_path))


@unittest.skipUnless(os.environ.get('PRK_R22_REVIEW_EXPORT'), 'private actual R22 export not configured')
class ActualR22BackgroundFocus(unittest.TestCase):
    def test_actual_background_prompt_and_invoke_shorten_without_losing_clear_region_geometry(self):
        root = Path(os.environ['PRK_R22_REVIEW_EXPORT'])
        state = json.loads((root / 'job/state.json').read_text('utf-8'))
        before = copy.deepcopy(state)
        task = next(iter(state['tasks'].values()))
        options = {'scene_allocation': task['scene_allocation'], 'source_layer_scope': task['source_layer_scope'],
                   'decorative_background': True}
        previous = retained('prompts').layer_brief('background', state['source']['transform'], **options)
        current = prompts.layer_brief('background', state['source']['transform'], **options)
        self.assertLess(len(current.encode('utf-8')), len(previous.encode('utf-8')))
        payload = json.loads(current.split(MARKER, 1)[1])
        self.assertEqual(payload['scene_allocation']['background_regions'], task['scene_allocation']['background_regions'])
        self.assertEqual(payload['scene_allocation']['source_pixel_sha256'], task['scene_allocation']['source_pixel_sha256'])
        for original, compact in zip(task['scene_allocation']['foreground_regions'], payload['scene_allocation']['foreground_regions']):
            self.assertEqual(compact['id'], original['id'])
            self.assertEqual(compact['box'], original['box'])
            self.assertNotIn('description', compact)
            self.assertNotIn('lettering', compact)
        request = {'transparent_background': False, 'references': ['actual-working.png']}
        old_goal = retained('execution')._invocation_instructions(task, request)
        goal = execution._invocation_instructions(task, request)
        self.assertLess(len(goal.encode('utf-8')), len(old_goal.encode('utf-8')))
        self.assertEqual(state, before)
        print(json.dumps({'actual_R22_text_bytes': {'prompt_before': len(previous.encode('utf-8')),
              'prompt_after': len(current.encode('utf-8')), 'invoke_before': len(old_goal.encode('utf-8')),
              'invoke_after': len(goal.encode('utf-8'))}, 'provider_calls': 0}))


if __name__ == '__main__':
    unittest.main()
