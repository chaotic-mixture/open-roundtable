"""Hand-labelled boundary cases and independent replay of the public scores."""
import csv
import unittest
from pathlib import Path

from examples.generate_overnight import generate
from examples.overnight_score import expected
from examples.preference_benchmark import facts, rule_key
from examples.preference_rubric import assess
from examples.repair_benchmark import candidates, schedule


class PreferenceTests(unittest.TestCase):
    def test_midnight_exception_and_childcare_deadline(self):
        # Original 20–24 ends exactly at midnight. 20–25 crosses it.
        for duration, start, flags in [
            (4, 14, (True, False, True)), (4, 15, (False, False, True)),
            (4, 20, (False, True, True)), (5, 20, (True, True, True)),
            (5, 44, (False, True, True)), (5, 13, (False, False, True)),
        ]:
            with self.subTest(duration=duration, start=start):
                self.assertEqual(assess(0,[20,23,8],[duration,1,1],[start,23,8]), flags)
        self.assertFalse(assess(0,[20,23,8],[4,1,1],[14,24,8])[2])

    def test_shuttle_windows_and_original_date(self):
        for original, start, flags in [
            (29,29,(True,False)), (29,5,(False,False)), (29,30,(False,False)),
            (30,30,(True,False)), (30,33,(True,False)), (30,34,(False,False)),
            (30,38,(False,True)), (30,45,(False,True)), (30,46,(False,False)),
        ]:
            with self.subTest(original=original,start=start):
                self.assertEqual(assess(1,[0,original,12],[1,1,1],[0,start,11]), (*flags,False))

    def test_appointment_duration_date_and_two_hour_limits(self):
        for duration, start, a, flags in [
            (7,21,12,(True,False,True)), (7,23,13,(False,True,False)),
            (8,22,8,(True,False,True)), (8,23,7,(True,True,False)),
            (8,24,10,(False,True,True)),
        ]:
            with self.subTest(duration=duration,start=start,a=a):
                self.assertEqual(assess(2,[10,4,21],[1,1,duration],[a,4,start]), flags)

    def test_public_scores_and_rule_optima(self):
        path = Path(__file__).resolve().parents[1]/'examples/preference_501_metrics.csv'
        with path.open() as stream:
            records = list(csv.DictReader(stream))
        self.assertEqual(len(records),27)
        for offset, seed in enumerate(range(501,510)):
            rows = []
            for token in generate(seed,(7,11,22)[offset//3]):
                key, span = token.split(':')
                start,end = map(int,span.split('-'))
                rows.append((key,start,end-start))
            old = [r[1] for r in rows]
            durations = [r[2] for r in rows]
            plans = candidates(rows)
            ranking, independently_scored = [], []
            for j, plan in enumerate(plans):
                self.assertFalse(any(expected(schedule(rows,plan)).values()))
                flags = assess(offset%3,old,durations,plan)
                points = 4*flags[0]+2*flags[1]+flags[2]
                moved = sum(x!=y for x,y in zip(plan,old))
                independently_scored.append((points,-moved,-j))
                ranking.append(rule_key(offset%3,facts(plan,rows),moved,j))
            reference = [r for r in records if int(r['seed'])==seed]
            self.assertEqual({r['arm'] for r in reference},{'default','rules','model'})
            for r in reference:
                j = int(r['selected'])
                self.assertEqual(independently_scored[j],(int(r['points']),-int(r['changed']),-j))
                self.assertEqual(tuple(map(int, assess(offset%3,old,durations,plans[j]))),
                                 tuple(int(r[k]) for k in ('high','medium','low')))
                if r['arm']=='rules':
                    self.assertEqual(ranking[j],max(ranking))
                    self.assertEqual(independently_scored[j],max(independently_scored))
