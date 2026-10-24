"""Substrate-only prompt contracts, no image service."""
import importlib.util
import inspect
from pathlib import Path
import sys
import unittest

PACKAGE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath.prompts import layer_brief,board_brief,wordart_brief
MODE='native-transparent-materials/1'
TRANSFORM={'offset':[0,0],'resized':[1920,1080]}

def board(key='magenta',native=False):
    result={'size':[600,400],'key':key,'members':[{'id':'label','slot':[10,10,200,100],
        'box':[200,200,200,100],'description':'painted label','lettering':'TITLE','inset_px':5}],
        'grid':{'rows':1,'columns':1,'cell_size':[200,100],'margin':10,'gap':20}}
    if native:result.update(background_mode=MODE,key=None)
    return result

class NativePromptContracts(unittest.TestCase):
    def test_foreground_explicitly_requests_real_alpha_without_opaque_substrate(self):
        self.assertIn('background_mode',inspect.signature(layer_brief).parameters,
                      'native foreground needs an explicit mode')
        text=layer_brief('foreground',TRANSFORM,background_mode=MODE)
        self.assertIn('transparent PNG',text)
        self.assertIn('alpha',text)
        self.assertNotIn('must be solid magenta',text)
        self.assertNotIn('one opaque PNG',text)
        self.assertNotIn('use no transparent field',text)
        self.assertIn('original positions and sizes',text)
        self.assertIn('writing and details',text)

    def test_board_keeps_assignment_geometry_with_alpha_clearances(self):
        try:text=board_brief(board(native=True))
        except KeyError:self.fail('native board must not require a colour key')
        self.assertIn('alpha',text)
        self.assertIn('transparent',text)
        self.assertNotIn('uniform opaque',text)
        self.assertNotIn('selected background colour',text)
        self.assertIn('do not swap, move, shrink or repack slots',text)
        self.assertIn('slot_pixels',text)
        self.assertIn('TITLE',text)
        self.assertIn('white',text.lower())

    def test_background_title_and_explicit_legacy_briefs_remain_identical(self):
        root=Path(__file__).resolve().parent/'fixtures/grid25-baseline/newpath/prompts.py'
        spec=importlib.util.spec_from_file_location('_base_native_prompt_contract',root)
        baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
        self.assertEqual(layer_brief('background',TRANSFORM),baseline.layer_brief('background',TRANSFORM))
        self.assertEqual(layer_brief('foreground',TRANSFORM),baseline.layer_brief('foreground',TRANSFORM))
        for key in ('magenta','green','cyan','yellow'):
            self.assertEqual(board_brief(board(key)),baseline.board_brief(board(key)))
        title={'id':'title','box':[10,10,1200,100],'lettering':'TITLE','description':'brush lettering'}
        self.assertEqual(wordart_brief(title),baseline.wordart_brief(title))

if __name__=='__main__':unittest.main()
