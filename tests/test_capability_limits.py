from contextlib import closing
from http.client import IncompleteRead
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.peer_node import find_capability, ledger, match_offers, verify_offer
from examples.capability_execution import execute_selection


class CapabilityLimitsTests(unittest.TestCase):
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
