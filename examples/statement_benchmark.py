"""Compare local statement pagination against the pre-index implementation."""
from contextlib import closing
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from examples import peer_node


# Scan baseline from commit 23d8fbe.
def scan_statement(root, db, partner, *, after='', limit=None):
    node = json.loads((root/'node.json').read_text())['name']
    if node == partner or not partner.strip():
        raise ValueError('choose a distinct partner')
    entries = []
    for key, raw, saved, credits in db.execute('SELECT id,request,result,credits FROM jobs WHERE id>? ORDER BY id',(after,)):
        request = json.loads(raw)
        if {request['sender'], request['recipient']} != {node,partner}:
            continue
        if request['id'] != key or peer_node.digest({k:v for k,v in request.items() if k!='id'}) != key:
            raise ValueError('job identity mismatch')
        receipt_hash = None
        if saved is not None:
            receipt = json.loads(saved)
            peer_node.validate_receipt(request,receipt)
            if credits != receipt['credits']:
                raise ValueError('invalid service charge')
            receipt_hash = peer_node.digest(receipt)
        elif credits:
            raise ValueError('pending job has a posted charge')
        entries.append(dict(job=key, provider=request['recipient'], credits=credits, receipt=receipt_hash))
        if limit is not None and len(entries) >= limit:
            break
    return dict(format=1, node=node, partner=partner, entries=entries)


def pages(reader, root, db):
    entries = []
    after = ''
    while True:
        batch = reader(root, db, 'B', after=after, limit=256)['entries']
        entries.extend(batch)
        if len(batch) < 256:
            return entries
        after = batch[-1]['job']


def run(settled=False):
    readers = {'scan': scan_statement, 'index': peer_node.partner_statement}
    for stride in (1, 10, 100):
        with TemporaryDirectory() as tmp, closing(peer_node.ledger(Path(tmp))) as db:
            root = Path(tmp)
            (root/'node.json').write_text('{"name":"A"}')
            for i in range(10000):
                partner = 'C' if i % stride else 'B'
                sender, recipient = ('A', partner) if (i // stride) % 2 else (partner, 'A')
                request = dict(sender=sender, recipient=recipient, source='synthetic', spec=dict(case=i))
                key = peer_node.digest(request)
                receipt = None
                if settled:
                    receipt = peer_node.packed(dict(id=key, sender=recipient, recipient=sender,
                                                    credits=2, outcome=dict(state='RESOLVED'))).decode()
                db.execute('INSERT INTO jobs(id,request,result,credits) VALUES (?,?,?,?)',
                           (key, peer_node.packed(dict(request, id=key)).decode(), receipt, 2 if settled else 0))
            db.commit()
            expected = pages(readers['scan'], root, db)
            if len(expected) != 10000 // stride:
                raise ValueError('incorrect baseline count')
            timings = {name: [] for name in readers}
            for trial in range(6):
                for name in (list(readers) if trial % 2 == 0 else list(reversed(readers))):
                    start = perf_counter()
                    actual = pages(readers[name], root, db)
                    elapsed = perf_counter() - start
                    if actual != expected:
                        raise ValueError('statement contents differ')
                    if trial:
                        timings[name].append(elapsed * 1000)
            print(json.dumps(dict(jobs=10000, settled=settled, matched=len(expected),
                                  median_ms={k: round(median(v), 2) for k, v in timings.items()})))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settled', action='store_true', help='include and validate a receipt for every job')
    run(parser.parse_args().settled)
