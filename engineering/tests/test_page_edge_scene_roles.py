"""Payload regressions only; scene ownership needs the real host smoke test."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'compat-runtime'))

import json
import tempfile
import unittest
from PIL import Image,ImageDraw

from newpath.artwork import prepare_boards
from newpath.layout import normalize_layout
from newpath.native import MODE
from newpath.prompts import layer_brief,board_brief
from newpath.wordart import select_primary_wordart


class PageEdgeScenePayloadContracts(unittest.TestCase):
    def test_explicit_content_images_keep_internal_writing_and_complete_rectangles(self):
        # Losing a marked content image, including the one placed at the edge,
        # or routing its internal writing to standalone art would break this case.
        fixtures=[('meeting-photo',[12,180,300,180],'MEETING'),
                  ('document-photo',[350,180,300,180],'REPORT'),
                  ('chat-collage',[12,420,300,180],'CHAT'),
                  ('dashboard',[350,420,300,180],'MAP')]
        items=[{'id':identity,'kind':'art','box':box,'description':identity,
                'embedded_image':True,'lettering':writing}
               for identity,box,writing in fixtures]
        layout=normalize_layout({'items':items})
        primary,remaining=select_primary_wordart(layout['items'])
        self.assertIsNone(primary)
        self.assertEqual(len(remaining),4)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/'source.png'
            Image.new('RGB',(1920,1080),(240,235,220)).save(source)
            for mode in (None,MODE):
                with self.subTest(mode=mode):
                    foreground=root/('native.png' if mode else 'matte.png')
                    image=Image.new('RGBA',(1920,1080),(0,0,0,0) if mode else (255,0,255,255))
                    draw=ImageDraw.Draw(image)
                    for identity,(x,y,width,height),writing in fixtures:
                        draw.rectangle((x,y,x+width-1,y+height-1),fill=(60,100,160,255))
                    image.save(foreground)
                    boards=prepare_boards(source,foreground,remaining,root/('n' if mode else 'm'),
                                          background_mode=mode)
                    assignments=[]
                    for board in boards:
                        payload,_=json.JSONDecoder().raw_decode(board_brief(board).split('Assignments: ',1)[1])
                        assignments.extend(payload)
                    actual={entry['id']:{'box':entry['source_box_pixels'],
                                         'embedded_image':entry['embedded_image'],
                                         'presentation':entry['presentation'],
                                         'writing':entry['image_internal_writing']}
                            for entry in assignments}
                    expected={identity:{'box':box,'embedded_image':True,
                                        'presentation':'rectangle','writing':writing}
                              for identity,box,writing in fixtures}
                    self.assertEqual(actual,expected)
                    self.assertTrue(all('required_artistic_writing' not in entry for entry in assignments))

    def test_padded_source_geometry_survives_both_layer_modes(self):
        # A policy edit must preserve real geometry and substrate selection.
        transform={'offset':[0,100],'resized':[1920,880]}
        expected={'working_pixels':[1920,1080],'content_offset':[0,100],'content_size':[1920,880]}
        for role in ('background','foreground'):
            for mode in (None,MODE):
                with self.subTest(role=role,mode=mode):
                    request=layer_brief(role,transform,background_mode=mode)
                    self.assertEqual(json.loads(request.split('Geometry: ',1)[1]),expected)
                    if role=='foreground' and mode==MODE:
                        self.assertIn('one transparent PNG',request)
                        self.assertIn('alpha 0',request)
                    else:
                        self.assertIn('one opaque PNG',request)


if __name__=='__main__':
    unittest.main()
