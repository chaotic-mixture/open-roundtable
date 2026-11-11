import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from examples import repair_benchmark as repair
from examples import select_repair as select
from examples.generate_overnight import generate
from examples.overnight_score import expected


def response(body):
    return {'done':True, 'done_reason':'stop', 'message':{'content':body},
            'prompt_eval_count':12, 'eval_count':4}


class RepairTests(unittest.TestCase):
    def test_verified_menu_and_rule_ranking(self):
        improvements = {}
        totals = [0, 0]
        for i, seed in enumerate(range(501,510)):
            rows = []
            for shift in generate(seed, (7,11,22)[i//3]):
                key, span = shift.split(':')
                start, end = map(int,span.split('-'))
                rows.append((key,start,end-start))
            scored = []
            for plan in repair.candidates(rows):
                self.assertEqual(len(plan),len(rows))
                self.assertTrue(all(0<=h<=168-r[2] for h,r in zip(plan,rows)))
                self.assertFalse(any(expected(repair.schedule(rows,plan)).values()))
                wishes = sum((plan[0]==rows[0][1], 6<=plan[1]%24<12, 12<=plan[2]%24<18))
                moved = sum(h!=r[1] for h,r in zip(plan,rows))
                scored.append((wishes,-moved))
            best = max(scored)
            if best[0]>scored[0][0]:
                improvements[seed] = best[0]-scored[0][0]
            totals[0] += best[0]
            totals[1] -= best[1]
        self.assertEqual(improvements,{505:3,506:2,507:2})
        self.assertEqual(totals,[17,91])

    def test_selector_rejects_edits_and_invalid_ids(self):
        for raw in ['{"plan":true}','{"plan":-1}','{"plan":2}','{"plan":0,"moves":{}}','']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                select.choice(response(raw),2)
        self.assertEqual(select.choice(response('{"plan":1}'),2),1)

    def test_default_delivered_and_charged_on_bad_reply_or_outage(self):
        calls = 0
        def provider(path,payload=None):
            nonlocal calls
            if path == '/api/chat':
                calls += 1
                if calls%2:
                    return response('{"plan":true}')
            raise URLError('offline fixture')
        with tempfile.TemporaryDirectory() as tmp, patch.object(select,'api',side_effect=provider):
            folder = Path(tmp)/'select'
            select.run(folder,'fixture')
            with (folder/'results.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows),9)
            for i,r in enumerate(rows):
                self.assertEqual([r[k] for k in ('success','selected','fallback','calls')],['1','0','1','2'])
                self.assertEqual(int(r['credits']),2 if i%2==0 else 1)
                record = json.loads((folder/(r['seed']+'.json')).read_text())
                starts = [int(s.split(':')[1].split('-')[0]) for s in record['result']['repaired']]
                self.assertEqual(starts,record['plans'][0])

    def test_free_edit_failure_does_not_count_as_delivery(self):
        def provider(path,payload=None):
            if path == '/api/tags':
                return {'models':[{'name':'fixture'}]}
            if path == '/api/version':
                return {'version':'fixture'}
            return response('{"plan":0,"moves":{"A":-1}}')
        with tempfile.TemporaryDirectory() as tmp, patch.object(repair,'api',side_effect=provider), redirect_stdout(io.StringIO()):
            folder = Path(tmp)/'repair'
            repair.run(folder,'fixture')
            with (folder/'results.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows),27)
            for r in rows:
                self.assertEqual(int(r['success']),int(r['arm']=='tool'))
                self.assertEqual(int(r['credits']),3 if r['arm']=='table' else 1)
