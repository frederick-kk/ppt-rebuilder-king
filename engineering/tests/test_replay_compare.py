import importlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


class ReplayComparisonContracts(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module('engineering.replay_compare')
        except ModuleNotFoundError:
            self.fail('An offline replay comparator must exist before compatibility can be assessed')

    def test_prompt_text_and_geometry_are_not_filtered_as_noise(self):
        m = self.module()
        for before, after in [({'text': 'AB'}, {'text': 'A'}),
                              ({'slot': [24, 24, 100, 100]}, {'slot': [25, 24, 100, 100]}),
                              ({'prompt': 'keep all'}, {'prompt': 'keep some'})]:
            self.assertTrue(m.deep_differences(before, after))

    def test_pptx_only_core_creation_and_modified_times_are_ignored(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary) / 'a.pptx', Path(temporary) / 'b.pptx'
            def write(path, timestamp, text):
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr('docProps/core.xml', '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>' + timestamp + '</dcterms:created><cp:keywords>provenance</cp:keywords></cp:coreProperties>')
                    z.writestr('ppt/slides/slide1.xml', '<slide>' + text + '</slide>')
            write(a, '2026-01-01T01:00:00Z', 'ABC')
            write(b, '2026-01-02T01:00:00Z', 'ABC')
            self.assertEqual(m.pptx_content(a)['content_sha256'], m.pptx_content(b)['content_sha256'])
            self.assertTrue(m.pptx_content(a)['exclusions'])
            write(b, '2026-01-02T01:00:00Z', 'AB')
            self.assertNotEqual(m.pptx_content(a)['content_sha256'], m.pptx_content(b)['content_sha256'])

    def test_unlisted_file_or_modified_fixture_fails_closed(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'job').mkdir()
            (root / 'job/a.txt').write_text('modified', encoding='utf-8')
            (root / 'fixture.json').write_text(json.dumps({'format': 'ppt-rebuilder.fixture/1', 'source': 'job/a.txt', 'files': [{'path': 'job/a.txt', 'sha256': '0'*64, 'size': 8}]}), encoding='utf-8')
            with self.assertRaises(ValueError):
                m.verify_fixture(root)

    def test_embedded_local_image_description_maps_only_exact_work_root(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, b = root / 'a.pptx', root / 'b.pptx'
            template = '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cNvPr id="3" name="art" descr="{path}/assets/art.png"/><text>{text}</text></p:sld>'
            for target, work in [(a, '/base'), (b, '/candidate')]:
                with zipfile.ZipFile(target, 'w') as z:
                    z.writestr('ppt/slides/slide1.xml', template.format(path=work, text='kept'))
            self.assertEqual(m.pptx_content(a, [('/base', '$WORK')])['content_sha256'],
                             m.pptx_content(b, [('/candidate', '$WORK')])['content_sha256'])
            self.assertNotEqual(m.pptx_content(a)['content_sha256'], m.pptx_content(b)['content_sha256'])

    def test_comparator_refuses_nonempty_output_and_input_overlap(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'; runtime.mkdir()
            fixture = root / 'fixture'; fixture.mkdir()
            with self.assertRaises(ValueError):
                m.compare(runtime, runtime, fixture, runtime / 'report')
            with self.assertRaises(ValueError):
                m.compare(runtime, runtime, fixture, fixture)

    def test_visible_text_containing_a_work_root_is_never_normalized(self):
        m = self.module()
        exclusions = []
        value = m.normalized({'text': '/base/example', 'path': '/base/assets/a.png'}, ('render_manifests', 0, 'items', 0), [('/base', '$WORK')], {}, exclusions)
        self.assertEqual(value['text'], '/base/example')
        self.assertEqual(value['path'], '$WORK/assets/a.png')


if __name__ == '__main__':
    unittest.main()
