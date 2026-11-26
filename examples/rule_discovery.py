"""Compare provider discovery on nine public rosters, optionally dispatching each match."""
import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.overnight_score import expected as expected_findings
from examples.peer_node import find_capability, ledger, offer, send, set_permission
from examples.two_node_demo import start
from open_roundtable.data import initialize, ingest, share_public


# Request scopes and independently labeled qualified providers, repeated at each size.
SCOPES = [('PV', {'overlap', 'full'}), ('RL', {'fatigue', 'full'}), ('PVRL', {'full'})]


def dispatch_candidates(root, db, topic, shifts, rules, matches):
    spec = dict(operation='schedule-audit-v1', rules=list(rules), shifts=shifts)
    ingest(root, 'public', [dict(source=topic, body=json.dumps(spec))])
    share_public(root, topic, True)
    truth = expected_findings(shifts)
    expected = {key: sorted(truth[key]) for key in rules}
    rejected = completed = 0
    for candidate in matches:
        try:
            receipt = send(root, db, candidate['node'], topic,
                           offer_ref=[candidate['source'], candidate['sha256']])
        except ValueError as error:
            if str(error) != 'selected offer does not cover the requested audit rules':
                raise
            rejected += 1
            continue
        if receipt['outcome']['findings'] != expected:
            raise ValueError('audit differs from independent scorer')
        completed += 1
    return dict(attempts=len(matches), rejected=rejected, verified_receipts=completed)


def run(dispatch=False):
    lines = Path(__file__).with_name('overnight_inputs.txt').read_text().splitlines()
    tables = [line.split() for line in lines if line[:1].isdigit()]
    with TemporaryDirectory() as folder, ExitStack() as stack:
        root = Path(folder)/'requester'
        initialize(root)
        (root/'node.json').write_text(json.dumps({'name': 'requester'}))
        db = stack.enter_context(closing(ledger(root)))
        if dispatch:
            _, requester_port = start(stack, root, 0)
        for name, rules in [('overlap', 'PV'), ('fatigue', 'RL'), ('full', 'PVRL'), ('legacy', '')]:
            peer = Path(folder)/name
            initialize(peer)
            (peer/'node.json').write_text(json.dumps({'name': name}))
            source = f'https://example.org/{name}/audit'
            offer(peer, source, 'audit', f'{name} roster audit', rules=list(rules))
            share_public(peer, source, True)
            if dispatch:
                with closing(ledger(peer)) as provider:
                    provider.execute('INSERT INTO peers VALUES (?, ?)', ('requester', f'http://127.0.0.1:{requester_port}'))
                    provider.commit()
                    set_permission(provider, 'requester', True, 2 * len(tables))
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
                metrics = {}
                credits = 0
                if dispatch:
                    topic = f'https://example.org/{mode}/table/{table}'
                    metrics = dispatch_candidates(root, db, topic, shifts, rules, result['matches'])
                    end, = db.execute('SELECT count(*) FROM traffic WHERE direction="send"').fetchone()
                    metrics['dispatch_requests'] = end - after
                    booked, credits = db.execute("""SELECT count(result),coalesce(sum(credits),0) FROM jobs
                        WHERE json_extract(request,'$.source')=?""", (topic,)).fetchone()
                    if booked != metrics['verified_receipts']:
                        raise ValueError('receipt count differs from requester ledger')
                yield dict(table=table, shifts=len(shifts), required=rules, mode=mode,
                           missed=len(expected-selected), extra=len(selected-expected),
                           expected_providers=sorted(expected), missed_providers=sorted(expected-selected),
                           extra_providers=sorted(selected-expected),
                           candidates=sorted(selected), directory_requests=after-before, credits=credits, **metrics)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch', action='store_true', help='send each candidate a public synthetic audit')
    for row in run(parser.parse_args().dispatch):
        print(json.dumps(row))
