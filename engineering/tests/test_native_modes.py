"""Native mode cannot silently downgrade while resuming a saved job."""
from unittest import TestCase, main, mock
import test_native_workflow as workflow

c=workflow.c
MODE=workflow.MODE

class NativeModeGuards(TestCase):
    def fixture(self):
        helper=workflow.NativeWorkflowContracts(methodName='runTest')
        helper.setUp();self.addCleanup(helper.doCleanups)
        return helper

    def test_unknown_state_mode_is_rejected_before_resume_or_done(self):
        for done in (False,True):
            with self.subTest(done=done):
                helper=self.fixture();work,_=helper.start()
                state=workflow.read_state(work)
                state['material_background_mode']='native-transparent-materials/999'
                for task in state['tasks'].values():
                    if task['role']=='foreground':task['background_mode']='native-transparent-materials/999'
                if done:state['done']={'kind':'done'}
                workflow.save_state(work,state,'test_unknown_mode')
                with mock.patch.object(c,'_done',return_value={'kind':'done'}):
                    with self.assertRaisesRegex(ValueError,'[Mm]aterial|mode'):
                        c.run(work)

    def test_unknown_foreground_mode_cannot_accept_opaque_return(self):
        helper=self.fixture();work,first=helper.start()
        tasks={t['role']:t for t in first['jobs']}
        helper.submit(work,tasks['background'],helper.background)
        state=workflow.read_state(work)
        state['tasks'][tasks['foreground']['id']]['background_mode']='unknown-mode'
        workflow.save_state(work,state,'test_unknown_foreground_mode')
        with self.assertRaisesRegex(ValueError,'[Mm]aterial|mode'):
            helper.submit(work,tasks['foreground'],helper.legacy_foreground)
        self.assertNotIn('result',workflow.read_state(work)['tasks'][tasks['foreground']['id']])

    def test_native_state_cannot_drop_or_mix_board_mode(self):
        for mode in (None,'unknown-mode'):
            with self.subTest(mode=mode):
                helper=self.fixture();work,_,_=helper.ready()
                response=c.run(work,data={'kind':'layout','layout':{'items':[helper.primary,helper.art]}})
                board=next(t for t in response['jobs'] if t['role']=='board')
                state=workflow.read_state(work);stored=state['tasks'][board['id']]
                stored['background_mode']=mode;stored['board']['background_mode']=mode
                workflow.save_state(work,state,'test_mixed_board_mode')
                with self.assertRaisesRegex(ValueError,'[Mm]aterial|mode'):
                    c.run(work)

    def test_normalization_cannot_record_an_empty_padded_foreground(self):
        helper=self.fixture()
        workflow.Image.new('RGB',(100,100),'white').save(helper.source)
        work,first=helper.start()
        tasks={t['role']:t for t in first['jobs']}
        helper.submit(work,tasks['background'],helper.background)
        image=workflow.Image.new('RGBA',(1920,1080),(0,0,0,0))
        image.putpixel((0,10),(255,255,255,255))
        path=helper.root/'padding-only.png';image.save(path)
        digest=workflow.sha256(path)
        with self.assertRaisesRegex(ValueError,'registration|transparent'):
            helper.submit(work,tasks['foreground'],path)
        state=workflow.read_state(work);task=state['tasks'][tasks['foreground']['id']]
        self.assertEqual(task['status'],'failed')
        self.assertNotIn('result',task)
        self.assertEqual(workflow.sha256(work/task['attempts'][-1]['raw']),digest)
        self.assertEqual(c.run(work)['kind'],'paused')

if __name__=='__main__':main()
