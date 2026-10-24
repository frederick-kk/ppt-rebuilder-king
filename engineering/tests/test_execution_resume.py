"""Explicit image invocation audit and interrupted-batch recovery, no service calls."""
import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from zipfile import ZipFile

from PIL import Image, ImageDraw

PACKAGE=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath import controller as c
from newpath.storage import read_state,save_state,sha256

MODE='explicit-start/1'
IDENTITY={'package_id':'PPT Rebuilder King-20261023-delivery-test','archive_name':'test.zip',
          'manifest_sha256':'fixture-manifest','source_snapshot_sha256':'fixture-source',
          'verified_files':1,'identity_provenance':'local_manifest_verified'}


class ExplicitExecution(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='prk-explicit-execution-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.work=self.root/'job'
        self.source=self.root/'source.png';Image.new('RGB',(160,90),'white').save(self.source)
        self.bg=self.root/'bg.png';Image.new('RGB',(1920,1080),'white').save(self.bg)
        self.fg=self.root/'fg.png';image=Image.new('RGBA',(1920,1080),(0,0,0,0))
        ImageDraw.Draw(image).rectangle((100,100,600,600),fill='blue');image.save(self.fg)
        self.patch=mock.patch.object(c,'version',types.SimpleNamespace(runtime_identity=lambda expected_package=None:copy.deepcopy(IDENTITY)),create=True)
        self.patch.start();self.addCleanup(self.patch.stop)
        kwargs={'execution_policy':MODE} if 'execution_policy' in inspect.signature(c.run).parameters else {}
        with mock.patch.object(c,'_runtime'):
            self.first=c.run(self.work,source=self.source,**kwargs)

    def begin(self,task,**overrides):
        self.assertIn('begin_template',task,'A task must be prepared before the external invocation begins')
        data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic-test-image-tool'
        data['request'].update(overrides)
        result=c.run(self.work,data=data)
        self.assertEqual(result['kind'],'invoke_once')
        return data,result

    def submit(self,task,path,**extra):
        return c.run(self.work,data={**task['result_template'],'file':str(path),**extra})

    def layers(self):
        jobs={t['role']:t for t in self.first['jobs']}
        for role,path in [('background',self.bg),('foreground',self.fg)]:
            self.begin(jobs[role]);response=self.submit(jobs[role],path)
        self.assertEqual(response['kind'],'describe')

    def boards(self):
        self.layers();boards=[];items=[];returned=[]
        directory=self.work/'manual-references';directory.mkdir()
        for i in range(3):
            path=directory/f'board-{i}.png';image=Image.new('RGBA',(220,220),(0,0,0,0))
            ImageDraw.Draw(image).rectangle((35,35,180,180),fill='blue');image.save(path)
            output=self.root/f'output-{i}.png';image=Image.new('RGBA',(220,220),(0,0,0,0))
            ImageDraw.Draw(image).rectangle((45,45,170,170),fill=(40+i*30,80,110,255));image.save(output);returned.append(output)
            item={'id':f'art-{i}','kind':'art','box':[100+i*250,200,180,180],'description':'Synthetic independent square'}
            items.append(item)
            boards.append({'id':f'board-{i}','signature':f'synthetic-{i}','image':str(path),'size':[220,220],
                           'key':None,'background_mode':'native-transparent-materials/1',
                           'grid':{'rows':1,'columns':1,'margin':10,'gap':20,'cell_size':[200,200]},
                           'members':[{'id':item['id'],'box':item['box'],'target':item['box'],
                                       'description':item['description'],'slot':[10,10,200,200]}]})
        with mock.patch.object(c,'prepare_boards',return_value=boards):
            dispatch=c.run(self.work,data={'kind':'layout','layout':{'items':items}})
        self.assertEqual(len(dispatch['jobs']),3)
        return dispatch['jobs'],returned

    def test_startup_records_identity_and_explicit_policy(self):
        state=read_state(self.work)
        self.assertEqual(state.get('execution_policy'),MODE)
        self.assertEqual(state.get('runtime_identity'),IDENTITY)
        self.assertEqual(self.first.get('runtime_identity'),IDENTITY)

    def test_first_begin_records_exact_request_and_repeat_does_not_reissue_permit(self):
        task=self.first['jobs'][0];data,permit=self.begin(task)
        state=read_state(self.work);started=state['tasks'][task['id']]['execution']
        self.assertEqual(started['state'],'invoking')
        record=json.loads((self.work/started['request_file']).read_text(encoding='utf-8'))
        self.assertEqual(record['request']['prompt'],Path(task['prompt_file']).read_text(encoding='utf-8'))
        self.assertEqual(record['request']['references'],task['references'])
        self.assertIn('not_provider_verified',record['provenance'])
        before=(self.work/'state.json').read_bytes()
        again=c.run(self.work,data=data)
        self.assertEqual(again['kind'],'await_results');self.assertFalse(again['generate_more'])
        self.assertEqual(before,(self.work/'state.json').read_bytes())

    def test_request_mismatch_rejected_before_any_start(self):
        task=self.first['jobs'][0]
        self.assertIn('begin_template',task)
        for field,value in [('prompt','shortened'),('references',[]),('size',[10,10]),
                            ('transparent_background',True),('requested_outputs',2)]:
            with self.subTest(field=field):
                data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic'
                data['request'][field]=value
                with self.assertRaises(ValueError):c.run(self.work,data=data)
                self.assertEqual(read_state(self.work)['tasks'][task['id']]['execution']['state'],'not_started')

    def test_registration_without_begin_is_rejected(self):
        self.assertEqual(read_state(self.work).get('execution_policy'),MODE)
        with self.assertRaisesRegex(ValueError,'begin|started'):
            self.submit(self.first['jobs'][0],self.bg)

    def test_interrupted_batch_resumes_first_calls_and_reaches_done(self):
        jobs,files=self.boards();self.begin(jobs[0])
        extra=self.root/'extra.png';Image.new('RGB',(220,220),'red').save(extra)
        paused=self.submit(jobs[0],files[0],invocation_count=1,output_count=2,
                           returned_files=[str(files[0]),str(extra)])
        self.assertEqual(paused['kind'],'paused')
        with self.assertRaisesRegex(ValueError,'paused|violation'):
            data=copy.deepcopy(jobs[1]['begin_template']);data['request']['tool']='synthetic';c.run(self.work,data=data)
        continued=self.submit(jobs[0],files[0],invocation_count=1,output_count=2,
                              returned_files=[str(files[0]),str(extra)],
                              violation_authorization='User: keep the first existing result and continue remaining tasks.')
        self.assertEqual(continued['kind'],'generate')
        self.assertEqual([t['id'] for t in continued['jobs']],[t['id'] for t in jobs[1:]])
        # Queue/assembly completion uses a minimal saved container; separate
        # full-route tests exercise the actual PPT renderer and real returns.
        def build(layout,background,assets,output,**kwargs):
            with ZipFile(output,'w') as z:
                z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide1.xml','<slide/>')
            return {'warnings':[],'skipped':[]}
        for task,path in zip(jobs[1:],files[1:]):
            self.begin(task)
            with mock.patch.object(c,'build_presentation',side_effect=build):response=self.submit(task,path)
        self.assertEqual(response['kind'],'done')
        self.assertEqual(response['runtime_identity'],IDENTITY)
        self.assertEqual(c.run(self.work),response)

    def test_invoking_task_only_waits_while_unstarted_task_remains_preparable(self):
        self.begin(self.first['jobs'][0])
        pending=c.run(self.work)
        self.assertEqual(pending['kind'],'generate')
        self.assertEqual([t['role'] for t in pending['jobs']],['foreground'])
        self.begin(self.first['jobs'][1])
        waiting=c.run(self.work);self.assertEqual(waiting['kind'],'await_results')
        self.assertFalse(waiting['generate_more'])

    def test_mismatched_executed_prompt_preserves_output_and_pauses(self):
        task=self.first['jobs'][0];self.begin(task)
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.submit(task,self.bg,executed_prompt='Actually shortened')
        state=read_state(self.work);failed=state['tasks'][task['id']]
        self.assertEqual(failed['status'],'failed');self.assertNotIn('result',failed)
        self.assertEqual((self.work/failed['attempts'][-1]['raw']).read_bytes(),self.bg.read_bytes())
        self.assertEqual(c.run(self.work)['kind'],'paused')

    def test_request_secrets_are_not_persisted(self):
        task=self.first['jobs'][0];self.assertIn('begin_template',task)
        data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic'
        data['request']['tool_arguments']={'authorization':'Synthetic secret must never be written'}
        with self.assertRaises(ValueError):c.run(self.work,data=data)
        self.assertFalse((self.work/'requests').exists())

    def test_raw_tool_arguments_cannot_contradict_normalized_request(self):
        task=self.first['jobs'][0];self.assertIn('begin_template',task)
        cases=[{'prompt':'shortened provider prompt'},{'n':2},{'requested_outputs':4},{'transparent_background':True},
               {'referenced_image_paths':[]},{'size':'1x1'}]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic'
                data['request']['tool_arguments']=arguments
                with self.assertRaises(ValueError):c.run(self.work,data=data)
                self.assertEqual(read_state(self.work)['tasks'][task['id']]['execution']['state'],'not_started')
        self.assertFalse((self.work/'requests').exists())

    def test_credential_aliases_and_authorization_values_never_reach_disk(self):
        task=self.first['jobs'][0];self.assertIn('begin_template',task)
        cases=[{'api-key':'synthetic-value'},{'X-API-Key':'synthetic-value'},{'token':'synthetic-value'},
               {'extra_headers':{'irrelevant':'value'}},{'nested':{'token':'synthetic-value'}},
               {'arbitrary':'Bearer synthetic_token_12345'}]
        for index,arguments in enumerate(cases):
            with self.subTest(case=index):
                data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic'
                data['request']['tool_arguments']=arguments
                with self.assertRaisesRegex(ValueError,'credentials'):c.run(self.work,data=data)
        self.assertFalse((self.work/'requests').exists())

    def test_global_stop_blocks_only_new_starts_and_explicit_resume_restores_first_calls(self):
        jobs,files=self.boards();self.begin(jobs[0]);self.begin(jobs[1])
        extra=self.root/'stop-extra.png';Image.new('RGB',(220,220),'red').save(extra)
        payload={'invocation_count':1,'output_count':2,'returned_files':[str(files[0]),str(extra)]}
        self.submit(jobs[0],files[0],**payload)
        stopped=self.submit(jobs[0],files[0],**payload,
                            violation_authorization='User: accept the existing output; do not generate anything else.',
                            stop_remaining_generation=True)
        self.assertEqual(stopped['kind'],'paused')
        self.assertEqual(stopped['reason'],'user_stopped_remaining_generation')
        with self.assertRaisesRegex(ValueError,'paused'):
            data=copy.deepcopy(jobs[2]['begin_template']);data['request']['tool']='synthetic';c.run(self.work,data=data)
        # An already-started invocation is allowed to finish registration.
        self.submit(jobs[1],files[1])
        resumed=c.run(self.work,data={'kind':'resume_unstarted','authorization':'User: continue the remaining first invocations.'})
        self.assertEqual(resumed['kind'],'generate')
        self.assertEqual([t['id'] for t in resumed['jobs']],[jobs[2]['id']])
        self.assertNotIn('execution_hold',read_state(self.work))

    def test_failed_started_task_retry_requires_quote_and_new_start_record(self):
        task=self.first['jobs'][0];self.begin(task)
        response=c.run(self.work,data={**task['failure_template'],'reason':'Synthetic saved service failure'})
        self.assertEqual(response['kind'],'paused')
        with self.assertRaises(ValueError):c.run(self.work,retry_task=task['id'])
        retry=c.run(self.work,retry_task=task['id'],retry_authorization='User: try this failed task once.')
        next_task=retry['jobs'][0]
        self.assertEqual(next_task['attempt'],1)
        self.begin(next_task)
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['execution']['state'],'invoking')
        self.assertEqual(len(saved['execution_history']),1)
        self.assertTrue((self.work/saved['execution_history'][0]['request_file']).is_file())
        self.assertNotEqual(saved['execution']['request_file'],saved['execution_history'][0]['request_file'])

    def test_request_file_changes_are_rejected_before_resume(self):
        task=self.first['jobs'][0];self.begin(task)
        state=read_state(self.work);path=self.work/state['tasks'][task['id']]['execution']['request_file']
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'execution request'):
            c.run(self.work)

    def test_explicit_policy_work_directory_cannot_be_inside_runtime(self):
        inside=PACKAGE/'compat-runtime/never-created-test-job'
        self.assertFalse(inside.exists())
        self.assertIn('execution_policy',inspect.signature(c.run).parameters)
        with self.assertRaisesRegex(ValueError,'outside'):
            c.run(inside,source=self.source,execution_policy=MODE)
        self.assertFalse(inside.exists())

    def test_changed_runtime_identity_stops_before_any_action(self):
        self.assertEqual(read_state(self.work).get('execution_policy'),MODE)
        with mock.patch.object(c.version,'runtime_identity',return_value={**IDENTITY,'manifest_sha256':'changed'}):
            with self.assertRaisesRegex(ValueError,'runtime|version|identity'):
                c.run(self.work)

    def test_missing_execution_record_never_silently_downgrades(self):
        self.assertEqual(read_state(self.work).get('execution_policy'),MODE)
        state=read_state(self.work);state['tasks'][self.first['jobs'][0]['id']].pop('execution')
        save_state(self.work,state,'synthetic_corruption')
        with self.assertRaisesRegex(ValueError,'execution'):
            c.run(self.work)

    def test_deleted_execution_policy_never_downgrades_new_job_to_legacy(self):
        self.assertEqual(read_state(self.work).get('execution_policy'),MODE)
        state=read_state(self.work);state.pop('execution_policy')
        save_state(self.work,state,'synthetic_deleted_policy')
        with self.assertRaisesRegex(ValueError,'execution'):
            c.run(self.work)

    def test_provider_specific_size_is_recorded_as_unverified(self):
        task=self.first['jobs'][0];self.assertIn('begin_template',task)
        data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic'
        data['request']['tool_arguments']={'prompt':data['request']['prompt'],'size':'auto'}
        permit=c.run(self.work,data=data)
        self.assertEqual(permit['kind'],'invoke_once')
        record=json.loads(Path(permit['request_file']).read_text(encoding='utf-8'))
        self.assertIn('size',record['request'].get('unverified_tool_argument_fields',[]))


if __name__=='__main__':unittest.main()
