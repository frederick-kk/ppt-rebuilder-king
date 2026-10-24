"""One invocation's surplus outputs can be adopted in stable return order."""
import copy
from pathlib import Path
import unittest

from PIL import Image
from test_result_role_binding import ReviewedOutputFixture,c,POLICY
from newpath.storage import read_state,sha256


class SingleCallSurplus(ReviewedOutputFixture):
    def surplus(self,task,paths,reviews=None,**extra):
        entries=[{'file':str(p),'role_review':r} for p,r in zip(paths,reviews or [self.review(task,p) for p in paths])]
        data=self.payload(task,paths[0],output_count=len(paths),returned_files=[str(p) for p in paths],returned_outputs=entries)
        data.update(extra);return c.run(self.work,data=data)

    def test_first_usable_output_is_adopted_without_retry_or_user_ack(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        response=self.surplus(task,[self.bg,second])
        self.assertEqual(response['kind'],'generate')
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(self.bg))
        selection=saved['surplus_resolution'];self.assertEqual(selection['selected_index'],0)
        self.assertEqual(len(selection['returns']),2)
        self.assertEqual(saved['attempt'],0);self.assertEqual(len(saved.get('execution_history',[])),0)
        self.assertTrue(all(v['acknowledged'] for v in saved['generation_violations']))
        self.assertNotIn('authorization',saved['generation_violations'][0])

    def test_first_real_role_failure_then_second_usable_is_adopted_in_order(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        reviews=[self.review(task,self.bg,matches_task=False,observed='Only title; wrong background.'),self.review(task,second)]
        self.surplus(task,[self.bg,second],reviews)
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(second));self.assertEqual(saved['surplus_resolution']['selected_index'],1)
        self.assertTrue((self.work/saved['surplus_resolution']['returns'][0]['raw']).exists())

    def test_invalid_first_png_does_not_authorize_new_call_and_second_existing_can_pass(self):
        task=self.start()['background'];self.begin(task);bad=self.root/'broken.png';bad.write_bytes(b'broken PNG')
        self.surplus(task,[bad,self.bg])
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['result']['sha256'],sha256(self.bg));self.assertEqual(saved['attempt'],0)

    def test_multiple_or_unknown_invocations_never_auto_adopt(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        for count in [2,None]:
            with self.subTest(count=count):
                response=self.surplus(task,[self.bg,second],invocation_count=count)
                self.assertEqual(response['kind'],'paused');self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_all_role_failures_remain_real_failure_and_never_done(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        reviews=[self.review(task,p,matches_task=False,observed='Wrong title image.') for p in [self.bg,second]]
        response=self.surplus(task,[self.bg,second],reviews)
        self.assertEqual(response['kind'],'paused');self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_user_stop_and_incomplete_returns_keep_strict_pause(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        response=self.surplus(task,[self.bg,second],stop_remaining_generation=True,
                              violation_authorization='User: do not generate any more images.')
        self.assertEqual(response['kind'],'paused');self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])
        # A partial list cannot claim the first usable result from an actual full return order.
        data=self.payload(task,self.bg,output_count=3,returned_files=[str(self.bg),str(second)],
                          returned_outputs=[{'file':str(self.bg),'role_review':self.review(task,self.bg)}])
        self.assertEqual(c.run(self.work,data=data)['kind'],'paused')

    def test_unselected_return_cannot_be_reused_by_another_task(self):
        jobs=self.start();task=jobs['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        self.surplus(task,[self.bg,second]);self.begin(jobs['foreground'])
        with self.assertRaisesRegex(ValueError,'another task|cross.task'):
            self.submit(jobs['foreground'],second)

    def test_surplus_preserves_every_declared_review_and_output_identifier(self):
        task=self.start()['background'];self.begin(task)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        data=self.payload(task,self.bg,output_count=2,returned_files=[str(self.bg),str(second)],returned_outputs=[
            {'file':str(self.bg),'role_review':self.review(task,self.bg),'tool_output_id':'output-first'},
            {'file':str(second),'role_review':self.review(task,second),'tool_output_id':'output-second'}])
        c.run(self.work,data=data)
        saved=read_state(self.work)['tasks'][task['id']]
        self.assertEqual(saved['surplus_resolution']['returns'][1]['tool_output_id'],'output-second')
        self.assertEqual(saved['surplus_resolution']['returns'][1]['declared_role_review']['output_sha256'],sha256(second))
        self.assertEqual(len(saved['return_bindings']),2)

    def test_reference_substitution_in_surplus_is_a_hard_pause(self):
        task=self.start()['background'];self.begin(task);reference=Path(task['references'][0])
        response=self.surplus(task,[reference,self.bg])
        self.assertEqual(response['kind'],'paused')
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])


if __name__=='__main__':unittest.main()
