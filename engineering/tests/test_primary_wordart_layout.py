"""Optional primary artwork selection is a validated scene-level input."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from newpath.layout import normalize_layout


SOURCE_POLICY = 'source-layout-first/1'


class PrimaryWordartLayout(unittest.TestCase):
    @staticmethod
    def scene():
        return {'items': [
            {'kind': 'art', 'id': 'small-gold', 'description': 'Short gradient lettering.',
             'source_parts': [{'bounds': [10, 20, 55, 35]}, {'bounds': [15, 35, 60, 40]}],
             'target': [100, 200, 50, 20], 'lettering': 'ONE'},
            {'kind': 'art', 'id': 'large-writing', 'box': [200, 20, 400, 80],
             'description': 'Large handwritten title.', 'lettering': 'LARGE TITLE'},
            {'kind': 'text', 'id': 'native-heading', 'box': [20, 300, 700, 50],
             'text': 'Ordinary type', 'size': 40},
            {'kind': 'art', 'id': 'photo', 'box': [20, 400, 200, 150],
             'description': 'Complete photographed document.', 'embedded_image': True,
             'lettering': 'Writing inside the photograph'},
            {'kind': 'art', 'id': 'icon', 'box': [300, 400, 50, 50],
             'description': 'A symbol with no writing.'},
        ]}

    def test_explicit_null_is_preserved_without_changing_any_item(self):
        raw = self.scene()
        before = copy.deepcopy(raw)
        legacy = normalize_layout(raw)
        actual = normalize_layout({**raw, 'primary_wordart_id': None})
        self.assertIn('primary_wordart_id', actual)
        self.assertIsNone(actual['primary_wordart_id'])
        self.assertEqual(actual['items'], legacy['items'])
        self.assertEqual(raw, before)

    def test_explicit_short_written_art_id_is_preserved_even_when_another_is_larger(self):
        actual = normalize_layout({**self.scene(), 'primary_wordart_id': 'small-gold'})
        self.assertEqual(actual.get('primary_wordart_id'), 'small-gold')
        selected = actual['items'][0]
        self.assertEqual(selected['source_parts'], [
            {'name': 'part-01', 'bounds': [10, 20, 55, 35]},
            {'name': 'part-02', 'bounds': [15, 35, 60, 40]},
        ])
        self.assertEqual(selected['box'], [10, 20, 50, 20])
        self.assertEqual(selected['target'], [100, 200, 50, 20])

    def test_absent_field_keeps_exact_legacy_serialization(self):
        raw = {'items': [{'kind': 'art', 'id': 'label', 'box': [10, 20, 50, 12],
                          'description': 'Gold label.', 'lettering': 'ONE'}]}
        expected = {'format': 'newpath.scene/1', 'items': [
            {'id': 'label', 'kind': 'art', 'order': 0, 'box': [10, 20, 50, 12],
             'description': 'Gold label.', 'target': [10, 20, 50, 12],
             'presentation': 'cutout', 'lettering': 'ONE'},
        ]}
        self.assertEqual(json.dumps(normalize_layout(raw), separators=(',', ':')),
                         json.dumps(expected, separators=(',', ':')))
        self.assertNotIn('primary_wordart_id', normalize_layout(raw))

    def test_null_is_accepted_for_an_empty_scene(self):
        self.assertEqual(normalize_layout({'items': [], 'primary_wordart_id': None}),
                         {'format': 'newpath.scene/1', 'items': [], 'primary_wordart_id': None})

    def test_selection_validates_generated_item_ids_after_normalization(self):
        raw = {'primary_wordart_id': 'o001', 'items': [
            {'kind': 'art', 'box': [1, 2, 30, 40], 'description': 'Writing.', 'lettering': 'ABC'}]}
        actual = normalize_layout(raw)
        self.assertEqual(actual.get('primary_wordart_id'), 'o001')
        self.assertEqual(actual['items'][0]['id'], 'o001')

    def test_selection_is_idempotent_with_and_without_source_first_regions(self):
        for policy in (None, SOURCE_POLICY):
            for choice in (None, 'small-gold'):
                with self.subTest(policy=policy, choice=choice):
                    raw = {**self.scene(), 'background_regions': [], 'primary_wordart_id': choice}
                    canonical = normalize_layout(raw, source_layout_policy=policy)
                    self.assertIn('primary_wordart_id', canonical)
                    self.assertEqual(normalize_layout(canonical, source_layout_policy=policy), canonical)

    def test_invalid_root_types_and_empty_ids_are_rejected(self):
        for choice in (False, True, 0, 1.2, [], {}, '', '   '):
            with self.subTest(choice=choice), self.assertRaisesRegex(ValueError, 'primary_wordart_id'):
                normalize_layout({**self.scene(), 'primary_wordart_id': choice})

    def test_unknown_native_photo_and_unwritten_art_ids_are_rejected(self):
        for choice in ('missing', 'native-heading', 'photo', 'icon'):
            with self.subTest(choice=choice), self.assertRaisesRegex(ValueError, 'primary_wordart_id'):
                normalize_layout({**self.scene(), 'primary_wordart_id': choice})

    def test_whitespace_lettering_is_ineligible_without_rewriting_the_item(self):
        raw = {'items': [{'kind': 'art', 'id': 'blank', 'box': [10, 20, 30, 40],
                          'description': 'Unknown writing.', 'lettering': '  '}]}
        before = copy.deepcopy(raw)
        with self.assertRaisesRegex(ValueError, 'primary_wordart_id'):
            normalize_layout({**raw, 'primary_wordart_id': 'blank'})
        self.assertEqual(raw, before)


@unittest.skipUnless(os.environ.get('PRK_USER_R21_REVIEW_JOB'), 'private actual user source fixture not configured')
class ActualUserSceneSelection(unittest.TestCase):
    def test_null_keeps_real_gold_lettering_as_art_in_the_current_scene(self):
        job = Path(os.environ['PRK_USER_R21_REVIEW_JOB'])
        raw = json.loads((job / 'layout.json').read_text('utf-8'))
        baseline = normalize_layout(raw, source_layout_policy=SOURCE_POLICY)
        actual = normalize_layout({**raw, 'primary_wordart_id': None}, source_layout_policy=SOURCE_POLICY)
        self.assertIn('primary_wordart_id', actual)
        self.assertIsNone(actual['primary_wordart_id'])
        self.assertEqual(actual['items'], baseline['items'])
        self.assertEqual(actual['background_regions'], baseline['background_regions'])
        gold = next(item for item in actual['items'] if item['id'] == 'o004')
        self.assertEqual(gold['kind'], 'art')
        self.assertEqual(gold['lettering'], '落地尝试一：')
        self.assertEqual(gold['box'], [238, 112, 344, 78])
        self.assertEqual(gold['target'], [238, 112, 344, 78])
        self.assertEqual(normalize_layout(actual, source_layout_policy=SOURCE_POLICY), actual)


if __name__ == '__main__':
    unittest.main()
