"""Score recorded batch calls using hourly occupancy, including across midnight."""
import json
import sys
from contextlib import closing
from itertools import combinations, groupby, pairwise
from pathlib import Path

from open_roundtable.core import Kernel


def expected(tokens):
    answer = {rule: set() for rule in 'PVRL'}
    occupied = {}
    shifts = []
    for token in tokens:
        (name, worker, room), span = token.split(':')
        start, end = map(int, span.split('-'))
        hours = set(range(start, end))
        occupied.setdefault(worker, set()).update(hours)
        shifts.append((name, worker, room, hours))
    for left, right in combinations(shifts, 2):
        if left[3] & right[3]:
            for field, rule in [(1, 'P'), (2, 'V')]:
                if left[field] == right[field]:
                    answer[rule].add(''.join(sorted((left[0], right[0]))))
    for worker, hours in occupied.items():
        blocks = []
        for _, group in groupby(enumerate(sorted(hours)), lambda item: item[1] - item[0]):
            run = [hour for _, hour in group]
            blocks.append((run[0], run[-1] + 1))
        for start, end in blocks:
            if end - start > 8:
                answer['L'].add(f'{worker}@{start}-{end}')
        for (_, end), (start, _) in pairwise(blocks):
            if start - end < 8:
                answer['R'].add(f'{worker}@{end}-{start}')
    return answer


def score(db):
    lines = Path(__file__).with_name('overnight_inputs.txt').read_text().splitlines()
    truth = {line.split()[0]: expected(line.split()[1:]) for line in lines if line[:1].isdigit()}
    for topic, title in db.db.execute('SELECT id,title FROM topics ORDER BY rowid'):
        state = db.get(topic)
        messages = state['messages']
        if len(messages) != 2:
            raise ValueError(f'incomplete {title}')
        print(title, 'calls', len(messages), 'debits', [m['cost'] for m in messages],
              'remaining', state['credits'])
        solo = title.endswith('/solo')
        predictions = {}
        cases = [line.split()[0] for line in lines if line[:1].isdigit()
                 and len(line.split()) - 1 == int(title.split('/')[0])]
        for msg in messages[-1:] if solo else messages:
            rules = 'PVRL' if solo else {'pairs':'PV', 'hours':'RL'}[msg['member']]
            seen = set()
            for line in msg['body'].splitlines():
                case, *fields = [part.strip() for part in line.split('|')]
                if case in seen:
                    raise ValueError('duplicate case')
                seen.add(case)
                for rule, field in zip(rules, fields, strict=True):
                    predictions[case, rule] = set() if field == '-' else set(field.split())
            if seen != set(cases):
                raise ValueError('missing or unexpected case')
        for case in cases:
            result = {}
            for rule, actual in truth[case].items():
                predicted = predictions[case, rule]
                result[rule] = [len(actual), sorted(actual-predicted), sorted(predicted-actual)]
            print(case, json.dumps(result))


if __name__ == '__main__':
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit('missing ledger')
    with closing(Kernel(path)) as kernel:
        score(kernel)
