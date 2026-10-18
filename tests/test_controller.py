"""Synthetic state-machine checks, never evidence of image generation quality."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = self.root / 'job'
        self.source = self.root / 'current.png'
        Image.new('RGB', (400, 300), (190, 35, 45)).save(self.source)

    def api(self):
        try:
            from newpath.controller import run
        except ImportError:
            self.fail('The independent controller is not implemented')
        return run

    def state(self):
        return json.loads((self.job / 'state.json').read_text(encoding='utf-8'))

    def layer_result(self, task):
        path = self.root / (task['role'] + '.png')
        image = Image.new('RGB', (1920, 1080), (230, 220, 200) if task['role'] == 'background' else (255, 0, 255))
        if task['role'] == 'foreground':
            from PIL import ImageDraw
            ImageDraw.Draw(image).rectangle([300, 300, 500, 500], fill='navy')
        image.save(path)
        return {'task_id': task['id'], 'role': task['role'], 'file': str(path), 'tool': 'synthetic-fixture'}

    def layers(self):
        run = self.api()
        first = run(self.job, source=self.source)
        self.assertEqual(first['kind'], 'generate')
        tasks = first['jobs']
        self.assertEqual([t['role'] for t in tasks], ['background', 'foreground'])
        run(self.job, data=self.layer_result(tasks[0]))
        action = run(self.job, data=self.layer_result(tasks[1]))
        self.assertEqual(action['kind'], 'describe')
        return run, first, action

    def scene(self, count=1):
        return {'items': [{'id':'body', 'kind':'text', 'box':[50,50,400,60], 'text':'Current wording', 'size':40}] + [
            {'id':f'graphic{i}', 'kind':'art','box':[300,300,180,180], 'target':[300+i*20,300,180,180],
             'description':'synthetic navy square','lettering':'艺术' if i == 0 else None}
            for i in range(count)]}

    def fake_build(self, scene, background, assets, output):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w') as z:
            z.writestr('ppt/presentation.xml', '<presentation/>')
            z.writestr('ppt/slides/slide1.xml', '<slide/>')
        return {'path':str(output), 'warnings':[], 'skipped':[], 'objects':len(scene['items'])}

    def test_requires_explicit_current_source_and_does_not_pick_a_nearby_image(self):
        with self.assertRaises(ValueError):
            self.api()(self.job)
        self.assertFalse((self.job / 'state.json').exists())

    def test_normalize_keeps_full_source_and_restores_padding_for_both_layers(self):
        run, first, action = self.layers()
        source = self.state()['source']
        self.assertEqual(source['transform']['resized'], [1440,1080])
        self.assertEqual(source['transform']['offset'], [240,0])
        self.assertEqual(source['pixel_sha256'], first['source']['pixel_sha256'])
        self.assertEqual(len(action['files']), 2)
        for task in self.state()['tasks'].values():
            with Image.open(self.job / task['result']['normalized']) as image:
                self.assertEqual(image.getpixel((0,0))[:3], (255,255,255) if task['role']=='background' else (255,0,255))

    def test_role_and_id_bindings_prevent_a_background_becoming_foreground(self):
        run=self.api(); first=run(self.job,source=self.source)
        background, foreground=first['jobs']
        payload=self.layer_result(background)
        run(self.job,data=payload)
        before=(self.job/'state.json').read_bytes()
        response=run(self.job,data=payload)
        self.assertEqual(response['kind'],'await_results')
        self.assertEqual((self.job/'state.json').read_bytes(), before)
        with self.assertRaises(ValueError):
            run(self.job,data=dict(payload,task_id=foreground['id']))
        with self.assertRaises(ValueError):
            run(self.job,data=self.layer_result(foreground) | {'task_id':background['id']})

    def test_foreground_cannot_register_before_background(self):
        run=self.api(); first=run(self.job,source=self.source)
        with self.assertRaises(ValueError):
            run(self.job,data=self.layer_result(first['jobs'][1]))
        self.assertFalse(any(t.get('result') for t in self.state()['tasks'].values()))

    def test_same_pixels_resume_but_a_different_source_is_rejected(self):
        run=self.api(); first=run(self.job,source=self.source)
        again=self.root/'same.webp'
        with Image.open(self.source) as image:
            image.save(again,lossless=True)
        self.assertEqual(run(self.job,source=again)['kind'],'await_results')
        different=self.root/'other.png'; Image.new('RGB',(400,300),'blue').save(different)
        with self.assertRaises(ValueError):run(self.job,source=different)
        self.assertEqual(self.state()['source']['pixel_sha256'],first['source']['pixel_sha256'])

    def test_a_failed_invocation_pauses_and_only_explicit_retry_reopens_it(self):
        run=self.api(); task=run(self.job,source=self.source)['jobs'][0]
        data={'task_id':task['id'],'role':'background','outcome':'failed','reason':'Synthetic tool timeout'}
        result=run(self.job,data=data)
        self.assertEqual(result['kind'],'paused')
        self.assertFalse(result.get('jobs'))
        self.assertEqual(run(self.job)['kind'],'paused')
        resumed=run(self.job,retry_task=task['id'])
        self.assertEqual([j['id'] for j in resumed['jobs']],[task['id']])
        self.assertEqual(self.state()['tasks'][task['id']]['attempt'],1)

    def test_changed_registered_bytes_are_not_treated_as_valid_cache(self):
        run=self.api(); task=run(self.job,source=self.source)['jobs'][0]
        run(self.job,data=self.layer_result(task))
        result=self.state()['tasks'][task['id']]['result']
        (self.job/result['raw']).write_bytes(b'corrupted recorded data')
        with self.assertRaises(ValueError):run(self.job)

    def test_artwork_is_planned_after_layers_and_terminal_does_not_run_more_work(self):
        run, first, describe=self.layers()
        scene=self.scene()
        result=run(self.job,data={'kind':'layout','layout':scene})
        self.assertEqual(result['kind'],'generate')
        self.assertEqual(len(result['jobs']),1)
        board=result['jobs'][0]
        reference=Path(board['references'][0])
        actual=self.root/'returned-board.png'; actual.write_bytes(reference.read_bytes())
        payload={'task_id':board['id'],'role':'board','file':str(actual),'tool':'synthetic-fixture'}
        with patch('newpath.controller.build_presentation',side_effect=self.fake_build) as compiler:
            done=run(self.job,data=payload)
            self.assertEqual(done['kind'],'done')
            self.assertEqual(compiler.call_count,1)
            self.assertEqual(done['quality'],'not_evaluated')
            self.assertEqual(done['expected_generation_tasks'],3)
            before=(self.job/'state.json').read_bytes()
            for _ in range(3): self.assertEqual(run(self.job),done)
            self.assertEqual(run(self.job,data=payload),done)
            self.assertEqual((self.job/'state.json').read_bytes(),before)
            self.assertEqual(compiler.call_count,1)
        self.assertTrue(Path(done['pptx']).is_file())
        self.assertFalse(list((self.job/'output').glob('*.zip')))

    def test_description_truth_never_becomes_generated_foreground(self):
        run, _, describe=self.layers()
        self.assertNotIn('foreground',str(describe['files']))
        run(self.job,data={'kind':'layout','layout':self.scene()})
        state=self.state()
        self.assertEqual(state['layout_source_sha256'],state['source']['working_sha256'])
        self.assertNotIn('source_id',json.dumps(state['layout']))

    def test_source_pixels_come_from_the_immutable_saved_copy(self):
        from newpath.source import prepare_source, decoded_picture
        folder=self.root/'prepared'; folder.mkdir()
        def swapped(path):
            picture=decoded_picture(path)
            if Path(path)==self.source:
                Image.new('RGB',(400,300),'blue').save(self.source)
            return picture
        with patch('newpath.source.decoded_picture',side_effect=swapped):
            result=prepare_source(self.source,folder)
        saved=decoded_picture(folder/result['original'])
        with Image.open(folder/result['working']) as working:
            self.assertEqual(working.getpixel((960,540)),saved.getpixel((200,150)))

    def test_layer_returned_dimensions_map_the_full_frame_without_letterboxing(self):
        from newpath.source import normalize_layer
        from PIL import ImageDraw
        actual=self.root/'small-square.png'
        picture=Image.new('RGB',(1024,1024),'magenta')
        ImageDraw.Draw(picture).rectangle((204,410,307,614),fill='navy')
        picture.save(actual)
        output=self.root/'normalized.png'
        normalize_layer(actual,'foreground',{'offset':[0,0],'resized':[1920,1080]},output)
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((480,540))[:3],(0,0,128))

    def test_failure_records_survive_explicit_retry_and_do_not_underreport_calls(self):
        run=self.api(); tasks=run(self.job,source=self.source)['jobs']
        failed={'task_id':tasks[0]['id'],'role':'background','outcome':'failed',
                'reason':'Synthetic timeout','invocation_count':1,'tool_call_id':'fixture-first'}
        run(self.job,data=failed)
        run(self.job,retry_task=tasks[0]['id'])
        run(self.job,data=self.layer_result(tasks[0])|{'invocation_count':1,'attempt':1})
        run(self.job,data=self.layer_result(tasks[1])|{'invocation_count':1})
        with patch('newpath.controller.build_presentation',side_effect=self.fake_build):
            done=run(self.job,data={'kind':'layout','layout':self.scene(0)})
        history=self.state()['tasks'][tasks[0]['id']]['attempts']
        self.assertEqual(history[0]['reason'],'Synthetic timeout')
        self.assertEqual(history[0]['tool_call_id'],'fixture-first')
        self.assertEqual(done['caller_reported_invocations'],3)

    def test_board_batches_are_leased_once_and_accept_serial_out_of_order_results(self):
        run,_,_=self.layers()
        layout=self.scene(4)
        for item in layout['items'][1:]:item['box']=[250,10,900,900]
        action=run(self.job,data={'kind':'layout','layout':layout})
        self.assertEqual(len(action['jobs']),3)
        self.assertEqual(run(self.job)['kind'],'await_results')
        def payload(task):
            return {'task_id':task['id'],'role':'board','file':task['references'][0]}
        run(self.job,data=payload(action['jobs'][2]))
        run(self.job,data=payload(action['jobs'][0]))
        second=run(self.job,data=payload(action['jobs'][1]))
        self.assertEqual(len(second['jobs']),1)
        self.assertNotIn(second['jobs'][0]['id'],[j['id'] for j in action['jobs']])
        self.assertEqual(run(self.job,data=payload(action['jobs'][0]))['kind'],'await_results')

    def test_corrupt_and_empty_files_pause_without_reissuing_tasks(self):
        run=self.api(); task=run(self.job,source=self.source)['jobs'][0]
        bad=self.root/'broken.png';bad.write_bytes(b'not a PNG')
        with self.assertRaises(ValueError):run(self.job,data={'task_id':task['id'],'role':task['role'],'file':str(bad)})
        self.assertEqual(run(self.job)['kind'],'paused')
        self.assertFalse(run(self.job).get('jobs'))
        history=self.state()['tasks'][task['id']]['attempts']
        self.assertEqual((self.job/history[0]['raw']).read_bytes(),b'not a PNG')

    def test_imported_cli_main_handles_missing_source_without_name_error(self):
        from newpath.__main__ import main
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--work',str(self.job)]),2)
        self.assertEqual(json.loads(output.getvalue())['kind'],'error')

    def test_interrupted_initial_preparation_resumes_without_another_image(self):
        from newpath.controller import _task
        run=self.api()
        with patch('newpath.controller._task',side_effect=RuntimeError('Synthetic interruption')):
            with self.assertRaises(RuntimeError):run(self.job,source=self.source)
        self.assertTrue((self.job/'input'/'original.png').exists())
        self.assertFalse((self.job/'state.json').exists())
        self.source.unlink()
        result=run(self.job)
        self.assertEqual(result['kind'],'generate')
        self.assertEqual(len(result['jobs']),2)

    def test_stale_callback_after_explicit_retry_does_not_claim_the_new_attempt(self):
        run=self.api(); task=run(self.job,source=self.source)['jobs'][0]
        run(self.job,data={'task_id':task['id'],'role':task['role'],'outcome':'failed','reason':'synthetic'})
        run(self.job,retry_task=task['id'])
        with self.assertRaises(ValueError):run(self.job,data=self.layer_result(task))
        self.assertEqual(self.state()['tasks'][task['id']]['status'],'waiting')

    def test_horizontal_vertical_square_sources_preserve_edges_and_coordinate_transform(self):
        from newpath.source import prepare_source
        from PIL import ImageDraw
        for index,size in enumerate([(800,200),(200,800),(300,300)]):
            with self.subTest(size=size):
                path=self.root/f'orientation{index}.png'
                image=Image.new('RGB',size,'red')
                ImageDraw.Draw(image).rectangle((size[0]//2,0,size[0]-1,size[1]-1),fill='blue')
                image.save(path);work=self.root/f'orientation{index}';work.mkdir()
                prepared=prepare_source(path,work);t=prepared['transform']
                x,y=t['offset'];w,h=t['resized']
                with Image.open(work/prepared['working']) as actual:
                    self.assertEqual(actual.getpixel((x,y+h//2)),(255,0,0))
                    self.assertEqual(actual.getpixel((x+w-1,y+h//2)),(0,0,255))
                self.assertAlmostEqual((x+size[0]*.25*t['scale']-x)/t['scale'],size[0]*.25)

    def test_interruption_after_pptx_publication_recovers_without_compiling_again(self):
        from newpath.storage import save_state
        run,_,_=self.layers()
        def interrupted(work,state,event):
            if event=='presentation_published':raise RuntimeError('Synthetic final checkpoint interruption')
            return save_state(work,state,event)
        with patch('newpath.controller.build_presentation',side_effect=self.fake_build) as compiler:
            with patch('newpath.controller.save_state',side_effect=interrupted):
                with self.assertRaises(RuntimeError):
                    run(self.job,data={'kind':'layout','layout':self.scene(0)})
            self.assertTrue((self.job/'output'/'新路径.pptx').exists())
            self.assertEqual(run(self.job)['kind'],'done')
            self.assertEqual(compiler.call_count,1)

