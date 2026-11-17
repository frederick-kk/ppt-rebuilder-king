"""Recover unbuildable native text before image tasks and scene freezing."""
import copy
from pathlib import Path
import unittest
from unittest import mock
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image

from test_result_role_binding import ReviewedOutputFixture
from newpath import controller as c, renderer
from newpath.layout import normalize_layout
from newpath.storage import read_state, sha256


class NativeTextPreflight(ReviewedOutputFixture):
    def start(self, *, recover=True):
        options = {'execution_policy':'explicit-start/1',
                   'post_return_prompt_policy':'audit-existing-return/1',
                   'delivery_policy':'reviewed-existing-output/1'}
        if recover:
            options['recovery_policy'] = 'bounded-recovery/1'
        with mock.patch.object(c, '_runtime'):
            response = c.run(self.work, source=self.source, **options)
        self.jobs = {task['role']:task for task in response['jobs']}
        return self.jobs

    def ready(self, *, recover=True):
        jobs = self.start(recover=recover)
        for role, path in [('background',self.bg),('foreground',self.fg)]:
            self.begin(jobs[role])
            response = self.submit(jobs[role], path)
        self.assertEqual(response['kind'], 'describe')

    def body(self, width=20):
        return {'id':'body','kind':'text','text':'普通正文保持可编辑',
                'box':[100,100,width,48],'size':32}

    def table(self):
        return {'id':'table','kind':'table','rows':[['甲乙丙丁\n短']],
                'box':[500,500,32,100],'size':32}

    def preflight(self, layout):
        helper = getattr(renderer, 'preflight_native_text', None)
        self.assertTrue(callable(helper), 'Native text needs a reusable pre-freeze preflight')
        return helper(layout)

    def file_snapshot(self):
        return {path.relative_to(self.work).as_posix():sha256(path)
                for path in self.work.rglob('*') if path.is_file()}

    def test_preflight_reports_exact_text_and_table_line_without_mutating_scene(self):
        layout = normalize_layout({'items':[self.body(),self.table()]})
        original = copy.deepcopy(layout)
        issues = self.preflight(layout)
        self.assertEqual({issue['text_id'] for issue in issues},
                         {'body','table::text.0.0.0'})
        by_id = {issue['text_id']:issue for issue in issues}
        self.assertEqual(by_id['table::text.0.0.0']['text'], '甲乙丙丁')
        self.assertEqual(by_id['table::text.0.0.0']['authored_box'],[500,500,32,50])
        self.assertEqual(by_id['body']['authored_size_px'],32)
        self.assertIn(by_id['body']['measurement']['status'],{'measured','estimated'})
        self.assertGreater(by_id['body']['measurement']['width_px'],20)
        self.assertEqual(layout,original)

    def test_estimated_width_remains_estimated_and_keeps_existing_fit_limit(self):
        layout = {'items':[self.body(120)]}
        layout['items'][0]['text'] = 'Scientific method'
        with mock.patch.object(renderer,'_find_font',return_value=None):
            issues = self.preflight(layout)
        self.assertEqual(len(issues),1)
        measurement = issues[0]['measurement']
        self.assertEqual(measurement['status'],'estimated')
        self.assertFalse(measurement['estimate_is_strict_upper_bound'])
        self.assertEqual(measurement['max_font_reduction_fraction'],.25)

    def test_bad_native_text_keeps_describe_and_every_existing_job_file(self):
        self.ready()
        before = self.file_snapshot()
        icon = {'id':'icon','kind':'art','description':'single blue icon',
                'box':[100,100,400,400]}
        response = c.run(self.work,data={'kind':'layout','layout':{'items':[self.body(),icon]}})
        self.assertEqual(response['kind'],'describe')
        self.assertEqual(response['reason'],'native_text_requires_correction')
        self.assertFalse(response['generate_more'])
        self.assertEqual([issue['text_id'] for issue in response['issues']],['body'])
        self.assertEqual(response['files'],[str(self.work/'input'/'working.png'),
                                            str(self.work/'input'/'ruler.png')])
        self.assertEqual(response['reply_template']['layout']['items'][0]['text'],self.body()['text'])
        self.assertEqual(self.file_snapshot(),before)
        self.assertFalse((self.work/'layout.json').exists())
        self.assertFalse((self.work/'boards').exists())
        self.assertFalse(list(self.work.rglob('*.pptx')))
        self.assertEqual(read_state(self.work)['phase'],'describe')
        self.assertEqual(len(read_state(self.work)['tasks']),2)

    def test_table_error_is_recoverable_before_any_art_task_is_written(self):
        self.ready()
        before = self.file_snapshot()
        try:
            response = c.run(self.work,data={'kind':'layout','layout':{'items':[self.table()]}})
        except ValueError as error:
            self.fail('A table line error needs a recoverable describe action: '+str(error))
        self.assertEqual(response['kind'],'describe')
        self.assertEqual(response['issues'][0]['text_id'],'table::text.0.0.0')
        self.assertEqual(self.file_snapshot(),before)

    def test_corrected_layout_reuses_layers_and_writes_real_editable_pptx(self):
        self.ready()
        state = read_state(self.work)
        immutable = {path:digest for path,digest in self.file_snapshot().items()
                     if path.startswith(('layers/','requests/','returned/'))}
        try:
            response = c.run(self.work,data={'kind':'layout','layout':{'items':[self.body()]}})
        except ValueError as error:
            self.fail('Native text failure froze the page before correction: '+str(error))
        self.assertEqual(response['kind'],'describe')
        fixed = copy.deepcopy(response['reply_template'])
        fixed['layout']['items'][0]['box'][2] = 900
        result = c.run(self.work,data=fixed)
        self.assertEqual(result['kind'],'done')
        self.assertEqual(result['expected_generation_tasks'],2)
        self.assertEqual(set(read_state(self.work)['tasks']),set(state['tasks']))
        after = self.file_snapshot()
        self.assertTrue(all(after[path]==digest for path,digest in immutable.items()))
        with ZipFile(result['pptx']) as archive:
            slide=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        self.assertIn(self.body()['text'],[node.text for node in slide.findall('.//a:t',ns)])

    def test_legacy_nonrecovery_job_keeps_its_original_freeze_path(self):
        self.ready(recover=False)
        icon={'id':'icon','kind':'art','description':'single blue icon','box':[100,100,400,400]}
        response=c.run(self.work,data={'kind':'layout','layout':{'items':[self.body(),icon]}})
        self.assertEqual(response['kind'],'generate')
        self.assertEqual(read_state(self.work)['phase'],'boards')
        self.assertTrue((self.work/'layout.json').is_file())

    def test_preflight_does_not_accept_an_opaque_native_foreground(self):
        jobs=self.start()
        self.begin(jobs['background']);self.submit(jobs['background'],self.bg)
        self.begin(jobs['foreground'])
        opaque=self.root/'opaque.png'
        Image.new('RGB',(1920,1080),'red').save(opaque)
        response=self.submit(jobs['foreground'],opaque)
        self.assertEqual(response['kind'],'generate')
        state=read_state(self.work)
        self.assertNotEqual(state['tasks'][jobs['foreground']['id']]['status'],'recorded')
        self.assertEqual(state['tasks'][jobs['foreground']['id']]['attempts'][-1]['outcome'],'material')
        self.assertIsNone(state.get('layout'))

    def test_valid_table_lines_and_native_scene_write_expected_cell_geometry(self):
        layout={'items':[{'id':'table','kind':'table','rows':[['甲\n乙','丙'],['丁','戊']],
                         'box':[100,200,400,200],'column_widths':[160,240],
                         'row_heights':[100,100],'size':20}]}
        self.assertEqual(self.preflight(layout),[])
        output=self.root/'table.pptx'
        report=renderer.build_presentation(layout,self.bg,[],output,
                                            textfit_policy=renderer.TEXT_FIT_POLICY)
        table=next(item for item in report['objects'] if item['id']=='table')
        self.assertEqual([(line['row'],line['column'],line['line']) for line in table['cell_lines']],
                         [(0,0,0),(0,0,1),(0,1,0),(1,0,0),(1,1,0)])
        with ZipFile(output) as archive:
            slide=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
            'p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
        texts=[]
        for shape in slide.findall('.//p:sp',ns):
            text=''.join(node.text or '' for node in shape.findall('.//a:t',ns))
            if text:
                off=shape.find('.//a:xfrm/a:off',ns)
                ext=shape.find('.//a:xfrm/a:ext',ns)
                texts.append((text,int(off.attrib['x']),int(off.attrib['y']),
                              int(ext.attrib['cx']),int(ext.attrib['cy'])))
        self.assertEqual(texts,[('甲',635000,1270000,1016000,317500),
                                ('乙',635000,1587500,1016000,317500),
                                ('丙',1651000,1270000,1524000,635000),
                                ('丁',635000,1905000,1016000,635000),
                                ('戊',1651000,1905000,1524000,635000)])


if __name__=='__main__':
    unittest.main(verbosity=2)
