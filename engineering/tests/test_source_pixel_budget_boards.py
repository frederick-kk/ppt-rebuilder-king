"""Explicit source-budget planning preserves legacy sampling and image handling."""
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import artwork, boardplan
from newpath.native import MODE
from newpath.wordart import select_primary_wordart

POLICY = 'source-pixel-budget/1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


class SourcePixelBudgetBoards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-source-budget-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/adaptive-board-current.json').read_text('utf-8'))

    def plan(self, arts, policy=POLICY):
        self.assertIn('sampling_policy', inspect.signature(boardplan.plan_boards).parameters)
        return boardplan.plan_boards(arts, sampling_policy=policy)

    def prepare(self, source, foreground, arts, out, *, policy=POLICY, mode=None):
        self.assertIn('sampling_policy', inspect.signature(artwork.prepare_boards).parameters)
        return artwork.prepare_boards(source, foreground, arts, out,
                                      background_mode=mode, sampling_policy=policy)

    def test_policy_name_is_exported_for_controller_freezing(self):
        self.assertEqual(getattr(boardplan, 'SOURCE_BUDGET_POLICY', None), POLICY)

    def test_source_policy_skips_only_the_optional_sampling_gain_split(self):
        arts = copy.deepcopy(self.fixture['arts'])
        original = copy.deepcopy(arts)
        actual = self.plan(arts)
        self.assertEqual(arts, original)
        expected = [boardplan._public(board) for board in sorted(
            boardplan._best_plan(boardplan._requests(arts, (1920, 1080))),
            key=lambda board: min(item.identity for item in board[-1]))]
        self.assertEqual(len(actual), 3)
        for board in expected:
            board['policy'] = POLICY
            board['grid']['policy'] = POLICY
        self.assertEqual(actual, expected)
        self.assertTrue(all('adaptive_split' not in board['pixel_budget'] for board in actual))

    def test_default_and_explicit_none_keep_every_legacy_plan_field(self):
        implicit = boardplan.plan_boards(self.fixture['arts'])
        explicit = self.plan(self.fixture['arts'], None)
        self.assertEqual(implicit, explicit)
        self.assertEqual(len(explicit), 4)
        self.assertEqual(fingerprint(explicit),
                         'a7092ab62d8e353c62e5ba640435bb2596dd9d94945879583dae856377c4c9b2')

    def test_unknown_policies_reject_including_an_empty_member_list(self):
        for policy in ('unknown/1', '', False):
            for arts in ([], self.fixture['arts']):
                with self.subTest(policy=policy, empty=not arts):
                    self.assertIn('sampling_policy', inspect.signature(boardplan.plan_boards).parameters)
                    with self.assertRaisesRegex(ValueError, 'sampling policy'):
                        boardplan.plan_boards(arts, sampling_policy=policy)

    def test_empty_members_preserve_empty_result_and_skip_image_reads(self):
        self.assertEqual(self.plan([]), [])
        for policy in (None, POLICY):
            with self.subTest(policy=policy):
                self.assertEqual(self.prepare('absent-source', 'absent-foreground', [],
                                               self.root / 'unused', policy=policy), [])
        self.assertFalse((self.root / 'unused').exists())

    def test_prepare_empty_unknown_policy_is_an_error(self):
        self.assertIn('sampling_policy', inspect.signature(artwork.prepare_boards).parameters)
        with self.assertRaisesRegex(ValueError, 'sampling policy'):
            artwork.prepare_boards('absent-source', 'absent-foreground', [], self.root / 'unused',
                                   sampling_policy='unknown/1')
        self.assertFalse((self.root / 'unused').exists())

    def test_hardware_shortfall_stays_explicit_and_does_not_force_two_boards(self):
        arts = [{'id': str(index), 'box': [0, 0, 1920, 1080], 'lettering': 'LARGE'}
                for index in range(6)]
        plans = self.plan(arts)
        self.assertEqual(len(plans), 6)
        placements = [placement for board in plans for placement in board['placements']]
        self.assertEqual(len(placements), 6)
        for placement in placements:
            budget = placement['pixel_budget']
            self.assertTrue(budget['hardware_limited'])
            self.assertFalse(budget['meets_requirement'])
            self.assertEqual(budget['required_content_px'], [1920, 1080])
        self.assertTrue(all(board['pixel_budget']['all_members_meet_requirement'] is False for board in plans))

    def test_invalid_source_boxes_and_duplicate_ids_still_reject(self):
        cases = [[{'id': 'x', 'box': [3000, 0, 10, 10]}],
                 [{'id': 'x', 'box': [0, 0, 10, 10]}] * 2,
                 [{'id': 'x', 'box': [0, 0, float('inf'), 10]}]]
        for arts in cases:
            with self.subTest(arts=arts), self.assertRaises(ValueError):
                self.plan(arts)

    def test_source_policy_is_deterministic_for_input_order(self):
        arts = self.fixture['arts']
        expected = self.plan(arts)
        for sequence in (list(reversed(arts)), arts[3:] + arts[:3]):
            self.assertEqual(self.plan(sequence), expected)

    def test_prepare_policy_is_bound_and_pixels_and_crop_geometry_stay_identical(self):
        source = self.root / 'source.png'
        Image.new('RGB', (1920, 1080), 'white').save(source)
        arts = [{'id': 'icon', 'box': [90, 90, 100, 100], 'description': 'Disconnected blue and white marks'}]
        for mode in (None, MODE):
            with self.subTest(mode=mode):
                foreground = self.root / ('native.png' if mode else 'keyed.png')
                image = Image.new('RGBA', (1920, 1080), (5, 9, 11, 0) if mode else (255, 0, 255, 255))
                draw = ImageDraw.Draw(image)
                draw.rectangle((100, 100, 130, 130), fill=(15, 55, 220, 255))
                draw.rectangle((140, 140, 142, 142), fill=(255, 255, 255, 128 if mode else 255))
                draw.rectangle((155, 150, 157, 152), fill=(30, 190, 230, 2 if mode else 255))
                image.save(foreground)
                original_bytes = foreground.read_bytes()
                old = self.prepare(source, foreground, arts, self.root / ('old-native' if mode else 'old-key'),
                                   policy=None, mode=mode)[0]
                new = self.prepare(source, foreground, arts, self.root / ('new-native' if mode else 'new-key'),
                                   mode=mode)[0]
                self.assertEqual(new['planning_policy'], POLICY)
                self.assertEqual(new['grid']['policy'], POLICY)
                self.assertEqual(new['members'], old['members'])
                self.assertEqual(new['pixel_budget'], old['pixel_budget'])
                self.assertEqual(new['reference_warnings'], old['reference_warnings'])
                self.assertEqual(Path(new['image']).read_bytes(), Path(old['image']).read_bytes())
                self.assertNotEqual(new['signature'], old['signature'])
                self.assertEqual(foreground.read_bytes(), original_bytes)


    def actual(self, variable):
        if not os.environ.get(variable):
            self.skipTest('Actual job not supplied by ' + variable)
        job = Path(os.environ[variable])
        state = json.loads((job / 'state.json').read_text('utf-8'))
        primary, arts = select_primary_wordart([item for item in state['layout']['items'] if item['kind'] == 'art'])
        return job, state, primary, arts

    def test_actual_r13_default_three_boards_and_source_budget_two_preserve_all_minimum_pixels(self):
        job, state, primary, arts = self.actual('PRK_R13_REVIEW_JOB')
        original_state = (job / 'state.json').read_bytes()
        self.assertEqual(primary['id'], 'o001')
        self.assertEqual(len(arts), 24)
        legacy = self.plan(arts, None)
        new = self.plan(arts)
        self.assertEqual(len(legacy), 3)
        self.assertEqual(len(new), 2)
        self.assertEqual(sum(len(board['placements']) for board in new), 24)
        self.assertTrue(all(board['pixel_budget']['all_members_meet_requirement'] for board in new))
        for board in new:
            for placement in board['placements']:
                self.assertFalse(placement['pixel_budget']['hardware_limited'])
                self.assertTrue(placement['pixel_budget']['meets_requirement'])
        source = job / state['source']['working']
        front = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
        foreground = job / front['result']['normalized']
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [source, foreground]}
        prepared = self.prepare(source, foreground, arts, self.root / 'real-r13', mode=MODE)
        self.assertEqual(len(prepared), 2)
        self.assertTrue(all(board['planning_policy'] == POLICY for board in prepared))
        self.assertEqual(sum(len(board['members']) for board in prepared), 24)
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [source, foreground]})
        self.assertEqual((job / 'state.json').read_bytes(), original_state)

    def test_actual_r12_minimum_budget_still_needs_three_boards(self):
        job, state, _, arts = self.actual('PRK_R12_TITLE_EDGE_JOB')
        original = (job / 'state.json').read_bytes()
        self.assertEqual(len(arts), 31)
        new = self.plan(arts)
        self.assertGreaterEqual(len(new), 3)
        self.assertEqual(sum(len(board['placements']) for board in new), 31)
        self.assertTrue(all('adaptive_split' not in board['pixel_budget'] for board in new))
        self.assertEqual((job / 'state.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
