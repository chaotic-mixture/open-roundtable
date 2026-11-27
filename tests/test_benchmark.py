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
from examples.nested_benchmark import CASE_SIZES, summarize
from examples.paired_panel_benchmark import summarize_rounds


class BenchmarkTests(unittest.TestCase):
    def test_within_round_agreement_can_hide_changed_selections(self):
        def result(position, selected):
            metrics = dict(expert_calls=1, model_calls=1, model_seconds=1, consultation_seconds=2, credits=2,
                           input_tokens=10, output_tokens=3, selected=selected, changed_shifts=2+selected,
                           criteria_met=[True, bool(selected)], best_criteria_met=[True, True],
                           min_changes_at_best_score=3, state='RESOLVED' if selected else 'PAUSED',
                           used_fallback=not selected)
            return dict(seed=501, requested_model='fixture', request_sha256='same', same_schedule=True,
                        direct=dict(metrics, position=position), nested=dict(metrics, position=3-position))

        with patch('examples.paired_panel_benchmark.run', side_effect=[[result(1, 0)], [result(2, 1)]]):
            report = summarize_rounds('first', 'second')
        self.assertEqual(report['same_schedule_pairs'], 2)
        self.assertEqual(report['fallback_counts'], dict(direct=1, nested=1))
        for counts in report['topic_states'].values():
            self.assertEqual(counts, {'PAUSED': 1, 'RESOLVED': 1})
        self.assertEqual(report['ranked_candidates'], dict(met=4, evaluated=4, all_met_runs=2, changed_shifts=6))
        self.assertEqual(report['requested_model'], 'fixture')
        mixed_rounds = [
            [result(position, 0), dict(result(position, 0), seed=502, requested_model='other', request_sha256='second')]
            for position in (1, 2)
        ]
        with patch('examples.paired_panel_benchmark.run', side_effect=mixed_rounds):
            with self.assertRaisesRegex(ValueError, 'requested model differs across rosters at seed 502'):
                summarize_rounds('first', 'second')
        for mode in ('direct', 'nested'):
            self.assertEqual(report['changed_selections'][mode], [dict(seed=501, first=0, second=1)])
            self.assertEqual(report['preferences'][mode], dict(met=3, evaluated=4, all_met_runs=1))
            self.assertEqual(report['totals'][mode]['changed_shifts'], 5)
            self.assertEqual(report['totals'][mode]['model_calls'], 2)
            self.assertEqual(report['totals'][mode]['expert_calls'], 2)

    def test_nested_summary_detects_a_missing_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with (directory/'results.csv').open('w', newline='') as output:
                writer = csv.writer(output)
                writer.writerow('case shifts mode state missed extra credits elapsed_ms'.split())
                for case, size in enumerate(CASE_SIZES[:-1], 1):
                    for mode in ('direct', 'nested'):
                        writer.writerow([case, size, mode, 'RESOLVED', 0, 0, 2, 12])
            with self.assertRaisesRegex(ValueError, 'missing benchmark round or schedule'):
                summarize(directory)
            with (directory/'results.csv').open('a', newline='') as output:
                for mode in ('direct', 'nested'):
                    csv.writer(output).writerow([9, CASE_SIZES[-1], mode, 'RESOLVED', 0, 0, 2, 12])
            self.assertEqual(summarize(directory)['pairs'], 9)
            (directory/'design.json').write_text(json.dumps({'repeats': 2}))
            with self.assertRaisesRegex(ValueError, 'planned round count'):
                summarize(directory)

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
