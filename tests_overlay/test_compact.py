from pathlib import Path
import unittest
from jev_feishu.overlay.engine import demo_result
from jev_feishu.overlay.viewmodel import all_decisions
from jev_feishu.overlay.compact import decision_annotation


class CompactDecisionTests(unittest.TestCase):
    def setUp(self):
        self.rows=all_decisions(demo_result(Path('/tmp')))

    def test_all_eight_decisions_survive_without_recomputed_scores(self):
        self.assertEqual(len(self.rows),8)
        for row in self.rows:
            note=decision_annotation(row)
            self.assertIn(row.kind,note['meta'])
            self.assertTrue(note['value'])

    def test_every_choice_option_keeps_its_probability(self):
        for row in self.rows:
            if row.kind!='Choice':continue
            note=decision_annotation(row)
            for _,label,p in row.distribution:
                self.assertIn(f'{label} {p:.0%}',note['distribution'])
            self.assertIn('置信度',note['meta'])

    def test_noul_never_gains_a_confidence(self):
        for row in self.rows:
            if row.kind=='Noul':
                note=decision_annotation(row)
                self.assertIn('置信度不适用',note['meta'])
                self.assertIn('是 ',note['distribution']);self.assertIn('否 ',note['distribution'])

    def test_score_remains_zero_to_four(self):
        row=next(d for d in self.rows if d.kind=='Score')
        self.assertIn('/ 4',decision_annotation(row)['value'])
        self.assertNotIn('/ 10',decision_annotation(row)['value'])

    def test_recommended_action_is_not_claimed_executed(self):
        row=next(d for d in self.rows if d.key=='next_action')
        self.assertIn('未执行',decision_annotation(row)['value'])
