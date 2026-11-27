"""Compare completed three-arm scheduling trials from a benchmark results CSV."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from examples.benchmark_consult import ARMS
from examples.auto_consult import RULES


RULE_FIELDS = {f'{rule}_{metric}' for rule in RULES for metric in ('missed', 'extra')}


def validate_findings(row, key, arm):
    present = RULE_FIELDS.intersection(row)
    if not present:
        return  # Older CSVs can contain just the aggregate counts.
    missing = RULE_FIELDS - present
    if missing:
        raise ValueError(f'Missing rule columns for {key}, {arm}: {sorted(missing)}')
    for metric in ('missed', 'extra'):
        fields = [f'{rule}_{metric}' for rule in RULES]
        for field in [metric, *fields]:
            try:
                value = int(row[field])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f'Invalid {field} count for {key}, {arm}') from None
            if value < 0:
                raise ValueError(f'Negative {field} count for {key}, {arm}')
        if sum(int(row[field]) for field in fields) != int(row[metric]):
            raise ValueError(f'Rule counts disagree with {metric} total for {key}, {arm}')


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
        validate_findings(row, key, arm)
        trials[key][arm] = row

    comparisons = []
    incomplete = []
    decisions = []
    for (shifts, seed), arms in sorted(trials.items()):
        table = arms.get('table', {})
        if 'topic_state' in table:
            votes = [table.get(field) for field in ('model_vote', 'expert_vote')]
            if any(vote not in ('0', '1', 0, 1) for vote in votes):
                raise ValueError(f'Invalid panel votes for roster {seed}')
            model_vote, expert_vote = map(int, votes)
            expected = 'RESOLVED' if model_vote and expert_vote else 'PAUSED'
            if table['topic_state'] != expected:
                raise ValueError(f'Panel state disagrees with votes for roster {seed}')
            decisions.append(dict(seed=seed, shifts=shifts, state=expected,
                                  model_vote=model_vote, expert_vote=expert_vote,
                                  missed=int(table['missed']), extra=int(table['extra'])))
        missing = sorted(ARMS.keys() - arms.keys())
        if missing:
            incomplete.append(dict(shifts=shifts, seed=seed, missing=missing))
            continue
        for baseline in ('solo', 'tool'):
            delta = {}
            for field in ('missed', 'extra', 'calls', 'credits', 'input_tokens', 'output_tokens'):
                delta[field] = int(arms['table'][field]) - int(arms[baseline][field])
            delta['seconds'] = float(arms['table']['seconds']) - float(arms[baseline]['seconds'])
            if RULE_FIELDS.issubset(arms['table']) and RULE_FIELDS.issubset(arms[baseline]):
                delta['by_rule'] = {}
                for rule in RULES:
                    changes = {}
                    for metric in ('missed', 'extra'):
                        field = f'{rule}_{metric}'
                        changes[metric] = int(arms['table'][field]) - int(arms[baseline][field])
                    delta['by_rule'][rule] = changes
            comparisons.append(dict(shifts=shifts, seed=seed, baseline=baseline, table_minus_baseline=delta))
    report = dict(comparisons=comparisons, incomplete=incomplete)
    if decisions:
        report['panel_decisions'] = decisions
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--design', type=Path, help='Benchmark design.json used to identify unstarted rosters')
    args = parser.parse_args()
    with args.results.open(encoding='utf-8-sig', newline='') as source:
        design = json.loads(args.design.read_text(encoding='utf-8')) if args.design else None
        print(json.dumps(summarize(csv.DictReader(source), design), indent=2))
