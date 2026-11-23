from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from examples.nested_consult import run
from open_roundtable.core import Kernel


class DelegationTests(unittest.TestCase):
    def test_reopen_and_settle_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'example.sqlite3'
            result = run(path, ['AXR:20-26', 'BXR:24-29'])
            self.assertEqual((result['state'],result['remaining'],result['child_spent']), ('RESOLVED',2,1))
            self.assertTrue(result['verified'])
            with closing(Kernel(path)) as k:
                self.assertEqual(k.settle(result['child']), 1)
                self.assertEqual(k.get(result['parent'])['credits'], 2)
                self.assertEqual(len(k.get(result['parent'])['messages']), 2)

    def test_historical_proposal_is_evidence_without_a_seat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'history.sqlite3'
            with closing(Kernel(path)) as k:
                source = k.create('earlier review', 1)
                target = k.create('follow-up', 2)
                k.join(source, 'author', kind='human')
                k.join(target, 'reviewer', kind='expert')
                k.start(source)
                version = k.propose(source, 'original result', previous_version=0)
                with self.assertRaises(ValueError):
                    k.cite(target, source)
                k.vote(source, 'author', True, version=version)
                self.assertEqual(k.cite(target, source), version)
            with closing(Kernel(path)) as k:
                k.start(target)
                snapshot = k.get(target)
                self.assertEqual(snapshot['citations'], [dict(source=source, version=version,
                                     title='earlier review', body='original result')])
                self.assertEqual((snapshot['credits'], len(snapshot['members'])), (2, 1))
                with self.assertRaises(ValueError):
                    k.vote(target, 'author', True)
                k.vote(target, 'reviewer', True)
                self.assertEqual(k.cite(target, source), version)
                self.assertEqual((k.get(target)['state'], len(k.get(target)['citations'])), ('RESOLVED', 1))

    def test_three_levels(self):
        with closing(Kernel(':memory:')) as k:
            root = k.create('root', 6)
            mid = k.delegate(root, 'panel', 'middle', 5)
            self.assertEqual(k.delegate(root, 'panel', 'middle', 5), mid)
            leaf = k.delegate(mid, 'subpanel', 'leaf', 4)
            k.join(leaf, 'expert', kind='expert')
            for topic in (root, mid, leaf):
                k.start(topic)
            with self.assertRaises(ValueError):
                k.vote(root, 'panel', True)
            with self.assertRaises(ValueError):
                k.top_up(leaf, 1)
            k.post(leaf, 'expert', 'work', cost=2)
            for topic, member in [(leaf,'expert'), (mid,'subpanel')]:
                version = k.propose(topic, 'result', previous_version=0)
                k.vote(topic, member, True, version=version)
                self.assertEqual(k.settle(topic), 2)
            self.assertEqual([k.get(t)['credits'] for t in (root,mid,leaf)], [4,0,0])
