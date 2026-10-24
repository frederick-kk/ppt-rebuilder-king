"""Standalone primary-title routing contracts. Synthetic local PNGs, no generation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

sys.dont_write_bytecode=True
PACKAGE=Path(os.environ.get("PRK_PACKAGE_UNDER_TEST",str(Path(__file__).resolve().parents[2])))
sys.path.insert(0,str(PACKAGE/"compat-runtime"))
from PIL import Image,ImageDraw
from newpath import controller as c
from newpath import prompts
from newpath.storage import fingerprint,read_state,save_state,sha256
MODE="standalone-native-wordart/2"
MATERIAL_MODE="native-transparent-materials/1"


class HeadlineControllerContracts(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="prk-title-queue-");self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.work=self.root/"job"
        self.source=self.png("source.png","white",(160,90),"RGB")
        self.background=self.png("background.png","white",(160,90),"RGB")
        self.foreground=self.png("foreground.png",(0,0,0,0),(160,90))
        with Image.open(self.foreground) as opened:image=opened.copy()
        ImageDraw.Draw(image).rectangle((20,20,80,60),fill="blue");image.save(self.foreground)
        with mock.patch.object(c,"_runtime"):first=c.run(self.work,source=self.source)
        for task in first["jobs"]:
            file=self.background if task["role"]=="background" else self.foreground
            c.run(self.work,data={**task["result_template"],"file":str(file)})
        self.primary={"id":"title","kind":"art","box":[120,20,1000,140],"target":[120,20,1000,140],
                      "description":"white brush title with purple ink and real fine white outlines","lettering":"TEST TITLE","z":12}

    def png(self,name,color,size=(120,60),mode="RGBA"):
        path=self.root/name;Image.new(mode,size,color).save(path);return path

    def native(self,name="native.png",size=(174,43)):
        path=self.png(name,(255,0,255,0),size)
        with Image.open(path) as opened:image=opened.copy()
        draw=ImageDraw.Draw(image);draw.rectangle((5,5,size[0]-6,size[1]-6),fill=(255,255,255,255))
        draw.point((10,10),fill=(150,20,180,90));image.save(path);return path

    def add_wordart(self,issued=True):
        state=read_state(self.work)
        task=c._task(self.work,"wordart",fingerprint([MODE,state["source"]["pixel_sha256"],self.primary]),
            "Generate TEST TITLE as true alpha PNG",[self.work/"input"/"working.png"],member=copy.deepcopy(self.primary),background_mode=MODE)
        task["issued"]=issued;state["tasks"][task["id"]]=task
        state.update(phase="boards",active_batch=[task["id"]] if issued else [])
        save_state(self.work,state,"synthetic_title_dispatched");return task

    def submit(self,task,file):
        state=read_state(self.work)
        c._submit(self.work,state,{"task_id":task["id"],"role":"wordart","attempt":task["attempt"],"file":str(file)})
        return read_state(self.work)["tasks"][task["id"]]

    def test_wordart_public_true_alpha_single_original_reference(self):
        task=self.add_wordart();public=c._public_task(self.work,task)
        self.assertTrue(public["transparent_background"])
        self.assertEqual(public["background_mode"],MODE)
        self.assertEqual(public["references"],[str(self.work/"input"/"working.png")])
        self.assertNotIn("key",public);self.assertEqual(public["requested_outputs"],1)
        self.assertEqual(public["member"]["id"],"title")
        for task in read_state(self.work)["tasks"].values():
            if task["role"] in {"background","foreground"}:
                self.assertEqual(c._public_task(self.work,task)["transparent_background"],task["role"]=="foreground")

    def test_wordart_prompt_exact_writing_and_no_opaque_board_contract(self):
        brief=getattr(prompts,"wordart_brief",None);self.assertTrue(callable(brief),"standalone title prompt is missing")
        text=brief(self.primary)
        self.assertIn("TEST TITLE",text);self.assertIn("alpha",text.lower())
        self.assertIn(self.primary["description"],text)
        self.assertNotIn("uniform opaque",text.lower());self.assertNotIn("RGB(255,0,255)",text)
        self.assertNotIn("component board",text.lower())

    def test_arbitrary_canvas_ratios_registered_without_normalizing(self):
        task=self.add_wordart();file=self.native()
        with mock.patch.object(c,"normalize_layer",side_effect=AssertionError("wordart must not normalize layer")), \
             mock.patch.object(c,"validate_output",side_effect=AssertionError("wordart must not use chroma validator")):
            recorded=self.submit(task,file)
        self.assertEqual(recorded["status"],"recorded")
        self.assertNotIn("normalized",recorded["result"])
        self.assertEqual(recorded["result"]["validation"]["background_mode"],MODE)
        self.assertEqual((self.work/recorded["result"]["raw"]).read_bytes(),file.read_bytes())
        self.assertEqual(self.submit(task,file),recorded)
        changed=self.native("changed.png",(71,147))
        with self.assertRaisesRegex(ValueError,"immutable"):self.submit(task,changed)

    def test_opaque_fake_background_preserved_paused_no_automatic_retry(self):
        task=self.add_wordart();bad=self.png("opaque.png",(255,255,255,255))
        with self.assertRaisesRegex(ValueError,"no automatic generation"):self.submit(task,bad)
        state=read_state(self.work);failed=state["tasks"][task["id"]]
        self.assertEqual(failed["status"],"failed")
        self.assertEqual((self.work/failed["attempts"][-1]["raw"]).read_bytes(),bad.read_bytes())
        before=copy.deepcopy(state);paused=c.run(self.work)
        self.assertEqual(paused["kind"],"paused");self.assertFalse(paused["generate_more"])
        self.assertEqual(read_state(self.work),before)
        retry=c.run(self.work,retry_task=task["id"],retry_authorization="Synthetic user: retry failed title once.")
        self.assertEqual(len(retry["jobs"]),1);self.assertTrue(retry["jobs"][0]["transparent_background"])
        self.assertEqual(retry["jobs"][0]["attempt"],1)

    def test_unissued_wordart_cannot_register_or_retry(self):
        task=self.add_wordart(issued=False)
        with self.assertRaisesRegex(ValueError,"not been dispatched"):self.submit(task,self.native())
        with self.assertRaisesRegex(ValueError,"active batch"):c.run(self.work,retry_task=task["id"])

    def test_wordart_priority_shared_three_active_limit_and_resume(self):
        task=self.add_wordart(issued=False);state=read_state(self.work)
        # Insert ordinary boards before wordart to ensure explicit role priority.
        title=state["tasks"].pop(task["id"])
        for i in range(3):
            board={"id":str(i),"signature":str(i),"image":str(self.work/"layers"/"foreground.png"),
                "size":[1024,1024],"background_mode":MATERIAL_MODE,"key":None,"members":[]}
            other=c._task(self.work,"board",str(i),prompts.board_brief(board),[board["image"]],board=board,background_mode=MATERIAL_MODE)
            state["tasks"][other["id"]]=other
        state["tasks"][title["id"]]=title;save_state(self.work,state,"synthetic_priority_queue")
        result=c.run(self.work)
        self.assertEqual(result["kind"],"generate");self.assertEqual(len(result["jobs"]),3)
        self.assertEqual(result["jobs"][0]["role"],"wordart")
        state=read_state(self.work);self.assertEqual(sum(t["issued"] and t["status"]!="recorded" for t in state["tasks"].values()),3)
        before=copy.deepcopy(state);waiting=c.run(self.work)
        self.assertEqual(waiting["kind"],"await_results");self.assertFalse(waiting["generate_more"])
        self.assertEqual(read_state(self.work),before)

    def test_layout_removes_only_primary_from_ordinary_board_inputs(self):
        secondary={"id":"label","kind":"art","box":[50,300,140,60],"description":"brown label","lettering":"LABEL"}
        decoration={"id":"icon","kind":"art","box":[200,300,80,80],"description":"small blue icon"}
        items=[secondary,self.primary,decoration]
        real=c.prepare_boards
        with mock.patch.object(c,"prepare_boards",wraps=real) as planner:
            result=c.run(self.work,data={"kind":"layout","layout":{"items":items}})
        planned=planner.call_args.args[2]
        self.assertEqual([i["id"] for i in planned],["label","icon"])
        state=read_state(self.work);titles=[t for t in state["tasks"].values() if t["role"]=="wordart"]
        self.assertEqual(len(titles),1);self.assertEqual(titles[0]["member"]["id"],"title")
        self.assertEqual(result["jobs"][0]["role"],"wordart")
        self.assertEqual([i["id"] for i in state["layout"]["items"]],["label","title","icon"])
        ordinary=[t for t in state["tasks"].values() if t["role"]=="board"]
        self.assertEqual(sorted(m["id"] for t in ordinary for m in t["board"]["members"]),["icon","label"])
        self.assertTrue(all(t["board"].get("background_mode")==MATERIAL_MODE for t in ordinary))
        self.assertTrue(all(c._public_task(self.work,t)["transparent_background"] for t in ordinary))
        self.assertEqual(titles[0]["references"],{"input/working.png":sha256(self.work/"input"/"working.png")})

    def test_no_lettering_keeps_only_ordinary_board_tasks_and_prompt(self):
        item={"id":"icon","kind":"art","box":[200,300,80,80],"description":"small blue icon"}
        result=c.run(self.work,data={"kind":"layout","layout":{"items":[item]}})
        self.assertEqual([t["role"] for t in result["jobs"]],["board"])
        self.assertTrue(result["jobs"][0]["transparent_background"])
        self.assertEqual(len(read_state(self.work)["tasks"]),3)
        board=next(t for t in read_state(self.work)["tasks"].values() if t["role"]=="board")
        self.assertEqual((self.work/board["brief"]).read_text(encoding="utf-8"),prompts.board_brief(board["board"]))

    def test_finish_native_title_extraction_keeps_id_target_z(self):
        task=self.add_wordart();output=self.native();self.submit(task,output)
        state=read_state(self.work);state["layout"]={"format":"newpath.scene/1","items":[self.primary]}
        (self.work/"layout.json").write_text(json.dumps(state["layout"]),encoding="utf-8");save_state(self.work,state,"synthetic_frozen_layout")
        def minimal_build(layout,background,assets,path):
            with ZipFile(path,"w") as z:z.writestr("ppt/presentation.xml","<presentation/>");z.writestr("ppt/slides/slide1.xml","<slide/>")
            return {"warnings":[],"skipped":[],"synthetic_test_backend":True}
        with mock.patch.object(c,"extract_assets",side_effect=AssertionError("title must not use matte extraction")), \
             mock.patch.object(c,"build_presentation",side_effect=minimal_build):done=c.run(self.work)
        self.assertEqual(done["kind"],"done")
        final=read_state(self.work);self.assertEqual(len(final["assets"]),1)
        asset=final["assets"][0];self.assertEqual(asset["id"],"title");self.assertEqual(asset.get("authored_target",asset["target"]),self.primary["target"]);self.assertEqual(asset["z"],12)
        self.assertTrue(final["tasks"][task["id"]]["processed"])
        self.assertEqual(c.run(self.work),done)

    def _real_layout_route(self,case):
        fixture=json.loads((Path(__file__).parent/"fixtures"/"headline-plans.json").read_text(encoding="utf-8"))["cases"][case]
        result=c.run(self.work,data={"kind":"layout","layout":{"items":fixture["items"]}})
        self.assertEqual(result["kind"],"generate");self.assertEqual(len(result["jobs"]),3)
        self.assertEqual([t["role"] for t in result["jobs"]],["wordart","board","board"])
        self.assertEqual([t["transparent_background"] for t in result["jobs"]],[True,True,True])
        state=read_state(self.work);self.assertEqual(len(state["tasks"]),fixture["generation_tasks"]+1)
        title=[t for t in state["tasks"].values() if t["role"]=="wordart"]
        self.assertEqual(len(title),1);self.assertEqual(title[0]["member"]["id"],fixture["primary"])
        boards=[t["board"] for t in state["tasks"].values() if t["role"]=="board"]
        self.assertEqual(len(boards),fixture["ordinary_board_count"]+1)
        # The historical fixture records the former three-board plan. The
        # candidate may add one beneficial split; boardplan regression tests
        # verify its sampling gains, while this contract checks exact routing.
        self.assertNotEqual([[m["id"] for m in b["members"]] for b in boards],fixture["members"])
        self.assertEqual(sorted([title[0]["member"]["id"]]+[m["id"] for b in boards for m in b["members"]]),sorted(i["id"] for i in fixture["items"]))
        original={i["id"]:i.get("target",i["box"]) for i in fixture["items"]}
        actual={m["id"]:m["target"] for b in boards for m in b["members"]}
        actual[title[0]["member"]["id"]]=title[0]["member"]["target"]
        self.assertEqual(actual,original)
        self.assertEqual(sum(t["issued"] for t in state["tasks"].values() if t["role"] in {"wordart","board"}),3)

    def test_real_election_routes_only_main_title_seven_calls(self):
        self._real_layout_route("round-a")

    def test_real_midautumn_routes_only_main_title_seven_calls(self):
        self._real_layout_route("round-b")

    def test_wordart_identity_changes_for_exact_text_description_target_and_source(self):
        c.run(self.work,data={"kind":"layout","layout":{"items":[self.primary]}})
        state=read_state(self.work);original=next(t for t in state["tasks"].values() if t["role"]=="wordart")
        for field,value in [("lettering","CHANGED TITLE"),("description","changed purple ink"),("target",[120,20,1001,140])]:
            changed=copy.deepcopy(original["member"]);changed[field]=value
            identity={"policy":MODE,"source_pixel_sha256":state["source"]["pixel_sha256"],"member":changed}
            alternate=c._task(self.work,"wordart",identity,prompts.wordart_brief(changed),[self.work/"input"/"working.png"],member=changed,background_mode=MODE)
            self.assertNotEqual(original["id"],alternate["id"],field)
        identity={"policy":MODE,"source_pixel_sha256":"different-source","member":original["member"]}
        alternate=c._task(self.work,"wordart",identity,prompts.wordart_brief(original["member"]),[self.work/"input"/"working.png"],member=original["member"],background_mode=MODE)
        self.assertNotEqual(original["id"],alternate["id"])

    def test_frozen_old_board_job_is_not_migrated(self):
        board={"id":"old","signature":"old","image":str(self.work/"layers"/"foreground.png"),"size":[1024,1024],"key":"magenta",
            "members":[{"id":"old-title","description":"old title","lettering":"OLD","slot":[24,24,976,976],"box":[0,0,100,40]}]}
        state=read_state(self.work)
        # This scheduler-only saved-state fixture deliberately represents a
        # legacy keyed board job. Do not let setUp's new-job material mode
        # reclassify it or its old board as native material evidence.
        state.pop("material_background_mode",None)
        for previous in state["tasks"].values():
            if previous["role"]=="foreground":previous.pop("background_mode",None)
        task=c._task(self.work,"board","old",prompts.board_brief(board),[board["image"]],board=board)
        task["issued"]=True;state["tasks"][task["id"]]=task;state.pop("generation_policy",None);state.update(phase="boards",active_batch=[task["id"]]);save_state(self.work,state,"old_frozen_job")
        before=copy.deepcopy(read_state(self.work));waiting=c.run(self.work)
        self.assertEqual(waiting["kind"],"await_results");self.assertEqual(waiting["pending_results"][0]["role"],"board")
        self.assertFalse(waiting["pending_results"][0]["transparent_background"]);self.assertEqual(read_state(self.work),before)


if __name__=="__main__":unittest.main(verbosity=2)
