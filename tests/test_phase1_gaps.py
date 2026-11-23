import csv
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
from examples.user_consult import consult
from examples.refine_schedule import feasibility
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
    def test_paired_panel_settlement(self):
        source = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.json'
        with tempfile.TemporaryDirectory() as tmp, patch('examples.user_consult.api', side_effect=provider):
            direct, nested = (Path(tmp)/name for name in ('direct.db', 'nested.db'))
            consult(source, direct, 'fixture')
            panel = consult(source, nested, 'fixture', nested=True)
            summary = compare(direct, nested)
            self.assertTrue(summary['same_schedule'])
            self.assertEqual(summary['direct']['selected'], 0)
            self.assertFalse(summary['direct']['used_fallback'])
            self.assertEqual(summary['nested']['parent_state'], 'RESOLVED')
            self.assertEqual((summary['direct']['credits'], summary['nested']['settled_credits']), (2, 2))
            with closing(sqlite3.connect(direct)) as flat:
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
