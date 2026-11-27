"""Compare completed three-arm scheduling trials from a benchmark results CSV."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from examples.benchmark_consult import ARMS
from examples.auto_consult import RULES


def summarize(rows, design=None):
    trials = defaultdict(dict)
    if design is not None:
        seed = design['first_seed']
        for size in design['sizes']:
            for _ in range(design['repetitions']):
                trials[size, seed] = {}
                seed += 1
    for row in rows:
        key = int(row['shifts']), int(row['seed'])
        if design is not None and key not in trials:
            raise ValueError(f'Roster outside benchmark design: {key}')
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
            rule_fields = [f'{rule}_{metric}' for rule in RULES for metric in ('missed', 'extra')]
            if all(field in arms['table'] and field in arms[baseline] for field in rule_fields):
                delta['by_rule'] = {}
                for rule in RULES:
                    changes = {}
                    for metric in ('missed', 'extra'):
                        field = f'{rule}_{metric}'
                        changes[metric] = int(arms['table'][field]) - int(arms[baseline][field])
                    delta['by_rule'][rule] = changes
            comparisons.append(dict(shifts=shifts, seed=seed, baseline=baseline, table_minus_baseline=delta))
    return dict(comparisons=comparisons, incomplete=incomplete)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--design', type=Path, help='Benchmark design.json used to identify unstarted rosters')
    args = parser.parse_args()
    with args.results.open(encoding='utf-8-sig', newline='') as source:
        design = json.loads(args.design.read_text(encoding='utf-8')) if args.design else None
        print(json.dumps(summarize(csv.DictReader(source), design), indent=2))
