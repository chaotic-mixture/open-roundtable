"""Real SQLite acceptance tests; all participants/content are synthetic."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'core.sqlite3'

    def kernel(self):
        self.assertIsNotNone(importlib.util.find_spec('open_roundtable'), 'kernel package missing')
        from open_roundtable import Kernel
        k = Kernel(self.path)
        self.addCleanup(k.close)
        return k

    def test_create_survives_restart(self):
        k = self.kernel()
        topic = k.create('Synthetic question', credits=7)
        self.assertEqual(k.get(topic)['state'], 'DRAFT')
        k.close()
        reopened = self.kernel()
        self.assertEqual(reopened.get(topic)['credits'], 7)
        self.assertEqual(reopened.get(topic)['title'], 'Synthetic question')

    def test_invalid_create_leaves_database_unchanged(self):
        k = self.kernel()
        for value in [-1, True, False, float('nan'), float('inf'), 1.0, '1', None, 2**63]:
            with self.subTest(credits=repr(value)):
                before = list(k.db.iterdump())
                with self.assertRaises(ValueError):
                    k.create('Synthetic', credits=value)
                self.assertEqual(list(k.db.iterdump()), before)
        for title in ['', '   ', None, 123]:
            before = list(k.db.iterdump())
            with self.assertRaises(ValueError):
                k.create(title, credits=1)
            self.assertEqual(list(k.db.iterdump()), before)

    def test_join_preserves_all_kinds_and_source(self):
        k = self.kernel()
        topic = k.create('Synthetic', 5)
        self.assertTrue(callable(getattr(k, 'join', None)), 'join API missing')
        for kind in ['model', 'expert', 'human', 'roundtable']:
            k.join(topic, kind + '-1', kind=kind, source='synthetic:' + kind)
        k.close()
        members = self.kernel().get(topic)['members']
        self.assertEqual({m['kind'] for m in members}, {'model', 'expert', 'human', 'roundtable'})
        self.assertEqual(next(m['source'] for m in members if m['kind'] == 'roundtable'), 'synthetic:roundtable')
        before = list(self.kernel().db.iterdump())
        k = self.kernel()
        for member, kind, source in [('model-1', 'model', 'duplicate'), ('x', 'alien', 's'), ('', 'human', 's'), ('nested', 'roundtable', None)]:
            with self.assertRaises(ValueError):
                k.join(topic, member, kind=kind, source=source)
        with self.assertRaises(ValueError):
            k.join('unknown-topic', 'x', kind='human')
        self.assertEqual(list(k.db.iterdump()), before)

    def test_start_freezes_nonempty_roster(self):
        k = self.kernel()
        topic = k.create('Synthetic', 5)
        self.assertTrue(callable(getattr(k, 'start', None)), 'start API missing')
        with self.assertRaises(ValueError):
            k.start(topic)
        self.assertEqual(k.get(topic)['state'], 'DRAFT')
        k.join(topic, 'alice', kind='human')
        k.start(topic)
        self.assertEqual(k.get(topic)['state'], 'ACTIVE')
        before = list(k.db.iterdump())
        with self.assertRaises(ValueError):
            k.join(topic, 'late', kind='expert')
        with self.assertRaises(ValueError):
            k.start(topic)
        self.assertEqual(list(k.db.iterdump()), before)
        zero = k.create('Zero budget', 0)
        k.join(zero, 'bob', kind='model')
        k.start(zero)
        self.assertEqual(k.get(zero)['state'], 'PAUSED')

    def active(self, credits=5, count=3):
        k = self.kernel()
        topic = k.create('Synthetic', credits)
        for i in range(count):
            k.join(topic, f'm{i}', kind='human')
        k.start(topic)
        return k, topic

    def test_post_spends_credits_and_persists_message(self):
        k, topic = self.active()
        self.assertTrue(callable(getattr(k, 'post', None)), 'post API missing')
        self.assertTrue(k.post(topic, 'm0', 'Synthetic prewritten observation', cost=2))
        k.close()
        snapshot = self.kernel().get(topic)
        self.assertEqual(snapshot['credits'], 3)
        self.assertEqual(snapshot['state'], 'ACTIVE')
        self.assertEqual(len(snapshot['messages']), 1)
        self.assertEqual(snapshot['messages'][0]['body'], 'Synthetic prewritten observation')
        self.assertEqual(snapshot['messages'][0]['cost'], 2)

    def test_invalid_posts_never_change_state(self):
        k, topic = self.active()
        cases = [('m0', 'text', v) for v in [-1, 0, True, float('nan'), float('inf'), 1.0, '1', None, 2**63]]
        cases += [('unknown', 'text', 1), ('m0', '', 1), ('m0', None, 1)]
        for member, body, cost in cases:
            with self.subTest(member=member, body=body, cost=repr(cost)):
                before = list(k.db.iterdump())
                with self.assertRaises(ValueError):
                    k.post(topic, member, body, cost=cost)
                self.assertEqual(list(k.db.iterdump()), before)
        draft = k.create('Draft', 5)
        k.join(draft, 'm0', kind='human')
        with self.assertRaises(ValueError):
            k.post(draft, 'm0', 'Not yet', cost=1)

    def test_exhaustion_pauses_without_partial_spend(self):
        k, topic = self.active(credits=2)
        self.assertFalse(k.post(topic, 'm0', 'Too expensive', cost=3))
        paused = k.get(topic)
        self.assertEqual(paused['state'], 'PAUSED')
        self.assertEqual(paused['credits'], 2)
        self.assertEqual(paused['messages'], [])
        before = list(k.db.iterdump())
        with self.assertRaises(ValueError):
            k.post(topic, 'm0', 'While paused', cost=1)
        self.assertEqual(list(k.db.iterdump()), before)
        k2, exact = self.active(credits=2)
        self.assertTrue(k2.post(exact, 'm0', 'Exact spend', cost=2))
        self.assertEqual(k2.get(exact)['credits'], 0)
        self.assertEqual(k2.get(exact)['state'], 'PAUSED')
        self.assertEqual(len(k2.get(exact)['messages']), 1)

    def test_top_up_resumes_after_restart(self):
        k, topic = self.active(credits=1)
        k.post(topic, 'm0', 'Synthetic spend', cost=1)
        k.close()
        k = self.kernel()
        self.assertTrue(callable(getattr(k, 'top_up', None)), 'top_up API missing')
        k.top_up(topic, 3)
        self.assertEqual(k.get(topic)['state'], 'ACTIVE')
        self.assertTrue(k.post(topic, 'm1', 'Synthetic resumed', cost=2))
        self.assertEqual(k.get(topic)['credits'], 1)
        self.assertEqual(len(k.get(topic)['messages']), 2)
        draft = k.create('Draft', 0)
        k.top_up(draft, 2)
        self.assertEqual(k.get(draft)['state'], 'DRAFT')

    def test_invalid_top_up_and_overflow_are_atomic(self):
        k, topic = self.active(credits=0)
        for value in [-1, 0, True, False, float('nan'), float('inf'), 1.0, '1', None, 2**63]:
            with self.subTest(value=repr(value)):
                before = list(k.db.iterdump())
                with self.assertRaises(ValueError):
                    k.top_up(topic, value)
                self.assertEqual(list(k.db.iterdump()), before)
        large = k.create('Large', 2**63 - 1)
        before = list(k.db.iterdump())
        with self.assertRaises(ValueError):
            k.top_up(large, 1)
        self.assertEqual(list(k.db.iterdump()), before)

    def test_strict_majority_of_frozen_roster_survives_restart(self):
        for size in [1, 2, 3, 4, 5]:
            with self.subTest(size=size):
                k, topic = self.active(count=size)
                self.assertTrue(callable(getattr(k, 'vote', None)), 'vote API missing')
                for i in range(size // 2):
                    k.vote(topic, f'm{i}', True)
                    self.assertEqual(k.get(topic)['state'], 'ACTIVE')
                k.close()
                k = self.kernel()
                k.vote(topic, f'm{size // 2}', True)
                snapshot = k.get(topic)
                self.assertEqual(snapshot['state'], 'RESOLVED')
                self.assertEqual(len(snapshot['votes']), size // 2 + 1)
                self.assertEqual(len(snapshot['members']), size)
                self.assertEqual(snapshot['credits'], 5)

    def test_duplicate_vote_is_rejected_without_inflation(self):
        k, topic = self.active(count=4)
        k.vote(topic, 'm0', True)
        before = list(k.db.iterdump())
        for choice in [True, False]:
            with self.assertRaises(ValueError):
                k.vote(topic, 'm0', choice)
            self.assertEqual(list(k.db.iterdump()), before)
        k.vote(topic, 'm1', False)
        k.vote(topic, 'm2', True)
        self.assertEqual(k.get(topic)['state'], 'ACTIVE')
        self.assertEqual(len(k.get(topic)['votes']), 3)
        k.vote(topic, 'm3', True)
        self.assertEqual(k.get(topic)['state'], 'RESOLVED')

    def test_vote_rejects_unknown_invalid_and_unstarted(self):
        k, topic = self.active()
        for member, choice in [('unknown', True), ('m0', 1), ('m0', 'yes'), ('m0', None)]:
            before = list(k.db.iterdump())
            with self.assertRaises(ValueError):
                k.vote(topic, member, choice)
            self.assertEqual(list(k.db.iterdump()), before)
        draft = k.create('Draft', 0)
        k.join(draft, 'm0', kind='human')
        before = list(k.db.iterdump())
        with self.assertRaises(ValueError):
            k.vote(draft, 'm0', True)
        self.assertEqual(list(k.db.iterdump()), before)
        k.start(draft)
        k.vote(draft, 'm0', True)
        self.assertEqual(k.get(draft)['state'], 'RESOLVED')

    def test_resolved_records_are_retained_and_closed(self):
        k, topic = self.active()
        k.post(topic, 'm0', 'Retained synthetic observation', cost=1)
        k.vote(topic, 'm0', True)
        k.vote(topic, 'm1', True)
        before = list(k.db.iterdump())
        operations = [lambda: k.post(topic, 'm0', 'Late', cost=1),
                      lambda: k.top_up(topic, 1),
                      lambda: k.vote(topic, 'm2', False),
                      lambda: k.join(topic, 'late', kind='human'),
                      lambda: k.start(topic)]
        for operation in operations:
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(list(k.db.iterdump()), before)
        k.close()
        snapshot = self.kernel().get(topic)
        self.assertEqual(snapshot['state'], 'RESOLVED')
        self.assertEqual(len(snapshot['messages']), 1)
        self.assertEqual(len(snapshot['votes']), 2)

    def test_feedback_is_append_only_and_readable_after_resolution(self):
        k, topic = self.active(count=1)
        k.vote(topic, 'm0', True)
        self.assertTrue(callable(getattr(k, 'feedback', None)), 'feedback API missing')
        k.feedback(topic, 'm0', 'Synthetic practice observation', source='synthetic:trial-1')
        k.feedback(topic, 'm0', 'Synthetic follow-up', source='synthetic:trial-2')
        before = list(k.db.iterdump())
        for member, body, source in [('unknown', 'x', 's'), ('m0', '', 's'), ('m0', 'x', '')]:
            with self.assertRaises(ValueError):
                k.feedback(topic, member, body, source=source)
            self.assertEqual(list(k.db.iterdump()), before)
        k.close()
        snapshot = self.kernel().get(topic)
        self.assertEqual(snapshot['state'], 'RESOLVED')
        self.assertEqual([f['source'] for f in snapshot['feedback']], ['synthetic:trial-1', 'synthetic:trial-2'])
        self.assertEqual(snapshot['feedback'][0]['body'], 'Synthetic practice observation')

    def test_feature_tags_are_attributed_idempotent_and_persistent(self):
        k, topic = self.active(count=1)
        self.assertTrue(callable(getattr(k, 'tag', None)), 'tag API missing')
        k.tag(topic, 'm0', 'synthetic')
        k.tag(topic, 'm0', 'synthetic')
        k.vote(topic, 'm0', True)
        k.tag(topic, 'm0', 'needs-practice')
        before = list(k.db.iterdump())
        for member, label in [('unknown', 'x'), ('m0', ''), ('m0', None)]:
            with self.assertRaises(ValueError):
                k.tag(topic, member, label)
            self.assertEqual(list(k.db.iterdump()), before)
        k.close()
        snapshot = self.kernel().get(topic)
        self.assertEqual({t['label'] for t in snapshot['tags']}, {'synthetic', 'needs-practice'})
        self.assertEqual(len(snapshot['tags']), 2)
        self.assertTrue(all(t['member'] == 'm0' for t in snapshot['tags']))

    def test_meta_proposal_is_lowest_priority_data_only(self):
        import inspect
        k = self.kernel()
        self.assertIn('kind', inspect.signature(k.create).parameters)
        topic = k.create('Synthetic proposal: improve this open-source project', 0, kind='meta')
        snapshot = k.get(topic)
        self.assertEqual(snapshot['kind'], 'meta')
        self.assertEqual(snapshot['priority'], 'lowest')
        self.assertEqual(snapshot['state'], 'DRAFT')
        ordinary = k.create('Ordinary synthetic issue', 1)
        self.assertEqual(k.get(ordinary)['kind'], 'discussion')
        self.assertEqual(k.get(ordinary)['priority'], 'normal')
        before = list(k.db.iterdump())
        with self.assertRaises(ValueError):
            k.create('Not executable', kind='execute-code')
        self.assertEqual(list(k.db.iterdump()), before)

    def test_message_and_budget_rollback_on_sqlite_failure(self):
        import sqlite3
        k, topic = self.active(credits=1)
        k.db.execute("""CREATE TRIGGER reject_spend BEFORE UPDATE OF credits ON topics
                        BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
        k.db.commit()
        before = list(k.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            k.post(topic, 'm0', 'Must roll back inserted message', cost=1)
        self.assertEqual(list(k.db.iterdump()), before)
        self.assertEqual(k.get(topic)['state'], 'ACTIVE')
        self.assertEqual(k.get(topic)['messages'], [])

    def test_ballot_and_resolution_rollback_on_sqlite_failure(self):
        import sqlite3
        k, topic = self.active(count=1)
        k.db.execute("""CREATE TRIGGER reject_resolution BEFORE UPDATE OF state ON topics
                        BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
        k.db.commit()
        before = list(k.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            k.vote(topic, 'm0', True)
        self.assertEqual(list(k.db.iterdump()), before)
        self.assertEqual(k.get(topic)['votes'], [])

    def test_concurrent_writers_cannot_double_spend(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from open_roundtable import Kernel
        k, topic = self.active(credits=1)
        barrier = threading.Barrier(2)

        def spend():
            local = Kernel(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    return local.post(topic, 'm0', 'Concurrent synthetic post', cost=1)
                except ValueError:
                    return 'paused'
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(spend) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count('paused'), 1)
        snapshot = k.get(topic)
        self.assertEqual(snapshot['credits'], 0)
        self.assertEqual(snapshot['state'], 'PAUSED')
        self.assertEqual(len(snapshot['messages']), 1)

    def test_get_returns_one_consistent_snapshot_during_concurrent_vote(self):
        from open_roundtable import Kernel
        k, topic = self.active(count=1)
        k.db.execute('PRAGMA journal_mode=WAL')
        peer = Kernel(self.path)
        self.addCleanup(peer.close)
        fired = []

        def interleave(sql):
            if sql.startswith('SELECT * FROM members') and not fired:
                fired.append(True)
                peer.vote(topic, 'm0', True)

        k.db.set_trace_callback(interleave)
        try:
            snapshot = k.get(topic)
        finally:
            k.db.set_trace_callback(None)
        self.assertEqual(fired, [True])
        self.assertEqual(snapshot['state'], 'ACTIVE')
        self.assertEqual(snapshot['votes'], [])
        self.assertEqual(peer.get(topic)['state'], 'RESOLVED')


if __name__ == '__main__':
    unittest.main()
