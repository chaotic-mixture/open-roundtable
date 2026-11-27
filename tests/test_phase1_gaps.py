import csv
from http.client import BadStatusLine, IncompleteRead
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from examples import preference_benchmark as batch
from examples.compare_panel_receipts import compare
from examples.paired_panel_benchmark import seeded_spec
from examples.user_consult import consult, repair_preview, validate_roster
from examples.refine_schedule import feasibility
from examples.revise_panel_proposal import revise
from examples.verify_preferences import verify
from open_roundtable.core import Kernel


def provider(path, payload=None):
    if path=='/api/tags':
        return {'models':[{'name':'fixture'}]}
    if path=='/api/version':
        return {}
    return dict(done=True,done_reason='stop',message={'content':'{"plan":0}'},
                prompt_eval_count=10,eval_count=3)


class GapTests(unittest.TestCase):
    def test_preview_skips_search_for_an_already_optimal_roster(self):
        spec = dict(title='Night shift', preference='Keep Jo on the original shift',
                    shifts=[dict(id='night', worker='Jo', room='East', start=22, end=30)],
                    criteria=[dict(shift='night', field='delta', min=0, max=0)])
        with patch('examples.user_consult.candidates') as generate:
            result = repair_preview(spec)
            generate.assert_not_called()
        self.assertEqual(result['schedule'], spec['shifts'])
        self.assertEqual(result['changed_shifts'], 0)
        self.assertEqual(result['criteria_met'], (True,))

    def test_english_batch_rejects_language_change_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch, 'api', side_effect=provider):
            folder = Path(tmp) / 'batch'
            batch.run(folder, 'fixture', language='en')
            self.assertEqual(verify(folder), {'verified_rows': 27, 'retryable': 0})
            for seed in range(501, 510):
                evidence = json.loads((folder / f'{seed}-model.json').read_text(encoding='utf8'))
                prompt = evidence['request']['messages'][0]['content']
                self.assertTrue(prompt.startswith(batch.ENGLISH_BRIEFS[(seed-501) % 3]))
            with self.assertRaisesRegex(ValueError, 'incompatible run directory'):
                batch.run(folder, 'fixture', language='zh')
            path = folder / '501-model.json'
            record = json.loads(path.read_text(encoding='utf8'))
            ids = record['request']['format']['properties']['plan']['enum']
            last_id = ids.pop()
            path.write_text(json.dumps(record), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'model request candidate IDs differ'):
                verify(folder)
            ids.append(last_id)
            message = record['request']['messages'][0]
            prefix, menu = message['content'].split('Candidates: ')
            candidates = json.loads(menu)
            candidates[0]['changed'] += 1
            message['content'] = prefix + 'Candidates: ' + json.dumps(candidates)
            path.write_text(json.dumps(record), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'model request candidate facts differ'):
                verify(folder)

    def test_batch_rejects_unpaired_candidate_menus(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch, 'api', side_effect=provider):
            folder = Path(tmp) / 'batch'
            batch.run(folder, 'fixture')
            path = folder / '501-model.json'
            evidence = json.loads(path.read_text(encoding='utf8'))
            # Preserve the selected plan and its score but change the model arm's options.
            evidence['plans'].append(evidence['plans'][0])
            path.write_text(json.dumps(evidence), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'candidate lists differ between arms for seed 501'):
                verify(folder)

    def test_provider_error_text_stays_out_of_fallback_and_journal(self):
        source = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.en.json'
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / 'consult.sqlite3'
            with patch('examples.user_consult.api', side_effect=BadStatusLine('private upstream detail')):
                result = consult(source, database, 'fixture')
            expected = 'model dispatch failed (BadStatusLine)'
            self.assertEqual(result['fallback'], expected)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT error FROM attempts').fetchone(), (expected,))
                self.assertEqual(json.loads(db.execute('SELECT body FROM proposals').fetchone()[0])['fallback'], expected)

    def test_truncated_http_reply_is_recorded_without_model_charge(self):
        source = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.en.json'
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / 'consult.sqlite3'
            with patch('examples.user_consult.api', side_effect=IncompleteRead(b'partial', 10)):
                consult(source, database, 'fixture')
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT status,response FROM attempts').fetchall(), [('failed', None)])
                self.assertEqual(db.execute('SELECT member,cost FROM messages').fetchall(), [('expert', 1)])

    def test_invalid_second_criterion_identifies_its_location(self):
        spec = seeded_spec(501, 7)
        first, second = spec['criteria']
        for clause, expected in [
            (None, 'criteria[1] must be an object'),
            (dict(second, shift='missing'), 'criteria[1].shift'),
            (dict(second, field='hours'), 'criteria[1].field'),
            (dict(second, min=12, max=11), 'criteria[1] requires integer bounds'),
        ]:
            with self.subTest(clause=clause), self.assertRaises(ValueError) as error:
                validate_roster(dict(spec, criteria=[first, clause]))
            self.assertIn(expected, str(error.exception))

    def test_optional_criteria_require_an_array_when_present(self):
        spec = seeded_spec(501, 7)
        del spec['criteria']
        self.assertEqual(validate_roster(spec)[1], [])
        self.assertEqual(validate_roster(dict(spec, criteria=[]))[1], [])
        for invalid in ({}, '', None, False, 0):
            with self.subTest(criteria=invalid), self.assertRaisesRegex(ValueError, 'criteria must be an array'):
                validate_roster(dict(spec, criteria=invalid))

    def test_bilingual_examples_encode_the_same_shifts_and_ordered_preferences(self):
        examples = Path(__file__).resolve().parents[1] / 'examples'
        def load(name):
            return validate_roster(json.loads((examples / name).read_text(encoding='utf-8')))

        english = load('consult_input.en.json')
        self.assertEqual(english, load('consult_input.json'))
        rows, rules = english
        self.assertEqual([(start, duration) for _, start, duration in rows], [(8, 6), (12, 6)])
        self.assertEqual(rules, [(0, 'end', 0, 18), (1, 'delta', 0, 0)])

    def test_revision_minimizes_changes_from_first_proposal_on_tie(self):
        def choose_third_index(path, payload=None):
            reply = provider(path, payload)
            if path == '/api/chat':
                reply['message']['content'] = '{"plan":3}'
            return reply

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'507.json'
            source.write_text(json.dumps(seeded_spec(507,22)),encoding='utf8')
            with patch('examples.user_consult.api', side_effect=choose_third_index):
                first = consult(source,root/'first.db','fixture')
            revised = revise(root/'first.db',root/'second.db')
            self.assertEqual(first['selected'],3)
            self.assertEqual(revised['criteria_met'],[True,True])
            self.assertEqual(revised['changed_shifts'],19)
            self.assertEqual(len(revised['revised_shift_ids']),21)

    def test_revision_archives_ballots_and_preserves_source(self):
        spec = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.json'
        def choose_second(path, payload=None):
            reply = provider(path, payload)
            if path == '/api/chat':
                reply['message']['content'] = '{"plan":1}'
            return reply

        with tempfile.TemporaryDirectory() as tmp, patch('examples.user_consult.api', side_effect=choose_second) as api:
            for nested in (False, True):
                with self.subTest(nested=nested):
                    source = Path(tmp)/f'original-{nested}.db'
                    target = Path(tmp)/f'revised-{nested}.db'
                    first = consult(spec, source, 'fixture', nested=nested)
                    self.assertEqual(first['state'], 'PAUSED')
                    before = source.read_bytes()
                    api.reset_mock()
                    with patch.object(Kernel, 'vote', side_effect=OSError('interrupted')):
                        with self.assertRaises(OSError):
                            revise(source, target)
                    self.assertFalse(target.exists())
                    second = revise(source, target)
                    self.assertEqual((second['topic'], second['version'], second['state']),
                                     (first['topic'], 2, 'PAUSED'))
                    with closing(sqlite3.connect(target)) as db:
                        self.assertEqual(dict(db.execute('SELECT member,agree FROM prior_votes WHERE version=1')),
                                         {'expert':0, 'model':1})
                        self.assertEqual(db.execute('SELECT member,agree FROM votes').fetchall(), [('expert',1)])
                        self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone(), (1,))
                        self.assertEqual(db.execute('SELECT sum(cost) FROM messages').fetchone(), (2,))
                    saved = target.read_bytes()
                    with self.assertRaises(FileExistsError):
                        revise(source, target)
                    self.assertEqual(target.read_bytes(), saved)
                    self.assertEqual(source.read_bytes(), before)
                    api.assert_not_called()

    def test_returned_tool_request_falls_back_without_a_model_vote(self):
        source = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.en.json'
        reply = provider('/api/chat')
        reply['message'] = dict(content='{"plan":1}', tool_calls=[dict(name='inspect')])
        with tempfile.TemporaryDirectory() as tmp, patch('examples.user_consult.api', return_value=reply) as model:
            direct, nested = (Path(tmp)/name for name in ('direct.db', 'nested.db'))
            for path, delegated in ((direct, False), (nested, True)):
                consult(source, path, 'fixture', nested=delegated)
            self.assertEqual(model.call_count, 2)
            report = compare(direct, nested)
            self.assertTrue(report['same_schedule'])
            for mode in ('direct', 'nested'):
                result = report[mode]
                self.assertTrue(result['used_fallback'])
                self.assertEqual((result['selected'], result['votes'], result['state'], result['credits']),
                                 (0, {'expert': 1}, 'PAUSED', 2))
            self.assertIsNone(report['nested']['settled_credits'])

    def test_paired_panel_settlement(self):
        source = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.json'
        with tempfile.TemporaryDirectory() as tmp, patch('examples.user_consult.api', side_effect=provider):
            direct, nested = (Path(tmp)/name for name in ('direct.db', 'nested.db'))
            consult(source, direct, 'fixture')
            panel = consult(source, nested, 'fixture', nested=True)
            summary = compare(direct, nested)
            self.assertEqual(summary['requested_model'], 'fixture')
            self.assertTrue(summary['same_schedule'])
            self.assertEqual(summary['direct']['selected'], 0)
            self.assertFalse(summary['direct']['used_fallback'])
            self.assertEqual(summary['nested']['parent_state'], 'RESOLVED')
            self.assertEqual((summary['direct']['credits'], summary['nested']['settled_credits']), (2, 2))
            with closing(sqlite3.connect(direct)) as flat:
                with flat:
                    flat.execute("UPDATE attempts SET request=json_set(request,'$.model','other-model')")
                with self.assertRaisesRegex(ValueError, 'model requests differ'):
                    compare(direct, nested)
                with flat:
                    flat.execute("UPDATE attempts SET request=json_set(request,'$.model','fixture')")
                with flat:
                    flat.execute("UPDATE votes SET agree=0 WHERE member='expert'")
                with self.assertRaisesRegex(ValueError, 'ballots or consultation state'):
                    compare(direct, nested)
                with flat:
                    flat.execute("UPDATE votes SET agree=1 WHERE member='expert'")
                original = flat.execute('SELECT body FROM proposals').fetchone()[0]
                with flat:
                    flat.execute("UPDATE proposals SET body=json_set(body,'$.fallback','invalid reply')")
                with self.assertRaisesRegex(ValueError, 'recorded fallback'):
                    compare(direct, nested)
                with flat:
                    flat.execute('UPDATE proposals SET body=?', (original,))
                expert_body = flat.execute("SELECT body FROM messages WHERE member='expert'").fetchone()[0]
                expert = json.loads(expert_body)
                proposal = json.loads(original)
                proposal['schedule'] = expert['input']['shifts']
                expert['candidates'][proposal['selected']] = proposal['schedule']
                flat.execute('UPDATE proposals SET body=?', (json.dumps(proposal),))
                flat.execute("UPDATE messages SET body=? WHERE member='expert'", (json.dumps(expert),))
                flat.commit()
                with self.assertRaisesRegex(ValueError, 'violates hard constraints'):
                    compare(direct, nested)
                flat.execute('UPDATE proposals SET body=?', (original,))
                flat.execute("UPDATE messages SET body=? WHERE member='expert'", (expert_body,))
                flat.execute("UPDATE proposals SET body=json_set(body,'$.schedule[0].start',999)")
                flat.commit()
                with self.assertRaisesRegex(ValueError, 'differs from selected expert candidate'):
                    compare(direct, nested)
                flat.execute("UPDATE proposals SET body=json_set(?,'$.criteria_met',json('[]'))", (original,))
                flat.commit()
                with self.assertRaisesRegex(ValueError, 'preference scores'):
                    compare(direct, nested)
                flat.execute('UPDATE proposals SET body=?', (original,))
                flat.commit()
                changed = json.loads(expert_body)
                changed['candidates'].append(changed['candidates'][0])
                flat.execute("UPDATE messages SET body=? WHERE member='expert'", (json.dumps(changed),))
                flat.commit()
                with self.assertRaisesRegex(ValueError, 'model menu differs'):
                    compare(direct, nested)
                flat.execute("UPDATE messages SET body=? WHERE member='expert'", (expert_body,))
                flat.commit()
            with closing(sqlite3.connect(nested)) as db:
                for sql, error in [
                    ('UPDATE delegations SET spent=1 WHERE parent=?', 'settlement disagree'),
                    ('UPDATE messages SET cost=1 WHERE topic=?', 'settlement disagree'),
                    ('UPDATE delegations SET spent=NULL WHERE parent=?', 'already has a parent delivery'),
                ]:
                    with self.subTest(sql=sql):
                        db.execute(sql, (panel['parent'],))
                        db.commit()
                        with self.assertRaisesRegex(ValueError, error):
                            compare(direct, nested)
                        db.execute('UPDATE delegations SET spent=2 WHERE parent=?', (panel['parent'],))
                        db.execute('UPDATE messages SET cost=2 WHERE topic=?', (panel['parent'],))
                        db.commit()
                db.execute("UPDATE attempts SET response=json_set(response,'$.eval_count',999)")
                db.commit()
                with self.assertRaisesRegex(ValueError, 'differs from dispatch receipt'):
                    compare(direct, nested)

    def test_weekly_worker_rest_capacity_boundary(self):
        rows = [(chr(65+i)+'xu', 16*i, 8) for i in range(11)]
        self.assertEqual(feasibility(rows, lambda p: True, 11)['plan'], [16*i for i in range(11)])
        rows.append(('Lxu', 0, 1))
        self.assertEqual(feasibility(rows, lambda p: True, 1), dict(status='infeasible', nodes=0, plan=None))

    def test_exhausted_domain_differs_from_exhausted_budget(self):
        # Fourteen five-hour shifts need fourteen blocks and thirteen rest gaps.
        rows = [(chr(65+i)+'xu', 0, 5) for i in range(14)]
        self.assertEqual(feasibility(rows, lambda plan: True, 1),
                         dict(status='infeasible', nodes=0, plan=None))
        feasible = [(key, i*13, duration) for i, (key, _, duration) in enumerate(rows[:13])]
        self.assertEqual(feasibility(feasible, lambda plan: True, 13)['status'], 'feasible')
        rows=[('Axu',8,4)]
        impossible=lambda plan:plan[0]>168
        self.assertEqual(feasibility(rows,impossible,200)['status'],'infeasible')
        self.assertEqual(feasibility(rows,impossible,3)['status'],'unknown')
        self.assertEqual(feasibility(rows,lambda p:p[0]==10,200)['plan'],[10])

    def test_disagreement_revision_and_stale_ballot(self):
        with closing(Kernel(':memory:')) as k:
            topic=k.create('budget',2)
            for member in 'ab':
                k.join(topic,member,kind='human')
            k.start(topic)
            k.vote(topic,'a',False)
            first=k.propose(topic,'first',previous_version=0)
            k.vote(topic,'a',False,version=first)
            k.vote(topic,'b',True,version=first)
            second=k.propose(topic,'second',previous_version=first)
            with self.assertRaises(ValueError):
                k.vote(topic,'a',True,version=first)
            for member in 'ab':
                k.vote(topic,member,True,version=second)
            state=k.get(topic)
            self.assertEqual(state['state'],'RESOLVED')
            self.assertEqual(len(state['prior_votes']),3)
            self.assertEqual([p['body'] for p in state['proposals']],['first','second'])

    def test_recovery_search_reconciliation_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'batch'
            with patch.object(batch,'api',side_effect=URLError('offline')):
                batch.run(folder,'fixture',expand=True)
            self.assertEqual(verify(folder),{'verified_rows':27,'retryable':9})
            with patch.object(batch,'api',side_effect=provider) as mock:
                batch.run(folder,'fixture',expand=True)
                self.assertEqual(mock.call_count,9)
                batch.run(folder,'fixture',expand=True)
                self.assertEqual(mock.call_count,9)
            self.assertEqual(verify(folder),{'verified_rows':27,'retryable':0})
            with (folder/'results.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertTrue(all((r['calls'],r['credits'])==('3','2') for r in rows if r['arm']=='model'))
            for r in rows:
                if r['arm']=='rules' and r['seed'] in ('502','508'):
                    self.assertEqual((r['high'],r['points'],r['hard_pass']),('1','5','1'))
            record=folder/'502-rules.json'
            data=json.loads(record.read_text(encoding='utf8'))
            data['result']['score']=0
            record.write_text(json.dumps(data),encoding='utf8')
            with self.assertRaises(ValueError):
                verify(folder)

    def test_receipt_survives_interruption_before_charge(self):
        from examples.auto_consult import assert_post
        def interrupt(k,topic,member,body):
            if member=='model':
                raise KeyboardInterrupt
            assert_post(k,topic,member,body)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'batch'
            with patch.object(batch,'api',side_effect=provider), patch.object(batch,'assert_post',side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    batch.run(folder,'fixture')
            with patch.object(batch,'api',side_effect=provider) as mock:
                batch.run(folder,'fixture')
                self.assertEqual(mock.call_count,8)
            self.assertEqual(verify(folder)['verified_rows'],27)

    def test_custom_roster_and_named_preferences(self):
        with tempfile.TemporaryDirectory() as tmp, patch('examples.user_consult.api',side_effect=provider):
            folder=Path(tmp)
            source=folder/'source.json'
            source.write_text(json.dumps({'title':'Weekend','preference':'Keep the late shift if possible',
                'shifts':[dict(id='Early shift',worker='Kai',room='East',start=8,end=12),
                          dict(id='Late shift',worker='Kai',room='East',start=10,end=15)]}))
            result=consult(source,folder/'test.db','fixture')
            self.assertEqual(result['state'],'RESOLVED')
            self.assertEqual([r['id'] for r in result['schedule']],['Early shift','Late shift'])
            with closing(sqlite3.connect(folder/'test.db')) as db:
                self.assertEqual(db.execute('SELECT status FROM attempts').fetchone(),('returned',))
