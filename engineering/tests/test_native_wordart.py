"""Standalone wordart keeps native provider alpha and leaves other art intact."""
from __future__ import annotations
import copy
import importlib
import itertools
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"compat-runtime"))
try:
    wordart=importlib.import_module("newpath.wordart")
except ModuleNotFoundError as error:
    if error.name != "newpath.wordart":raise
    wordart=None


class StandaloneWordartTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(wordart,"Standalone native wordart module is missing")
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.member={"id":"o001","kind":"art","box":[62,30,1320,145],
            "target":[62,30,1320,145],"z":8,"order":0,
            "lettering":"探物知变，中秋风物时代探究","presentation":"cutout",
            "description":"深棕、朱红与深紫毛笔字，保留浅色描边"}
        self.raw=np.zeros((40,160,4),dtype=np.uint8)
        self.raw[...,:3]=[21,45,82]
        self.raw[10:20,10:30]=[255,255,255,255]
        self.raw[10:20,50:70]=[130,0,180,255]
        self.raw[12:18,90:110]=[255,0,255,255]
        self.raw[26,138]=[255,255,255,1]
        self.raw[27,137]=[170,30,170,7]

    def write(self,pixels,name="returned.png"):
        path=self.root/name;Image.fromarray(pixels).save(path);return path

    def test_select_largest_lettering_target_and_leave_other_art_order_intact(self):
        secondary={"id":"o003","kind":"art","box":[10,200,400,80],"lettering":"过去"}
        ordinary={"id":"o008","kind":"art","box":[0,0,1920,1080]}
        data=[secondary,ordinary,self.member];original=copy.deepcopy(data)
        selected,others=wordart.select_primary_wordart(data)
        self.assertEqual(selected,self.member)
        self.assertEqual(others,[secondary,ordinary])
        self.assertEqual(data,original)
        selected["target"][0]=999;others[0]["box"][0]=999
        self.assertEqual(data,original)

    def test_target_area_takes_precedence_over_source_box_area(self):
        first=copy.deepcopy(self.member);first["id"]="one";first["box"]=[0,0,900,800];first["target"]=[0,0,40,20]
        second=copy.deepcopy(self.member);second["id"]="two";second["box"]=[0,0,100,60];second["target"]=[0,0,100,60]
        selected,_=wordart.select_primary_wordart([first,second])
        self.assertEqual(selected["id"],"two")

    def test_selection_ties_use_source_y_x_then_identity_for_every_order(self):
        items=[]
        for identity,x,y in (("z",20,10),("b",10,10),("a",10,10),("early",0,30)):
            value=copy.deepcopy(self.member);value.update(id=identity,box=[x,y,40,20],target=[0,0,40,20]);items.append(value)
        for sequence in itertools.permutations(items):
            selected,others=wordart.select_primary_wordart(list(sequence))
            self.assertEqual(selected["id"],"a")
            self.assertEqual([a["id"] for a in others],[a["id"] for a in sequence if a["id"]!="a"])

    def test_no_lettering_returns_none_and_a_deep_copy(self):
        ordinary={"id":"o008","box":[0,0,200,100],"lettering":"   "}
        selected,others=wordart.select_primary_wordart([ordinary])
        self.assertIsNone(selected);self.assertEqual(others,[ordinary]);self.assertIsNot(others[0],ordinary)
        self.assertEqual(wordart.select_primary_wordart([]),(None,[]))

    def test_selection_rejects_invalid_lettering_geometry(self):
        for target in ([0,0,0,50],[0,0,float("nan"),40],[0,0,200]):
            member=copy.deepcopy(self.member);member["target"]=target
            with self.subTest(target=target),self.assertRaises(ValueError):wordart.select_primary_wordart([member])

    def test_png_alpha_can_have_any_canvas_ratio(self):
        for shape in ((40,160),(160,40),(80,80)):
            pixels=np.zeros(shape+(4,),dtype=np.uint8);pixels[10:20,10:20]=[255,255,255,255]
            with self.subTest(shape=shape):
                info=wordart.validate_wordart_output(self.write(pixels))
                self.assertEqual(info["size"],[shape[1],shape[0]])
                self.assertEqual(info["background_mode"],wordart.MODE)
                self.assertTrue(info["transparent_background"])

    def test_alpha_one_tip_and_white_purple_magenta_colors_remain_exact(self):
        result=wordart.extract_wordart_asset(self.member,self.write(self.raw),self.root/"assets")
        self.assertEqual(len(result["assets"]),1)
        asset=result["assets"][0];left,top,right,bottom=asset["crop_box"]
        with Image.open(asset["path"]) as image:actual=np.asarray(image)
        np.testing.assert_array_equal(actual,self.raw[top:bottom,left:right])
        self.assertEqual(int(np.count_nonzero(actual[...,3])),522)
        self.assertTrue(np.any(actual[...,3]==1))
        self.assertTrue(np.any((actual[...,3]==0)&(actual[...,0]==21)))

    def test_asset_preserves_member_identity_authored_target_z_and_source_metadata(self):
        original=copy.deepcopy(self.member)
        result=wordart.extract_wordart_asset(self.member,self.write(self.raw),self.root/"assets")
        asset=result["assets"][0]
        for key in ("id","box","z","order","lettering","description","presentation"):
            self.assertEqual(asset[key],self.member[key])
        self.assertIn("authored_target",asset)
        self.assertEqual(asset["authored_target"],self.member["target"])
        self.assertEqual(asset["stretch"][0],asset["stretch"][1])
        self.assertEqual(self.member,original)
        self.assertEqual(asset["extraction_policy"],wordart.MODE)
        self.assertEqual(asset["actual_slot"],[0,0,160,40])
        self.assertEqual(result["cell_mapping"]["unsafe_members"],[])
        self.assertEqual(asset["placement"]["fit"],"contain")

    def test_tight_crop_keeps_all_disconnected_strokes_not_only_largest_component(self):
        result=wordart.extract_wordart_asset(self.member,self.write(self.raw),self.root/"assets")
        self.assertEqual(result["assets"][0]["crop_box"],[10,10,139,28])
        self.assertEqual(result["assets"][0]["source_size"],[129,18])

    def test_each_canvas_edge_rejects_even_one_core_pixel(self):
        for x,y in ((0,15),(159,15),(90,0),(90,39)):
            pixels=self.raw.copy();pixels[y,x]=[255,255,255,2]
            with self.subTest(point=(x,y)),self.assertRaisesRegex(ValueError,"edge"):
                wordart.validate_wordart_output(self.write(pixels))

    def test_opaque_white_magenta_checkerboard_or_single_transparent_corner_rejected(self):
        samples=[]
        for color in ([255,255,255,255],[255,0,255,255]):
            raw=np.empty_like(self.raw);raw[:]=color;samples.append(raw)
        yy,xx=np.indices(self.raw.shape[:2]);checker=np.empty_like(self.raw)
        checker[...,:3]=np.where((((yy//5)+(xx//5))%2)[...,None],210,255);checker[...,3]=255;samples.append(checker)
        nearly=checker.copy();nearly[0,0,3]=0;samples.append(nearly)
        for n,raw in enumerate(samples):
            with self.subTest(index=n),self.assertRaises(ValueError):wordart.validate_wordart_output(self.write(raw))

    def test_all_transparent_rgb_and_non_png_rejected(self):
        with self.assertRaises(ValueError):wordart.validate_wordart_output(self.write(np.zeros_like(self.raw)))
        with self.assertRaises(ValueError):wordart.validate_wordart_output(self.write(self.raw[...,:3]))
        jpeg=self.root/"returned.jpg";Image.new("RGB",(100,100),"white").save(jpeg)
        with self.assertRaises(ValueError):wordart.validate_wordart_output(jpeg)

    def test_palette_png_transparency_is_real_alpha_and_is_supported(self):
        path=self.root/"palette.png";image=Image.new("P",(60,40),0)
        image.putpalette([21,45,82,255,255,255]+[0,0,0]*254)
        for x in range(10,30):
            for y in range(10,20):image.putpixel((x,y),1)
        image.save(path,transparency=0)
        self.assertEqual(wordart.validate_wordart_output(path)["visible_pixels"],200)

    def test_validation_does_not_claim_correct_text_or_inner_background_semantics(self):
        # A painted interior rectangle is technically transparent around its
        # outside. Correct text and inner checkerboard artifacts require the
        # separately recorded real visual review, rather than an alpha claim.
        pixels=np.zeros_like(self.raw);pixels[10:30,10:150]=[255,255,255,255]
        info=wordart.validate_wordart_output(self.write(pixels))
        self.assertEqual(info["quality"],"not_evaluated")


if __name__=="__main__":unittest.main()
