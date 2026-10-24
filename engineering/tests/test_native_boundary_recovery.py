"""Native board geometry recovery; original RGBA is never thresholded away."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE/'compat-runtime'))
from newpath.artwork import extract_assets
from newpath.native import MODE


class NativeBoundaryRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-boundary-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self):
        board = {'id':'native-grid', 'size':[220,220], 'key':None, 'background_mode':MODE,
                 'grid':{'rows':2,'columns':2,'margin':10,'gap':20,'cell_size':[90,90]},
                 'members':[]}
        reference = np.zeros((220,220,4),dtype=np.uint8)
        returned = np.zeros_like(reference)
        regions = [(30,20,70,98),(140,20,180,135),(30,126,70,200),(140,160,180,205)]
        refs = [(30,20,70,98),(140,20,180,90),(30,126,70,200),(140,170,180,205)]
        for i, ((left,top,right,bottom),region) in enumerate(zip(regions,refs)):
            row,column = divmod(i,2)
            board['members'].append({'id':str(i),'slot':[10+column*110,10+row*110,90,90],
                                     'box':[100+column*200,100+row*200,80,90]})
            returned[top:bottom,left:right] = [20+i*30,60,100,230]
            l,t,r,b = region; reference[t:b,l:r] = [20+i*30,60,100,230]
        # Broad faint provider overspray is preserved but must not determine
        # structural boundaries or the subject's visible placement box.
        returned[returned[...,3]==0] = [11,22,33,1]
        returned[0,0,3] = 0
        reference_path = self.root/'reference.png'; Image.fromarray(reference).save(reference_path)
        board['image'] = str(reference_path)
        return board,returned

    def extract(self, board, pixels, name='case'):
        raw = self.root/(name+'.png'); Image.fromarray(pixels).save(raw)
        before = raw.read_bytes()
        result = extract_assets(board,raw,self.root/(name+'-assets'))
        self.assertEqual(raw.read_bytes(),before)
        return result

    def assert_preserved_and_placed(self, result, pixels, members):
        self.assertEqual(result['cell_mapping']['unsafe_members'],[])
        self.assertEqual({a['id'] for a in result['assets']},{m['id'] for m in members})
        by_id={m['id']:m for m in members}
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];l,t,r,b=asset['crop_box']
            expected=pixels[y:y+h,x:x+w][t:b,l:r]
            with Image.open(asset['path']) as image:
                np.testing.assert_array_equal(np.asarray(image.convert('RGBA')),expected)
            self.assertEqual(int(np.count_nonzero(expected[...,3])),
                             int(np.count_nonzero(pixels[y:y+h,x:x+w,3])))
            authored=by_id[asset['id']].get('target',by_id[asset['id']]['box'])
            self.assertEqual(asset['authored_target'],authored)
            np.testing.assert_allclose(asset['placement']['visible_core_target'],authored)
            core=asset['placement']['core_bbox'];sx,sy=asset['stretch'];tx,ty,_,_=asset['target']
            placed=[tx+(core[0]-l)*sx,ty+(core[1]-t)*sy,
                    (core[2]-core[0])*sx,(core[3]-core[1])*sy]
            np.testing.assert_allclose(placed,authored,atol=1e-8)
            self.assertTrue(asset['placement']['all_rgba_pixels_preserved'])

    def test_column_local_rows_recover_overlap_and_keep_alpha_one_fog(self):
        board,pixels=self.fixture()
        result=self.extract(board,pixels)
        self.assert_preserved_and_placed(result,pixels,board['members'])
        cells=result['cell_mapping']['cells']
        self.assertNotEqual(cells['0'][3],cells['1'][3])

    def test_real_core_bridge_remains_unsafe(self):
        board,pixels=self.fixture()
        pixels[55,60:150]=[255,255,255,2]
        result=self.extract(board,pixels)
        self.assertTrue(result['cell_mapping']['unsafe_members'])
        self.assertIn('unseparable_grid_boundary',{w['code'] for w in result['warnings']})

    def test_real_core_canvas_clipping_remains_unsafe(self):
        board,pixels=self.fixture()
        pixels[40,0:35]=[255,255,255,2]
        result=self.extract(board,pixels)
        self.assertIn('0',result['cell_mapping']['unsafe_members'])
        self.assertIn('subject_clipped_at_canvas_edge',{w['code'] for w in result['warnings']})

    def test_unknown_fragment_between_same_column_members_remains_unsafe(self):
        board,pixels=self.fixture()
        pixels[108:111,90:95]=[255,0,0,230]
        result=self.extract(board,pixels)
        self.assertTrue({'0','2'} <= set(result['cell_mapping']['unsafe_members']))
        self.assertIn('ambiguous_grid_fragments',{w['code'] for w in result['warnings']})

    def test_absent_reference_keeps_strict_alpha_one_guard(self):
        board,pixels=self.fixture();board.pop('image')
        result=self.extract(board,pixels)
        self.assertEqual(result['assets'],[])
        self.assertEqual(len(result['cell_mapping']['unsafe_members']),4)

    def test_alpha_one_only_board_falls_back_without_deleting_pixels(self):
        board,pixels=self.fixture()
        pixels[...,3]=np.where(pixels[...,3]>1,1,0)
        result=self.extract(board,pixels)
        self.assertNotEqual(result['cell_mapping'].get('geometry_policy'),'native-core-local-rows/1')
        for asset in result['assets']:
            x,y,w,h=asset['actual_slot'];l,t,r,b=asset['crop_box']
            with Image.open(asset['path']) as image:
                np.testing.assert_array_equal(np.asarray(image.convert('RGBA')),pixels[y:y+h,x:x+w][t:b,l:r])

    def test_one_alpha_one_only_member_keeps_strict_fallback_for_whole_board(self):
        board,pixels=self.fixture()
        pixels[120:,120:,3]=np.minimum(pixels[120:,120:,3],1)
        result=self.extract(board,pixels)
        self.assertNotEqual(result['cell_mapping'].get('geometry_policy'),'native-core-local-rows/1')
        self.assertIn('3',result['cell_mapping']['unsafe_members'])

    def test_invalid_enlarged_picture_frame_stops_before_asset_write(self):
        board,pixels=self.fixture()
        # Same box scale as ordinary material, but huge retained transparent
        # frame must not become an unbounded PowerPoint object.
        board['members'][0]['box']=[0,0,10000,10000]
        with self.assertRaisesRegex(ValueError,'placement|budget|four'):
            self.extract(board,pixels)
        self.assertFalse(any((self.root/'case-assets').glob('*.png')))

    @unittest.skipUnless(os.environ.get('PRK_NATIVE_MISSING_DIAGNOSTIC'),'actual missing-components diagnostic not supplied')
    def test_actual_three_boards_recover_all_eleven_with_exact_rgba(self):
        with zipfile.ZipFile(os.environ['PRK_NATIVE_MISSING_DIAGNOSTIC']) as archive:
            state_name='job/ppt_rebuild_current/state.json'
            state=json.loads(archive.read(state_name));prefix=state_name.rsplit('/',1)[0]+'/'
            boards=[task for task in state['tasks'].values() if task['role']=='board']
            recovered=[]
            for index,task in enumerate(boards):
                with self.subTest(task=task['id']):
                    board=copy.deepcopy(task['board'])
                    ref=self.root/('actual-reference-'+str(index)+'.png')
                    ref.write_bytes(archive.read(prefix+'boards/'+Path(board['image']).name))
                    board['image']=str(ref)
                    path=self.root/('actual-return-'+str(index)+'.png');path.write_bytes(archive.read(prefix+task['result']['raw']))
                    with Image.open(path) as image:pixels=np.asarray(image.convert('RGBA')).copy()
                    result=extract_assets(board,path,self.root/('actual-assets-'+str(index)))
                    self.assert_preserved_and_placed(result,pixels,board['members'])
                    recovered.extend(a['id'] for a in result['assets'])
            self.assertEqual(len(recovered),11)
            self.assertEqual(len(set(recovered)),11)


if __name__=='__main__':unittest.main()
