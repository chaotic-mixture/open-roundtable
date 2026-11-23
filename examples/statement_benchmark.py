"""Compare local statement pagination against the pre-index implementation."""
from contextlib import closing
import json
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter

from examples import peer_node


def pages(reader, root, db):
    entries = []
    after = ''
    while True:
        batch = reader(root, db, 'B', after=after, limit=256)['entries']
        entries.extend(batch)
        if len(batch) < 256:
            return entries
        after = batch[-1]['job']


def run():
    repo = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(['git', 'show', '23d8fbe:examples/peer_node.py'], cwd=repo)
    previous = {'__name__': 'baseline'}
    exec(compile(source, 'baseline_peer_node.py', 'exec'), previous)
    readers = {'scan': previous['partner_statement'], 'index': peer_node.partner_statement}
    for stride in (1, 10, 100):
        with TemporaryDirectory() as tmp, closing(peer_node.ledger(Path(tmp))) as db:
            root = Path(tmp)
            (root/'node.json').write_text('{"name":"A"}')
            for i in range(10000):
                partner = 'C' if i % stride else 'B'
                sender, recipient = ('A', partner) if (i // stride) % 2 else (partner, 'A')
                request = dict(sender=sender, recipient=recipient, source='synthetic', spec=dict(case=i))
                key = peer_node.digest(request)
                db.execute('INSERT INTO jobs(id,request) VALUES (?,?)',
                           (key, peer_node.packed(dict(request, id=key)).decode()))
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
            print(json.dumps(dict(jobs=10000, matched=len(expected),
                                  median_ms={k: round(median(v), 2) for k, v in timings.items()})))


if __name__ == '__main__':
    run()
