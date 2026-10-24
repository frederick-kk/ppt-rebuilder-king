"""Post-return prompt discrepancies keep usable returns without claiming compliance."""
import copy
import inspect
import io
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
from newpath import __main__ as cli
from newpath.storage import read_state,save_state,sha256

POLICY='audit-existing-return/1'
EXECUTION='explicit-start/1'
IDENTITY={'package_id':'synthetic-recovery-package','archive_name':'fixture.zip',
          'manifest_sha256':'fixture-manifest','source_snapshot_sha256':'fixture-source',
          'verified_files':1,'identity_provenance':'local_manifest_verified'}


class PostReturnPromptAudit(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='prk-prompt-audit-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.work=self.root/'job';self.source=self.root/'source.png'
        Image.new('RGB',(160,90),'white').save(self.source)
        self.bg=self.root/'bg.png';Image.new('RGB',(1920,1080),'navy').save(self.bg)
        self.fg=self.root/'fg.png';im=Image.new('RGBA',(1920,1080),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((100,100,600,600),fill='blue');im.save(self.fg)
        patch=mock.patch.object(c,'version',types.SimpleNamespace(runtime_identity=lambda expected_package=None:copy.deepcopy(IDENTITY)))
        patch.start();self.addCleanup(patch.stop)

    def start(self,policy=POLICY):
        self.assertIn('post_return_prompt_policy',inspect.signature(c.run).parameters,
                      'New jobs need an explicit frozen post-return policy')
        with mock.patch.object(c,'_runtime'):
            response=c.run(self.work,source=self.source,execution_policy=EXECUTION,
                           post_return_prompt_policy=policy)
        return {t['role']:t for t in response['jobs']}

    def begin(self,task):
        data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic-test-image-tool'
        self.assertEqual(c.run(self.work,data=data)['kind'],'invoke_once')
        return data

    def submit(self,task,path=None,**extra):
        return c.run(self.work,data={**task['result_template'],'file':str(path or self.bg),**extra})

    def test_policy_is_frozen_in_state_initialization_and_response(self):
        self.start();state=read_state(self.work)
        marker=json.loads((self.work/'initialization.json').read_text('utf-8'))
        self.assertEqual(state.get('post_return_prompt_policy'),POLICY)
        self.assertEqual(marker.get('post_return_prompt_policy'),POLICY)
        self.assertEqual(c.run(self.work).get('post_return_prompt_policy'),POLICY)

    def test_valid_mismatched_return_is_recorded_with_full_audit_and_no_new_permit(self):
        jobs=self.start();task=jobs['background'];begin=self.begin(task)
        response=self.submit(task,executed_prompt='A different visible host prompt',invocation_count=1,output_count=1)
        self.assertEqual(response['kind'],'generate')
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['status'],'recorded');self.assertEqual(saved['execution']['state'],'recorded')
        audit=saved['prompt_audits'][0]
        self.assertEqual(audit['status'],'mismatch')
        self.assertEqual(audit['expected_prompt'],begin['request']['prompt'])
        self.assertEqual(audit['reported_prompt'],'A different visible host prompt')
        self.assertEqual(audit['output_sha256'],sha256(self.bg));self.assertEqual(audit['attempt'],0)
        self.assertEqual(audit['task_id'],task['id']);self.assertEqual(len(audit['expected_prompt_sha256']),64)
        self.assertIn('not_provider_verified',audit['provenance'])
        self.assertTrue(any(w.get('code')=='post_return_prompt_mismatch' for w in state['warnings']))
        self.assertEqual([t['role'] for t in response['jobs']],['foreground'])
        self.assertEqual(c.run(self.work,data=begin)['kind'],'await_results')

    def test_unknown_actual_prompt_remains_unknown_and_not_claimed_match(self):
        task=self.start()['background'];self.begin(task);self.submit(task)
        audit=read_state(self.work)['tasks'][task['id']]['prompt_audits'][0]
        self.assertEqual(audit['status'],'unknown');self.assertIsNone(audit['reported_prompt'])
        self.assertIsNone(audit['reported_prompt_sha256'])

    def test_exact_report_is_match_and_has_no_mismatch_warning(self):
        task=self.start()['background'];data=self.begin(task)
        self.submit(task,executed_prompt=data['request']['prompt'])
        state=read_state(self.work)
        self.assertEqual(state['tasks'][task['id']]['prompt_audits'][0]['status'],'match')
        self.assertFalse(any(w.get('code')=='post_return_prompt_mismatch' for w in state['warnings']))

    def test_repeat_with_missing_or_changed_prompt_cannot_erase_first_mismatch(self):
        task=self.start()['background'];begin=self.begin(task)
        self.submit(task,executed_prompt='Original actual deviation')
        before=copy.deepcopy(read_state(self.work)['tasks'][task['id']]['prompt_audits'])
        self.submit(task);self.submit(task,executed_prompt=begin['request']['prompt'])
        state=read_state(self.work)
        self.assertEqual(state['tasks'][task['id']]['prompt_audits'],before)
        self.assertEqual(sum(w.get('code')=='post_return_prompt_mismatch' for w in state['warnings']),1)

    def test_old_explicit_job_keeps_strict_behavior_without_policy(self):
        task=self.start(None)['background'];self.begin(task)
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.submit(task,executed_prompt='Different actual prompt')
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'],'failed')

    def test_later_conflicting_report_is_retained_without_replacing_first_match(self):
        task=self.start()['background'];begin=self.begin(task)
        self.submit(task,executed_prompt=begin['request']['prompt'])
        original=copy.deepcopy(read_state(self.work)['tasks'][task['id']]['prompt_audits'])
        self.submit(task,executed_prompt='A later actual deviation')
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['prompt_audits'],original)
        reports=saved.get('prompt_followup_reports',[])
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['reported_prompt'],'A later actual deviation')
        self.assertEqual(reports[0]['kind'],'conflict')
        self.assertEqual(reports[0]['status'],'mismatch')
        summary=c.execution.prompt_audit_summary(state)
        self.assertEqual(summary['conflict_count'],1)
        self.assertEqual(summary['effective_mismatch_count'],1)
        self.assertTrue(any(w.get('code')=='post_return_prompt_followup' for w in state['warnings']))

    def test_unknown_first_report_can_retain_later_actual_prompt_as_supplement(self):
        task=self.start()['background'];self.begin(task);self.submit(task)
        self.submit(task,executed_prompt='Later visible actual prompt')
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertEqual(saved['prompt_audits'][0]['status'],'unknown')
        reports=saved.get('prompt_followup_reports',[])
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['kind'],'supplement')
        self.assertEqual(reports[0]['reported_prompt'],'Later visible actual prompt')
        summary=c.execution.prompt_audit_summary(state)
        self.assertEqual(summary['supplement_count'],1)
        self.assertEqual(summary['unknown_count'],1)
        self.assertEqual(summary['effective_mismatch_count'],1)

    def test_repeated_followup_is_idempotent_and_missing_report_cannot_remove_it(self):
        task=self.start()['background'];self.begin(task)
        self.submit(task,executed_prompt='Initial deviation')
        self.submit(task,executed_prompt='Later different declaration')
        state=read_state(self.work);reports=state['tasks'][task['id']].get('prompt_followup_reports',[])
        self.assertEqual(len(reports),1)
        before=(self.work/'state.json').read_bytes()
        self.submit(task,executed_prompt='Later different declaration');self.submit(task)
        self.assertEqual((self.work/'state.json').read_bytes(),before)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['prompt_audits'][0]['reported_prompt'],'Initial deviation')

    def test_strict_failed_mismatch_cannot_be_washed_by_omitting_later_prompt(self):
        # Existing API intentionally creates the frozen strict behavior.
        with mock.patch.object(c,'_runtime'):
            first=c.run(self.work,source=self.source,execution_policy=EXECUTION)
        task=first['jobs'][0];self.begin(task)
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.submit(task,executed_prompt='First actual mismatch')
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.submit(task)
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_before_call_exact_request_guard_still_blocks_shortened_prompt(self):
        task=self.start()['background'];data=copy.deepcopy(task['begin_template'])
        data['request']['tool']='synthetic';data['request']['prompt']='short'
        with self.assertRaisesRegex(ValueError,'prompt'):c.run(self.work,data=data)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['execution']['state'],'not_started')

    def test_policy_cannot_be_removed_changed_or_added_to_old_state(self):
        self.start();original=read_state(self.work)
        for value in [None,'unknown/1']:
            with self.subTest(value=value):
                state=copy.deepcopy(original)
                if value is None:state.pop('post_return_prompt_policy')
                else:state['post_return_prompt_policy']=value
                save_state(self.work,state,'synthetic-policy-tamper')
                with self.assertRaisesRegex(ValueError,'policy'):c.run(self.work)
        save_state(self.work,original,'restore-fixture')
        with self.assertRaisesRegex(ValueError,'policy'):
            c.run(self.work,post_return_prompt_policy='unknown/1')
        state=copy.deepcopy(original);state.pop('post_return_prompt_policy');save_state(self.work,state,'strict-fixture')
        marker=json.loads((self.work/'initialization.json').read_text('utf-8'));marker.pop('post_return_prompt_policy')
        (self.work/'initialization.json').write_text(json.dumps(marker),'utf-8')
        with self.assertRaisesRegex(ValueError,'policy'):
            c.run(self.work,post_return_prompt_policy=POLICY)

    def test_unknown_or_nonexplicit_policy_is_rejected_before_job_creation(self):
        self.assertIn('post_return_prompt_policy',inspect.signature(c.run).parameters)
        for execution,policy in [(EXECUTION,'unknown/1'),(None,POLICY)]:
            with self.subTest(execution=execution,policy=policy):
                with self.assertRaisesRegex(ValueError,'policy|explicit'):
                    c.run(self.work,source=self.source,execution_policy=execution,post_return_prompt_policy=policy)
                self.assertFalse(self.work.exists())

    def test_interrupted_initialization_keeps_frozen_policy(self):
        self.start();state=read_state(self.work);marker=json.loads((self.work/'initialization.json').read_text('utf-8'))
        # The source preparation interruption path permits input and brief files.
        (self.work/'state.json').unlink()
        with mock.patch.object(c,'_runtime'):
            c.run(self.work,source=self.source,execution_policy=EXECUTION)
        self.assertEqual(read_state(self.work)['post_return_prompt_policy'],POLICY)
        self.assertEqual(json.loads((self.work/'initialization.json').read_text('utf-8')),marker)

    def test_invalid_output_is_not_accepted_or_given_successful_audit(self):
        task=self.start()['background'];self.begin(task)
        invalid=self.root/'invalid.png';invalid.write_bytes(b'not an image')
        with self.assertRaises(ValueError):self.submit(task,invalid,executed_prompt='Different')
        state=read_state(self.work);self.assertEqual(state['tasks'][task['id']]['status'],'failed')
        self.assertFalse(state['tasks'][task['id']].get('prompt_audits'))

    def test_budget_violation_still_pauses_before_auditing_return(self):
        task=self.start()['background'];self.begin(task)
        extra=self.root/'extra.png';Image.new('RGB',(1920,1080),'red').save(extra)
        response=self.submit(task,executed_prompt='Different',invocation_count=1,output_count=2,
                             returned_files=[str(self.bg),str(extra)])
        self.assertEqual(response['kind'],'paused')
        state=read_state(self.work);self.assertNotIn('result',state['tasks'][task['id']])
        self.assertFalse(state['tasks'][task['id']].get('prompt_audits'))

    def test_reference_output_and_request_tampering_remain_blocked(self):
        jobs=self.start();self.begin(jobs['background']);self.submit(jobs['background'])
        self.begin(jobs['foreground']);self.submit(jobs['foreground'],self.fg)
        reference=self.work/'board-input.png';im=Image.new('RGBA',(220,220),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((35,35,180,180),fill='blue');im.save(reference)
        item={'id':'art-1','kind':'art','box':[100,200,180,180],'description':'Synthetic independent square'}
        board={'id':'board-1','signature':'synthetic-board','image':str(reference),'size':[220,220],
               'key':None,'background_mode':'native-transparent-materials/1',
               'grid':{'rows':1,'columns':1,'margin':10,'gap':20,'cell_size':[200,200]},
               'members':[{'id':item['id'],'box':item['box'],'target':item['box'],
                           'description':item['description'],'slot':[10,10,200,200]}]}
        with mock.patch.object(c,'prepare_boards',return_value=[board]):
            task=c.run(self.work,data={'kind':'layout','layout':{'items':[item]}})['jobs'][0]
        self.begin(task)
        with self.assertRaisesRegex(ValueError,'reference|input'):
            self.submit(task,Path(task['references'][0]),executed_prompt='Different')
        self.assertFalse(read_state(self.work)['tasks'][task['id']].get('prompt_audits'))
        state=read_state(self.work);p=self.work/state['tasks'][task['id']]['execution']['request_file']
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'execution request'):c.run(self.work)

    def test_done_receipt_contains_durable_audit_summary(self):
        jobs=self.start()
        self.begin(jobs['background']);self.submit(jobs['background'],executed_prompt='Different')
        self.begin(jobs['foreground']);self.submit(jobs['foreground'],self.fg)
        def build(layout,background,assets,output,**options):
            with ZipFile(output,'w') as z:
                z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide1.xml','<slide/>')
            return {'warnings':[],'skipped':[]}
        with mock.patch.object(c,'build_presentation',side_effect=build):
            done=c.run(self.work,data={'kind':'layout','layout':{'items':[]}})
        self.assertEqual(done['kind'],'done');self.assertEqual(done['post_return_prompt_policy'],POLICY)
        summary=done['post_return_prompt_audit']
        self.assertEqual(summary['mismatch_count'],1);self.assertEqual(summary['unknown_count'],1)
        self.assertEqual(summary['entries'][0]['reported_prompt'],'Different')
        self.assertEqual(c.run(self.work),done)

    def test_cli_defaults_policy_only_for_new_not_existing_or_interrupted_jobs(self):
        with mock.patch.object(c,'run',return_value={'kind':'test'}) as run, mock.patch('sys.stdout',io.StringIO()):
            self.assertEqual(cli.main(['--work',str(self.work),'--source',str(self.source)]),0)
        self.assertEqual(run.call_args.kwargs.get('post_return_prompt_policy'),POLICY)
        self.work.mkdir();(self.work/'initialization.json').write_text('{}','utf-8')
        with mock.patch.object(c,'run',return_value={'kind':'test'}) as run,mock.patch('sys.stdout',io.StringIO()):
            self.assertEqual(cli.main(['--work',str(self.work),'--source',str(self.source)]),0)
        self.assertNotIn('post_return_prompt_policy',run.call_args.kwargs)
        (self.work/'state.json').write_text('{}','utf-8')
        with mock.patch.object(c,'run',return_value={'kind':'test'}) as run,mock.patch('sys.stdout',io.StringIO()):
            self.assertEqual(cli.main(['--work',str(self.work)]),0)
        self.assertNotIn('post_return_prompt_policy',run.call_args.kwargs)


if __name__=='__main__':unittest.main()
