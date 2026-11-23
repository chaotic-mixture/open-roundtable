from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples.overnight_score import expected
from examples.peer_node import find_capability, ledger, offer, send, set_permission
from examples.two_node_demo import start
from open_roundtable.data import initialize, ingest, share_public


class PeerAuditTests(unittest.TestCase):
    def test_nine_public_rosters_and_replay(self):
        fixture = Path(__file__).resolve().parents[1] / 'examples' / 'overnight_inputs.txt'
        tables = [line.split() for line in fixture.read_text().splitlines() if line[:1].isdigit()]
        self.assertEqual(len(tables), 9)
        with TemporaryDirectory() as folder, ExitStack() as stack:
            a, b = (Path(folder)/name for name in ('A', 'B'))
            for node in (a, b):
                initialize(node)
                (node/'node.json').write_text(json.dumps(dict(name=node.name)))
            source = 'https://example.org/service'
            offer(b, source, 'audit', 'Weekly roster checks', rules=list('PVRL'))
            share_public(b, source, True)
            _, port_a = start(stack, a, 0)
            _, port_b = start(stack, b, 0)
            sender = stack.enter_context(closing(ledger(a)))
            receiver = stack.enter_context(closing(ledger(b)))
            for db, name, port in ((sender, 'B', port_b), (receiver, 'A', port_a)):
                db.execute('INSERT INTO peers VALUES (?,?)', (name, f'http://127.0.0.1:{port}'))
                db.commit()
            set_permission(receiver, 'A', True, len(tables))
            for index, (table, *shifts) in enumerate(tables):
                scope = ('PV', 'RL', 'PVRL')[index % 3]
                spec = dict(operation='schedule-audit-v1', rules=list(scope), shifts=shifts)
                topic = 'https://example.org/table/' + table
                ingest(a, 'public', [dict(source=topic, body=json.dumps(spec))])
                share_public(a, topic, True)
                candidate, = find_capability(a, sender, 'audit', required_rules=scope)['matches']
                ref = [candidate['source'], candidate['sha256']]
                receipt = send(a, sender, 'B', topic, offer_ref=ref)
                truth = expected(shifts)
                self.assertEqual(receipt['outcome'], dict(state='AUDITED', findings={k: sorted(truth[k]) for k in scope}))
                self.assertEqual(receipt['credits'], 0)
            self.assertEqual(receipt, send(a, sender, 'B', topic, offer_ref=ref))
            self.assertEqual(receiver.execute('SELECT remaining FROM grant_limits').fetchone(), (0,))
            for db in (sender, receiver):
                self.assertEqual(db.execute('SELECT count(result), sum(credits) FROM jobs').fetchone(), (9, 0))
