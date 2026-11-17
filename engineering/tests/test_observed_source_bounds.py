"""Optional observed edges derive geometry without changing legacy layout semantics."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, layout
from newpath.storage import read_state
from test_result_role_binding import ReviewedOutputFixture


class ObservedSourceBounds(unittest.TestCase):
    def normalized(self, item):
        try:
            return layout.normalize_layout({'items': [item]})['items'][0]
        except ValueError as error:
            self.fail('Valid optional source observations must derive their box: ' + str(error))

    def test_actual_title_parts_include_the_arc_tail_in_one_complete_artwork(self):
        parts = [{'name': '文字', 'bounds': [170, 27, 1051, 96]},
                 {'name': '圆弧', 'bounds': [116, 11, 233, 153]}]
        raw = {'kind': 'art', 'id': 'main_title', 'description': 'Complete writing and arc.',
               'lettering': '让非遗融入现代生活', 'source_parts': parts,
               'source_bounds': [170, 27, 1051, 96], 'box': [118, 10, 945, 96]}
        before = copy.deepcopy(raw)
        actual = self.normalized(raw)
        self.assertEqual(actual['box'], [116, 11, 935, 142])
        self.assertEqual(actual['target'], actual['box'])
        self.assertEqual(actual['source_parts'], parts)
        self.assertEqual(actual['source_bounds'], [170, 27, 1051, 96])
        self.assertEqual(raw, before)
        self.assertEqual(layout.normalize_layout({'items': [actual]})['items'], [actual])

    def test_all_five_box_based_kinds_accept_edges_without_box(self):
        for raw in [
            {'kind': 'text', 'text': 'BODY', 'size': 20},
            {'kind': 'shape', 'fill': 'AABBCC'},
            {'kind': 'table', 'rows': [['a', 'b']], 'size': 20},
            {'kind': 'formula', 'text': 'x^2', 'size': 20},
            {'kind': 'art', 'description': 'One complete foreground member.'},
        ]:
            with self.subTest(kind=raw['kind']):
                actual = self.normalized({**raw, 'source_bounds': (10.5, 20, 310.5, 120)})
                self.assertEqual(actual['box'], [10.5, 20, 300, 100])
                self.assertEqual(actual['source_bounds'], [10.5, 20, 310.5, 120])
                if raw['kind'] == 'art':
                    self.assertEqual(actual['target'], actual['box'])
                if raw['kind'] == 'table':
                    self.assertEqual(actual['column_widths'], [150, 150])

    def test_observation_precedence_ignores_stale_box_and_preserves_explicit_target(self):
        for stale in ([1, 2, 3, 4], 'old derived value', None):
            with self.subTest(stale=stale):
                actual = self.normalized({'kind': 'art', 'description': 'Complete symbol.',
                    'source_bounds': [20, 30, 80, 100], 'box': stale,
                    'target': [600, 700, 120, 140]})
                self.assertEqual(actual['box'], [20, 30, 60, 70])
                self.assertEqual(actual['target'], [600, 700, 120, 140])
        with self.assertRaises(ValueError):
            layout.normalize_layout({'items': [{'kind': 'art', 'description': 'Symbol.',
                'source_bounds': [20, 30, 80, 100], 'target': [1, 2, 0, 4]}]})

    def test_disjoint_parts_duplicate_names_and_input_order_are_preserved(self):
        raw = {'kind': 'art', 'description': 'Detached writing and separate emblem.',
               'source_parts': [{'name': '笔画', 'bounds': (20, 30, 50, 60)},
                                {'name': '笔画', 'bounds': (400, 500, 450, 560)}]}
        actual = self.normalized(raw)
        self.assertEqual(actual['box'], [20, 30, 430, 530])
        self.assertEqual(actual['source_parts'], [
            {'name': '笔画', 'bounds': [20, 30, 50, 60]},
            {'name': '笔画', 'bounds': [400, 500, 450, 560]}])
        reverse = self.normalized({**raw, 'source_parts': list(reversed(raw['source_parts']))})
        self.assertEqual(reverse['box'], actual['box'])
        self.assertEqual(reverse['source_parts'], list(reversed(actual['source_parts'])))
        self.assertEqual(actual, self.normalized(actual))

    def test_new_bounds_accept_canvas_edges_and_reject_invalid_ranges_or_values(self):
        self.assertEqual(self.normalized({'kind': 'shape', 'source_bounds': [0, 0, 1920, 1080]})['box'],
                         [0, 0, 1920, 1080])
        invalid = [None, [], [1, 2, 3], '1,2,3,4', [True, 0, 10, 10],
                   [0, 0, float('inf'), 10], [0, 0, 10, float('nan')],
                   [0, 0, '10', 10], [100, 20, 10, 30], [0, 10, 10, 10],
                   [-1, 0, 10, 10], [0, -1, 10, 10], [0, 0, 1921, 1080], [0, 0, 1920, 1081]]
        for bounds in invalid:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                layout.normalize_layout({'items': [{'kind': 'shape', 'source_bounds': bounds}]})

    def test_parts_require_nonempty_named_valid_observations(self):
        self.normalized({'kind': 'art', 'description': 'One source part.',
                         'source_parts': [{'name': '字', 'bounds': [0, 0, 10, 10]}]})
        invalid = [None, [], {}, 'parts', [True], [{}], [{'name': '字'}],
                   [{'name': '', 'bounds': [0, 0, 10, 10]}],
                   [{'name': 1, 'bounds': [0, 0, 10, 10]}],
                   [{'name': '字', 'bounds': [0, 0, 10, 10], 'invented': True}],
                   [{'name': '字', 'bounds': [0, 0, 0, 10]}],
                   [{'name': '字', 'bounds': [0, 0, 10, float('nan')]}],
                   [{'name': '字', 'bounds': [0, 0, 1921, 10]}]]
        for parts in invalid:
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                layout.normalize_layout({'items': [{'kind': 'art', 'description': 'Source part.',
                                                    'source_parts': parts}]})
        with self.assertRaises(ValueError):
            layout.normalize_layout({'items': [{'kind': 'art', 'description': 'Source part.',
                'source_parts': [{'name': '字', 'bounds': [0, 0, 10, 10]}],
                'source_bounds': [0, 0, float('inf'), 10]}]})

    def test_unnamed_parts_get_stable_internal_names_without_an_extra_input_requirement(self):
        raw = {'kind': 'art', 'description': 'One group with observed pieces.',
               'source_parts': [{'bounds': [20, 30, 50, 60]},
                                {'bounds': [400, 500, 450, 560]},
                                {'name': 'part-01', 'bounds': [50, 80, 60, 100]}]}
        before = copy.deepcopy(raw)
        actual = self.normalized(raw)
        self.assertEqual(actual['box'], [20, 30, 430, 530])
        self.assertEqual([part['name'] for part in actual['source_parts']], ['part-01', 'part-02', 'part-01'])
        self.assertEqual(actual, self.normalized(actual))
        self.assertEqual(raw, before)

    def test_parts_are_art_only_and_point_lines_keep_the_existing_contract(self):
        for kind in ('text', 'shape', 'table', 'formula', 'line'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'unsupported'):
                layout.normalize_layout({'items': [{'kind': kind, 'box': [0, 0, 100, 40],
                    'source_parts': [{'name': 'part', 'bounds': [0, 0, 10, 10]}]}]})
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            layout.normalize_layout({'items': [{'kind': 'line', 'points': [0, 0, 10, 10],
                                               'source_bounds': [0, 0, 10, 10]}]})

    def test_named_style_observations_expand_into_stable_independent_metadata(self):
        payload = {'styles': {'header': {'size': 22, 'source_bounds': [10, 20, 500, 70]}},
                   'items': [{'kind': 'text', 'style': 'header', 'text': 'A'},
                             {'kind': 'text', 'style': 'header', 'text': 'B',
                              'source_bounds': [10, 100, 500, 150]}]}
        before = copy.deepcopy(payload)
        try:
            scene = layout.normalize_layout(payload)
        except ValueError as error:
            self.fail('Optional observed edges must use the existing named-style expansion: ' + str(error))
        self.assertEqual([item['id'] for item in scene['items']], ['o001', 'o002'])
        self.assertEqual([item['box'] for item in scene['items']], [[10, 20, 490, 50], [10, 100, 490, 50]])
        self.assertEqual(scene, layout.normalize_layout(scene))
        self.assertEqual(payload, before)

    def test_legacy_payloads_ids_key_order_and_validation_remain_exactly_r16(self):
        old_file = PACKAGE.parent / 'source-r16/compat-runtime/newpath/layout.py'
        if not old_file.is_file():
            self.skipTest('Retained R16 layout module unavailable outside this checkout.')
        spec = importlib.util.spec_from_file_location('r16_layout_bounds', old_file)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        payloads = [
            {'items': []},
            {'items': [{'kind': 'text', 'text': 'Native', 'box': [-5, 1080, 300, 50]},
                       {'id': 'o001', 'kind': 'art', 'description': 'Outside placement.',
                        'box': [1900, -30, 80, 90], 'target': [1900, -30, 80, 90]},
                       {'kind': 'line', 'points': [-10, 10, 2000, 1100]}]},
            {'styles': {'s': {'size': 24, 'color': '#aabbcc'}},
             'items': [{'kind': 'text', 'style': 's', 'text': 'AB', 'box': [0, 0, 200, 40],
                        'runs': [{'text': 'A', 'bold': True}, {'text': 'B'}]},
                       {'kind': 'shape', 'shape': 'roundRect', 'box': [0, 60, 300, 200]},
                       {'kind': 'table', 'rows': [['甲', '乙']], 'box': [0, 300, 600, 100]},
                       {'kind': 'formula', 'text': 'x^2', 'box': [600, 500, 200, 70]}]},
        ]
        actual_scene = PACKAGE.parent / 'heritage-20261112/evidence/r16-medium-done/job/layout.json'
        digest = None
        if actual_scene.is_file():
            digest = hashlib.sha256(actual_scene.read_bytes()).hexdigest()
            payloads.append(json.loads(actual_scene.read_text('utf-8')))
        for policy in (None, layout.SOURCE_LAYOUT_POLICY):
            for payload in payloads:
                with self.subTest(policy=policy, items=len(payload['items'])):
                    expected = old.normalize_layout(payload, source_layout_policy=policy)
                    actual = layout.normalize_layout(payload, source_layout_policy=policy)
                    self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
        if actual_scene.is_file():
            self.assertEqual(hashlib.sha256(actual_scene.read_bytes()).hexdigest(), digest)


class SourceBoundsNormalEntry(ReviewedOutputFixture):
    def test_complete_art_parts_freeze_as_one_member_and_bind_the_full_derived_reference(self):
        with mock.patch.object(controller, '_runtime'):
            controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                source_layout_policy=layout.SOURCE_LAYOUT_POLICY)
        raw = {'kind': 'art', 'description': 'Complete word and arc.', 'lettering': 'TITLE',
               'source_parts': [{'name': '文字', 'bounds': [170, 27, 1051, 96]},
                                {'name': '圆弧', 'bounds': [116, 11, 233, 153]}]}
        try:
            action = controller.run(self.work, data={'items': [raw], 'background_regions': []})
        except ValueError as error:
            self.fail('The normal art entry must derive a single complete member: ' + str(error))
        self.assertEqual(len(action['jobs']), 1)
        background = action['jobs'][0]
        self.begin(background)
        action = self.submit(background, self.bg)
        self.assertEqual(len(action['jobs']), 1)
        self.assertEqual(action['jobs'][0]['role'], 'wordart')
        state = read_state(self.work)
        self.assertEqual(len(state['layout']['items']), 1)
        item = state['layout']['items'][0]
        self.assertEqual(item['box'], [116, 11, 935, 142])
        self.assertEqual(item['source_parts'], raw['source_parts'])
        self.assertEqual(item['target'], item['box'])
        task = state['tasks'][action['jobs'][0]['id']]
        self.assertEqual(task['member'], item)
        self.assertEqual(task['source_detail']['member_box'], item['box'])
        self.assertEqual(task['source_detail']['crop_box'], [22, 0, 1145, 247])
        self.assertFalse(list(self.work.rglob('*.pptx')))

    def test_native_edges_use_normal_description_preflight_and_actual_node_pptx_output(self):
        with mock.patch.object(controller, '_runtime'):
            first = controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                recovery_policy='bounded-recovery/1', source_layout_policy=layout.SOURCE_LAYOUT_POLICY)
        self.assertEqual(first['kind'], 'describe')
        items = [
            {'id': 'body', 'kind': 'text', 'text': 'BODY', 'size': 20,
             'source_bounds': [100, 100, 600, 150]},
            {'id': 'shape', 'kind': 'shape', 'fill': 'FFCC33', 'source_bounds': [700, 100, 1000, 200]},
            {'id': 'table', 'kind': 'table', 'rows': [['A', 'B'], ['C', 'D']], 'size': 20,
             'source_bounds': [100, 300, 700, 500]},
            {'id': 'formula', 'kind': 'formula', 'text': 'x^2', 'size': 24,
             'source_bounds': [800, 300, 1100, 380]},
        ]
        try:
            action = controller.run(self.work, data={'items': items, 'background_regions': []})
        except ValueError as error:
            self.fail('The normal description entry must normalize observed source edges: ' + str(error))
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual(len(action['jobs']), 1)
        background = action['jobs'][0]
        self.assertEqual(background['role'], 'background')
        self.begin(background)
        done = self.submit(background, self.bg)
        self.assertEqual(done['kind'], 'done')
        state = read_state(self.work)
        self.assertEqual(len(state['tasks']), 1)
        self.assertEqual(state['layout']['items'][0]['box'], [100, 100, 500, 50])
        self.assertEqual(state['layout']['items'][2]['column_widths'], [300, 300])
        with ZipFile(done['pptx']) as archive:
            self.assertIsNone(archive.testzip())
            slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
              'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
              'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}
        body = next(shape for shape in slide.findall('.//p:sp', ns)
                    if ''.join(node.text or '' for node in shape.findall('.//a:t', ns)) == 'BODY')
        self.assertEqual(body.find('.//a:xfrm/a:off', ns).attrib, {'x': '635000', 'y': '635000'})
        self.assertEqual(body.find('.//a:xfrm/a:ext', ns).attrib, {'cx': '3175000', 'cy': '317500'})
        texts = [node.text for node in slide.findall('.//a:t', ns)]
        for word in ('BODY', 'A', 'B', 'C', 'D'):
            self.assertIn(word, texts)
        self.assertTrue(slide.findall('.//m:oMath', ns))
        self.assertEqual(done['quality'], 'not_evaluated')


if __name__ == '__main__':
    unittest.main()
