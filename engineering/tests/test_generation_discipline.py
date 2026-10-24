"""Audit caller-reported generation without claiming provider enforcement."""
import copy
import json
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from PIL import Image, ImageDraw
from newpath import controller as c
from newpath.storage import read_state, save_state


class GenerationDiscipline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.work = self.root / 'job'
        self.source = self.root / 'source.png'; Image.new('RGB', (160, 90), 'white').save(self.source)
        self.image = self.root / 'returned.png'; Image.new('RGB', (160, 90), 'blue').save(self.image)
        self.foreground = self.root / 'foreground.png'
        foreground = Image.new('RGBA', (160, 90), (0, 0, 0, 0))
        ImageDraw.Draw(foreground).rectangle((20, 20, 100, 70), fill='blue')
        foreground.save(self.foreground)
        with mock.patch.object(c, '_runtime'):
            self.first = c.run(self.work, source=self.source)
        self.background = next(t for t in self.first['jobs'] if t['role'] == 'background')

    def payload(self, task=None, **extra):
        task = task or self.background
        return {'task_id': task['id'], 'role': task['role'], 'attempt': task['attempt'],
                'file': str(self.foreground if task['role'] == 'foreground' else self.image), **extra}

    def state(self): return read_state(self.work)

    def test_dispatch_contract_is_single_call_single_output_no_automatic_retry(self):
        for task in self.first['jobs']:
            self.assertEqual(task['requested_outputs'], 1)
            contract = task.get('execution_contract', {})
            self.assertEqual(contract.get('max_invocations'), 1)
            self.assertEqual(contract.get('max_outputs'), 1)
            self.assertEqual(contract.get('provider_output_count'), 1)
            self.assertFalse(contract.get('automatic_retry', True))
            self.assertEqual(contract.get('count_provenance'), 'caller_reported_not_provider_verified')

    def test_waiting_descriptors_only_authorize_registration_and_state_does_not_change(self):
        before = (self.work / 'state.json').read_bytes()
        waiting = c.run(self.work)
        self.assertEqual(waiting['kind'], 'await_results')
        self.assertFalse(waiting['generate_more'])
        for task in waiting['pending_results']:
            self.assertEqual(task.get('action'), 'register_existing_output_only')
            for key in ('prompt_file', 'references', 'size', 'requested_outputs', 'execution_contract'):
                self.assertNotIn(key, task)
            self.assertIn('result_template', task)
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_more_than_one_invocation_pauses_and_preserves_known_return(self):
        result = c.run(self.work, data=self.payload(invocation_count=5))
        self.assertEqual(result['kind'], 'paused')
        task = self.state()['tasks'][self.background['id']]
        self.assertEqual(task['status'], 'failed')
        violation = task.get('generation_violations', [])[-1]
        self.assertEqual(violation['invocation_count'], 5)
        self.assertEqual((self.work / violation['preserved_files'][0]['raw']).read_bytes(), self.image.read_bytes())
        self.assertFalse(result['generate_more'])
        self.assertEqual(c.run(self.work)['kind'], 'paused')

    def test_multiple_outputs_are_audited_independently_of_invocation_count(self):
        result = c.run(self.work, data=self.payload(invocation_count=1, output_count=4))
        self.assertEqual(result['kind'], 'paused')
        violation = self.state()['tasks'][self.background['id']]['generation_violations'][-1]
        self.assertEqual(violation['output_count'], 4)
        self.assertEqual(violation['invocation_count'], 1)

    def test_multiple_returned_files_are_preserved_even_if_output_count_omitted(self):
        second = self.root / 'second.png'; Image.new('RGB', (80, 40), 'green').save(second)
        result = c.run(self.work, data=self.payload(returned_files=[str(self.image), str(second)]))
        self.assertEqual(result['kind'], 'paused')
        violation = self.state()['tasks'][self.background['id']]['generation_violations'][-1]
        actual = {(self.work / entry['raw']).read_bytes() for entry in violation['preserved_files']}
        self.assertEqual(actual, {self.image.read_bytes(), second.read_bytes()})
        self.assertEqual(violation['observed_returned_files'], 2)

    def test_identical_file_copies_do_not_by_themselves_prove_extra_generation(self):
        alias=self.root/'returned-copy.png';alias.write_bytes(self.image.read_bytes())
        result=c.run(self.work,data=self.payload(returned_files=[str(self.image),str(alias)]))
        self.assertEqual(result['kind'],'await_results')
        task=self.state()['tasks'][self.background['id']]
        self.assertFalse(task.get('generation_violations'))
        self.assertEqual(task['result'].get('observed_returned_paths'),2)
        self.assertEqual(task['result'].get('observed_unique_returned_files'),1)

    def test_explicit_multiple_outputs_still_pause_when_image_bytes_are_identical(self):
        alias=self.root/'second-output.png';alias.write_bytes(self.image.read_bytes())
        result=c.run(self.work,data=self.payload(output_count=2,returned_files=[str(self.image),str(alias)]))
        self.assertEqual(result['kind'],'paused')
        violation=self.state()['tasks'][self.background['id']]['generation_violations'][-1]
        self.assertEqual(violation.get('observed_returned_paths'),2)
        self.assertEqual(violation.get('observed_unique_returned_files'),1)

    def test_underreported_output_count_records_conflict_and_observed_file_lower_bound(self):
        second=self.root/'second.png';Image.new('RGB',(80,40),'green').save(second)
        c.run(self.work,data=self.payload(output_count=1,returned_files=[str(self.image),str(second)],
                    violation_authorization='Synthetic user: accept this already existing output.'))
        foreground=next(t for t in self.first['jobs'] if t['role']=='foreground')
        c.run(self.work,data=self.payload(foreground,invocation_count=1,output_count=1))
        def build(layout,background,assets,output):
            with ZipFile(output,'w') as archive:
                archive.writestr('ppt/presentation.xml','<presentation/>')
                archive.writestr('ppt/slides/slide1.xml','<slide/>')
            return {'warnings':[],'skipped':[]}
        with mock.patch.object(c,'build_presentation',side_effect=build):
            done=c.run(self.work,data={'kind':'layout','layout':{'items':[]}})
        self.assertIsNone(done.get('caller_reported_outputs'))
        self.assertGreaterEqual(done.get('observed_unique_returned_files_lower_bound',0),2)
        conflicts=done.get('reported_count_conflicts',[])
        self.assertTrue(any(row.get('field')=='output_count' and row.get('observed_unique_files_lower_bound')==2 for row in conflicts))
        task=self.state()['tasks'][self.background['id']]
        self.assertEqual(task['result']['output_count'],1)

    def test_late_duplicate_overspend_is_not_discarded_as_idempotent(self):
        c.run(self.work, data=self.payload(invocation_count=1))
        before = copy.deepcopy(self.state()['tasks'][self.background['id']]['result'])
        result = c.run(self.work, data=self.payload(invocation_count=5))
        self.assertEqual(result['kind'], 'paused')
        task = self.state()['tasks'][self.background['id']]
        self.assertEqual(task['result'], before)
        self.assertEqual(task['generation_violations'][-1]['invocation_count'], 5)

    def test_accept_existing_after_user_authorization_keeps_real_excess_count(self):
        c.run(self.work, data=self.payload(invocation_count=5, output_count=5))
        result = c.run(self.work, data=self.payload(invocation_count=5, output_count=5,
                       violation_authorization='User: keep this existing output; do not generate again.'))
        self.assertEqual(result['kind'], 'await_results')
        task = self.state()['tasks'][self.background['id']]
        self.assertEqual(task['result']['invocation_count'], 5)
        self.assertEqual(task['result'].get('output_count'), 5)
        self.assertTrue(all(v.get('acknowledged') for v in task['generation_violations']))
        self.assertEqual(task['generation_violations'][0]['authorization_provenance'], 'caller_reported_user_request_not_verified')
        self.assertEqual(task['attempt'], 0)

    def test_duplicate_authorized_acceptance_is_idempotent(self):
        payload=self.payload(invocation_count=5,output_count=5,
                             violation_authorization='Synthetic user: retain this existing result only.')
        c.run(self.work,data=payload)
        before=(self.work/'state.json').read_bytes()
        again=c.run(self.work,data=payload)
        self.assertEqual(again['kind'],'await_results')
        self.assertEqual((self.work/'state.json').read_bytes(),before)

    @unittest.skipUnless(os.environ.get('PRK_REAL_TITLE_DIAGNOSTIC'),'Real diagnostic ZIP not supplied')
    def test_supplied_real_five_backgrounds_and_two_foregrounds_pause_without_new_calls(self):
        # These actual provider returns predate native material transparency.
        # Keep this test-built saved job explicitly legacy; preserving their
        # overspend evidence must never label the opaque files native-accepted.
        state = self.state(); state.pop('material_background_mode', None)
        for task in state['tasks'].values():
            if task['role'] == 'foreground': task.pop('background_mode', None)
        save_state(self.work, state, 'synthetic_legacy_diagnostic_fixture')
        with ZipFile(os.environ['PRK_REAL_TITLE_DIAGNOSTIC']) as archive:
            records=json.loads(archive.read('execution/image_generation_records.json'))['records']
            for role,expected in (('background',5),('foreground',2)):
                selected=[r for r in records if r['record_role'].startswith(role)]
                self.assertEqual(len(selected),expected)
                files=[]
                for index,row in enumerate(selected):
                    path=self.root/(role+str(index)+'.png')
                    path.write_bytes(archive.read('generation_returns/'+row['return_file']));files.append(str(path))
                task=next(t for t in self.first['jobs'] if t['role']==role)
                result=c.run(self.work,data=self.payload(task,file=files[-1],returned_files=files,
                                                         output_count=expected))
                self.assertEqual(result['kind'],'paused')
                actual=self.state()['tasks'][task['id']]['generation_violations'][-1]
                self.assertEqual(actual['observed_returned_files'],expected)
                self.assertEqual(len(actual['preserved_files']),expected)
                self.assertIsNone(actual['invocation_count'])
        self.assertEqual(len(self.state()['tasks']),2)
        self.assertTrue(all(t['status']=='failed' for t in self.state()['tasks'].values()))
        self.assertFalse(c.run(self.work)['generate_more'])

    def test_authorization_for_another_task_does_not_clear_pause(self):
        c.run(self.work, data=self.payload(invocation_count=5))
        other = next(t for t in self.first['jobs'] if t['role'] == 'foreground')
        with self.assertRaises(ValueError):
            c.run(self.work, data=self.payload(other, violation_authorization='User approved the background.'))
        self.assertEqual(c.run(self.work)['kind'], 'paused')

    def test_waiting_task_cannot_be_retried_even_with_authorization(self):
        self.assertIn('retry_authorization', inspect.signature(c.run).parameters)
        with self.assertRaisesRegex(ValueError, 'failed'):
            c.run(self.work, retry_task=self.background['id'], retry_authorization='User: retry the background.')
        self.assertEqual(self.state()['tasks'][self.background['id']]['attempt'], 0)

    def test_failed_retry_requires_task_scoped_recorded_authorization(self):
        c.run(self.work, data={**self.payload(), 'outcome': 'failed', 'reason': 'Synthetic service failure'})
        with self.assertRaisesRegex(ValueError, 'authorization'):
            c.run(self.work, retry_task=self.background['id'])
        result = c.run(self.work, retry_task=self.background['id'], retry_authorization='Synthetic user: regenerate background once.')
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(result['jobs'][0]['attempt'], 1)
        task = self.state()['tasks'][self.background['id']]
        self.assertEqual(task['retry_authorizations'][0]['authorization'], 'Synthetic user: regenerate background once.')
        other = next(t for t in self.state()['tasks'].values() if t['role'] == 'foreground')
        self.assertEqual(other['attempt'], 0)
        self.assertFalse(other.get('retry_authorizations'))

    def test_count_metadata_is_truthful_and_does_not_infer_unknowns(self):
        for key in ('invocation_count', 'output_count'):
            for value in (True, 0, '1', -1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    c.run(self.work, data=self.payload(**{key: value}))
        c.run(self.work, data=self.payload())
        task = self.state()['tasks'][self.background['id']]
        self.assertIsNone(task['result']['invocation_count'])
        self.assertIsNone(task['result'].get('output_count'))

    def test_completion_separates_planned_registered_and_reported_counts(self):
        c.run(self.work, data=self.payload(invocation_count=5, output_count=5,
                   violation_authorization='Synthetic user: retain the existing result.'))
        foreground = next(t for t in self.first['jobs'] if t['role'] == 'foreground')
        # A program-made foreground has visible blue content and real alpha.
        c.run(self.work, data=self.payload(foreground, invocation_count=1, output_count=1))
        def build(layout, background, assets, output):
            with ZipFile(output, 'w') as archive:
                archive.writestr('ppt/presentation.xml', '<presentation/>')
                archive.writestr('ppt/slides/slide1.xml', '<slide/>')
            return {'warnings': [], 'skipped': []}
        with mock.patch.object(c, 'build_presentation', side_effect=build):
            done = c.run(self.work, data={'kind': 'layout', 'layout': {'items': []}})
        self.assertEqual(done['expected_generation_tasks'], 2)
        self.assertEqual(done.get('registered_outputs'), 2)
        self.assertEqual(done['caller_reported_invocations'], 6)
        self.assertEqual(done.get('caller_reported_outputs'), 6)
        self.assertEqual(done.get('generation_count_provenance'), 'caller_reported_not_provider_verified')


if __name__ == '__main__': unittest.main()
