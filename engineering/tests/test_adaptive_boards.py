"""A fourth board must buy documented sampling benefit, not merely a count."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode=True
PACKAGE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PACKAGE/'compat-runtime'))
from newpath.boardplan import plan_boards


def placements(plan):
    return {p['art']['id']:p for board in plan for p in board['placements']}


class AdaptiveBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((Path(__file__).parent/'fixtures/adaptive-board-current.json').read_text(encoding='utf-8'))

    def test_real_dense_written_group_can_use_one_beneficial_fourth_board(self):
        actual=plan_boards(self.fixture['arts'])
        self.assertEqual(len(self.fixture['baseline_plan']),3)
        self.assertEqual(len(actual),4,'A dense real lettering group should earn a fourth board')
        audits=[b['pixel_budget'].get('adaptive_split') for b in actual if b['pixel_budget'].get('adaptive_split')]
        self.assertEqual(len(audits),2)
        self.assertEqual(audits[0],audits[1])
        self.assertEqual(audits[0]['extra_board_count'],1)
        self.assertGreaterEqual(len(audits[0]['improved_lettering_ids']),2)

    def test_sampling_never_reduced_and_all_art_identity_geometry_preserved(self):
        original=copy.deepcopy(self.fixture['arts']);actual=plan_boards(original)
        before=placements(self.fixture['baseline_plan']);after=placements(actual)
        self.assertEqual(set(before),set(after))
        self.assertEqual(len(after),sum(len(b['placements']) for b in actual))
        self.assertEqual(original,self.fixture['arts'])
        for identity,item in after.items():
            self.assertEqual(item['art'],before[identity]['art'])
            self.assertEqual(item['crop'],before[identity]['crop'])
            for old,new in zip(before[identity]['pixel_budget']['planned_content_px'],item['pixel_budget']['planned_content_px']):
                self.assertGreaterEqual(new,old)

    def test_extra_board_metadata_matches_actual_integer_samples(self):
        actual=plan_boards(self.fixture['arts']);after=placements(actual)
        audits=[b['pixel_budget'].get('adaptive_split') for b in actual if b['pixel_budget'].get('adaptive_split')]
        self.assertTrue(audits,'Chosen extra board must explain its value')
        audit=audits[0]
        before=placements(self.fixture['baseline_plan'])
        for row in audit['sampling']:
            identity=row['id']
            self.assertEqual(row['before_content_px'],before[identity]['pixel_budget']['planned_content_px'])
            self.assertEqual(row['after_content_px'],after[identity]['pixel_budget']['planned_content_px'])
            expected=min(n/o for o,n in zip(row['before_content_px'],row['after_content_px']))
            self.assertAlmostEqual(row['linear_gain'],expected)
        for identity in audit['improved_lettering_ids']:
            row=next(r for r in audit['sampling'] if r['id']==identity)
            self.assertGreaterEqual(row['linear_gain'],1.25)

    def test_wide_hardware_limited_paper_does_not_receive_a_pointless_board(self):
        actual=plan_boards(self.fixture['arts'])
        before=placements(self.fixture['baseline_plan'])['o026'];after=placements(actual)['o026']
        self.assertTrue(after['pixel_budget']['hardware_limited'])
        self.assertEqual(after['pixel_budget']['planned_content_px'],[1190,224])
        self.assertEqual(after,before)
        board=next(b for b in actual if any(p['art']['id']=='o026' for p in b['placements']))
        self.assertIsNone(board['pixel_budget'].get('adaptive_split'))

    def test_simple_page_is_not_forced_to_four_boards(self):
        arts=[{'id':str(n),'box':[10+100*n,20,80,50],'lettering':'LABEL'} for n in range(2)]
        actual=plan_boards(arts)
        self.assertEqual(len(actual),1)
        self.assertTrue(all('adaptive_split' not in b['pixel_budget'] for b in actual))

    def test_width_limited_written_group_does_not_split_without_gain(self):
        arts=[{'id':str(n),'box':[20,20+n*100,1800,60],'lettering':'LONG LABEL'} for n in range(3)]
        actual=plan_boards(arts)
        self.assertEqual(len(actual),1)
        self.assertTrue(all('adaptive_split' not in b['pixel_budget'] for b in actual))

    def test_non_written_dense_group_keeps_existing_policy(self):
        arts=[{'id':str(n),'box':[20+n*100,20,70,70]} for n in range(6)]
        actual=plan_boards(arts)
        self.assertTrue(all('adaptive_split' not in b['pixel_budget'] for b in actual))
        self.assertTrue(all(b['policy']=='equal-cells-pixel-budget/1' for b in actual))

    def test_older_complex_plans_are_not_capped_at_four(self):
        arts=[{'id':str(n),'box':[0,0,1920,1080],'lettering':'LARGE'} for n in range(6)]
        actual=plan_boards(arts)
        self.assertEqual(len(actual),6)
        self.assertTrue(all('adaptive_split' not in b['pixel_budget'] for b in actual))

    def test_order_is_deterministic_and_only_one_extra_board_is_allowed(self):
        arts=self.fixture['arts'];actual=plan_boards(arts)
        for sequence in (list(reversed(arts)),arts[3:]+arts[:3],sorted(arts,key=lambda a:a['box'][0])):
            self.assertEqual(actual,plan_boards(sequence))
        self.assertLessEqual(len(actual),len(self.fixture['baseline_plan'])+1)


if __name__=='__main__':unittest.main()
