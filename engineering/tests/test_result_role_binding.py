"""Local task/output binding and explicit host visual review; no image calls."""
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

from PIL import Image,ImageDraw

PACKAGE=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath import controller as c
from newpath import __main__ as cli
from newpath.storage import read_state,save_state,sha256

POLICY='reviewed-existing-output/1'
IDENTITY={'package_id':'binding-fixture','archive_name':'test.zip','manifest_sha256':'fixture',
          'source_snapshot_sha256':'fixture','verified_files':1,'identity_provenance':'local_not_provider_verified'}


class ReviewedOutputFixture(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='prk-role-binding-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.work=self.root/'job';self.source=self.root/'source.png'
        Image.new('RGB',(160,90),'white').save(self.source)
        self.bg=self.root/'bg.png';Image.new('RGB',(1920,1080),'navy').save(self.bg)
        self.fg=self.root/'fg.png';im=Image.new('RGBA',(1920,1080),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((100,100,600,600),fill='blue');im.save(self.fg)
        patch=mock.patch.object(c,'version',types.SimpleNamespace(runtime_identity=lambda expected_package=None:copy.deepcopy(IDENTITY)))
        patch.start();self.addCleanup(patch.stop)
        self.permits={}

    def start(self,policy=POLICY):
        self.assertIn('delivery_policy',inspect.signature(c.run).parameters,
                      'New jobs need frozen delivery policy before image results are used')
        with mock.patch.object(c,'_runtime'):
            result=c.run(self.work,source=self.source,execution_policy='explicit-start/1',
                         post_return_prompt_policy='audit-existing-return/1',delivery_policy=policy)
        self.jobs={t['role']:t for t in result['jobs']};return self.jobs

    def begin(self,task):
        data=copy.deepcopy(task['begin_template']);data['request']['tool']='synthetic-image-tool'
        permit=c.run(self.work,data=data);self.assertEqual(permit['kind'],'invoke_once')
        self.permits[task['id']]=permit;return permit

    def review(self,task,path,**extra):
        review={'role':task['role'],'output_sha256':sha256(path),'looked_at_return':True,
                'matches_task':True,'observed':'A host inspected the saved synthetic output and its assigned subject.'}
        if task['role']=='background':review['foreground_text_absent']=True
        elif task['role']=='foreground':review['page_elements_match']=True
        elif task['role']=='wordart':review.update(lettering_matches=True,only_assigned_title=True)
        elif task['role']=='board':
            review.update(matched_members=[m['id'] for m in task['members']],ordinary_text_removed=True)
        review.update(extra);return review

    def payload(self,task,path,**extra):
        result={**self.permits[task['id']]['result_template'],'file':str(path),
                'tool':'synthetic-image-tool','invocation_count':1,'output_count':1,
                'role_review':self.review(task,path)}
        result.update(extra);return result

    def submit(self,task,path,**extra):return c.run(self.work,data=self.payload(task,path,**extra))

    def board(self):
        jobs=self.start();self.begin(jobs['background']);self.submit(jobs['background'],self.bg)
        self.begin(jobs['foreground']);self.submit(jobs['foreground'],self.fg)
        reference=self.work/'board-input.png';im=Image.new('RGBA',(220,220),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((35,35,180,180),fill='blue');im.save(reference)
        item={'id':'photo','kind':'art','box':[100,200,180,180],'description':'Assigned auditorium photo'}
        board={'id':'board-1','signature':'synthetic-board','image':str(reference),'size':[220,220],
               'key':None,'background_mode':'native-transparent-materials/1',
               'grid':{'rows':1,'columns':1,'margin':10,'gap':20,'cell_size':[200,200]},
               'members':[{'id':'photo','box':item['box'],'target':item['box'],'description':item['description'],'slot':[10,10,200,200]}]}
        with mock.patch.object(c,'prepare_boards',return_value=[board]):
            task=c.run(self.work,data={'kind':'layout','layout':{'items':[item]}})['jobs'][0]
        self.begin(task);return task,reference


class ResultRoleBinding(ReviewedOutputFixture):
    def test_policy_and_task_request_binding_are_frozen(self):
        task=self.start()['background'];permit=self.begin(task)
        self.assertEqual(permit['result_template']['request_sha256'],permit['request_sha256'])
        state=read_state(self.work);marker=json.loads((self.work/'initialization.json').read_text('utf-8'))
        self.assertEqual(state['delivery_policy'],POLICY);self.assertEqual(marker['delivery_policy'],POLICY)
        self.assertEqual(c.run(self.work)['delivery_policy'],POLICY)

    def test_missing_or_false_review_preserves_actual_file_and_does_not_record(self):
        task=self.start()['background'];self.begin(task)
        for review in [None,self.review(task,self.bg,matches_task=False),self.review(task,self.bg,looked_at_return=False)]:
            with self.subTest(review=review):
                with self.assertRaisesRegex(ValueError,'review|role|subject'):
                    self.submit(task,self.bg,role_review=review)
                saved=read_state(self.work)['tasks'][task['id']]
                self.assertNotIn('result',saved);self.assertTrue((self.work/saved['attempts'][-1]['raw']).is_file())

    def test_obvious_title_for_background_is_real_failure_despite_prompt_audit_policy(self):
        task=self.start()['background'];self.begin(task)
        review=self.review(task,self.bg,matches_task=False,foreground_text_absent=False,
                           observed='Only a blue title is present; the scenery is absent.')
        with self.assertRaisesRegex(ValueError,'review|role|subject'):
            self.submit(task,self.bg,role_review=review,executed_prompt='Only title typography')
        self.assertEqual(c.run(self.work)['kind'],'paused')

    def test_exact_request_hash_required_and_cannot_be_swapped_between_tasks(self):
        jobs=self.start();a=self.begin(jobs['background']);b=self.begin(jobs['foreground'])
        for token in [None,'changed',b['request_sha256']]:
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError,'request|binding'):
                    self.submit(jobs['background'],self.bg,request_sha256=token)
        self.assertNotIn('result',read_state(self.work)['tasks'][jobs['background']['id']])

    def test_cross_task_same_sha_is_rejected(self):
        jobs=self.start();self.begin(jobs['background']);self.submit(jobs['background'],self.bg)
        self.begin(jobs['foreground'])
        with self.assertRaisesRegex(ValueError,'another task|cross.task'):
            self.submit(jobs['foreground'],self.bg)

    def test_cross_task_reencoded_same_pixels_is_rejected(self):
        jobs=self.start();self.begin(jobs['background']);self.submit(jobs['background'],self.bg)
        reencoded=self.root/'reencoded.png'
        with Image.open(self.bg) as im:im.convert('RGBA').save(reencoded,compress_level=0)
        self.assertNotEqual(sha256(reencoded),sha256(self.bg));self.begin(jobs['foreground'])
        with self.assertRaisesRegex(ValueError,'another task|cross.task'):
            self.submit(jobs['foreground'],reencoded)

    def test_same_tool_output_id_cannot_own_distinct_task_files(self):
        jobs=self.start();self.begin(jobs['background']);self.submit(jobs['background'],self.bg,tool_output_id='known-local-id')
        self.begin(jobs['foreground'])
        with self.assertRaisesRegex(ValueError,'another task|cross.task'):
            self.submit(jobs['foreground'],self.fg,tool_output_id='known-local-id')

    def test_review_is_bound_to_actual_pixels_and_exact_role(self):
        task=self.start()['background'];self.begin(task)
        for review in [self.review(task,self.bg,role='wordart'),self.review(task,self.bg,output_sha256=sha256(self.fg)),
                       self.review(task,self.bg,foreground_text_absent=False),self.review(task,self.bg,observed='')]:
            with self.subTest(review=review):
                with self.assertRaises(ValueError):self.submit(task,self.bg,role_review=review)

    def test_board_requires_all_members_and_body_removal(self):
        task,reference=self.board();output=self.root/'photo-output.png'
        im=Image.new('RGBA',(220,220),(0,0,0,0));ImageDraw.Draw(im).rectangle((45,45,170,170),fill='red');im.save(output)
        for extra in [{'matched_members':[]},{'ordinary_text_removed':False},{'matches_task':False}]:
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):self.submit(task,output,role_review=self.review(task,output,**extra))
        with self.assertRaisesRegex(ValueError,'reference'):
            self.submit(task,reference)

    def test_repeated_good_registration_is_idempotent_and_ids_can_be_unknown(self):
        task=self.start()['background'];self.begin(task);self.submit(task,self.bg)
        before=(self.work/'state.json').read_bytes();self.submit(task,self.bg)
        self.assertEqual(before,(self.work/'state.json').read_bytes())
        saved=read_state(self.work)['tasks'][task['id']]['result']
        self.assertIsNone(saved.get('tool_output_id'));self.assertIn('not_provider_verified',saved['role_review_provenance'])

    def test_repeated_registration_cannot_omit_its_request_binding(self):
        task=self.start()['background'];self.begin(task);self.submit(task,self.bg)
        before=(self.work/'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'request|binding'):
            self.submit(task,self.bg,request_sha256=None)
        self.assertEqual(before,(self.work/'state.json').read_bytes())

    def test_source_input_cannot_be_used_as_any_generated_layer(self):
        task=self.start()['background'];self.begin(task)
        source=Path(task['references'][0])
        with self.assertRaisesRegex(ValueError,'reference|source|input'):
            self.submit(task,source)
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_explicit_viewed_wrong_subject_cannot_be_washed_by_later_true(self):
        task=self.start()['background'];self.begin(task)
        rejected=self.review(task,self.bg,matches_task=False,observed='Viewed actual pixels: title only, no background scene.')
        with self.assertRaises(ValueError):self.submit(task,self.bg,role_review=rejected)
        with self.assertRaisesRegex(ValueError,'rejected|role|subject'):
            self.submit(task,self.bg)
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertNotIn('result',saved);self.assertEqual(saved['role_review_rejections'][0]['output_sha256'],sha256(self.bg))

    def test_unviewed_default_review_can_be_corrected_without_new_invocation(self):
        task=self.start()['background'];self.begin(task)
        unviewed=self.review(task,self.bg,looked_at_return=False,matches_task=False)
        with self.assertRaises(ValueError):self.submit(task,self.bg,role_review=unviewed)
        self.submit(task,self.bg)
        self.assertEqual(read_state(self.work)['tasks'][task['id']]['status'],'recorded')

    def test_delivery_policy_tampering_and_legacy_migration_rejected(self):
        self.start();original=read_state(self.work)
        for value in [None,'unknown/1']:
            state=copy.deepcopy(original)
            if value is None:state.pop('delivery_policy')
            else:state['delivery_policy']=value
            save_state(self.work,state,'synthetic-delivery-tamper')
            with self.assertRaisesRegex(ValueError,'policy'):c.run(self.work)

    def test_cli_only_new_jobs_get_delivery_policy(self):
        with mock.patch.object(c,'run',return_value={'kind':'test'}) as run,mock.patch('sys.stdout',io.StringIO()):
            self.assertEqual(cli.main(['--work',str(self.work),'--source',str(self.source)]),0)
        self.assertEqual(run.call_args.kwargs.get('delivery_policy'),POLICY)
        self.work.mkdir();(self.work/'state.json').write_text('{}','utf-8')
        with mock.patch.object(c,'run',return_value={'kind':'test'}) as run,mock.patch('sys.stdout',io.StringIO()):
            cli.main(['--work',str(self.work)])
        self.assertNotIn('delivery_policy',run.call_args.kwargs)


if __name__=='__main__':unittest.main()
