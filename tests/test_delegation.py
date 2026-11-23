from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
import unittest

from examples.nested_consult import run
from open_roundtable.core import Kernel


class DelegationTests(unittest.TestCase):
    def test_top_up_and_refund_share_integer_limit(self):
        maximum = 2**63 - 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'refund.sqlite3'
            with closing(Kernel(path)) as k:
                parent = k.create('review', maximum - 1)
                child = k.delegate(parent, 'panel', 'review finding', 4)
                k.join(child, 'reviewer', kind='human')
                k.start(parent)
                k.start(child)
                version = k.propose(child, 'accepted', previous_version=0)
                k.vote(child, 'reviewer', True, version=version)
                with self.assertRaises(ValueError):
                    k.top_up(parent, 2)
            ready = Barrier(2)

            def apply(operation):
                with closing(Kernel(path)) as k:
                    ready.wait(timeout=10)
                    return operation(k)

            with ThreadPoolExecutor(2) as pool:
                refund = pool.submit(apply, lambda k: k.settle(child))
                deposit = pool.submit(apply, lambda k: k.top_up(parent, 1))
                self.assertEqual(refund.result(), 0)
                deposit.result()
            with closing(Kernel(path)) as k:
                self.assertEqual(k.get(parent)['credits'], maximum)
                self.assertIs(type(k.get(parent)['credits']), int)
                self.assertEqual(k.get(child)['credits'], 0)
                with self.assertRaises(ValueError):
                    k.top_up(parent, 1)

    def test_simultaneous_settlement_refunds_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'concurrent.sqlite3'
            with closing(Kernel(path)) as k:
                parent = k.create('review', 5)
                child = k.delegate(parent, 'panel', 'check', 4)
                k.join(child, 'expert', kind='expert')
                k.start(parent)
                k.start(child)
                k.post(child, 'expert', 'findings', cost=1)
                version = k.propose(child, 'verified result', previous_version=0)
                k.vote(child, 'expert', True, version=version)
            ready = Barrier(2)

            def settle():
                with closing(Kernel(path)) as connection:
                    ready.wait(timeout=10)
                    return connection.settle(child)

            with ThreadPoolExecutor(2) as pool:
                attempts = [pool.submit(settle) for _ in range(2)]
                self.assertEqual([attempt.result() for attempt in attempts], [1, 1])
            with closing(Kernel(path)) as k:
                outcome = k.get(parent)
                self.assertEqual((outcome['credits'], k.get(child)['credits']), (4, 0))
                self.assertEqual([(m['body'], m['cost']) for m in outcome['messages']], [('verified result', 1)])

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
