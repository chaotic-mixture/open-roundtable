"""Evaluate a proposed selector change as a local meta-topic."""
import argparse
from contextlib import closing
import json
from pathlib import Path

from examples.generate_overnight import generate
from examples.overnight_score import expected
from examples.preference_benchmark import facts, rule_key
from examples.preference_rubric import assess
from examples.repair_benchmark import candidates, schedule
from open_roundtable.core import Kernel


def prepare(tokens):
    rows = []
    for token in tokens:
        key, span = token.split(':')
        start, end = map(int, span.split('-'))
        if len(key) != 3 or not 0 <= start < end <= 168:
            raise ValueError('expected a three-character shift key and a positive interval within one week')
        rows.append((key, start, end-start))
    if len({r[0][0] for r in rows}) != len(rows):
        raise ValueError('shift identifiers must be unique')
    if len(rows) < 3 or [r[0][0] for r in rows[:3]] != list('ABC'):
        raise ValueError('the three supported briefs require leading shifts A, B and C')
    plans = candidates(rows)
    if not plans:
        raise ValueError('no feasible candidates')
    return rows, plans


def rule_choice(profile, rows, plans):
    def rank(i):
        plan = plans[i]
        moved = sum(hour != row[1] for hour,row in zip(plan, rows))
        return rule_key(profile, facts(plan, rows), moved, i)
    return max(range(len(plans)), key=rank)


def evaluate(seed, index):
    profile = index % 3
    size = (7, 11, 22)[index // 3]
    rows, plans = prepare(generate(seed, size))
    original = [r[1] for r in rows]
    durations = [r[2] for r in rows]
    record = dict(seed=seed, profile=profile, candidates=len(plans))
    for name, i in [('default', 0), ('rules', rule_choice(profile, rows, plans))]:
        plan = plans[i]
        passed = not any(expected(schedule(rows, plan)).values())
        score = sum(w*b for w,b in zip((4,2,1), assess(profile, original, durations, plan)))
        record[name] = dict(candidate=i, hard_pass=passed, preference=score,
                            changed=sum(a != b for a,b in zip(original,plan)))
    return record


def run(path):
    path.touch(exist_ok=False)
    with closing(Kernel(path)) as k:
        topic = k.create('Compare verified candidate selectors', 2, kind='meta')
        k.join(topic, 'proposer', kind='expert')
        k.join(topic, 'evaluator', kind='expert')
        k.start(topic)
        proposal = dict(baseline='first feasible candidate', candidate='priority rules',
                        seeds=list(range(601,610)), weights=[4,2,1],
                        gate='both feasible on every case, no preference loss, at least one gain')
        version = k.propose(topic, json.dumps(proposal), previous_version=0)
        k.post(topic, 'proposer', json.dumps(proposal))
        rows = [evaluate(seed, i) for i,seed in enumerate(proposal['seeds'])]
        gains = [r['rules']['preference']-r['default']['preference'] for r in rows]
        passed = all(r[arm]['hard_pass'] for r in rows for arm in ('default','rules')) and min(gains)>=0 and max(gains)>0
        k.post(topic, 'evaluator', json.dumps(rows))
        k.feedback(topic, 'evaluator', json.dumps(dict(passed=passed, gains=gains)), source='independent schedule and preference checks')
        k.vote(topic, 'proposer', True, version=version)
        k.vote(topic, 'evaluator', passed, version=version)
        return dict(topic=topic, state=k.get(topic)['state'], passed=passed, gains=gains, results=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    print(json.dumps(run(parser.parse_args().database)))
