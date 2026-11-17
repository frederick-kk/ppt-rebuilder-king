"""R26 outgoing reference contracts, without provider or browser calls.

These tests verify explicit task roles and coordinate payloads. Actual visual
ownership and transparent subject edges still require the real returned images.
"""
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import prompts
from newpath.artwork import prepare_boards, prepare_source_boards
from newpath.native import MODE


TRANSFORM = {'offset': [0, 100], 'resized': [1920, 880]}
OLD_LAYER_HASHES = {'background|None|False': '7a66c08e16beff7f6e06c4e65560b9abb1ac722281fb3443c499cba9565701f5',
 'background|None|True': '83df1bcc81fe6e4be915b9333dd676dbd9358e1237a8610cba1bcd8943cfe749',
 'background|native-transparent-materials/1|False': '7a66c08e16beff7f6e06c4e65560b9abb1ac722281fb3443c499cba9565701f5',
 'background|native-transparent-materials/1|True': '83df1bcc81fe6e4be915b9333dd676dbd9358e1237a8610cba1bcd8943cfe749',
 'foreground|None|False': 'a6be348656fda937e4ddfdbed3597b8f8d8eb389f27160a41bb01e13e16fa02a',
 'foreground|None|True': 'b1b875b46b6e9bd99f99ccb3b0fe034f60cac7a9844bdf48bd31da1dc8e01732',
 'foreground|native-transparent-materials/1|False': '1fb4c5b635244c2b46c3d3586766156bc88a25da54e2051bdaf150b047e85b78',
 'foreground|native-transparent-materials/1|True': '59dbc56aef3ad029505a69dbaf35f0139d43b5c0135cc8c98cbf7eba23b72de0'}
OLD_BOARD_HASHES = {'None': '97e861a6c3f994883616d18179cfb2bbe8984d4d9100c21f0b11a9962089dee1',
 'native-transparent-materials/1': '03519052c42aa74f19e5af86ebeebde7afcdad4af5ed7f7103e1c77c8b38917c'}


def board_fixture(mode=MODE, foreground_sample=False):
    members = [
        {'id': 'line-icon', 'box': [100, 200, 80, 70], 'slot': [8, 8, 240, 240],
         'description': 'Complete source line icon', 'presentation': 'cutout', 'inset_px': 8},
        {'id': 'content-photo', 'box': [400, 250, 180, 130], 'slot': [264, 8, 240, 240],
         'description': 'Complete rectangular content photo', 'presentation': 'rectangle',
         'embedded_image': True, 'lettering': 'IMAGE INTERNAL WRITING', 'inset_px': 8},
    ]
    if foreground_sample:
        members[0].update(foreground_box=[110.5, 205.25, 82, 72],
                          foreground_crop_pixels=[110, 205, 193, 278])
        members[1].update(foreground_box=[420, 260, 180, 130],
                          foreground_crop_pixels=[420, 260, 600, 390])
    return {'members': members, 'size': [512, 256], 'key': 'magenta',
            'background_mode': mode,
            'grid': {'rows': 1, 'columns': 2, 'cell_size': [240, 240], 'margin': 8, 'gap': 16}}


def assignments(brief):
    return json.JSONDecoder().raw_decode(brief.split('Assignments: ', 1)[1])[0]


class ForegroundComponentScopePrompts(unittest.TestCase):
    def test_new_layer_option_is_optional_keyword_only(self):
        parameter = inspect.signature(prompts.layer_brief).parameters.get('foreground_components_only')
        self.assertIsNotNone(parameter)
        self.assertIs(parameter.default, False)
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_legacy_layer_payloads_are_byte_identical(self):
        self.assertEqual(len(OLD_LAYER_HASHES), 8)
        for role in ('background', 'foreground'):
            for mode in (None, MODE):
                for decorative in (False, True):
                    options = {'background_mode': mode, 'decorative_background': decorative}
                    expected = OLD_LAYER_HASHES[f'{role}|{mode}|{decorative}']
                    before = prompts.layer_brief(role, TRANSFORM, **options)
                    self.assertEqual(hashlib.sha256(before.encode()).hexdigest(), expected)
                    self.assertEqual(prompts.layer_brief(role, TRANSFORM,
                        foreground_components_only=False, **options), before)

    def test_new_foreground_clears_assigned_background_and_preserves_real_member_material(self):
        text = prompts.layer_brief('foreground', TRANSFORM, background_mode=MODE,
            decorative_background=True, foreground_components_only=True)
        self.assertIn('actual component-sampling reference', text)
        self.assertIn('integrated background scenes and their people', text)
        self.assertIn('fixed empty paper bases, gold frames and card containers assigned to background', text)
        self.assertIn('Ordinary native text remains a reference', text)
        self.assertIn('without adding a paper or card underlay', text)
        self.assertIn('badge', text)
        self.assertIn('complete rectangular photos', text)
        self.assertIn('fully transparent with alpha 0', text)
        self.assertIn('complete foreground groups', text)

    def test_background_bytes_and_selected_material_mode_remain_unchanged(self):
        for role in ('background', 'foreground'):
            for mode in (None, MODE):
                before = prompts.layer_brief(role, TRANSFORM, background_mode=mode,
                                             decorative_background=True)
                after = prompts.layer_brief(role, TRANSFORM, background_mode=mode,
                    decorative_background=True, foreground_components_only=True)
                if role == 'background':
                    self.assertEqual(after, before)
                else:
                    self.assertTrue(after.startswith(before))
                    self.assertIn('Return one'.lower(), after.lower())
                    if mode is None:
                        self.assertIn('solid magenta', after)
                    else:
                        self.assertNotIn('solid magenta', after)

    def test_new_board_assignments_keep_source_and_foreground_coordinate_systems_separate(self):
        board = board_fixture(foreground_sample=True)
        original = copy.deepcopy(board)
        text = prompts.board_brief(board)
        rows = assignments(text)
        for row, member in zip(rows, board['members']):
            self.assertEqual(row['source_box_pixels'], member['box'])
            self.assertEqual(row['slot_pixels'], member['slot'])
            self.assertEqual(row.get('foreground_box'), member['foreground_box'])
            self.assertEqual(row.get('foreground_crop_pixels'), member['foreground_crop_pixels'])
            self.assertEqual(row.get('presentation'), member['presentation'])
        self.assertIn('foreground_box=[x,y,width,height]', text)
        self.assertIn('generated 1920x1080 foreground', text)
        self.assertIn('foreground_crop_pixels=[left,top,right,bottom]', text)
        self.assertIn('source_box_pixels continues to locate the subject in image 2', text)
        self.assertEqual(board, original)

    def test_new_board_preserves_photo_internal_writing_and_original_cutout_backing(self):
        text = prompts.board_brief(board_fixture(foreground_sample=True))
        row = assignments(text)[1]
        self.assertIs(row['embedded_image'], True)
        self.assertEqual(row['presentation'], 'rectangle')
        self.assertEqual(row['image_internal_writing'], 'IMAGE INTERNAL WRITING')
        self.assertNotIn('required_artistic_writing', row)
        self.assertIn('neighbouring red label', text)
        self.assertIn('badge', text)
        self.assertIn('complete rectangular photos', text)
        self.assertIn('outside each cutout subject', text)
        self.assertIn('Do not erase white or other colours globally', text)
        self.assertIn('Preserve source translucency', text)

    def test_legacy_boards_keep_complete_frozen_prompt_bytes(self):
        self.assertEqual(len(OLD_BOARD_HASHES), 2)
        for mode in (None, MODE):
            text = prompts.board_brief(board_fixture(mode=mode))
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), OLD_BOARD_HASHES[str(mode)])
            self.assertNotIn('foreground_box', text)
            self.assertNotIn('foreground_crop_pixels', text)

    def test_unconsumed_foreground_description_box_does_not_claim_a_generated_sample(self):
        board = board_fixture()
        board['members'][0]['foreground_box'] = [110, 205, 82, 72]
        before = prompts.board_brief(board_fixture())
        self.assertEqual(prompts.board_brief(board), before)

    def test_embedded_marker_keeps_rectangle_even_with_a_cutout_presentation(self):
        board = board_fixture(foreground_sample=True)
        board['members'][1]['presentation'] = 'cutout'
        row = assignments(prompts.board_brief(board))[1]
        self.assertEqual(row['presentation'], 'rectangle')

    def test_actual_prepared_reference_exposes_its_fg_sample_and_original_source_coordinates(self):
        with tempfile.TemporaryDirectory(prefix='prk-prompt-fg-sample-') as temporary:
            work = Path(temporary)
            source = work / 'source.png'
            foreground = work / 'foreground.png'
            Image.new('RGBA', (1920, 1080), (25, 45, 70, 255)).save(source)
            image = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((221, 311, 280, 360), fill=(210, 40, 80, 255))
            image.save(foreground)
            art = {'id': 'icon-a', 'kind': 'art', 'box': [100, 200, 60, 50],
                   'foreground_box': [220, 310, 62, 52], 'presentation': 'cutout',
                   'description': 'Complete independent icon'}
            board = prepare_boards(source, foreground, [art], work / 'fg', background_mode=MODE)[0]
            row = assignments(prompts.board_brief(board))[0]
            self.assertEqual(row['source_box_pixels'], art['box'])
            self.assertEqual(row['foreground_box'], art['foreground_box'])
            self.assertEqual(row['foreground_crop_pixels'], [220, 310, 282, 362])
            direct = prepare_source_boards(source, [art], work / 'source', background_mode=MODE)[0]
            direct_text = prompts.board_brief(direct)
            self.assertNotIn('foreground_box', direct_text)
            self.assertNotIn('foreground_crop_pixels', direct_text)

    def test_owned_image_wrapper_describes_the_actual_foreground_sampling_window(self):
        board = board_fixture(foreground_sample=True)
        board['board_reference_policy'] = prompts.BOARD_REFERENCE_POLICY
        text = prompts.board_brief(board)
        self.assertIn('An embedded-image member with foreground_crop_pixels', text)
        self.assertIn('retains its complete generated-foreground sampling window', text)
        self.assertNotIn('Each embedded-image reference contains only its own source-box pixels', text)
        self.assertEqual(assignments(text)[1]['foreground_crop_pixels'], [420, 260, 600, 390])


if __name__ == '__main__':
    unittest.main()
