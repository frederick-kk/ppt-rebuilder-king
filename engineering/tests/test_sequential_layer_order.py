"""Explicit layer order keeps background, foreground and description sequential."""
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from PIL import Image, ImageDraw
from newpath import binding, controller
from newpath.storage import read_state, save_state, sha256, write_json
from test_result_role_binding import ReviewedOutputFixture

ORDER = 'background-then-foreground/1'


def old_controller():
    path = PACKAGE.parent / 'source-r24/compat-runtime/newpath/controller.py'
    if not path.is_file():
        raise unittest.SkipTest('Retained R24 module unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r24_sequential_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SequentialLayerInterface(unittest.TestCase):
    def test_explicit_policy_is_optional_at_the_existing_controller_entry(self):
        self.assertEqual(getattr(controller, 'SEQUENTIAL_LAYER_ORDER', None), 'background-then-foreground/1')
        self.assertIn('layer_generation_order', inspect.signature(controller.run).parameters)
        self.assertIsNone(inspect.signature(controller.run).parameters['layer_generation_order'].default)


class SequentialLayerOrder(ReviewedOutputFixture):
    def start_sequential(self):
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                recovery_policy='bounded-recovery/1', layer_generation_order=ORDER)

    def first_background(self):
        action = self.start_sequential()
        self.assertEqual(action['kind'], 'generate')
        self.assertEqual([job['role'] for job in action['jobs']], ['background'])
        return action['jobs'][0]

    def layers_ready(self):
        background = self.first_background()
        self.begin(background)
        action = self.submit(background, self.bg)
        self.assertEqual([job['role'] for job in action['jobs']], ['foreground'])
        foreground = action['jobs'][0]
        self.begin(foreground)
        description = self.submit(foreground, self.fg)
        self.assertEqual(description['kind'], 'describe')
        return background, foreground, description

    def test_initial_action_issues_only_background_and_freezes_order_without_description(self):
        action = self.start_sequential()
        self.assertEqual([job['role'] for job in action['jobs']], ['background'])
        self.assertEqual(action['layer_generation_order'], ORDER)
        state = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(state.get('layer_generation_order'), ORDER)
        self.assertEqual(marker.get('layer_generation_order'), ORDER)
        self.assertEqual(state['phase'], 'layers')
        self.assertIsNone(state['layout'])
        self.assertFalse((self.work / 'layout.json').exists())
        self.assertNotIn('source_layout_policy', state)
        self.assertNotIn('reading_images', state['source'])
        tasks = {task['role']: task for task in state['tasks'].values()}
        self.assertTrue(tasks['background']['issued'])
        self.assertFalse(tasks['foreground']['issued'])
        self.assertEqual(tasks['foreground']['execution']['state'], 'not_started')
        self.assertEqual(tasks['foreground'].get('layer_generation_order'), ORDER)

    def test_new_order_preparation_guidance_only_describes_the_current_layer(self):
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work, source=self.source, layer_generation_order=ORDER)
        self.assertEqual([job['role'] for job in action['jobs']], ['background'])
        self.assertIn('Only the listed current layer', action['instructions'])
        self.assertNotIn('Parallel calls are optional', action['instructions'])

    def test_only_successful_background_unlocks_foreground_and_then_one_description(self):
        background, foreground, description = self.layers_ready()
        state = read_state(self.work)
        self.assertEqual(state['phase'], 'describe')
        self.assertIsNone(state['layout'])
        self.assertEqual(list(state['tasks']), [background['id'], foreground['id']])
        self.assertEqual(controller.run(self.work), description)
        self.assertEqual([event['event'] for event in state['events']].count('layers_ready'), 1)
        self.assertNotIn('description_frozen', [event['event'] for event in state['events']])

    def test_waiting_background_never_exposes_or_begins_the_foreground(self):
        background = self.first_background()
        self.begin(background)
        waiting = controller.run(self.work)
        self.assertEqual(waiting['kind'], 'await_results')
        self.assertEqual(waiting['tasks'], [background['id']])
        state = read_state(self.work)
        foreground = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
        early = controller._public_task(self.work, foreground)
        before = (self.work / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'dispatch|issued|background'):
            self.begin(early)
        with self.assertRaisesRegex(ValueError, 'dispatch|issued|background'):
            controller.run(self.work, data={'task_id': early['id'], 'role': 'foreground',
                'attempt': 0, 'file': str(self.fg), 'invocation_count': 1, 'output_count': 1})
        self.assertEqual((self.work / 'state.json').read_bytes(), before)
        self.assertTrue(self.fg.is_file())

    def test_background_role_repair_stays_before_unissued_foreground(self):
        background = self.first_background()
        self.begin(background)
        repair = self.submit(background, self.bg, role_review=self.review(background, self.bg,
            matches_task=False, foreground_text_absent=False, observed='Actual synthetic background has wrong assigned subjects.'))
        self.assertEqual([(job['role'], job['attempt']) for job in repair['jobs']], [('background', 1)])
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 1)
        self.assertFalse(next(task for task in state['tasks'].values() if task['role'] == 'foreground')['issued'])
        self.begin(repair['jobs'][0])
        alternate = self.root / 'background-repair.png'
        Image.new('RGB', (1920, 1080), 'purple').save(alternate)
        next_action = self.submit(repair['jobs'][0], alternate)
        self.assertEqual([(job['role'], job['attempt']) for job in next_action['jobs']], [('foreground', 0)])

    def test_foreground_repair_preserves_completed_background(self):
        background = self.first_background()
        self.begin(background)
        foreground = self.submit(background, self.bg)['jobs'][0]
        preserved = copy.deepcopy(read_state(self.work)['tasks'][background['id']])
        self.begin(foreground)
        repair = self.submit(foreground, self.fg, role_review=self.review(foreground, self.fg,
            matches_task=False, page_elements_match=False, observed='The synthetic foreground lacks required members.'))
        self.assertEqual([(job['role'], job['attempt']) for job in repair['jobs']], [('foreground', 1)])
        self.assertEqual(read_state(self.work)['tasks'][background['id']], preserved)

    def test_r24_pixel_hash_registration_correction_does_not_unlock_foreground_early(self):
        background = self.first_background()
        self.begin(background)
        pending = self.submit(background, self.bg, role_review=self.review(
            background, self.bg, output_sha256=binding.pixel_digest(self.bg)))
        self.assertEqual(pending['kind'], 'registration_required')
        state = read_state(self.work)
        self.assertEqual(state['recovery']['used'], 0)
        self.assertFalse(next(task for task in state['tasks'].values() if task['role'] == 'foreground')['issued'])
        action = self.submit(background, self.bg)
        self.assertEqual([job['role'] for job in action['jobs']], ['foreground'])

    def test_repeated_background_callback_never_creates_a_second_layer_invocation(self):
        background = self.first_background()
        self.begin(background)
        foreground = self.submit(background, self.bg)['jobs'][0]
        self.begin(foreground)
        before = copy.deepcopy(read_state(self.work)['tasks'][background['id']])
        waiting = self.submit(background, self.bg)
        self.assertEqual(waiting['kind'], 'await_results')
        self.assertEqual(waiting['tasks'], [foreground['id']])
        self.assertEqual(read_state(self.work)['tasks'][background['id']], before)
        self.assertEqual(read_state(self.work)['recovery']['used'], 0)

    def test_assembly_references_are_really_made_from_the_saved_generated_foreground(self):
        self.layers_ready()
        raw = {'items': [{'id': 'blue-component', 'kind': 'art', 'box': [100, 100, 500, 500],
                          'description': 'Actual blue square fixture from the saved foreground.'}],
               'primary_wordart_id': None}
        action = controller.run(self.work, data={'kind': 'layout', 'layout': raw})
        self.assertEqual([job['role'] for job in action['jobs']], ['board'])
        state = read_state(self.work)
        board = next(task for task in state['tasks'].values() if task['role'] == 'board')['board']
        self.assertIsNone(board.get('source_board_reference_policy'))
        with Image.open(board['image']) as image:
            colours = image.convert('RGBA').getdata()
            self.assertGreater(sum(1 for red, green, blue, alpha in colours
                                   if blue > 240 and red < 10 and green < 10 and alpha > 200), 10000)
        with Image.open(self.work / 'input/working.png') as source:
            self.assertEqual(source.convert('RGB').getextrema(), ((255, 255), (255, 255), (255, 255)))
        self.assertEqual(state['layout']['primary_wordart_id'], None)
        self.assertEqual(sum(event['event'] == 'description_frozen' for event in state['events']), 1)

    def test_optional_wordart_keeps_the_existing_full_source_reference_after_layers(self):
        self.layers_ready()
        action = controller.run(self.work, data={'kind': 'layout', 'layout': {'items': [{
            'id': 'written', 'kind': 'art', 'box': [100, 100, 800, 80],
            'description': 'Complex writing.', 'lettering': 'TITLE'}], 'primary_wordart_id': 'written'}})
        title = next(job for job in action['jobs'] if job['role'] == 'wordart')
        self.assertEqual(title['references'], [str(self.work / 'input/working.png')])
        task = read_state(self.work)['tasks'][title['id']]
        self.assertNotIn('source_detail', task)
        self.assertNotIn('primary_source_reference_policy', task)

    def test_existing_component_extraction_and_native_assembly_complete_after_the_two_layers(self):
        self.layers_ready()
        action = controller.run(self.work, data={'kind': 'layout', 'layout': {
            'items': [{'id': 'blue-component', 'kind': 'art', 'box': [100, 100, 500, 500],
                       'description': 'Synthetic blue square.'},
                      {'id': 'body', 'kind': 'text', 'box': [100, 720, 700, 50],
                       'text': 'Editable after both layers', 'size': 30}],
            'primary_wordart_id': None}})
        board_task = action['jobs'][0]
        board = read_state(self.work)['tasks'][board_task['id']]['board']
        left, top, width, height = board['members'][0]['slot']
        image = Image.new('RGBA', tuple(board['size']), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((left + 40, top + 40, left + width - 40, top + height - 40), fill=(0, 0, 255, 255))
        draw.ellipse((left + 100, top + 100, left + 150, top + 150), fill=(255, 255, 255, 255))
        returned = self.root / 'synthetic-new-board.png'
        image.save(returned)
        self.begin(board_task)
        done = self.submit(board_task, returned)
        self.assertEqual(done['kind'], 'done')
        self.assertEqual(len(read_state(self.work)['assets']), 1)
        self.assertEqual(done['skipped'], [])
        with ZipFile(done['pptx']) as presentation:
            self.assertIsNone(presentation.testzip())
            xml = presentation.read('ppt/slides/slide1.xml')
            self.assertIn(b'Editable after both layers', xml)
            self.assertEqual(xml.count(b'<p:pic>'), 1)
        before = sha256(done['pptx'])
        resumed = controller.run(self.work)
        self.assertEqual(resumed['kind'], 'done')
        self.assertEqual(sha256(done['pptx']), before)

    def test_description_after_layers_explains_optional_primary_without_extra_analysis(self):
        _, _, description = self.layers_ready()
        for phrase in ('primary_wordart_id', 'null', 'native text', 'source_bounds'):
            self.assertIn(phrase, description['instructions'])
        self.assertNotIn('The largest artistic-writing group alone will use standalone', description['instructions'])
        self.assertIsNone(read_state(self.work)['layout'])

    def test_user_stop_and_resume_continue_only_the_next_foreground(self):
        background = self.first_background()
        self.begin(background)
        held = self.submit(background, self.bg, stop_remaining_generation=True,
                           violation_authorization='Synthetic test user stops the remaining first invocations.')
        self.assertEqual(held['kind'], 'paused')
        self.assertEqual(held['reason'], 'user_stopped_remaining_generation')
        resumed = controller.run(self.work, data={'kind': 'resume_unstarted',
            'authorization': 'Synthetic test user now resumes the remaining layer.'})
        self.assertEqual([job['role'] for job in resumed['jobs']], ['foreground'])
        self.assertEqual(read_state(self.work)['tasks'][background['id']]['status'], 'recorded')

    def test_policy_marker_state_and_layer_task_are_frozen(self):
        self.first_background()
        original = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        for target in ('state', 'marker', 'task'):
            with self.subTest(target=target):
                state, saved = copy.deepcopy(original), copy.deepcopy(marker)
                if target == 'state':
                    state.pop('layer_generation_order')
                elif target == 'marker':
                    saved.pop('layer_generation_order')
                else:
                    next(iter(state['tasks'].values())).pop('layer_generation_order')
                write_json(self.work / 'state.json', state)
                write_json(self.work / 'initialization.json', saved)
                with self.assertRaisesRegex(ValueError, 'order|frozen|layer'):
                    controller.run(self.work)
        write_json(self.work / 'state.json', original)
        write_json(self.work / 'initialization.json', marker)
        with self.assertRaises(ValueError):
            controller.run(self.work, layer_generation_order='other')

    def test_invalid_policy_combinations_are_rejected_before_creating_jobs(self):
        for index, options in enumerate(({'layer_generation_order': 'other'},
                {'layer_generation_order': ORDER, 'source_layout_policy': 'source-layout-first/1'},
                {'layer_generation_order': ORDER, 'source_board_reference_policy': 'direct-source-board-reference/1'},
                {'layer_generation_order': ORDER, 'primary_source_reference_policy': 'source-parts-title-reference/1'})):
            with self.subTest(options=options):
                work = self.root / ('invalid-' + str(index))
                with self.assertRaises(ValueError), mock.patch.object(controller, '_runtime'):
                    controller.run(work, source=self.source, **options)
                self.assertFalse(work.exists())

    def test_interrupted_initialization_inherits_the_saved_order(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('Synthetic interruption')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic interruption'):
                self.start_sequential()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker.get('layer_generation_order'), ORDER)
        with mock.patch.object(controller, '_runtime'):
            resumed = controller.run(self.work)
        self.assertEqual([job['role'] for job in resumed['jobs']], ['background'])
        self.assertEqual(read_state(self.work).get('layer_generation_order'), ORDER)

    def test_old_job_and_old_interrupted_marker_cannot_enable_the_new_order(self):
        with mock.patch.object(controller, '_runtime'):
            controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                recovery_policy='bounded-recovery/1')
        before = (self.work / 'state.json').read_bytes()
        with self.assertRaises(ValueError):
            controller.run(self.work, layer_generation_order=ORDER)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)
        interrupted = self.root / 'old-interrupted'
        with mock.patch.object(controller, '_runtime'), mock.patch.object(
                controller, 'prepare_source', side_effect=RuntimeError('Synthetic interruption')):
            with self.assertRaises(RuntimeError):
                controller.run(interrupted, source=self.source)
        with self.assertRaises(ValueError), mock.patch.object(controller, '_runtime'):
            controller.run(interrupted, layer_generation_order=ORDER)

    def test_absent_order_keeps_old_default_tasks_requests_and_source_first_choice(self):
        with mock.patch.object(controller, '_runtime'):
            action = controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                post_return_prompt_policy='audit-existing-return/1', delivery_policy='reviewed-existing-output/1',
                recovery_policy='bounded-recovery/1')
        self.assertEqual([job['role'] for job in action['jobs']], ['background', 'foreground'])
        state = read_state(self.work)
        self.assertNotIn('layer_generation_order', state)
        self.assertNotIn('layer_generation_order', json.loads((self.work / 'initialization.json').read_text('utf-8')))
        old = old_controller()
        previous, current = copy.deepcopy(state), copy.deepcopy(state)
        for candidate in (previous, current):
            for task in candidate['tasks'].values():
                task['issued'] = False
        with mock.patch.object(controller, 'save_state'), mock.patch.object(old, 'save_state'):
            self.assertEqual(controller._drive(self.work, current), old._drive(self.work, previous))
        self.assertEqual(current, previous)
        alternate = self.root / 'source-first-still-supported'
        with mock.patch.object(controller, '_runtime'):
            source_first = controller.run(alternate, source=self.source, source_layout_policy='source-layout-first/1')
        self.assertEqual(source_first['kind'], 'describe')
        self.assertNotIn('layer_generation_order', read_state(alternate))


if __name__ == '__main__':
    unittest.main()
