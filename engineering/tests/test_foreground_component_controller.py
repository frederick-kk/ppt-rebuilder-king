import copy
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller, __main__ as cli
from newpath.storage import read_state, save_state
from test_result_role_binding import ReviewedOutputFixture

POLICY = 'generated-foreground-members/1'
ORDER = 'background-then-foreground/1'


class ForegroundComponentController(ReviewedOutputFixture):
    def start_components(self, policy=POLICY):
        self.assertIn('foreground_component_policy', inspect.signature(controller.run).parameters)
        with mock.patch.object(controller, '_runtime'):
            return controller.run(self.work, source=self.source,
                execution_policy='explicit-start/1', delivery_policy='reviewed-existing-output/1',
                layer_generation_order=ORDER, foreground_component_policy=policy)

    def ready(self):
        bg = self.start_components()['jobs'][0]
        self.begin(bg)
        fg = self.submit(bg, self.bg)['jobs'][0]
        self.begin(fg)
        return fg, self.submit(fg, self.fg)

    def test_opt_in_is_explicit_and_frozen(self):
        self.start_components()
        state = read_state(self.work)
        marker = json.loads((self.work / 'initialization.json').read_text('utf8'))
        self.assertEqual(state['foreground_component_policy'], POLICY)
        self.assertEqual(marker['foreground_component_policy'], POLICY)
        before = (self.work / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'foreground component'):
            controller.run(self.work, foreground_component_policy='unsupported')
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_describe_includes_actual_foreground_without_changing_source_facts(self):
        fg, action = self.ready()
        state = read_state(self.work)
        saved = state['tasks'][fg['id']]['result']['normalized']
        self.assertEqual(action['files'][-1], str(self.work / saved))
        self.assertIn('foreground_box', action['instructions'])
        self.assertIn('original source', action['instructions'])
        self.assertIsNone(action['reply_template']['layout']['primary_wordart_id'])

    def test_written_art_uses_generated_foreground_board(self):
        self.ready()
        action = controller.run(self.work, data={'kind':'layout', 'layout':{'items':[
            {'id':'title','kind':'art','box':[100,100,500,120],
             'foreground_box':[100,100,500,120], 'lettering':'Title',
             'description':'Complete blue title with detached arc'}]}})
        self.assertTrue(action['jobs'])
        self.assertTrue(all(task['role']=='board' for task in action['jobs']))
        state = read_state(self.work)
        self.assertIsNone(state['layout']['primary_wordart_id'])
        self.assertFalse(any(task['role']=='wordart' for task in state['tasks'].values()))

    def test_missing_observation_requests_same_description_without_generation(self):
        self.ready()
        action = controller.run(self.work, data={'kind':'layout', 'layout':{'items':[
            {'id':'icon','kind':'art','box':[100,100,80,80],'description':'Blue icon'}]}})
        self.assertEqual(action['kind'], 'describe')
        self.assertEqual(action['reason'], 'foreground_sampling_requires_correction')
        self.assertFalse(action['generate_more'])
        self.assertEqual(len(read_state(self.work)['tasks']), 2)

    def test_resume_cannot_change_frozen_sampling_policy(self):
        self.start_components()
        state = read_state(self.work)
        state.pop('foreground_component_policy')
        save_state(self.work, state, 'fixture_tamper')
        with self.assertRaisesRegex(ValueError, 'foreground component'):
            controller.run(self.work)

    def test_legacy_job_keeps_independent_title_route(self):
        with mock.patch.object(controller, '_runtime'):
            action=controller.run(self.work, source=self.source, execution_policy='explicit-start/1',
                delivery_policy='reviewed-existing-output/1',layer_generation_order=ORDER)
        bg=action['jobs'][0]
        self.begin(bg)
        fg=self.submit(bg,self.bg)['jobs'][0]
        self.begin(fg)
        self.submit(fg,self.fg)
        action=controller.run(self.work,data={'kind':'layout','layout':{'items':[
            {'id':'title','kind':'art','box':[100,100,500,120],
             'lettering':'Title','description':'Original title'}]}})
        self.assertEqual(action['jobs'][0]['role'],'wordart')

    def test_cli_advertises_foreground_components(self):
        import contextlib
        import io
        output=io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            cli.main(['--help'])
        self.assertEqual(error.exception.code,0)
        self.assertIn('--foreground-components',output.getvalue())

    def test_generation_and_begin_expose_component_observation_scope(self):
        bg=self.start_components()['jobs'][0]
        self.assertIn('actual saved PNG',bg['role_review_scope'])
        permit=self.begin(bg)
        self.assertIn('actual saved PNG',permit['role_review_scope'])
        fg=self.submit(bg,self.bg)['jobs'][0]
        self.assertIn('surrounding transparency',fg['role_review_scope'])
        permit=self.begin(fg)
        self.assertIn('surrounding transparency',permit['role_review_scope'])


if __name__ == '__main__':
    unittest.main()
