from contextlib import closing
from http.client import IncompleteRead
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.peer_node import deliver, digest, find_capability, ledger, match_offers, send, set_permission, verify_offer
from examples.capability_execution import execute_selection
from open_roundtable.data import initialize, ingest, share_public


class CapabilityLimitsTests(unittest.TestCase):
    def test_read_only_offer_creates_no_repair_job_at_either_node(self):
        spec = json.loads((Path(__file__).resolve().parents[1]/'examples'/'consult_input.json').read_bytes())
        topic = dict(source='https://a.example/topic',body=json.dumps(spec))
        offer = dict(source='https://b.example/audit',body=json.dumps(dict(kind='capability',node='B',tag='audit',
                                                                description='Report overlapping shifts')))
        ref = [offer['source'],hashlib.sha256(offer['body'].encode()).hexdigest()]
        with tempfile.TemporaryDirectory() as tmp:
            a,b = Path(tmp)/'A',Path(tmp)/'B'
            for root,record in [(a,topic),(b,offer)]:
                initialize(root)
                (root/'node.json').write_text(json.dumps(dict(name=root.name)))
                ingest(root,'public',[record])
                share_public(root,record['source'],True)
            with closing(ledger(a)) as sender, closing(ledger(b)) as receiver:
                sender.execute("INSERT INTO peers VALUES ('B','http://127.0.0.1:8002')")
                receiver.execute("INSERT INTO peers VALUES ('A','http://127.0.0.1:8001')")
                set_permission(receiver,'A',True,1)
                with patch('examples.peer_node.fetch_public_subset',return_value=[offer]), patch('examples.peer_node.transfer') as wire:
                    with self.assertRaisesRegex(ValueError,'does not declare schedule repair'):
                        send(a,sender,'B',topic['source'],offer_ref=ref)
                    wire.assert_not_called()
                body = dict(sender='A',recipient='B',source=topic['source'],spec=spec,offer=ref)
                with patch('examples.peer_node.fetch_public_subset',return_value=[topic]), patch('examples.peer_node.consult') as consult:
                    with self.assertRaisesRegex(ValueError,'does not declare schedule repair'):
                        deliver(b,receiver,dict(body,id=digest(body)))
                    consult.assert_not_called()
                for db in (sender,receiver):
                    self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone(),(0,))
                self.assertEqual(receiver.execute("SELECT remaining FROM grant_limits WHERE name='A'").fetchone(),(1,))

    def test_mixed_recommendation_never_runs_either_provider(self):
        with patch('examples.capability_execution.prepare') as repair, patch('examples.capability_execution.check') as audit:
            for requested in ('audit', 'repair'):
                self.assertEqual(execute_selection(requested, ['audit', 'repair'], []), (False, 0))
            repair.assert_not_called()
            audit.assert_not_called()

    def test_admission_requires_a_complete_offer_even_with_matching_digest(self):
        for fields in ({}, {'tag':'repair'}, {'tag':' ', 'description':'fix shifts'},
                       {'tag':'repair', 'description':7}):
            with self.subTest(fields=fields):
                body = json.dumps(dict(kind='capability', node='B', **fields))
                record = dict(source='public-offer', body=body)
                reference = (record['source'], hashlib.sha256(body.encode()).hexdigest())
                with self.assertRaises(ValueError):
                    verify_offer([record], 'B', reference)

    def test_repair_admission_checks_tag_of_selected_revision(self):
        records = []
        for tag in ('audit', 'repair', 'reschedule'):
            body = json.dumps(dict(kind='capability', node='B', tag=tag, description='roster service'))
            records.append(dict(source=tag,body=body))
        for record in records:
            ref = record['source'], hashlib.sha256(record['body'].encode()).hexdigest()
            if record['source'] == 'audit':
                with self.assertRaisesRegex(ValueError,'does not declare schedule repair'):
                    verify_offer(records,'B',ref,repair_only=True)
            else:
                verify_offer(records,'B',ref,repair_only=True)

    def test_model_receives_only_allowed_tags(self):
        offers = [dict(tag='repair',description='change shifts'),dict(tag='audit',description='check conflicts')]
        reply = dict(done=True,done_reason='stop',message=dict(content='{"ids":[0]}'),prompt_eval_count=12,eval_count=4)
        with patch('examples.auto_consult.api',return_value=reply) as api:
            result = match_offers('check only',offers,'fixture',allowed_tags=['audit'])
            menu = json.loads(api.call_args.args[1]['messages'][1]['content'])['offers']
            self.assertEqual([o['tag'] for o in menu], ['audit'])
            self.assertEqual(result['matches'], [offers[1]])
            api.reset_mock()
            empty = match_offers('check only',offers,'fixture',allowed_tags=['review'])
            self.assertEqual((empty['matches'],empty['calls']), ([],0))
            api.assert_not_called()

    def test_invalid_model_receipt_cannot_recommend_a_provider(self):
        offer = dict(tag='audit', description='check conflicts')
        reply = dict(done=True, done_reason='stop', message=dict(content='{"ids":[0]}'),
                     prompt_eval_count=12, eval_count=4)
        for changes in (dict(done_reason='length'), dict(done='false'), dict(eval_count=None),
                        dict(prompt_eval_count=-1), dict(eval_count=True), dict(message={}),
                        dict(message=dict(content='{"ids":[true]}'))):
            with self.subTest(changes=changes), patch('examples.auto_consult.api', return_value=reply | changes):
                with self.assertRaises(ValueError):
                    match_offers('check conflicts', [offer], 'fixture')

    def test_partial_discovery_survives_invalid_peers(self):
        with tempfile.TemporaryDirectory() as tmp, closing(ledger(Path(tmp))) as db:
            root = Path(tmp)
            (root/'node.json').write_text('{"name":"A"}')
            for node in ('B','C','D'):
                db.execute('INSERT INTO peers VALUES (?,?)',(node,'http://example.invalid'))
            body = json.dumps(dict(kind='capability',node='D',tag='audit',description='check only'))
            replies = [None, IncompleteRead(b''), [dict(body=body),dict(source='published',body=body)]]
            with patch('examples.peer_node.transfer',side_effect=replies):
                result = find_capability(root,db,'audit')
            self.assertEqual(result,dict(matches=[dict(node='D',source='published',description='check only',sha256=hashlib.sha256(body.encode()).hexdigest())],
                                         unavailable=['B','C']))

    def test_multiple_tags_use_one_catalog_read_per_peer(self):
        with tempfile.TemporaryDirectory() as tmp, closing(ledger(Path(tmp))) as db:
            root = Path(tmp)
            (root/'node.json').write_text('{"name":"A"}')
            db.execute("INSERT INTO peers VALUES ('B','http://example.invalid')")
            records = [dict(source=tag, body=json.dumps(dict(kind='capability', node='B', tag=tag, description=tag)))
                       for tag in ('audit','reschedule','repair')]
            with patch('examples.peer_node.fetch_public_subset',return_value=records) as fetch:
                result = find_capability(root,db,'repair','reschedule','repair')
                fetch.assert_called_once()
            self.assertEqual([m['tag'] for m in result['matches']], ['reschedule','repair'])
