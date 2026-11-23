"""Compare public audit discovery on nine rosters, without dispatching consultations."""
from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.peer_node import find_capability, ledger, offer
from examples.two_node_demo import start
from open_roundtable.data import initialize, share_public


# Request scopes and independently labeled qualified providers, repeated at each size.
SCOPES = [('PV', {'overlap', 'full'}), ('RL', {'fatigue', 'full'}), ('PVRL', {'full'})]


def run():
    lines = Path(__file__).with_name('overnight_inputs.txt').read_text().splitlines()
    tables = [line.split() for line in lines if line[:1].isdigit()]
    with TemporaryDirectory() as folder, ExitStack() as stack:
        root = Path(folder)
        (root/'node.json').write_text(json.dumps({'name': 'requester'}))
        db = stack.enter_context(closing(ledger(root)))
        for name, rules in [('overlap', 'PV'), ('fatigue', 'RL'), ('full', 'PVRL'), ('legacy', '')]:
            peer = root/name
            initialize(peer)
            (peer/'node.json').write_text(json.dumps({'name': name}))
            source = f'https://example.org/{name}/audit'
            offer(peer, source, 'audit', f'{name} roster audit', rules=list(rules))
            share_public(peer, source, True)
            _, port = start(stack, peer, 0)
            db.execute('INSERT INTO peers VALUES (?, ?)', (name, f'http://127.0.0.1:{port}'))
        db.commit()
        for index, (table, *shifts) in enumerate(tables):
            rules, expected = SCOPES[index % len(SCOPES)]
            for mode, required in [('tag', ''), ('rules', rules)]:
                before = db.execute('SELECT count(*) FROM traffic WHERE direction="send"').fetchone()[0]
                result = find_capability(root, db, 'audit', required_rules=required)
                if result['unavailable']:
                    raise RuntimeError(result['unavailable'])
                selected = {match['node'] for match in result['matches']}
                after = db.execute('SELECT count(*) FROM traffic WHERE direction="send"').fetchone()[0]
                credits, = db.execute('SELECT coalesce(sum(credits),0) FROM jobs').fetchone()
                yield dict(table=table, shifts=len(shifts), required=rules, mode=mode,
                           missed=len(expected-selected), extra=len(selected-expected),
                           candidates=sorted(selected), directory_requests=after-before, credits=credits)


if __name__ == '__main__':
    for row in run():
        print(json.dumps(row))
