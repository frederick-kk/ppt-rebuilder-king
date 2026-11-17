import copy
import inspect
import json
import zipfile
from PIL import Image, ImageDraw
from unittest import mock
import unittest

from test_result_role_binding import ReviewedOutputFixture
from newpath import controller, __main__ as cli
from newpath import execution
from newpath.storage import read_state, save_state, sha256

POLICY = 'keyed-materials/1'


class KeyedMaterialController(ReviewedOutputFixture):
    def start_keyed(self):
        self.assertIn('keyed_material_policy', inspect.signature(controller.run).parameters)
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', delivery_policy='reviewed-existing-output/1',
                layer_generation_order='background-then-foreground/1',
                foreground_component_policy='generated-foreground-members/1',
                keyed_material_policy=POLICY)

    def foreground_task(self):
        bg = self.start_keyed()['jobs'][0]
        self.begin(bg)
        fg = self.submit(bg, self.bg)['jobs'][0]
        self.begin(fg)
        return fg

    def opaque_foreground(self):
        path = self.root / 'keyed-foreground.png'
        image = Image.new('RGB', (1920,1080), '#FF00FF')
        ImageDraw.Draw(image).rectangle((100,100,600,600), fill='blue')
        image.save(path)
        return path

    def test_policy_is_frozen_and_request_uses_solid_field(self):
        fg = self.foreground_task()
        state = read_state(self.work)
        marker = json.loads((self.work/'initialization.json').read_text('utf8'))
        self.assertEqual(state['keyed_material_policy'], POLICY)
        self.assertEqual(marker['keyed_material_policy'], POLICY)
        self.assertFalse(fg['transparent_background'])
        self.assertEqual(fg['matte_key'], 'magenta')
        self.assertIn('RGB(255,0,255)', (self.work/state['tasks'][fg['id']]['brief']).read_text('utf8'))

    def test_opaque_magenta_registers_original_and_derives_actual_alpha(self):
        fg = self.foreground_task()
        raw = self.opaque_foreground()
        digest = sha256(raw)
        action = self.submit(fg, raw)
        self.assertEqual(action['kind'], 'describe')
        result = read_state(self.work)['tasks'][fg['id']]['result']
        self.assertEqual(result['sha256'], digest)
        self.assertEqual(sha256(self.work/result['raw']), digest)
        self.assertEqual(sha256(raw), digest)
        with Image.open(self.work/result['normalized']) as image:
            self.assertEqual(image.getpixel((10,10))[3], 0)
            self.assertEqual(image.getpixel((250,250)), (0,0,255,255))
        self.assertIn('material_file', result)

    def test_true_alpha_material_bytes_are_preserved(self):
        fg = self.foreground_task()
        digest = sha256(self.fg)
        self.assertEqual(self.submit(fg,self.fg)['kind'], 'describe')
        result = read_state(self.work)['tasks'][fg['id']]['result']
        self.assertEqual(sha256(self.work/result['material_file']), digest)

    def test_changed_prepared_material_is_detected_before_resume(self):
        fg = self.foreground_task()
        self.submit(fg, self.opaque_foreground())
        state = read_state(self.work)
        prepared = self.work/state['tasks'][fg['id']]['result']['material_file']
        prepared.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            controller.run(self.work)

    def test_saved_job_cannot_drop_frozen_keyed_policy(self):
        self.start_keyed()
        state = read_state(self.work)
        state.pop('keyed_material_policy')
        save_state(self.work,state,'test_tamper')
        with self.assertRaisesRegex(ValueError, 'keyed material'):
            controller.run(self.work)

    def test_final_boards_keep_native_alpha_after_keyed_reference(self):
        fg = self.foreground_task()
        self.submit(fg,self.opaque_foreground())
        action = controller.run(self.work,data={'kind':'layout','layout':{'items':[
            {'id':'icon','kind':'art','box':[100,100,500,500],
             'foreground_box':[100,100,500,500],'description':'Solid blue square'}]}})
        board = action['jobs'][0]
        self.assertTrue(board['transparent_background'])
        self.assertNotIn('matte_key',board)

    def test_native_board_after_keyed_foreground_flows_through_real_pptx_writer(self):
        fg = self.foreground_task()
        self.submit(fg, self.opaque_foreground())
        action = controller.run(self.work,data={'kind':'layout','layout':{'items':[
            {'id':'icon','kind':'art','box':[100,100,500,500],
             'foreground_box':[100,100,500,500],'description':'Solid blue square'}]}})
        task = action['jobs'][0]
        self.assertEqual(task['role'],'board')
        self.assertTrue(task['transparent_background'])
        self.begin(task)
        saved = read_state(self.work)['tasks'][task['id']]
        board = saved['board']
        member = board['members'][0]
        x,y,w,h = member['slot']
        inset_x,inset_y = member['inset_xy']
        raw = self.root/'returned-board.png'
        image = Image.new('RGBA',tuple(board['size']),(0,0,0,0))
        ImageDraw.Draw(image).rectangle((x+inset_x+10,y+inset_y+10,
            x+w-inset_x-10,y+h-inset_y-10),fill='blue')
        image.save(raw)
        result = self.submit(task,raw)
        self.assertEqual(result['kind'],'done',result)
        state = read_state(self.work)
        self.assertEqual(len(state['assets']),1)
        self.assertFalse(state['done']['skipped'])
        with zipfile.ZipFile(state['done']['pptx']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('ppt/slides/slide1.xml',archive.namelist())
        self.assertEqual(sha256(raw),state['tasks'][task['id']]['result']['sha256'])

    def test_keyed_cli_help_is_available(self):
        import contextlib, io
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            cli.main(['--help'])
        self.assertEqual(error.exception.code,0)
        self.assertIn('--keyed-materials',output.getvalue())

    def test_keyed_short_goal_exposes_exact_field_colour(self):
        task = {'role':'foreground','keyed_material_policy':POLICY,'matte_key':'green'}
        goal = execution._invocation_instructions(task,{'transparent_background':False,'references':[]})
        self.assertIn('RGB(0,255,0)',goal)
        self.assertIn('程序',goal)

    def test_foreground_review_is_scoped_to_sampling_and_final_boards_remain_strict(self):
        fg = self.foreground_task()
        self.assertIn('sampling reference',fg['role_review_scope'])
        self.assertIn('outside the sampled subjects',fg['role_review_scope'])
        board = {'role':'board','keyed_material_policy':POLICY,'matte_key':'magenta'}
        self.assertIn('exact lettering',controller._role_review_scope(board))

    def test_description_reads_stacked_icon_and_caption_boxes_separately(self):
        fg = self.foreground_task()
        action = self.submit(fg,self.opaque_foreground())
        self.assertIn('stacked icon',action['instructions'])
        self.assertIn('caption glyph',action['instructions'])

    def test_background_repair_short_goal_names_edit_target(self):
        task = {'role':'background','background_repair_reference':{'path':'refs/failed.png'}}
        goal = execution._invocation_instructions(task,{'transparent_background':False,
            'references':['/job/refs/failed.png','/job/input/working.png']})
        self.assertIn('待修背景',goal)
        self.assertIn('原图事实',goal)


if __name__ == '__main__':
    unittest.main()
