"""An explicit viewed rejection remains attached to the actual returned image."""
from PIL import Image
from test_result_role_binding import ReviewedOutputFixture
from newpath.storage import sha256,read_state
from newpath import binding
from pathlib import Path


class RejectedOutputIdentity(ReviewedOutputFixture):
    def reject(self,task,**extra):
        review=self.review(task,self.bg,matches_task=False,observed='Actually viewed: title returned for background.')
        with self.assertRaises(ValueError):self.submit(task,self.bg,role_review=review,**extra)

    def test_reencoding_same_pixels_cannot_wash_explicit_rejection(self):
        task=self.start()['background'];self.begin(task);self.reject(task)
        path=self.root/'reencoded.png'
        with Image.open(self.bg) as image:image.convert('RGBA').save(path,compress_level=0)
        self.assertNotEqual(sha256(path),sha256(self.bg))
        with self.assertRaisesRegex(ValueError,'rejected|role|subject'):self.submit(task,path)
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_same_known_output_id_cannot_wash_explicit_rejection(self):
        task=self.start()['background'];self.begin(task);self.reject(task,tool_output_id='known-output-id')
        path=self.root/'changed.png';Image.new('RGB',(1920,1080),'red').save(path)
        with self.assertRaisesRegex(ValueError,'rejected|role|subject'):self.submit(task,path,tool_output_id='known-output-id')
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_original_source_at_original_resolution_is_also_input(self):
        task=self.start()['background'];self.begin(task)
        with self.assertRaisesRegex(ValueError,'source|reference|input'):self.submit(task,self.source)
        self.assertNotIn('result',read_state(self.work)['tasks'][task['id']])

    def test_later_known_return_remains_owned_after_first_hard_binding_failure(self):
        jobs=self.start();task=jobs['background'];other=jobs['foreground']
        self.begin(task);self.begin(other)
        first=Path(task['references'][0]);second=self.bg
        entries=[{'file':str(p),'role_review':self.review(task,p)} for p in [first,second]]
        data=self.payload(task,first,output_count=2,returned_files=[str(first),str(second)],returned_outputs=entries)
        self.assertEqual(__import__('newpath.controller',fromlist=['run']).run(self.work,data=data)['kind'],'paused')
        state=read_state(self.work)
        with self.assertRaisesRegex(ValueError,'another task|cross-task'):
            binding.claim_return(self.work,state,state['tasks'][other['id']],self.payload(other,second),second,sha256(second))

    def test_inconsistent_surplus_count_preserves_and_binds_every_known_file(self):
        jobs=self.start();task=jobs['background'];other=jobs['foreground']
        self.begin(task);self.begin(other)
        second=self.root/'second.png';Image.new('RGB',(1920,1080),'red').save(second)
        entries=[{'file':str(p),'role_review':self.review(task,p)} for p in [self.bg,second]]
        data=self.payload(task,self.bg,output_count=1,returned_outputs=entries)
        response=__import__('newpath.controller',fromlist=['run']).run(self.work,data=data)
        self.assertEqual(response['kind'],'paused')
        state=read_state(self.work);saved=state['tasks'][task['id']]
        self.assertNotIn('result',saved)
        self.assertEqual(len(saved['generation_violations'][-1]['preserved_files']),2)
        with self.assertRaisesRegex(ValueError,'another task|cross-task'):
            binding.claim_return(self.work,state,state['tasks'][other['id']],self.payload(other,second),second,sha256(second))
