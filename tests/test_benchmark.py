import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from examples.benchmark_consult import run
from examples.generate_overnight import generate
from examples.overnight_score import expected


class BenchmarkTests(unittest.TestCase):
    def test_paired_inputs_blinding_scoring_and_charges(self):
        def provider(path, payload=None):
            if path == '/api/tags':
                return {'models': [{'name': 'fixture'}]}
            if path == '/api/version':
                return {'version': 'fixture'}
            self.assertEqual(path, '/api/chat')
            text = payload['messages'][0]['content']
            result = {r: [] for r in 'PVRL'}
            if 'final list: ' in text:
                result.update(json.loads(text.split('final list: ')[1]))
            result['P'].append('invalid-pair')
            result['approve_final'] = True
            return {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(result)},
                    'prompt_eval_count': 41, 'eval_count': 17}

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'paired'
            with patch('examples.benchmark_consult.api', side_effect=provider), redirect_stdout(StringIO()):
                run(folder, 'fixture', 1, 501)
                with self.assertRaises(FileExistsError):
                    run(folder, 'fixture', 1, 501)
            with (folder/'results.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 9)
            self.assertEqual({(int(r['seed']), r['arm']) for r in rows},
                             {(s, a) for s in range(501, 504) for a in ('solo', 'tool', 'table')})
            with closing(sqlite3.connect(folder/'ledger.sqlite3')) as db:
                for row in rows:
                    seed, arm = int(row['seed']), row['arm']
                    data = json.loads((folder/f'{seed}-{arm}.json').read_text())
                    shifts = generate(seed, (7,11,22)[seed-501])
                    self.assertEqual(data['shifts'], shifts)
                    members = [c['member'] for c in data['calls']]
                    self.assertEqual(members, {'solo':['model'], 'tool':['expert'],
                                               'table':['expert','model']}[arm])
                    if arm != 'tool':
                        text = data['calls'][-1]['input']['messages'][0]['content']
                        self.assertEqual('expert proposal' in text, arm == 'table')
                    self.assertEqual(int(row['missed']), sum(map(len, expected(shifts).values()))
                                     if arm == 'solo' else 0)
                    self.assertEqual(int(row['extra']), int(arm != 'tool'))
                    self.assertEqual((int(row['input_tokens']), int(row['output_tokens'])),
                                     (0,0) if arm == 'tool' else (41,17))
                    charged = db.execute('SELECT sum(cost) FROM messages WHERE topic=?',
                                         (data['topic'],)).fetchone()[0]
                    self.assertEqual((int(row['calls']), int(row['credits']), charged),
                                     (len(members),)*3)
                    self.assertGreaterEqual(float(row['seconds']), 0)
