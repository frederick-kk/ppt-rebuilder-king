"""Embedded-image roles through real layout, board and request construction.

Fixtures are program-made PNGs. No image service or browser is used.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath.artwork import prepare_boards
from newpath.layout import normalize_layout
from newpath.native import MODE
from newpath.prompts import board_brief, layer_brief
from newpath.wordart import select_primary_wordart


class EmbeddedPhotoContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-embedded-photo-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.photo = {
            'id': 'conference-photo', 'kind': 'art', 'box': [100, 180, 600, 360],
            'description': 'Borderless rectangular conference photo with people and a banner',
            'embedded_image': True, 'lettering': 'MEETING BANNER',
        }
        self.source = self.root / 'source.png'
        Image.new('RGB', (1920, 1080), (25, 55, 80)).save(self.source)

    def foreground(self, native):
        path = self.root / ('native.png' if native else 'legacy.png')
        image = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0) if native else (255, 0, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 180, 699, 539), fill=(60, 90, 140, 255))
        draw.rectangle((180, 220, 599, 260), fill=(240, 240, 230, 255))
        image.save(path)
        return path

    def accepted_layout(self, item):
        try:
            return normalize_layout({'items': [item]})['items'][0]
        except ValueError as error:
            self.fail(f'Valid embedded-image layout was rejected: {error}')

    def test_layout_keeps_photo_marker_and_defaults_to_complete_rectangle(self):
        photo = self.accepted_layout(self.photo)
        self.assertIs(photo['embedded_image'], True)
        self.assertEqual(photo['presentation'], 'rectangle')
        self.assertEqual(photo['box'], [100, 180, 600, 360])
        self.assertEqual(normalize_layout({'items': [photo]})['items'][0], photo)

    def test_embedded_photo_remains_rectangular_when_input_says_cutout(self):
        photo = self.accepted_layout({**self.photo, 'presentation': 'cutout'})
        self.assertEqual(photo['presentation'], 'rectangle')

    def test_explicit_false_marker_keeps_existing_cutout_role(self):
        art = self.accepted_layout({**self.photo, 'embedded_image': False})
        self.assertIs(art['embedded_image'], False)
        self.assertEqual(art['presentation'], 'cutout')

    def test_nonboolean_photo_marker_is_rejected(self):
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'embedded_image must be a boolean'):
                normalize_layout({'items': [{**self.photo, 'embedded_image': value}]})

    def test_marker_survives_normalization_board_and_assignment_in_both_modes(self):
        photo = self.accepted_layout(self.photo)
        for native in (False, True):
            with self.subTest(native=native):
                boards = prepare_boards(self.source, self.foreground(native), [photo],
                    self.root / str(native), background_mode=MODE if native else None)
                self.assertEqual(len(boards), 1)
                member = boards[0]['members'][0]
                self.assertIs(member.get('embedded_image'), True)
                self.assertEqual(member['presentation'], 'rectangle')
                self.assertEqual(member['target'], [100, 180, 600, 360])
                brief = board_brief(boards[0])
                assignments, _ = json.JSONDecoder().raw_decode(brief.split('Assignments: ', 1)[1])
                self.assertEqual(len(assignments), 1)
                assignment = assignments[0]
                self.assertIs(assignment.get('embedded_image'), True)
                self.assertEqual(assignment.get('presentation'), 'rectangle')
                self.assertEqual(assignment.get('image_internal_writing'), 'MEETING BANNER')
                self.assertNotIn('required_artistic_writing', assignment)

    def test_layer_requests_use_the_same_complete_photo_role_in_both_modes(self):
        transform = {'offset': [0, 0], 'resized': [1920, 1080]}
        for role in ('background', 'foreground'):
            for mode in (None, MODE):
                with self.subTest(role=role, mode=mode):
                    brief = layer_brief(role, transform, background_mode=mode)
                    self.assertIn('independent foreground image assets', brief)
                    self.assertIn('complete rectangular', brief)
                    self.assertIn('borderless', brief)
                    self.assertIn('Image-internal text', brief)
                    self.assertIn('fog', brief)

    def test_photo_role_changes_board_identity_without_changing_reference_pixels(self):
        foreground = self.foreground(True)
        photo = {**self.photo, 'presentation': 'rectangle'}
        ordinary = {**photo, 'embedded_image': False}
        photo_board = prepare_boards(self.source, foreground, [photo], self.root / 'photo', background_mode=MODE)[0]
        ordinary_board = prepare_boards(self.source, foreground, [ordinary], self.root / 'ordinary', background_mode=MODE)[0]
        self.assertNotEqual(photo_board['signature'], ordinary_board['signature'])
        self.assertNotEqual(photo_board['id'], ordinary_board['id'])
        self.assertIs(ordinary_board['members'][0]['embedded_image'], False)
        with Image.open(photo_board['image']) as first, Image.open(ordinary_board['image']) as second:
            self.assertEqual(first.tobytes(), second.tobytes())

    def test_photo_internal_writing_cannot_replace_the_page_title(self):
        title = {'id': 'page-title', 'kind': 'art', 'box': [100, 20, 500, 100],
                 'description': 'Independent painted page title', 'lettering': 'PAGE TITLE'}
        arts = [copy.deepcopy(self.photo), title]
        primary, remaining = select_primary_wordart(arts)
        self.assertEqual(primary['id'], 'page-title')
        self.assertEqual([member['id'] for member in remaining], ['conference-photo'])
        self.assertEqual(arts[0], self.photo)

    def test_photo_internal_writing_alone_keeps_the_existing_board_route(self):
        primary, remaining = select_primary_wordart([self.photo])
        self.assertIsNone(primary)
        self.assertEqual([member['id'] for member in remaining], ['conference-photo'])


if __name__ == '__main__':
    unittest.main()
