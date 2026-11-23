"""Compare direct and delegated repair consultations on the nine seeded rosters."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from time import perf_counter

from examples.compare_panel_receipts import compare
from examples.generate_overnight import generate
from examples.user_consult import consult


def run(directory, model, *, verify_only=False, reverse_order=False):
    if not verify_only:
        directory.mkdir(parents=True, exist_ok=False)
    for index, size in enumerate((7, 7, 7, 11, 11, 11, 22, 22, 22)):
        seed = 501 + index
        shifts = []
        for token in generate(seed, size):
            (shift, worker, room), interval = token.split(':')
            start, end = map(int, interval.split('-'))
            shifts.append(dict(id=shift, worker=worker, room=room, start=start, end=end))
        spec = dict(title=f'Schedule {seed}', shifts=shifts,
                    preference='First preserve the original start of A; next prefer B starting before noon.',
                    criteria=[dict(shift='A', field='delta', min=0, max=0),
                              dict(shift='B', field='hour', min=0, max=11)])
        source = directory/f'{seed}.json'
        paths = [directory/f'{seed}-{mode}.sqlite3' for mode in ('direct', 'nested')]
        if verify_only:
            for path in paths:
                if not path.is_file():
                    raise FileNotFoundError(f'missing consultation ledger: {path}')
        else:
            source.write_text(json.dumps(spec), encoding='utf8')
            nested_first = bool(index % 2) != reverse_order
            for position, nested in enumerate((nested_first, not nested_first), start=1):
                path = paths[int(nested)]
                start = perf_counter()
                consult(source, path, model, nested=nested)
                seconds = perf_counter() - start
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('CREATE TABLE benchmark_timing (position INTEGER, seconds REAL)')
                    db.execute('INSERT INTO benchmark_timing VALUES (?,?)', (position, seconds))
        yield dict(seed=seed, shifts=size, **compare(*paths, expected_input=spec))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--model', default='qwen3.5:9b')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify-only', action='store_true', help='read existing ledgers without calling a model')
    mode.add_argument('--reverse-order', action='store_true', help='swap which consultation runs first on each roster')
    args = parser.parse_args()
    for result in run(args.directory, args.model, verify_only=args.verify_only, reverse_order=args.reverse_order):
        print(json.dumps(result), flush=True)
