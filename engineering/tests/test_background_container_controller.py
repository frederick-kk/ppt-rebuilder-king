"""Freeze the container route at initialization without changing legacy jobs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'compat-runtime'))
from PIL import Image, ImageDraw
from newpath import controller, execution
from newpath.storage import read_state, save_state

POLICY = 'decorative-background-containers/1'


class BackgroundContainerController(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-container-policy-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'
        self.source = self.root / 'source.png'
        Image.new('RGB', (160, 90), 'white').save(self.source)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(controller, '_runtime').start()
        mock.patch.object(controller.version, 'runtime_identity', return_value={
            'package_id': 'synthetic-container-test', 'manifest_sha256': '0' * 64,
        }).start()

    def begin(self, production=True):
        options = {'execution_policy': execution.MODE} if production else {}
        return controller.run(self.work, source=self.source, **options)

    def test_new_production_job_freezes_container_policy_in_both_records(self):
        self.begin()
        marker = json.loads((self.work / 'initialization.json').read_text('utf-8'))
        self.assertEqual(marker.get('decorative_background_policy'), POLICY)
        self.assertEqual(read_state(self.work).get('decorative_background_policy'), POLICY)
        state = read_state(self.work)
        for task in state['tasks'].values():
            self.assertEqual(task.get('decorative_background_policy'), POLICY)
            self.assertEqual(controller._public_task(self.work, task).get('decorative_background_policy'), POLICY)

    def test_legacy_programmatic_entry_keeps_original_layer_route(self):
        self.begin(production=False)
        state = read_state(self.work)
        self.assertNotIn('decorative_background_policy', state)
        background = next(task for task in state['tasks'].values() if task['role'] == 'background')
        self.assertIn('paper or card panels', (self.work / background['brief']).read_text('utf-8'))

    def test_interrupted_production_initialization_retains_frozen_policy(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture stop')):
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                self.begin()
        controller.run(self.work)
        self.assertEqual(read_state(self.work).get('decorative_background_policy'), POLICY)

    def test_interrupted_legacy_initialization_retains_original_route(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture stop')):
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                self.begin(production=False)
        controller.run(self.work)
        self.assertNotIn('decorative_background_policy', read_state(self.work))

    def describe_fixture(self):
        self.begin(production=False)
        state = read_state(self.work)
        state['phase'] = 'describe'
        save_state(self.work, state, 'synthetic_description_fixture')
        return controller.run(self.work)

    def test_description_exposes_existing_schema_and_pixel_size_guidance(self):
        result = self.describe_fixture()
        self.assertEqual(result['kind'], 'describe')
        self.assertEqual(len(result['files']), 2)
        schema = Path(result.get('schema_file', 'missing-schema'))
        self.assertTrue(schema.is_file(), 'Current action must point to its existing scene schema')
        self.assertEqual(schema.name, 'scene.md')
        self.assertIn('size', result['instructions'])
        self.assertIn('pixels', result['instructions'])

    def test_description_policy_only_applies_to_opted_in_job(self):
        self.begin()
        state = read_state(self.work)
        state['phase'] = 'describe'
        save_state(self.work, state, 'synthetic_container_description_fixture')
        result = controller.run(self.work)
        self.assertEqual(result.get('decorative_background_policy'), POLICY)
        self.assertIn('empty decorative containers', result['instructions'])

    def test_saved_legacy_job_rejects_an_unbound_policy_change(self):
        self.describe_fixture()
        state = read_state(self.work)
        state['decorative_background_policy'] = POLICY
        save_state(self.work, state, 'synthetic_policy_corruption')
        with self.assertRaisesRegex(ValueError, 'frozen decorative background policy'):
            controller.run(self.work)

    def test_unknown_policy_in_interrupted_initialization_is_rejected(self):
        with mock.patch.object(controller, 'prepare_source', side_effect=RuntimeError('fixture stop')):
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                self.begin()
        path = self.work / 'initialization.json'
        marker = json.loads(path.read_text('utf-8'))
        marker['decorative_background_policy'] = 'unknown-policy'
        path.write_text(json.dumps(marker), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unsupported initialization decorative background policy'):
            controller.run(self.work)

    def test_native_text_correction_retains_frozen_container_policy(self):
        self.begin()
        state = read_state(self.work)
        state['phase'] = 'describe'
        data = {'kind': 'layout', 'layout': {'items': [
            {'kind': 'text', 'id': 'line', 'box': [20, 20, 100, 30],
             'text': 'source line', 'size': 20},
        ]}}
        with mock.patch.object(controller.recovery, 'enabled', return_value=True), \
                mock.patch.object(controller, 'preflight_native_text', return_value=[{'text_id': 'line'}]):
            result = controller._submit(self.work, state, data)
        self.assertEqual(result['kind'], 'describe')
        self.assertEqual(result['reason'], 'native_text_requires_correction')
        self.assertEqual(result.get('decorative_background_policy'), POLICY)
        self.assertTrue(Path(result['schema_file']).is_file())


if __name__ == '__main__':
    unittest.main()
