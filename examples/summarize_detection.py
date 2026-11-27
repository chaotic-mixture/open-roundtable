"""Compare completed three-arm scheduling trials from a benchmark results CSV."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from examples.benchmark_consult import ARMS


def summarize(rows):
    trials = defaultdict(dict)
    for row in rows:
        key = int(row['shifts']), int(row['seed'])
        arm = row['arm']
        if arm not in ARMS or arm in trials[key]:
            raise ValueError(f'Unexpected or duplicate arm {arm!r} for {key}')
        trials[key][arm] = row

    comparisons = []
    incomplete = []
    for (shifts, seed), arms in sorted(trials.items()):
        missing = sorted(ARMS.keys() - arms.keys())
        if missing:
            incomplete.append(dict(shifts=shifts, seed=seed, missing=missing))
            continue
        for baseline in ('solo', 'tool'):
            delta = {}
            for field in ('missed', 'extra', 'calls', 'credits', 'input_tokens', 'output_tokens'):
                delta[field] = int(arms['table'][field]) - int(arms[baseline][field])
            delta['seconds'] = float(arms['table']['seconds']) - float(arms[baseline]['seconds'])
            comparisons.append(dict(shifts=shifts, seed=seed, baseline=baseline, table_minus_baseline=delta))
    return dict(comparisons=comparisons, incomplete=incomplete)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    with args.results.open(encoding='utf-8-sig', newline='') as source:
        print(json.dumps(summarize(csv.DictReader(source)), indent=2))
