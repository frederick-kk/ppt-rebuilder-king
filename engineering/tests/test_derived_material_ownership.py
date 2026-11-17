"""Program-made materials cannot become new generated raw image returns."""
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from test_result_role_binding import ReviewedOutputFixture
import test_keyed_material_controller as keyed_fixture
from newpath import controller, keyed
from newpath.storage import read_state, sha256


class DerivedMaterialOwnership(ReviewedOutputFixture):
    start_keyed = keyed_fixture.KeyedMaterialController.start_keyed
    foreground_task = keyed_fixture.KeyedMaterialController.foreground_task
    opaque_foreground = keyed_fixture.KeyedMaterialController.opaque_foreground

    def board_task(self):
        task = controller.run(self.work, data={'kind': 'layout', 'layout': {'items': [
            {'id': 'icon', 'kind': 'art', 'box': [100, 100, 500, 500],
             'foreground_box': [100, 100, 500, 500], 'description': 'Solid blue square'}]}})['jobs'][0]
        self.begin(task)
        return task

    def prepared_foreground(self, variant='path', field='material_file'):
        fg = self.foreground_task()
        self.submit(fg, self.opaque_foreground())
        saved = read_state(self.work)['tasks'][fg['id']]['result']
        path = self.work / saved[field]
        return self.copy_variant(path, variant), saved

    def copy_variant(self, path, variant):
        if variant == 'path':
            return path
        output = self.root / (variant + '.png')
        if variant == 'bytes':
            output.write_bytes(path.read_bytes())
        else:
            with Image.open(path) as image:
                image.convert('RGBA').save(output, compress_level=0)
            self.assertNotEqual(sha256(output), sha256(path))
        return output

    def assert_cross_task_rejected(self, field, variant):
        derived, _ = self.prepared_foreground(variant, field)
        board = self.board_task()
        with self.assertRaisesRegex(ValueError, 'derived|normalized|material|another task'):
            self.submit(board, derived)
        self.assertNotIn('result', read_state(self.work)['tasks'][board['id']])

    def test_material_path_cannot_be_a_board_raw(self):
        self.assert_cross_task_rejected('material_file', 'path')

    def test_copied_material_bytes_cannot_be_a_board_raw(self):
        self.assert_cross_task_rejected('material_file', 'bytes')

    def test_reencoded_material_pixels_cannot_be_a_board_raw(self):
        self.assert_cross_task_rejected('material_file', 'reencoded')

    def test_normalized_foreground_path_cannot_be_a_board_raw(self):
        self.assert_cross_task_rejected('normalized', 'path')

    def test_reencoded_normalized_pixels_cannot_be_a_board_raw(self):
        self.assert_cross_task_rejected('normalized', 'reencoded')

    def test_unregistered_prepared_material_cannot_be_the_same_tasks_first_raw(self):
        fg = self.foreground_task()
        raw = self.opaque_foreground()
        output = self.work / 'materials' / f"{fg['id']}-{fg['attempt']}-{sha256(raw)[:16]}.png"
        keyed.prepare_material(raw, output, fg['matte_key'])
        with self.assertRaisesRegex(ValueError, 'derived|material|program'):
            self.submit(fg, output)
        self.assertNotIn('result', read_state(self.work)['tasks'][fg['id']])

    def test_reencoded_unregistered_prepared_material_is_also_rejected(self):
        fg = self.foreground_task()
        raw = self.opaque_foreground()
        output = self.work / 'materials' / f"{fg['id']}-{fg['attempt']}-{sha256(raw)[:16]}.png"
        keyed.prepare_material(raw, output, fg['matte_key'])
        copy = self.copy_variant(output, 'reencoded')
        with self.assertRaisesRegex(ValueError, 'derived|material|program'):
            self.submit(fg, copy)

    def test_native_pass_through_keeps_own_real_raw_and_returned_preservation_idempotent(self):
        fg = self.foreground_task()
        self.submit(fg, self.fg)
        saved = read_state(self.work)['tasks'][fg['id']]['result']
        self.assertEqual(saved['material_sha256'], saved['sha256'])
        before = (self.work / 'state.json').read_bytes()
        self.submit(fg, self.fg)
        self.submit(fg, self.work / saved['raw'])
        self.assertEqual((self.work / 'state.json').read_bytes(), before)

    def test_actual_opaque_raw_remains_valid_after_its_material_was_prepared(self):
        fg = self.foreground_task()
        raw = self.opaque_foreground()
        output = self.work / 'materials' / f"{fg['id']}-{fg['attempt']}-{sha256(raw)[:16]}.png"
        keyed.prepare_material(raw, output, fg['matte_key'])
        self.assertEqual(self.submit(fg, raw)['kind'], 'describe')
        self.assertEqual(read_state(self.work)['tasks'][fg['id']]['result']['sha256'], sha256(raw))

    def test_actual_native_raw_remains_valid_after_pass_through_preparation(self):
        fg = self.foreground_task()
        output = self.work / 'materials' / f"{fg['id']}-{fg['attempt']}-{sha256(self.fg)[:16]}.png"
        keyed.prepare_material(self.fg, output, fg['matte_key'])
        self.assertEqual(self.submit(fg, self.fg)['kind'], 'describe')
        self.assertEqual(read_state(self.work)['tasks'][fg['id']]['result']['sha256'], sha256(self.fg))


if __name__ == '__main__':
    unittest.main()
