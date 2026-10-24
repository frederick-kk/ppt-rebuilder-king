"""Reject board inputs posing as generated returns; no image-service calls."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import zipfile

from PIL import Image, ImageDraw, PngImagePlugin

import test_native_workflow as workflow

c = workflow.c
read_state = workflow.read_state
save_state = workflow.save_state
sha256 = workflow.sha256


class BoardResultProvenance(unittest.TestCase):
    def setUp(self):
        self.helper = workflow.NativeWorkflowContracts(methodName='runTest')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.work, unused, unused_description = self.helper.ready()
        dispatch = c.run(self.work, data={'kind': 'layout', 'layout': {'items': [self.helper.art]}})
        self.public = next(t for t in dispatch['jobs'] if t['role'] == 'board')
        self.state = read_state(self.work)
        self.task = self.state['tasks'][self.public['id']]
        self.reference = self.work / next(iter(self.task['references']))

    def submit(self, path):
        c._submit(self.work, self.state, {**self.public['result_template'], 'file': str(path)})

    def rejected(self, path, *, check_pending=True):
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'reference input'):
            self.submit(path)
        saved = read_state(self.work)
        task = saved['tasks'][self.task['id']]
        self.assertEqual(task['status'], 'failed')
        self.assertNotIn('result', task)
        self.assertEqual((self.work / task['attempts'][-1]['raw']).read_bytes(), original)
        self.assertFalse(any(t.get('result') for t in saved['tasks'].values() if t['role'] == 'board'))
        if not check_pending:
            return
        before = (self.work / 'state.json').read_bytes()
        paused = c.run(self.work)
        self.assertEqual(paused['kind'], 'paused')
        self.assertFalse(paused['generate_more'])
        self.assertNotIn('jobs', paused)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_same_reference_path_is_preserved_and_rejected(self):
        self.rejected(self.reference)

    def test_renamed_byte_copy_is_rejected(self):
        candidate = self.helper.root / 'renamed-return.png'
        candidate.write_bytes(self.reference.read_bytes())
        self.rejected(candidate)

    def test_reencoded_equal_rgba_pixels_are_rejected(self):
        candidate = self.helper.root / 'reencoded-return.png'
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('fixture', 'different PNG bytes, same input pixels')
        with Image.open(self.reference) as image:
            image.convert('RGBA').save(candidate, pnginfo=metadata, compress_level=0)
        self.assertNotEqual(sha256(candidate), sha256(self.reference))
        self.rejected(candidate)

    def test_second_reference_source_is_also_rejected(self):
        source = self.work / list(self.task['references'])[1]
        candidate = self.helper.root / 'source-renamed.png'
        candidate.write_bytes(source.read_bytes())
        self.rejected(candidate)

    def test_recorded_reference_sha_rejects_copy_even_if_input_now_differs(self):
        candidate = self.helper.root / 'recorded-reference.png'
        candidate.write_bytes(self.reference.read_bytes())
        self.reference.write_bytes(self.reference.read_bytes() + b'changed input')
        # Exercise registration directly: ordinary run also rejects changed inputs.
        self.rejected(candidate, check_pending=False)

    def test_independent_native_board_is_accepted_and_locked(self):
        candidate = self.helper.root / 'independent-synthetic-return.png'
        image = Image.new('RGBA', tuple(self.task['board']['size']), (0, 0, 0, 0))
        x, y, w, h = self.task['board']['members'][0]['slot']
        ImageDraw.Draw(image).rectangle((x + w // 3, y + h // 3, x + 2*w // 3, y + 2*h // 3),
                                        fill=(45, 67, 89, 255))
        image.save(candidate)
        self.submit(candidate)
        self.assertEqual(self.task['status'], 'recorded')
        self.assertEqual((self.work / self.task['result']['raw']).read_bytes(), candidate.read_bytes())
        before = (self.work / 'state.json').read_bytes()
        self.submit(candidate)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.submit(self.reference)

    def test_oversized_decoded_image_is_preserved_and_pauses(self):
        candidate = self.helper.root / 'independent-oversized-return.png'
        image = Image.new('RGBA', tuple(self.task['board']['size']), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((100, 100, 200, 200), fill=(45, 67, 89, 255))
        image.save(candidate)
        with mock.patch.object(Image, 'MAX_IMAGE_PIXELS', 1):
            try:
                self.submit(candidate)
            except Exception as exc:
                self.assertIsInstance(exc, ValueError)
                self.assertIn('no automatic generation', str(exc))
            else:
                self.fail('Oversized image registration must stop safely')
        task = read_state(self.work)['tasks'][self.task['id']]
        self.assertEqual(task['status'], 'failed')
        self.assertNotIn('result', task)
        self.assertEqual((self.work / task['attempts'][-1]['raw']).read_bytes(), candidate.read_bytes())
        paused = c.run(self.work)
        self.assertEqual(paused['kind'], 'paused')
        self.assertFalse(paused['generate_more'])
        self.assertNotIn('jobs', paused)

    def test_preexisting_recorded_reference_is_not_revalidated_or_relabelled(self):
        self.task.update(status='recorded', result={'sha256': sha256(self.reference),
                                                  'raw': 'historical-file.png'})
        before = copy.deepcopy(self.state)
        self.submit(self.reference)
        self.assertEqual(self.state, before)

    def test_board_dispatch_and_waiting_registration_explain_input_output_boundary(self):
        self.assertTrue(self.public['execution_contract'].get('reference_inputs_are_not_outputs'))
        self.assertIn('reference input', self.public['execution_contract']['instructions'])
        waiting = c.run(self.work)
        pending = next(t for t in waiting['pending_results'] if t['id'] == self.task['id'])
        self.assertIn('reference input', pending.get('instructions', ''))
        self.assertNotIn('requested_outputs', pending)

    def test_violation_acceptance_is_scoped_to_the_named_task(self):
        self.task['generation_violations'] = [{'acknowledged': False}]
        paused = c._drive(self.work, self.state)
        self.assertEqual(paused['kind'], 'paused')
        self.assertIn('named task', paused['instructions'])
        self.assertIn('unfinished', paused['instructions'])

    @unittest.skipUnless(os.environ.get('PRK_NATIVE_FAILURE_DIAGNOSTIC'),
                         'actual native-alpha diagnostic not supplied')
    def test_three_actual_diagnostic_reference_returns_are_rejected(self):
        with zipfile.ZipFile(os.environ['PRK_NATIVE_FAILURE_DIAGNOSTIC']) as archive:
            state_name = next(n for n in archive.namelist()
                              if n.endswith('/current_job/ppt_rebuilder_job_current/state.json'))
            prefix = state_name.rsplit('/', 1)[0] + '/'
            evidence = json.loads(archive.read(state_name))
            boards = [t for t in evidence['tasks'].values() if t['role'] == 'board']
            self.assertEqual(len(boards), 3)
            for number, original_task in enumerate(boards):
                with self.subTest(board=original_task['id']):
                    folder = self.work / ('actual-reference-' + str(number))
                    folder.mkdir()
                    refs = []
                    for index, reference in enumerate(original_task['references']):
                        path = folder / ('input-' + str(index) + '.png')
                        path.write_bytes(archive.read(prefix + reference))
                        refs.append(path)
                    raw = folder / 'adopted-return.png'
                    raw.write_bytes(archive.read(prefix + original_task['result']['raw']))
                    self.assertEqual(raw.read_bytes(), refs[0].read_bytes())
                    board = copy.deepcopy(original_task['board'])
                    board['image'] = str(refs[0])
                    task = c._task(self.work, 'board', 'actual-' + str(number),
                                   'Read-only actual diagnostic rejection fixture', refs,
                                   board=board, background_mode=workflow.MODE)
                    task['issued'] = True
                    self.state['tasks'] = {task['id']: task}
                    self.state['active_batch'] = [task['id']]
                    self.state['phase'] = 'boards'
                    save_state(self.work, self.state, 'actual_diagnostic_test_only')
                    self.task = task
                    self.public = c._public_task(self.work, task)
                    self.rejected(raw)


if __name__ == '__main__':
    unittest.main()
