"""Generated-foreground observations sample pixels independently of source placement."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import artwork, layout
from newpath.native import MODE
from newpath.wordart import select_primary_wordart


def retained_module(name):
    path = PACKAGE.parent / 'source-r25/compat-runtime/newpath' / (name + '.py')
    if not path.is_file():
        raise unittest.SkipTest('Optional previous-runtime comparison source is unavailable')
    spec = importlib.util.spec_from_file_location('newpath.r25_foreground_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ForegroundReferenceCoordinates(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prk-foreground-coordinate-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.png'
        self.foreground = self.root / 'foreground.png'
        Image.new('RGB', (1920, 1080), (249, 244, 232)).save(self.source)

    def art(self, **extra):
        return {'id': 'subject', 'kind': 'art', 'box': [100, 120, 80, 60],
                'target': [320, 360, 160, 120], 'description': 'An independent painted subject.',
                'presentation': 'cutout', **extra}

    def save_foreground(self, image=None):
        if image is None:
            image = Image.new('RGBA', (1920, 1080))
            image.putpixel((100, 120), (20, 80, 220, 255))
        image.save(self.foreground)

    def prepare(self, arts, name='new', **options):
        return artwork.prepare_boards(self.source, self.foreground, arts, self.root / name,
                                      background_mode=MODE, **options)

    def assert_exact_sampling(self, board, member, bounds):
        with Image.open(self.foreground) as original:
            crop = original.convert('RGBA').crop(bounds)
        ref = crop.resize(tuple(member['rendered_size']), Image.Resampling.LANCZOS)
        expected = Image.new('RGBA', tuple(board['size']))
        x, y, width, height = member['slot']
        expected.paste(ref, (x + (width-ref.width)//2, y + (height-ref.height)//2))
        with Image.open(board['image']) as actual:
            self.assertEqual(actual.convert('RGBA').tobytes(), expected.tobytes())

    def test_observation_roundtrips_without_changing_source_or_ppt_coordinates(self):
        raw = {'items': [self.art(source_bounds=[20, 30, 60, 70],
                                  foreground_box=[800, 420, 90, 65])]}
        before = copy.deepcopy(raw)
        try:
            clean = layout.normalize_layout(raw)
        except ValueError as error:
            self.fail('The valid optional foreground_box was rejected: ' + str(error))
        item = clean['items'][0]
        self.assertEqual(item['box'], [20, 30, 40, 40])
        self.assertEqual(item['source_bounds'], [20, 30, 60, 70])
        self.assertEqual(item['target'], [320, 360, 160, 120])
        self.assertEqual(item['foreground_box'], [800, 420, 90, 65])
        self.assertEqual(layout.normalize_layout(clean), clean)
        self.assertEqual(raw, before)

    def test_invalid_observation_is_rejected_by_layout_and_direct_reference_entry(self):
        invalid = [None, True, '100,100,20,20', [1, 2, 3], [-1, 0, 20, 20],
                   [0, -1, 20, 20], [0, 0, 0, 20], [0, 0, 20, -1],
                   [1900, 0, 21, 20], [0, 1060, 20, 21],
                   [0, 0, float('nan'), 20], [0, 0, 20, float('inf')],
                   [True, 0, 20, 20]]
        self.save_foreground()
        for index, box in enumerate(invalid):
            with self.subTest(index=index, entry='layout'):
                with self.assertRaisesRegex(ValueError, 'foreground_box'):
                    layout.normalize_layout({'items': [self.art(foreground_box=box)]})
            with self.subTest(index=index, entry='reference'):
                with self.assertRaisesRegex(ValueError, 'foreground_box'):
                    self.prepare([self.art(foreground_box=box)], 'bad-' + str(index))

    def test_only_art_accepts_generated_foreground_coordinates(self):
        with self.assertRaises(ValueError):
            layout.normalize_layout({'items': [{'kind': 'text', 'text': 'Native',
                'box': [10, 20, 100, 30], 'foreground_box': [800, 420, 90, 65]}]})

    def test_shifted_subject_uses_observed_foreground_pixels_with_original_target(self):
        image = Image.new('RGBA', (1920, 1080))
        ImageDraw.Draw(image).rectangle((800, 420, 889, 484), fill=(20, 80, 220, 255))
        self.save_foreground(image)
        art = self.art(foreground_box=[800, 420, 90, 65])
        before = copy.deepcopy(art)
        board = self.prepare([art])[0]
        member = board['members'][0]
        self.assertEqual(member['box'], art['box'])
        self.assertEqual(member['target'], art['target'])
        self.assertIn('foreground_box', member)
        self.assertEqual(member['foreground_box'], [800, 420, 90, 65])
        self.assertEqual(member['foreground_crop_pixels'], [800, 420, 890, 485])
        self.assert_exact_sampling(board, member, [800, 420, 890, 485])
        self.assertEqual(art, before)

    def test_full_arc_and_disconnected_stroke_keep_the_supplied_window_without_neighbour_red_bar(self):
        image = Image.new('RGBA', (1920, 1080))
        draw = ImageDraw.Draw(image)
        draw.arc((500, 600, 575, 689), 45, 315, fill=(120, 20, 10, 255), width=5)
        draw.rectangle((580, 610, 660, 646), fill=(5, 5, 5, 255))
        image.putpixel((679, 689), (160, 20, 10, 2))
        draw.rectangle((685, 600, 715, 649), fill=(255, 0, 100, 255))
        self.save_foreground(image)
        art = self.art(box=[500, 600, 180, 90], foreground_box=[500, 600, 180, 90])
        board = self.prepare([art])[0]
        member = board['members'][0]
        self.assertIn('foreground_crop_pixels', member)
        self.assertEqual(member['foreground_crop_pixels'], [500, 600, 680, 690])
        self.assert_exact_sampling(board, member, [500, 600, 680, 690])

    def test_fractional_observation_keeps_every_intersecting_pixel_without_context_expansion(self):
        image = Image.new('RGBA', (1920, 1080))
        ImageDraw.Draw(image).rectangle((500, 600, 680, 690), fill=(20, 120, 50, 211))
        self.save_foreground(image)
        board = self.prepare([self.art(foreground_box=[500.25, 600.25, 180.5, 90.5])])[0]
        member = board['members'][0]
        self.assertIn('foreground_crop_pixels', member)
        self.assertEqual(member['foreground_crop_pixels'], [500, 600, 681, 691])
        self.assert_exact_sampling(board, member, [500, 600, 681, 691])

    def test_same_pixels_at_different_observed_locations_have_different_bound_signatures(self):
        image = Image.new('RGBA', (1920, 1080))
        draw = ImageDraw.Draw(image)
        draw.rectangle((700, 200, 749, 249), fill=(20, 80, 220, 255))
        draw.rectangle((900, 400, 949, 449), fill=(20, 80, 220, 255))
        self.save_foreground(image)
        a = self.prepare([self.art(foreground_box=[700, 200, 50, 50])], 'one')[0]
        b = self.prepare([self.art(foreground_box=[900, 400, 50, 50])], 'two')[0]
        self.assertNotEqual(a['signature'], b['signature'])
        with Image.open(a['image']) as aa, Image.open(b['image']) as bb:
            self.assertEqual(aa.tobytes(), bb.tobytes())

    def test_missing_field_keeps_r25_layout_boards_metadata_and_png_bytes(self):
        old_layout = retained_module('layout')
        old_artwork = retained_module('artwork')
        arts = [self.art()]
        payload = {'items': arts}
        self.assertEqual(json.dumps(layout.normalize_layout(payload), ensure_ascii=False),
                         json.dumps(old_layout.normalize_layout(payload), ensure_ascii=False))
        for mode in (MODE, None):
            image = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0) if mode else (255, 0, 255, 255))
            ImageDraw.Draw(image).ellipse((100, 120, 179, 179), fill=(25, 130, 220, 255))
            self.save_foreground(image)
            with self.subTest(mode=mode):
                a = artwork.prepare_boards(self.source, self.foreground, arts,
                    self.root / ('new-' + str(mode)), background_mode=mode)
                b = old_artwork.prepare_boards(self.source, self.foreground, arts,
                    self.root / ('old-' + str(mode)), background_mode=mode)
                for actual, expected in zip(a, b):
                    self.assertEqual(Path(actual['image']).read_bytes(), Path(expected['image']).read_bytes())
                    self.assertEqual({k:v for k,v in actual.items() if k!='image'},
                                     {k:v for k,v in expected.items() if k!='image'})

    def test_direct_source_reference_does_not_consume_a_generated_foreground_observation(self):
        plain = [self.art()]
        observed = [self.art(foreground_box=[800, 420, 90, 65])]
        a = artwork.prepare_source_boards(self.source, plain, self.root / 'source-plain')[0]
        b = artwork.prepare_source_boards(self.source, observed, self.root / 'source-observed')[0]
        self.assertEqual(Path(a['image']).read_bytes(), Path(b['image']).read_bytes())
        self.assertEqual({k:v for k,v in a.items() if k!='image'}, {k:v for k,v in b.items() if k!='image'})

    def test_retained_real_r25_layout_and_all_three_legacy_reference_boards_are_unchanged(self):
        job = PACKAGE.parent / 'r25-two-page-20261115/heritage-qa/actual-diagnostics/job'
        if not (job / 'state.json').is_file():
            self.skipTest('Retained private R25 heritage job is unavailable')
        before = {str(p.relative_to(job)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in job.rglob('*') if p.is_file()}
        state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        old_layout = retained_module('layout')
        old_artwork = retained_module('artwork')
        self.assertEqual(json.dumps(layout.normalize_layout(state['layout']), ensure_ascii=False),
                         json.dumps(old_layout.normalize_layout(state['layout']), ensure_ascii=False))
        arts = [item for item in state['layout']['items'] if item['kind'] == 'art']
        _, arts = select_primary_wordart(arts)
        actual = artwork.prepare_boards(job / 'input/working.png', job / 'layers/foreground.png',
            arts, self.root / 'real-new', background_mode=MODE)
        expected = old_artwork.prepare_boards(job / 'input/working.png', job / 'layers/foreground.png',
            arts, self.root / 'real-old', background_mode=MODE)
        self.assertEqual(len(actual), 3)
        for a, b in zip(actual, expected):
            self.assertEqual(Path(a['image']).read_bytes(), Path(b['image']).read_bytes())
            self.assertEqual({k:v for k,v in a.items() if k!='image'}, {k:v for k,v in b.items() if k!='image'})
        self.assertEqual(before, {str(p.relative_to(job)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in job.rglob('*') if p.is_file()})

    def test_observed_rectangle_does_not_apply_its_source_box_as_an_alpha_mask(self):
        image = Image.new('RGBA', (1920, 1080))
        ImageDraw.Draw(image).rectangle((800, 420, 889, 484), fill=(245, 245, 245, 137))
        self.save_foreground(image)
        board = self.prepare([self.art(embedded_image=True, presentation='rectangle',
            foreground_box=[800, 420, 90, 65])], reference_policy=artwork.BOARD_REFERENCE_POLICY)[0]
        self.assert_exact_sampling(board, board['members'][0], [800, 420, 890, 485])

    def test_observed_photo_with_another_member_never_uses_legacy_source_context(self):
        image = Image.new('RGBA', (1920, 1080))
        draw = ImageDraw.Draw(image)
        draw.rectangle((800, 420, 889, 484), fill=(245, 245, 245, 137))
        draw.rectangle((175, 125, 254, 184), fill=(20, 140, 60, 255))
        self.save_foreground(image)
        for observed_id, legacy_id in [('A_observed', 'Z_legacy'), ('Z_observed', 'A_legacy')]:
            with self.subTest(observed_id=observed_id):
                observed = self.art(id=observed_id, embedded_image=True, presentation='rectangle',
                                    foreground_box=[800, 420, 90, 65])
                legacy = self.art(id=legacy_id, embedded_image=True, presentation='rectangle',
                                  box=[175, 125, 80, 60])
                try:
                    boards = self.prepare([observed, legacy], observed_id,
                                          reference_policy=artwork.BOARD_REFERENCE_POLICY)
                except UnboundLocalError as error:
                    self.fail('Observed FG pixels reached uninitialized source-context coordinates: ' + str(error))
                board = next(board for board in boards
                             if any(member['id'] == observed_id for member in board['members']))
                member = next(member for member in board['members'] if member['id'] == observed_id)
                self.assertNotIn('context_exclusions', member)
                self.assertEqual(member['foreground_crop_pixels'], [800, 420, 890, 485])
                x, y, width, height = member['slot']
                with Image.open(self.foreground) as foreground:
                    crop = foreground.convert('RGBA').crop([800, 420, 890, 485])
                ref = crop.resize(tuple(member['rendered_size']), Image.Resampling.LANCZOS)
                expected = Image.new('RGBA', (width, height))
                expected.paste(ref, ((width-ref.width)//2, (height-ref.height)//2))
                with Image.open(board['image']) as actual:
                    cell = actual.convert('RGBA').crop([x, y, x+width, y+height])
                self.assertEqual(cell.tobytes(), expected.tobytes())


if __name__ == '__main__':
    unittest.main()
