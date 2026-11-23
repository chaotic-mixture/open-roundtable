"""Compare flat and nested execution on the nine seeded overnight schedules."""
import argparse
import csv
from contextlib import closing
from pathlib import Path
import sqlite3
from time import perf_counter

from examples.generate_overnight import generate
from examples.nested_consult import run
from examples.overnight_score import expected


def _round(directory, number):
    rows = []
    for case, size in enumerate([7]*3 + [11]*3 + [22]*3, 1):
        shifts = generate(case, size)
        truth = expected(shifts)
        # Alternate order to distribute startup and filesystem cache effects.
        nested_first = (case + number) % 2 == 0
        for nested in (nested_first, not nested_first):
            mode = 'nested' if nested else 'direct'
            path = directory / f'{case}-{mode}.sqlite3'
            start = perf_counter()
            result = run(path, shifts, nested=nested)
            elapsed = perf_counter() - start
            findings = result['findings']
            with closing(sqlite3.connect(path)) as db:
                credits = db.execute('SELECT sum(cost) FROM messages WHERE topic=?',
                                     (result['parent'],)).fetchone()[0]
            if credits + result['remaining'] != 4:
                raise ValueError('root budget does not reconcile')
            rows.append(dict(case=case, shifts=size, mode=mode, state=result['state'],
                             missed=sum(len(items-set(findings[r])) for r,items in truth.items()),
                             extra=sum(len(set(findings[r])-items) for r,items in truth.items()),
                             credits=credits, elapsed_ms=round(1000*elapsed, 2)))
    return rows


def benchmark(directory, repeats=1):
    if repeats < 1:
        raise ValueError('repeats must be positive')
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    for number in range(1, repeats + 1):
        target = directory
        if repeats > 1:
            target = directory / f'round-{number}'
            target.mkdir()
        for row in _round(target, number):
            if repeats > 1:
                row['round'] = number
            rows.append(row)
    with (directory/'results.csv').open('w', newline='', encoding='utf8') as output:
        writer = csv.DictWriter(output, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    for row in benchmark(args.directory, args.repeats):
        print(row)
