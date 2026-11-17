"""Explicit missing-content receipts through normal controller completion."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile


sys.dont_write_bytecode = True
PACKAGE = Path(os.environ.get('PRK_PACKAGE_UNDER_TEST', str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import controller as c
from newpath.storage import sha256, write_json


NORMAL_INSTRUCTIONS = ('Deliver this PowerPoint now and stop. No preview, visual check or further generation '
                       'is part of this workflow.')
LIVE_FIXTURE = os.environ.get('PRK_LIVE_PURE56_FIXTURE')


class MissingDeliverySummary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-delivery-summary-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'

    def ready_compiled(self, items, skipped, warnings=None):
        """An interrupted compiled-stage fixture; only receipt publishing runs."""
        self.work.mkdir()
        source = {}
        for name, key in [('original', 'byte_sha256'), ('working', 'working_sha256'), ('guide', 'guide_sha256')]:
            path = self.work / 'input' / (name + '.fixture')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(('Synthetic immutable ' + name).encode())
            source[name] = path.relative_to(self.work).as_posix()
            source[key] = sha256(path)
        layout = {'format': 'newpath.scene/1', 'items': copy.deepcopy(items)}
        write_json(self.work / 'layout.json', layout)
        build = self.work / 'build'
        build.mkdir()
        compiled = build / 'compiled.pptx'
        with ZipFile(compiled, 'w') as archive:
            archive.writestr('ppt/presentation.xml', '<presentation/>')
            archive.writestr('ppt/slides/slide1.xml', '<slide/>')
        write_json(build / 'compiled.report.json', {'compiled_sha256': sha256(compiled),
            'warnings': [], 'skipped': copy.deepcopy(skipped), 'synthetic_compiled_container': True})
        write_json(self.work / 'state.json', {'format': 'newpath.job/1', 'phase': 'boards',
            'source': source, 'tasks': {}, 'assets': [], 'layout': layout,
            'warnings': copy.deepcopy(warnings or []), 'active_batch': [],
            'started_at_utc': datetime.now(timezone.utc).isoformat(), 'events': []})

    def snapshot(self):
        return {path.relative_to(self.work).as_posix(): sha256(path)
                for path in self.work.rglob('*') if path.is_file()
                and path.name not in {'state.json', 'receipt.json', '.lock'}}

    def test_missing_chinese_name_and_layout_identity_are_prominent(self):
        item = {'id': 'radar', 'kind': 'art', 'description': '蓝色雷达图标',
                'box': [10, 20, 30, 40], 'target': [50, 60, 70, 80]}
        skipped = [{'id': 'radar', 'reason': 'missing_asset'}]
        warnings = [{'id': 'radar', 'code': 'ambiguous_member_omitted'}]
        self.ready_compiled([item], skipped, warnings)
        before = self.snapshot()
        response = c.run(self.work)
        self.assertEqual(response['kind'], 'done')
        summary = response.get('delivery_summary')
        self.assertIsInstance(summary, dict, 'Missing artwork needs an explicit high-priority delivery summary')
        self.assertEqual(summary['priority'], 'missing_content')
        self.assertEqual(summary['missing_count'], 1)
        missing = summary['missing_items'][0]
        self.assertEqual(missing, {'id': 'radar', 'name': '蓝色雷达图标', 'kind': 'art',
            'description': '蓝色雷达图标', 'source_box': [10, 20, 30, 40],
            'target_box': [50, 60, 70, 80], 'reason': 'missing_asset',
            'warning_codes': ['ambiguous_member_omitted']})
        self.assertLess(list(response).index('delivery_summary'), list(response).index('warnings'))
        self.assertIn('delivery_summary', response['instructions'])
        self.assertIn('before', response['instructions'])
        self.assertEqual(response['skipped'], skipped)
        self.assertEqual(response['warnings'], warnings)
        self.assertEqual(response['quality'], 'not_evaluated')
        self.assertEqual({key: self.snapshot()[key] for key in before}, before)

    def test_lettering_provides_the_exact_visible_name(self):
        item = {'id': 'gold', 'kind': 'art', 'description': 'An embossed gold prefix',
                'lettering': '落地尝试一：', 'box': [236, 112, 355, 72]}
        self.ready_compiled([item], [{'id': 'gold', 'reason': 'missing_asset_file'}])
        response = c.run(self.work)
        missing = response.get('delivery_summary', {}).get('missing_items', [{}])[0]
        self.assertEqual(missing.get('name'), '落地尝试一：')
        self.assertEqual(missing['target_box'], item['box'])
        self.assertEqual(missing['reason'], 'missing_asset_file')

    def test_unknown_layout_member_keeps_id_and_unknown_metadata(self):
        self.ready_compiled([], [{'id': 'unlisted', 'reason': 'missing_asset'}])
        response = c.run(self.work)
        missing = response.get('delivery_summary', {}).get('missing_items', [{}])[0]
        self.assertEqual(missing.get('name'), 'unlisted')
        self.assertIsNone(missing['description'])
        self.assertIsNone(missing['kind'])
        self.assertIsNone(missing['source_box'])

    def test_no_missing_items_keeps_existing_receipt_shape(self):
        self.ready_compiled([], [], ['An existing warning'])
        response = c.run(self.work)
        self.assertNotIn('delivery_summary', response)
        self.assertEqual(response['instructions'], NORMAL_INSTRUCTIONS)
        self.assertEqual(response['quality'], 'not_evaluated')
        self.assertEqual(response['warnings'], ['An existing warning'])

    def test_existing_terminal_receipt_is_not_rewritten(self):
        self.ready_compiled([], [])
        first = c.run(self.work)
        before = {path.name: path.read_bytes() for path in self.work.glob('*.json')}
        self.assertEqual(c.run(self.work), first)
        self.assertEqual({path.name: path.read_bytes() for path in self.work.glob('*.json')}, before)

    def test_older_terminal_missing_receipt_is_preserved_without_migration(self):
        self.ready_compiled([], [{'id': 'older_missing', 'reason': 'missing_asset'}])
        old_receipt = c.run(self.work)
        old_receipt.pop('delivery_summary', None)
        old_receipt['instructions'] = NORMAL_INSTRUCTIONS
        state = json.loads((self.work / 'state.json').read_text(encoding='utf-8'))
        state['done'] = old_receipt
        write_json(self.work / 'state.json', state)
        write_json(self.work / 'receipt.json', old_receipt)
        before = {path.name: path.read_bytes() for path in self.work.glob('*.json')}
        self.assertEqual(c.run(self.work), old_receipt)
        self.assertEqual({path.name: path.read_bytes() for path in self.work.glob('*.json')}, before)

    @unittest.skipUnless(LIVE_FIXTURE, 'Set PRK_LIVE_PURE56_FIXTURE for the private archived live fixture')
    def test_real_four_omissions_remain_done_and_preserve_actual_pptx_and_raw(self):
        fixture = Path(LIVE_FIXTURE)
        shutil.copytree(fixture, self.work)
        state = json.loads((self.work / 'state.json').read_text(encoding='utf-8'))
        compiled = json.loads((self.work / 'build/compiled.report.json').read_text(encoding='utf-8'))
        original_receipt = copy.deepcopy(state['done'])
        state.pop('done')
        state['phase'] = 'boards'
        for asset in state['assets']:
            parts = PurePosixPath(asset['path']).parts
            asset['path'] = str(self.work.joinpath(*parts[parts.index('assets'):]))
        render_warnings = compiled['warnings']
        self.assertEqual(state['warnings'][-len(render_warnings):], render_warnings)
        state['warnings'] = state['warnings'][:-len(render_warnings)]
        write_json(self.work / 'state.json', state)
        before = self.snapshot()
        # The private archived job keeps its original package identity. Only
        # identity lookup is adapted; normal integrity, drive and finish run.
        with mock.patch.object(c.version, 'runtime_identity', return_value=state['runtime_identity']):
            response = c.run(self.work)
        self.assertEqual(response['kind'], 'done')
        self.assertEqual(response['quality'], 'not_evaluated')
        summary = response.get('delivery_summary')
        self.assertIsInstance(summary, dict, 'The actual four omissions must be disclosed')
        self.assertEqual(summary['missing_count'], 4)
        names = {item['id']: item for item in summary['missing_items']}
        self.assertEqual(set(names), {'title_yellow', 'photo_nameplate', 'img_phones', 'img_dashboard'})
        self.assertEqual(names['title_yellow']['name'], '落地尝试一：')
        self.assertEqual(names['title_yellow']['source_box'], [236, 112, 355, 72])
        self.assertIn('ambiguous_member_omitted', names['photo_nameplate']['warning_codes'])
        self.assertEqual(response['skipped'], original_receipt['skipped'])
        self.assertEqual(response['warnings'], original_receipt['warnings'])
        self.assertEqual(response['recorded_invocation_starts'], 5)
        self.assertEqual(response['sha256'], original_receipt['sha256'])
        self.assertEqual({key: self.snapshot()[key] for key in before}, before)
        self.assertEqual(len(list((self.work / 'requests').iterdir())), 5)


if __name__ == '__main__':
    unittest.main()
