"""Native material contracts; local synthetic returns are not provider evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(os.environ.get('PRK_PACKAGE_UNDER_TEST', str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller as c
from newpath import prompts
from newpath.storage import read_state, save_state, sha256

# Keep this literal: a RED run must fail on behavior, never on a missing module.
MODE = 'native-transparent-materials/1'
TITLE_MODE = 'standalone-native-wordart/2'


class NativeWorkflowContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-native-contract-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.png'
        Image.new('RGB', (160, 90), 'white').save(self.source)
        self.background = self.root / 'background.png'
        Image.new('RGB', (1920, 1080), 'white').save(self.background)
        self.foreground = self.root / 'foreground.png'
        image = Image.new('RGBA', (1920, 1080), (23, 47, 71, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 170, 110), fill=(255, 255, 255, 255))
        draw.rectangle((220, 40, 290, 90), fill=(255, 0, 255, 255))
        draw.rectangle((360, 180, 410, 220), fill=(0, 255, 0, 255))
        draw.point((1500, 850), fill=(255, 255, 255, 1))
        # Foreground is a full-frame layer; source subjects may touch its edge.
        draw.point((0, 400), fill=(12, 34, 56, 127))
        image.save(self.foreground)
        self.legacy_foreground = self.root / 'legacy-foreground.png'
        image = Image.new('RGB', (1920, 1080), 'magenta')
        ImageDraw.Draw(image).rectangle((20, 20, 170, 110), fill='white')
        image.save(self.legacy_foreground)
        self.primary = {'id': 'main-title', 'kind': 'art', 'box': [100, 20, 900, 130],
                        'target': [100, 20, 900, 130], 'lettering': 'EXACT TITLE',
                        'description': 'white and purple brush title', 'z': 12}
        self.art = {'id': 'card', 'kind': 'art', 'box': [20, 200, 450, 300],
                    'description': 'white paper card with genuine magenta and green artwork', 'z': 4}

    def start(self, name='job', *, legacy=False):
        work = self.root / name
        if legacy:
            # Construct an old job while every task is still unissued. This
            # does not migrate or relabel an already dispatched native job.
            with mock.patch.object(c, '_runtime'), mock.patch.object(c, '_drive', return_value=None):
                c.run(work, source=self.source)
            state = read_state(work)
            state.pop('material_background_mode', None)
            state['tasks'] = {}
            for role in ('background', 'foreground'):
                task = c._task(work, role, state['source']['pixel_sha256'],
                               prompts.layer_brief(role, state['source']['transform']),
                               [work / state['source']['working']])
                state['tasks'][task['id']] = task
            save_state(work, state, 'synthetic_frozen_legacy_before_dispatch')
            first = c.run(work)
        else:
            with mock.patch.object(c, '_runtime'):
                first = c.run(work, source=self.source)
        return work, first

    def submit(self, work, task, path):
        return c.run(work, data={**task['result_template'], 'file': str(path)})

    def ready(self, name='job', *, legacy=False):
        work, first = self.start(name, legacy=legacy)
        tasks = {task['role']: task for task in first['jobs']}
        self.submit(work, tasks['background'], self.background)
        described = self.submit(work, tasks['foreground'],
                                self.legacy_foreground if legacy else self.foreground)
        self.assertEqual(described['kind'], 'describe')
        self.assertEqual(described['files'], [str(work / 'input/working.png'), str(work / 'input/ruler.png')])
        return work, first, described

    def test_new_job_persists_mode_and_requests_only_foreground_transparency(self):
        work, first = self.start()
        self.assertEqual(first['kind'], 'generate')
        self.assertEqual([task['role'] for task in first['jobs']], ['background', 'foreground'])
        state = read_state(work)
        self.assertEqual(state.get('material_background_mode'), MODE)
        for task in first['jobs']:
            self.assertEqual(task['size'], [1920, 1080])
            self.assertEqual(task['references'], [str(work / 'input/working.png')])
            self.assertEqual(task['requested_outputs'], 1)
            self.assertEqual(task['execution_contract']['max_invocations'], 1)
            self.assertEqual(task['execution_contract']['max_outputs'], 1)
            self.assertFalse(task['execution_contract']['automatic_retry'])
            self.assertEqual(task['transparent_background'], task['role'] == 'foreground')
            if task['role'] == 'foreground':
                self.assertEqual(task.get('background_mode'), MODE)
                self.assertEqual(state['tasks'][task['id']].get('background_mode'), MODE)
            else:
                self.assertNotIn('background_mode', task)

    def test_restart_keeps_native_contract_and_waiting_is_registration_only(self):
        work, first = self.start()
        before = (work / 'state.json').read_bytes()
        waiting = c.run(work)
        self.assertEqual(waiting['kind'], 'await_results')
        self.assertFalse(waiting['generate_more'])
        self.assertEqual((work / 'state.json').read_bytes(), before)
        self.assertEqual([task['id'] for task in waiting['pending_results']],
                         [task['id'] for task in first['jobs']])
        for task in waiting['pending_results']:
            self.assertEqual(task['action'], 'register_existing_output_only')
            for key in ('prompt_file', 'references', 'requested_outputs', 'execution_contract'):
                self.assertNotIn(key, task)
        state = read_state(work)
        front = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
        self.assertEqual(front.get('background_mode'), MODE)
        self.assertTrue(c._public_task(work, front)['transparent_background'])

    def test_native_foreground_registration_uses_alpha_and_keeps_all_rgba_bytes(self):
        work, first = self.start()
        tasks = {task['role']: task for task in first['jobs']}
        self.submit(work, tasks['background'], self.background)
        with mock.patch.object(c, 'validate_output', side_effect=AssertionError('native foreground used keyed validator')):
            result = self.submit(work, tasks['foreground'], self.foreground)
        self.assertEqual(result['kind'], 'describe')
        task = read_state(work)['tasks'][tasks['foreground']['id']]
        self.assertEqual(task['status'], 'recorded')
        self.assertEqual(task['result']['validation']['background_mode'], MODE)
        self.assertTrue(task['result']['validation']['transparent_background'])
        self.assertEqual((work / task['result']['raw']).read_bytes(), self.foreground.read_bytes())
        with Image.open(self.foreground) as expected, Image.open(work / task['result']['normalized']) as actual:
            np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))
        before = (work / 'state.json').read_bytes()
        self.submit(work, tasks['foreground'], self.foreground)
        self.assertEqual((work / 'state.json').read_bytes(), before)

    def test_opaque_foreground_is_preserved_and_pauses_without_more_generation(self):
        work, first = self.start()
        tasks = {task['role']: task for task in first['jobs']}
        self.submit(work, tasks['background'], self.background)
        opaque = self.root / 'opaque-foreground.png'
        Image.new('RGBA', (160, 90), (255, 255, 255, 255)).save(opaque)
        with self.assertRaisesRegex(ValueError, 'no automatic generation'):
            self.submit(work, tasks['foreground'], opaque)
        state = read_state(work)
        failed = state['tasks'][tasks['foreground']['id']]
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('result', failed)
        self.assertEqual((work / failed['attempts'][-1]['raw']).read_bytes(), opaque.read_bytes())
        self.assertEqual(len(state['tasks']), 2)
        before = copy.deepcopy(state)
        for unused in range(2):
            paused = c.run(work)
            self.assertEqual(paused['kind'], 'paused')
            self.assertFalse(paused['generate_more'])
            self.assertNotIn('jobs', paused)
        self.assertEqual(read_state(work), before)

    def test_layout_keeps_title_separate_and_every_ordinary_board_native(self):
        work, unused, unused_description = self.ready()
        result = c.run(work, data={'kind': 'layout', 'layout': {'items': [self.primary, self.art]}})
        self.assertEqual(result['kind'], 'generate')
        self.assertEqual([task['role'] for task in result['jobs']], ['wordart', 'board'])
        state = read_state(work)
        self.assertEqual(len(state['tasks']), 4)
        title, board = result['jobs']
        self.assertTrue(title['transparent_background'])
        self.assertEqual(title['background_mode'], TITLE_MODE)
        self.assertEqual(title['member']['lettering'], 'EXACT TITLE')
        self.assertEqual(title['references'], [str(work / 'input/working.png')])
        self.assertTrue(board['transparent_background'])
        self.assertEqual(board.get('background_mode'), MODE)
        stored = state['tasks'][board['id']]['board']
        self.assertEqual(stored.get('background_mode'), MODE)
        self.assertIsNone(stored['key'])
        self.assertEqual([member['id'] for member in stored['members']], ['card'])
        self.assertEqual(board['references'], [stored['image'], str(work / 'input/working.png')])
        self.assertEqual(stored['members'][0]['target'], self.art['box'])
        self.assertEqual(state['layout']['items'][0]['target'], self.primary['target'])

    def test_native_board_return_avoids_keyed_validator_and_retains_raw_bytes(self):
        work, unused, unused_description = self.ready()
        issued = c.run(work, data={'kind': 'layout', 'layout': {'items': [self.art]}})['jobs'][0]
        board = read_state(work)['tasks'][issued['id']]['board']
        path = self.root / 'native-board.png'
        image = Image.new('RGBA', tuple(board['size']), (23, 47, 71, 0))
        draw = ImageDraw.Draw(image)
        x, y, width, height = board['members'][0]['slot']
        draw.rectangle((x + width // 3, y + height // 3, x + 2 * width // 3, y + 2 * height // 3), fill=(255, 0, 255, 255))
        image.save(path)
        with mock.patch.object(c, 'validate_output', side_effect=AssertionError('native board used keyed validator')), \
             mock.patch.object(c, '_finish', return_value={'kind': 'synthetic-finish-boundary'}):
            self.submit(work, issued, path)
        recorded = read_state(work)['tasks'][issued['id']]
        self.assertEqual(recorded['status'], 'recorded')
        self.assertEqual(recorded['result']['validation']['background_mode'], MODE)
        self.assertEqual((work / recorded['result']['raw']).read_bytes(), path.read_bytes())
        self.assertNotIn('normalized', recorded['result'])

    def test_explicit_frozen_legacy_job_retains_keyed_tasks_and_no_mode_migration(self):
        work, first, unused = self.ready(legacy=True)
        self.assertTrue(all(not task['transparent_background'] for task in first['jobs']))
        self.assertNotIn('material_background_mode', read_state(work))
        result = c.run(work, data={'kind': 'layout', 'layout': {'items': [self.art]}})
        self.assertEqual([task['role'] for task in result['jobs']], ['board'])
        public = result['jobs'][0]
        self.assertFalse(public['transparent_background'])
        self.assertNotIn('background_mode', public)
        state = read_state(work)
        self.assertNotIn('material_background_mode', state)
        board = state['tasks'][public['id']]['board']
        self.assertIn(board['key'], ('magenta', 'green', 'cyan', 'yellow'))
        self.assertNotIn('background_mode', board)
        before = (work / 'state.json').read_bytes()
        self.assertEqual(c.run(work)['kind'], 'await_results')
        self.assertEqual((work / 'state.json').read_bytes(), before)

    def test_layer_policy_is_shared_and_title_remains_exact(self):
        background = prompts.layer_brief('background', {'offset': [0, 0], 'resized': [1920, 1080]})
        native_background = prompts.layer_brief('background', {'offset': [0, 0], 'resized': [1920, 1080]},
                                               background_mode=MODE)
        title = prompts.wordart_brief(self.primary)
        self.assertEqual(background, native_background)
        self.assertIn('complete embedded images', background)
        self.assertIn('one opaque PNG covering the full working rectangle', background)
        self.assertEqual(json.loads(background.split('Geometry: ', 1)[1]),
                         {'working_pixels': [1920, 1080], 'content_offset': [0, 0], 'content_size': [1920, 1080]})
        self.assertEqual(hashlib.sha256(title.encode()).hexdigest(),
                         '797efb2aafbd6f90249b483c9eb0894bfc53a6dc84fa94a42bb19a638901991b')
        work, first, unused = self.ready()
        background_task = next(task for task in first['jobs'] if task['role'] == 'background')
        self.assertEqual(Path(background_task['prompt_file']).read_text(encoding='utf-8'), native_background)
        front = next(task for task in first['jobs'] if task['role'] == 'foreground')
        foreground_text = Path(front['prompt_file']).read_text(encoding='utf-8')
        self.assertIn('alpha', foreground_text.lower())
        self.assertNotIn('uniform RGB(255,0,255)', foreground_text)
        self.assertNotIn('must be solid magenta', foreground_text)
        self.assertNotIn('use no transparent field', foreground_text)
        result = c.run(work, data={'kind': 'layout', 'layout': {'items': [self.art]}})
        board_text = Path(result['jobs'][0]['prompt_file']).read_text(encoding='utf-8')
        self.assertIn('alpha', board_text.lower())
        self.assertNotIn('uniform opaque', board_text)
        self.assertIn(self.art['description'], board_text)
        self.assertIn('Image 2', board_text)
        self.assertIn('slot_pixels', board_text)

    def test_native_queue_dispatches_two_then_three_then_two_with_one_description(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/headline-plans.json').read_text(encoding='utf-8'))['cases']['round-a']
        work, first, unused = self.ready()
        result = c.run(work, data={'kind': 'layout', 'layout': {'items': fixture['items']}})
        dispatched = [task['id'] for task in first['jobs']]
        batch_sizes = [len(first['jobs'])]
        with mock.patch.object(c, '_finish', return_value={'kind': 'synthetic-finish-boundary'}) as finish:
            while result['kind'] == 'generate':
                batch = result['jobs']
                batch_sizes.append(len(batch))
                self.assertLessEqual(len(batch), 3)
                for public in batch:
                    self.assertNotIn(public['id'], dispatched)
                    dispatched.append(public['id'])
                    self.assertTrue(public['transparent_background'])
                    state = read_state(work)
                    pending = [task for task in state['tasks'].values()
                               if task['issued'] and task['status'] != 'recorded']
                    self.assertLessEqual(len(pending), 3)
                    path = self.root / (public['id'] + '.png')
                    if public['role'] == 'wordart':
                        image = Image.new('RGBA', (174, 43), (23, 47, 71, 0))
                        ImageDraw.Draw(image).rectangle((8, 8, 166, 35), fill='white')
                    else:
                        board = state['tasks'][public['id']]['board']
                        image = Image.new('RGBA', tuple(board['size']), (23, 47, 71, 0))
                        draw = ImageDraw.Draw(image)
                        for member in board['members']:
                            x, y, width, height = member['slot']
                            draw.rectangle((x + width // 3, y + height // 3,
                                            x + 2 * width // 3, y + 2 * height // 3),
                                           fill=(255, 0, 255, 255))
                    image.save(path)
                    result = self.submit(work, public, path)
                    self.assertNotEqual(result['kind'], 'describe')
            self.assertEqual(result['kind'], 'synthetic-finish-boundary')
            finish.assert_called_once()
        self.assertEqual(batch_sizes, [2, 3, 2])
        self.assertEqual(len(dispatched), 7)
        state = read_state(work)
        self.assertEqual(len(state['tasks']), 7)
        self.assertTrue(all(task['status'] == 'recorded' and task['attempt'] == 0
                            for task in state['tasks'].values()))
        self.assertEqual(sum(event['event'] == 'description_frozen' for event in state['events']), 1)
        self.assertEqual(sum(event['event'] == 'layers_ready' for event in state['events']), 1)

    def test_real_layout_fixtures_keep_board_geometry_order_count_and_three_active_limit(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/headline-plans.json').read_text(encoding='utf-8'))['cases']
        for name, fixture in cases.items():
            with self.subTest(case=name):
                native_work, unused, unused_description = self.ready(name + '-native')
                legacy_work, unused, unused_description = self.ready(name + '-legacy', legacy=True)
                layout = {'kind': 'layout', 'layout': {'items': fixture['items']}}
                native = c.run(native_work, data=layout)
                legacy = c.run(legacy_work, data=layout)
                self.assertEqual([task['role'] for task in native['jobs']], ['wordart', 'board', 'board'])
                self.assertEqual([task['role'] for task in legacy['jobs']], ['wordart', 'board', 'board'])
                self.assertEqual([task['transparent_background'] for task in native['jobs']], [True, True, True])
                native_state, legacy_state = read_state(native_work), read_state(legacy_work)
                self.assertEqual(len(native_state['tasks']), 7)
                self.assertEqual(len(native_state['tasks']), len(legacy_state['tasks']))
                def geometries(state):
                    return [{key: copy.deepcopy(board.get(key)) for key in
                             ('size', 'capacity', 'grid', 'members', 'planning_policy', 'pixel_budget')}
                            for task in state['tasks'].values() if task['role'] == 'board'
                            for board in [task['board']]]
                self.assertEqual(geometries(native_state), geometries(legacy_state))
                native_boards = [task['board'] for task in native_state['tasks'].values() if task['role'] == 'board']
                legacy_boards = [task['board'] for task in legacy_state['tasks'].values() if task['role'] == 'board']
                self.assertEqual(len(native_boards), 4)
                self.assertTrue(all(board.get('background_mode') == MODE and board['key'] is None for board in native_boards))
                self.assertTrue(set(board['signature'] for board in native_boards).isdisjoint(
                    board['signature'] for board in legacy_boards))
                self.assertEqual(len(native_state['active_batch']), 3)
                before = (native_work / 'state.json').read_bytes()
                waiting = c.run(native_work)
                self.assertEqual(waiting['kind'], 'await_results')
                self.assertEqual(len(waiting['pending_results']), 3)
                self.assertFalse(waiting['generate_more'])
                self.assertEqual((native_work / 'state.json').read_bytes(), before)
                self.assertEqual(sum(event['event'] == 'description_frozen' for event in native_state['events']), 1)
                self.assertEqual(sum(event['event'] == 'layers_ready' for event in native_state['events']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
