import csv
import unittest
from pathlib import Path

from examples.summarize_detection import summarize


class DetectionSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference = Path(__file__).resolve().parents[1] / 'examples' / 'paired_501_metrics.csv'
        with reference.open(encoding='utf-8', newline='') as source:
            cls.rows = list(csv.DictReader(source))
        cls.design = dict(first_seed=501, sizes=[7, 11, 22], repetitions=3)

    def test_interruption_preserves_partial_and_unstarted_rosters(self):
        report = summarize(self.rows[:4], self.design)
        self.assertEqual([(r['seed'], r['baseline']) for r in report['comparisons']],
                         [(501, 'solo'), (501, 'tool')])
        self.assertEqual(report['incomplete'][0], dict(seed=502, shifts=7, missing=['solo', 'tool']))
        self.assertEqual([r['seed'] for r in report['incomplete'][1:]], list(range(503, 510)))
        for roster in report['incomplete'][1:]:
            self.assertEqual(roster['missing'], ['solo', 'table', 'tool'])

    def test_reference_rule_deltas_match_reported_totals(self):
        report = summarize(self.rows, self.design)
        self.assertEqual(report['incomplete'], [])
        self.assertEqual(len(report['comparisons']), 18)
        for comparison in report['comparisons']:
            delta = comparison['table_minus_baseline']
            for metric in ('missed', 'extra'):
                self.assertEqual(sum(rule[metric] for rule in delta['by_rule'].values()), delta[metric])
            if comparison['seed'] == 505 and comparison['baseline'] == 'tool':
                self.assertEqual(delta['by_rule']['L'], dict(missed=2, extra=0))

    def test_inconsistent_rule_total_rejected_even_for_partial_roster(self):
        with self.assertRaisesRegex(ValueError, 'disagree with missed total'):
            summarize([dict(self.rows[0], missed='999')], self.design)

    def test_panel_decision_survives_missing_baselines(self):
        panel = dict(next(row for row in self.rows if row['arm'] == 'table'),
                     topic_state='PAUSED', model_vote='1', expert_vote='0')
        report = summarize([panel])
        self.assertEqual(report['comparisons'], [])
        self.assertEqual(report['panel_decisions'], [dict(seed=501, shifts=7, state='PAUSED',
                                                        model_vote=1, expert_vote=0, missed=0, extra=0)])
        with self.assertRaisesRegex(ValueError, 'disagrees with votes'):
            summarize([dict(panel, topic_state='RESOLVED')])
        self.assertNotIn('panel_decisions', summarize(self.rows))
