from contextlib import closing
from http.client import IncompleteRead
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.peer_node import find_capability, ledger, match_offers


class CapabilityLimitsTests(unittest.TestCase):
    def test_model_receives_only_allowed_tags(self):
        offers = [dict(tag='repair',description='change shifts'),dict(tag='audit',description='check conflicts')]
        reply = dict(done=True,message=dict(content='{"ids":[0]}'),prompt_eval_count=12,eval_count=4)
        with patch('examples.auto_consult.api',return_value=reply) as api:
            result = match_offers('check only',offers,'fixture',allowed_tags=['audit'])
            menu = json.loads(api.call_args.args[1]['messages'][1]['content'])['offers']
            self.assertEqual([o['tag'] for o in menu], ['audit'])
            self.assertEqual(result['matches'], [offers[1]])
            api.reset_mock()
            empty = match_offers('check only',offers,'fixture',allowed_tags=['review'])
            self.assertEqual((empty['matches'],empty['calls']), ([],0))
            api.assert_not_called()

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
