"""Observed background edges use the same source geometry as page items."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import __main__ as cli, controller, layout
from newpath.storage import read_state, sha256
from test_result_role_binding import ReviewedOutputFixture


SOURCE_POLICY = 'source-layout-first/1'


def previous_module(name):
    path = PACKAGE.parent / 'source-r22/compat-runtime/newpath' / (name + '.py')
    if not path.is_file():
        raise unittest.SkipTest('Retained R22 module is unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r22_background_bounds_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackgroundRegionSourceBounds(unittest.TestCase):
    def normalize(self, region):
        try:
            return layout.normalize_layout({'items': [], 'background_regions': [region]},
                                           source_layout_policy=SOURCE_POLICY)
        except ValueError as error:
            self.fail('Valid background source_bounds must derive the source rectangle: ' + str(error))

    def test_observed_edges_derive_background_box_without_a_second_coordinate_entry(self):
        region = {'id': 'central-card', 'source_bounds': [565, 216, 1352, 933],
                  'description': 'Empty central content card.'}
        self.assertEqual(self.normalize(region)['background_regions'], [{
            'id': 'central-card', 'box': [565, 216, 787, 717],
            'source_bounds': [565, 216, 1352, 933], 'description': 'Empty central content card.'}])

    def test_source_observation_takes_precedence_over_stale_box(self):
        for stale in ([1362, 216, 1904, 911], None, 'stale box'):
            with self.subTest(stale=stale):
                region = {'box': stale, 'source_bounds': [1362, 216, 1904, 911],
                          'description': 'Empty right card.'}
                self.assertEqual(self.normalize(region)['background_regions'][0]['box'],
                                 [1362, 216, 542, 695])

    def test_new_observation_preserves_input_and_has_stable_ids_keys_and_roundtrip(self):
        raw = {'source_bounds': (20.5, 18, 602.5, 90), 'description': 'Empty header plaque.'}
        before = copy.deepcopy(raw)
        canonical = self.normalize(raw)
        region = canonical['background_regions'][0]
        self.assertEqual(list(region), ['id', 'box', 'source_bounds', 'description'])
        self.assertEqual(region['id'], 'bg001')
        self.assertEqual(region['box'], [20.5, 18, 582.0, 72])
        self.assertEqual(region['source_bounds'], [20.5, 18, 602.5, 90])
        self.assertEqual(raw, before)
        self.assertEqual(layout.normalize_layout(canonical, source_layout_policy=SOURCE_POLICY), canonical)

    def test_new_bounds_share_the_existing_explicit_observation_validation(self):
        self.assertEqual(self.normalize({'source_bounds': [0, 0, 1920, 1080],
                                        'description': 'Whole source.'})['background_regions'][0]['box'],
                         [0, 0, 1920, 1080])
        for bounds in (None, [], [1, 2, 3], 'edges', [True, 0, 10, 10],
                       [0, 0, float('inf'), 10], [0, 0, 10, float('nan')],
                       [10, 20, 10, 30], [-1, 0, 10, 10], [0, 0, 1921, 1080]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                layout.normalize_layout({'items': [], 'background_regions': [
                    {'source_bounds': bounds, 'description': 'Invalid observation.'}]},
                    source_layout_policy=SOURCE_POLICY)

    def test_old_box_values_and_serialized_key_order_stay_exactly_r22(self):
        old = previous_module('layout')
        for box in ([565, 216, 1352, 933], [1362, 216, 1904, 911],
                    [-5, -8, 2000, 1100], [0, 0, 1920, 1080]):
            with self.subTest(box=box):
                raw = {'items': [], 'primary_wordart_id': None, 'background_regions': [
                    {'box': box, 'description': 'Existing rectangle keeps XYWH semantics.'}]}
                actual = layout.normalize_layout(raw, source_layout_policy=SOURCE_POLICY)
                expected = old.normalize_layout(raw, source_layout_policy=SOURCE_POLICY)
                self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
                self.assertEqual(actual['background_regions'][0]['box'], box)
                self.assertNotIn('source_bounds', actual['background_regions'][0])

    def test_non_source_first_layout_ignores_background_metadata_as_before(self):
        raw = {'items': [], 'background_regions': [{'source_bounds': None}]}
        self.assertEqual(layout.normalize_layout(raw), previous_module('layout').normalize_layout(raw))

    def test_background_regions_do_not_accept_art_parts_or_unknown_fields(self):
        for key in ('source_parts', 'target', 'invented'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'unsupported'):
                layout.normalize_layout({'items': [], 'background_regions': [
                    {'box': [0, 0, 10, 10], 'description': 'Known fields only.', key: []}]},
                    source_layout_policy=SOURCE_POLICY)


class BackgroundBoundsController(ReviewedOutputFixture):
    def start_source_first(self):
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', post_return_prompt_policy='audit-existing-return/1',
                delivery_policy='reviewed-existing-output/1', source_layout_policy=SOURCE_POLICY,
                source_board_reference_policy='direct-source-board-reference/1',
                primary_source_reference_policy='source-parts-title-reference/1')

    @staticmethod
    def description(bounds):
        return {'items': [{'id': 'native', 'kind': 'text', 'box': [100, 50, 700, 50],
                           'text': 'Ordinary title', 'size': 30}], 'primary_wordart_id': None,
                'background_regions': [{'id': 'card', 'source_bounds': bounds,
                                        'description': 'Empty card with its original contour.'}]}

    def submit_description(self, value):
        try:
            return controller.run(self.work, data=value)
        except ValueError as error:
            self.fail('The normal description entry must accept background source observations: ' + str(error))

    def test_normal_bare_layout_freezes_computed_region_in_the_actual_layer_task(self):
        self.start_source_first()
        result = self.submit_description(self.description([565, 216, 1352, 933]))
        self.assertEqual(result['kind'], 'generate')
        self.assertEqual([job['role'] for job in result['jobs']], ['background'])
        state = read_state(self.work)
        expected = {'id': 'card', 'box': [565, 216, 787, 717],
                    'source_bounds': [565, 216, 1352, 933],
                    'description': 'Empty card with its original contour.'}
        self.assertEqual(state['layout']['background_regions'], [expected])
        self.assertEqual(state['scene_allocation']['background_regions'], [expected])
        task = next(iter(state['tasks'].values()))
        self.assertEqual(task['scene_allocation']['background_regions'], [expected])
        self.assertEqual(task['execution']['state'], 'not_started')
        self.assertIsNone(state['layout']['primary_wordart_id'])
        self.assertEqual(state['assets'], [])

    def test_invalid_then_corrected_bounds_keep_the_same_unstarted_job(self):
        self.start_source_first()
        before = read_state(self.work)
        with self.assertRaises(ValueError):
            controller.run(self.work, data={'kind': 'layout', 'layout': self.description([0, 0, 0, 10])})
        self.assertEqual(read_state(self.work), before)
        self.assertFalse((self.work / 'layout.json').exists())
        result = self.submit_description({'kind': 'layout', 'layout': self.description([565, 216, 1352, 933])})
        self.assertEqual(result['kind'], 'generate')
        self.assertEqual(read_state(self.work)['started_at_utc'], before['started_at_utc'])

    def test_current_action_exposes_coordinate_forms_and_optional_split_title(self):
        action = self.start_source_first()
        guidance = action['instructions']
        for phrase in ('background_regions', 'source_bounds=[left,top,right,bottom]',
                       'box=[x,y,width,height]', 'width = right - left', 'height = bottom - top',
                       'primary_wordart_id', 'null keeps every artwork on component boards',
                       'ordinary-font section', 'complex metallic', 'native text',
                       'If a standalone artistic member is selected'):
            self.assertIn(phrase, guidance)
        self.assertNotIn('The primary title will use a single source-parts reference', guidance)
        self.assertEqual(read_state(self.work)['tasks'], {})

    def test_older_action_without_scope_keeps_its_prior_instruction_bytes(self):
        self.start_source_first()
        state = read_state(self.work)
        state.pop('source_layer_scope_policy', None)
        current = controller._source_description_action(self.work, state)
        previous = previous_module('controller')._source_description_action(self.work, state)
        self.assertEqual(Path(current.pop('schema_file')), PACKAGE / 'compat-runtime/references/scene.md')
        previous.pop('schema_file')
        self.assertEqual(current, previous)

    def test_normal_cli_submits_observed_background_edges_without_image_calls(self):
        def invoke(arguments):
            output = io.StringIO()
            with mock.patch.object(sys, 'argv', ['newpath', *arguments]), contextlib.redirect_stdout(output):
                with mock.patch.object(controller, '_runtime'):
                    cli.main()
            return json.loads(output.getvalue())
        initial = invoke(['--work', str(self.work), '--source', str(self.source), '--source-first',
                          '--direct-source-boards', '--compact'])
        self.assertEqual(initial['kind'], 'describe')
        data_file = self.root / 'observed-layout.json'
        data_file.write_text(json.dumps(self.description([565, 216, 1352, 933])), 'utf-8')
        try:
            planned = invoke(['--work', str(self.work), '--input', str(data_file), '--compact'])
        except SystemExit as error:
            self.fail('The ordinary CLI must accept observed background bounds: ' + str(error))
        self.assertEqual(planned['kind'], 'generate')
        state = read_state(self.work)
        self.assertEqual(state['layout']['background_regions'][0]['box'], [565, 216, 787, 717])
        self.assertTrue(all(task['execution']['state'] == 'not_started' for task in state['tasks'].values()))


class ActualR22RegionObservation(unittest.TestCase):
    def test_real_regions_can_be_reexpressed_explicitly_without_guessing_or_changing_old_job(self):
        fixture = os.environ.get('PRK_R22_USER_JOB')
        if not fixture:
            self.skipTest('Set PRK_R22_USER_JOB for the retained read-only R22 export')
        job = Path(fixture)
        before = {str(p.relative_to(job)): sha256(p) for p in job.rglob('*') if p.is_file()}
        raw = json.loads((job / 'layout.json').read_text('utf-8'))
        original_regions = layout.normalize_layout(raw, source_layout_policy=SOURCE_POLICY)['background_regions']
        observed = copy.deepcopy(raw)
        for region in observed['background_regions']:
            if region['id'] != 'bg001':
                region['source_bounds'] = region.pop('box')
        try:
            actual = layout.normalize_layout(observed, source_layout_policy=SOURCE_POLICY)
        except ValueError as error:
            self.fail('The existing scene must accept explicitly supplied background observations: ' + str(error))
        boxes = {region['id']: region['box'] for region in actual['background_regions']}
        self.assertEqual(boxes, {'bg001': [0, 0, 1920, 1080], 'bg002': [18, 216, 540, 686],
            'bg003': [565, 216, 787, 717], 'bg004': [1362, 216, 542, 695],
            'bg005': [20, 18, 582, 72], 'bg006': [1360, 12, 545, 79],
            'bg007': [225, 188, 1473, 20], 'bg008': [20, 946, 1885, 114]})
        self.assertEqual(actual['items'], raw['items'])
        self.assertEqual(actual['primary_wordart_id'], 'main-title')
        self.assertEqual(layout.normalize_layout(raw, source_layout_policy=SOURCE_POLICY)['background_regions'],
                         original_regions)
        self.assertEqual({str(p.relative_to(job)): sha256(p) for p in job.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
