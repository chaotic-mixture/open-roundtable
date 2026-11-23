import json
import unittest
from unittest.mock import patch

from examples.peer_node import match_offers


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
