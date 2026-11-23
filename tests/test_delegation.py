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
