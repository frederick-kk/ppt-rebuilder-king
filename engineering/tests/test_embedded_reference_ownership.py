"""Keep embedded-image references inside their frozen image boundary."""
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

import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import artwork, controller, execution, prompts
from newpath.native import MODE
from newpath.storage import read_state, save_state, write_json
from test_result_role_binding import ReviewedOutputFixture

POLICY = 'embedded-own-source-box/1'


def old_module(name):
    path = PACKAGE.parent / ('source-r17/compat-runtime/newpath/' + name + '.py')
    if not path.exists():
        raise unittest.SkipTest('Retained R17 fixture unavailable')
    spec = importlib.util.spec_from_file_location('newpath.old_embedded_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmbeddedReferenceOwnership(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-owned-image-reference-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.png'
        self.foreground = self.root / 'foreground.png'
        Image.new('RGB', (1920, 1080), 'white').save(self.source)
        foreground = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
        draw = ImageDraw.Draw(foreground)
        draw.rectangle([200, 200, 399, 399], fill=(20, 70, 120, 255))
        draw.text([220, 230], 'REAL INTERNAL LOGO', fill='white')
        draw.rectangle([220, 397, 379, 439], fill=(160, 0, 0, 255))
        draw.text([230, 405], 'SEPARATE LABEL', fill='white')
        foreground.save(self.foreground)
        self.arts = [
            {'id': 'photo', 'kind': 'art', 'box': [200, 200, 200, 200],
             'description': 'Complete image with internal writing.', 'embedded_image': True,
             'presentation': 'rectangle'},
            {'id': 'label', 'kind': 'art', 'box': [220, 397, 160, 43],
             'description': 'Separate red artistic label.', 'lettering': 'LABEL'},
        ]

    def prepare(self, **options):
        return artwork.prepare_boards(self.source, self.foreground, self.arts,
                                      self.root / 'boards', background_mode=MODE, **options)

    def test_new_reference_clears_only_context_outside_the_image_box(self):
        boards = self.prepare(reference_policy=POLICY)
        board = next(b for b in boards if any(m['id'] == 'photo' for m in b['members']))
        self.assertEqual(board.get('board_reference_policy'), POLICY)
        member = next(m for m in board['members'] if m['id'] == 'photo')
        self.assertEqual(member.get('context_exclusions'), [{
            'id': 'label', 'source_box_pixels': [220, 397, 160, 43],
            'description': 'Separate red artistic label.', 'lettering': 'LABEL'}])
        old = old_module('artwork').prepare_boards(self.source, self.foreground, self.arts,
            self.root / 'old-boards', background_mode=MODE)
        same_members = next(b for b in old if [m['id'] for m in b['members']] == [m['id'] for m in board['members']])
        self.assertEqual(board['size'], same_members['size'])
        self.assertEqual(board['grid'], same_members['grid'])
        self.assertEqual(member['slot'], next(m for m in same_members['members'] if m['id'] == 'photo')['slot'])
        self.assertNotEqual(board['signature'], same_members['signature'])
        actual = np.asarray(Image.open(board['image']).convert('RGBA'))
        old_pixels = np.asarray(Image.open(same_members['image']).convert('RGBA'))
        self.assertLess(np.count_nonzero(actual[..., 3]), np.count_nonzero(old_pixels[..., 3]))
        self.assertTrue(np.any((actual[..., 0] == 255) & (actual[..., 1] == 255) & (actual[..., 3] > 0)))

    def test_default_and_none_prepare_boards_keep_every_old_field_and_png_exact(self):
        previous = old_module('artwork')
        out = self.root / 'same-output'
        expected = previous.prepare_boards(self.source, self.foreground, self.arts, out, background_mode=MODE)
        pngs = {b['id']: Path(b['image']).read_bytes() for b in expected}
        for options in ({}, {'reference_policy': None}):
            actual = artwork.prepare_boards(self.source, self.foreground, self.arts, out,
                                            background_mode=MODE, **options)
            self.assertEqual(actual, expected)
            self.assertEqual({b['id']: Path(b['image']).read_bytes() for b in actual}, pngs)

    def test_new_prompt_and_short_goal_name_other_member_ownership_without_removing_true_internal_text(self):
        board = self.prepare(reference_policy=POLICY)[0]
        text = prompts.board_brief(board)
        for phrase in ('other separately assigned member IDs', 'not image-internal writing',
                       'genuine image-internal text, logos and people', 'label', 'photo'):
            self.assertIn(phrase, text)
        task = {'role': 'board', 'board': board}
        goal = execution._invocation_instructions(task, {'transparent_background': True, 'references': []})
        for phrase in ('其他独立ID', '不并入', '真实图内文字', 'logo'):
            self.assertIn(phrase, goal)
        self.assertIn('原许可范围保持一次调用', goal)

    def test_old_board_brief_goal_and_unknown_policy_behavior_are_preserved(self):
        board = self.prepare()[0]
        self.assertEqual(prompts.board_brief(board), old_module('prompts').board_brief(board))
        self.assertEqual(prompts.board_brief({**board, 'board_reference_policy': None}),
                         old_module('prompts').board_brief(board))
        task = {'role': 'board', 'board': board}
        request = {'transparent_background': True, 'references': []}
        self.assertEqual(execution._invocation_instructions(task, request),
                         old_module('execution')._invocation_instructions(task, request))
        with self.assertRaises(ValueError):
            self.prepare(reference_policy='unsupported')

    def test_input_pixels_and_arts_are_unchanged_and_policy_does_not_touch_label_references(self):
        before = self.foreground.read_bytes(), self.source.read_bytes(), copy.deepcopy(self.arts)
        board = self.prepare(reference_policy=POLICY)[0]
        old = old_module('artwork').prepare_boards(self.source, self.foreground, self.arts,
            self.root / 'old-boards', background_mode=MODE)[0]
        label = next(m for m in board['members'] if m['id'] == 'label')
        x, y, w, h = label['slot']
        a = np.asarray(Image.open(board['image']).convert('RGBA'))
        b = np.asarray(Image.open(old['image']).convert('RGBA'))
        np.testing.assert_array_equal(a[y:y+h, x:x+w], b[y:y+h, x:x+w])
        self.assertEqual(before, (self.foreground.read_bytes(), self.source.read_bytes(), self.arts))

    def test_actual_r17_photo_references_match_exact_masked_foreground_pixels(self):
        job = PACKAGE.parent / 'heritage-20261112/evidence/r17-medium-done/job'
        if not job.exists():
            self.skipTest('Private real R17 job unavailable')
        state = json.loads((job / 'state.json').read_text('utf-8'))
        _, arts = controller.select_primary_wordart([i for i in state['layout']['items'] if i['kind'] == 'art'])
        actual = artwork.prepare_boards(job / state['source']['working'], job / 'layers/foreground.png',
            arts, self.root / 'actual-r17-new-refs', background_mode=MODE,
            sampling_policy=state['board_sampling_policy'], reference_policy=POLICY)
        from newpath.boardplan import plan_boards
        placements = {p['art']['id']: p for b in plan_boards(arts, sampling_policy=state['board_sampling_policy'])
                      for p in b['placements']}
        foreground = Image.open(job / 'layers/foreground.png').convert('RGBA')
        count = 0
        for board in actual:
            image = Image.open(board['image']).convert('RGBA')
            for member in board['members']:
                if not member.get('embedded_image'):
                    continue
                count += 1
                placement = placements[member['id']]
                crop = placement['crop']
                reference = foreground.crop(crop)
                pixels = np.asarray(reference).copy()
                x, y, w, h = member['box']
                yy, xx = np.indices(pixels.shape[:2])
                keep = (xx + crop[0] >= x) & (xx + crop[0] < x+w) & (yy + crop[1] >= y) & (yy + crop[1] < y+h)
                pixels[~keep] = 0
                rendered = Image.fromarray(pixels).resize(tuple(member['rendered_size']), Image.Resampling.LANCZOS)
                sx, sy, sw, sh = member['slot']
                px, py = sx+(sw-rendered.width)//2, sy+(sh-rendered.height)//2
                np.testing.assert_array_equal(np.asarray(image.crop([px, py, px+rendered.width, py+rendered.height])),
                                              np.asarray(rendered))
                self.assertEqual(len(member['context_exclusions']), 1)
        self.assertEqual(count, 4)

    def test_current_board_guidance_changes_no_request_state_unknown_or_other_permit_fields(self):
        board = self.prepare(reference_policy=POLICY)[0]
        (self.root / 'briefs').mkdir()
        brief = 'briefs/current-board.txt'
        (self.root / brief).write_text(prompts.board_brief(board), 'utf-8')
        task = {'id': 'task-current-board', 'role': 'board', 'attempt': 0, 'brief': brief, 'board': board}
        execution.enroll(task)
        references = [str(self.foreground), str(self.source)]
        public = {'references': references, 'size': board['size'], 'transparent_background': True,
                  'result_template': {'executed_prompt': None}, 'failure_template': {}}
        request = {'tool': 'actual-host-tool', 'prompt': (self.root / brief).read_text('utf-8'),
                   'references': references, 'size': board['size'], 'transparent_background': True,
                   'requested_outputs': 1, 'tool_arguments': {'transparent_background': True, 'unknown': None}}
        fixed = types.SimpleNamespace(now=lambda unused: datetime(2026, 11, 14, tzinfo=timezone.utc))
        previous = old_module('execution')
        old_task, new_task = copy.deepcopy(task), copy.deepcopy(task)
        with mock.patch.object(previous, 'datetime', fixed):
            old = previous.begin(self.root, old_task, public, request)
        old_bytes = Path(old['request_file']).read_bytes()
        with mock.patch.object(execution, 'datetime', fixed):
            new = execution.begin(self.root, new_task, public, request)
        self.assertEqual(old_bytes, Path(new['request_file']).read_bytes())
        self.assertEqual(old_task, new_task)
        old.pop('instructions')
        new.pop('instructions')
        self.assertEqual(old, new)
        self.assertIsNone(new['result_template']['executed_prompt'])

    def test_fractional_image_bounds_preserve_all_intersecting_pixel_cells_and_true_overlap(self):
        self.arts[0]['box'] = [200.5, 200.5, 199, 199]
        boards = self.prepare(reference_policy=POLICY)
        self.assertTrue(boards)
        member = next(m for b in boards for m in b['members'] if m['id'] == 'photo')
        self.assertEqual(member['box'], [200.5, 200.5, 199, 199])
        self.assertEqual(member['context_exclusions'][0]['source_box_pixels'], [220, 397, 160, 43])


class EmbeddedReferencePolicyController(ReviewedOutputFixture):
    def initialize(self, *, explicit=False):
        options = {'execution_policy': 'explicit-start/1',
                   'post_return_prompt_policy': 'audit-existing-return/1',
                   'delivery_policy': 'reviewed-existing-output/1'} if explicit else {}
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                                  source_layout_policy='source-layout-first/1', **options)

    def board_stage(self, *, legacy=False):
        self.initialize(explicit=True)
        if legacy:
            state = read_state(self.work)
            marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
            state.pop('board_reference_policy')
            marker.pop('board_reference_policy')
            save_state(self.work, state, 'simulate_pre_policy_r17_job')
            write_json(self.work / 'initialization.json', marker)
        from test_source_layer_reference_scope import sample_layout
        response = controller.run(self.work, data={'kind': 'layout', 'layout': sample_layout()})
        jobs = {j['role']: j for j in response['jobs']}
        self.begin(jobs['background'])
        self.submit(jobs['background'], self.bg)
        self.begin(jobs['foreground'])
        response = self.submit(jobs['foreground'], self.fg)
        board = next(j for j in response['jobs'] if j['role'] == 'board')
        return board, read_state(self.work)['tasks'][board['id']]

    def test_policy_is_only_new_source_first_and_is_frozen_in_marker_and_state(self):
        self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        state = read_state(self.work)
        self.assertEqual(marker.get('board_reference_policy'), POLICY)
        self.assertEqual(state.get('board_reference_policy'), POLICY)
        for value in (None, 'unsupported'):
            changed = copy.deepcopy(state)
            if value is None:
                changed.pop('board_reference_policy')
            else:
                changed['board_reference_policy'] = value
            save_state(self.work, changed, 'private_policy_corruption')
            with self.assertRaisesRegex(ValueError, 'reference|policy'):
                controller.run(self.work)
        save_state(self.work, state, 'restore')

    def test_old_source_first_marker_and_state_stay_without_policy(self):
        self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        state = read_state(self.work)
        marker.pop('board_reference_policy', None)
        state.pop('board_reference_policy', None)
        write_json(self.work / 'initialization.json', marker)
        save_state(self.work, state, 'old_r17_compatible_state')
        self.assertEqual(controller.run(self.work)['kind'], 'describe')
        self.assertNotIn('board_reference_policy', read_state(self.work))

    def test_normal_entry_transfers_new_policy_and_board_signature_while_legacy_job_does_not(self):
        _, task = self.board_stage()
        self.assertEqual(task['board'].get('board_reference_policy'), POLICY)
        self.assertIn('Reference ownership follows.', (self.work / task['brief']).read_text('utf-8'))
        original = read_state(self.work)
        for value in (None, 'unsupported'):
            changed = copy.deepcopy(original)
            board = changed['tasks'][task['id']]['board']
            if value is None:
                board.pop('board_reference_policy')
            else:
                board['board_reference_policy'] = value
            save_state(self.work, changed, 'private_board_policy_mutation')
            with self.assertRaisesRegex(ValueError, 'reference policy'):
                controller.run(self.work)
        save_state(self.work, original, 'restore')

    def test_legacy_normal_entry_keeps_original_board_metadata_prompt_and_goal(self):
        public, task = self.board_stage(legacy=True)
        self.assertNotIn('board_reference_policy', task['board'])
        self.assertTrue(all('context_exclusions' not in m for m in task['board']['members']))
        self.assertEqual((self.work / task['brief']).read_text('utf-8'),
                         old_module('prompts').board_brief(task['board']))
        permit = self.begin(public)
        request = json.loads(Path(permit['request_file']).read_text('utf-8'))['request']
        self.assertEqual(permit['instructions'], old_module('execution')._invocation_instructions(task, request))

    def test_unknown_interrupted_marker_and_marker_only_removal_are_rejected(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        marker['board_reference_policy'] = 'unsupported'
        write_json(self.work / 'initialization.json', marker)
        with mock.patch.object(controller, '_runtime'), self.assertRaisesRegex(ValueError, 'reference policy'):
            controller.run(self.work)
        self.assertFalse((self.work / 'state.json').exists())

    def test_non_source_first_default_has_no_new_reference_policy(self):
        self.start()
        self.assertNotIn('board_reference_policy', read_state(self.work))
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertNotIn('board_reference_policy', marker)

    def test_marker_only_policy_removal_cannot_downgrade_new_job(self):
        self.initialize()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        marker.pop('board_reference_policy')
        write_json(self.work / 'initialization.json', marker)
        with self.assertRaisesRegex(ValueError, 'reference policy'):
            controller.run(self.work)


if __name__ == '__main__':
    unittest.main()
