"""Compare provider discovery on nine public rosters, optionally dispatching each match."""
import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.overnight_score import expected as expected_findings
from examples.peer_node import find_capability, ledger, offer, send, set_permission, validate_receipt
from examples.two_node_demo import start
from open_roundtable.data import initialize, ingest, share_public


# Request scopes and independently labeled qualified providers, repeated at each size.
SCOPES = [('PV', {'overlap', 'full'}), ('RL', {'fatigue', 'full'}), ('PVRL', {'full'})]


def score_request(spec):
    truth = expected_findings(spec['shifts'])
    return {key: sorted(truth[key]) for key in spec['rules']}


def dispatch_candidates(root, db, topic, shifts, rules, matches):
    spec = dict(operation='schedule-audit-v1', rules=list(rules), shifts=shifts)
    ingest(root, 'public', [dict(source=topic, body=json.dumps(spec))])
    share_public(root, topic, True)
    expected = score_request(spec)
    rejected_providers = []
    verified_providers = []
    for candidate in matches:
        try:
            receipt = send(root, db, candidate['node'], topic,
                           offer_ref=[candidate['source'], candidate['sha256']])
        except ValueError as error:
            if str(error) != 'selected offer does not cover the requested audit rules':
                raise
            rejected_providers.append(candidate['node'])
            continue
        if receipt['outcome']['findings'] != expected:
            raise ValueError('audit differs from independent scorer')
        verified_providers.append(candidate['node'])
    return dict(attempts=len(matches), rejected=len(rejected_providers), verified_receipts=len(verified_providers),
                rejected_providers=sorted(rejected_providers), verified_providers=sorted(verified_providers))


def reconcile_dispatch(db, topic, verified_providers):
    bookings = db.execute("""SELECT id,request,result,credits FROM jobs
                             WHERE json_extract(request,'$.source')=?""", (topic,))
    providers = []
    credits = 0
    for job_id, raw_request, raw_result, booked in bookings:
        request = json.loads(raw_request)
        if job_id != request['id'] or raw_result is None:
            raise ValueError('missing or mismatched dispatch receipt')
        receipt = json.loads(raw_result)
        validate_receipt(request, receipt)
        if receipt['outcome']['findings'] != score_request(request['spec']):
            raise ValueError('stored audit findings differ from independent scorer')
        if booked != receipt['credits']:
            raise ValueError('receipt credits differ from requester ledger')
        providers.append(request['recipient'])
        credits += booked
    if sorted(providers) != sorted(verified_providers):
        raise ValueError('booked providers differ from verified dispatches')
    return credits


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
                    credits = reconcile_dispatch(db, topic, metrics['verified_providers'])
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
