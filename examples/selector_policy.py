"""Owner-operated selector for the three scheduling briefs in the meta experiment."""
import argparse
from contextlib import closing
import json
from pathlib import Path

from examples.meta_consult import design, evaluate, grade, prepare, rule_choice
from examples.overnight_score import expected
from examples.repair_benchmark import schedule
from open_roundtable.core import Kernel


def verify_experiment(snapshot):
    proposal = design()
    votes = {v['member']:v['agree'] for v in snapshot['votes']}
    if (snapshot['state'] != 'RESOLVED' or snapshot['current_version'] != 1
            or json.loads(snapshot['proposals'][0]['body']) != proposal
            or votes != {'proposer':1, 'evaluator':1}):
        raise ValueError('expected the approved priority-rule experiment')
    rows = [evaluate(seed, i) for i, seed in enumerate(proposal['seeds'])]
    passed, gains = grade(rows)
    evidence = [(m['member'], json.loads(m['body'])) for m in snapshot['messages']]
    feedback = dict(passed=passed, gains=gains)
    if (not passed or evidence != [('proposer', proposal), ('evaluator', rows)]
            or not any(f['member']=='evaluator' and f['source']=='independent schedule and preference checks'
                       and json.loads(f['body']) == feedback for f in snapshot['feedback'])):
        raise ValueError('recorded evidence differs from the repeated experiment')


def policy(kernel, topic, action=None):
    with kernel.db as db:
        db.execute('BEGIN IMMEDIATE' if action else 'BEGIN')
        snapshot = kernel.get(topic)
        if snapshot['kind'] != 'meta':
            raise ValueError('expected a meta-topic')
        db.execute('''CREATE TABLE IF NOT EXISTS selector_decisions (
            id INTEGER PRIMARY KEY, topic TEXT NOT NULL REFERENCES topics(id),
            version INTEGER NOT NULL, enabled INTEGER NOT NULL)''')
        last = db.execute('SELECT version,enabled FROM selector_decisions WHERE topic=? ORDER BY id DESC LIMIT 1',
                          (topic,)).fetchone()
        version = snapshot['current_version']
        enabled = bool(last and last[0] == version and last[1])
        if action:
            if action not in ('enable','withdraw'):
                raise ValueError('unknown policy action')
            desired = action == 'enable'
            if desired:
                verify_experiment(snapshot)
            if desired != enabled:
                db.execute('INSERT INTO selector_decisions(topic,version,enabled) VALUES (?,?,?)',
                           (topic,version,desired))
            enabled = desired
        return 'rules' if enabled else 'default'


def select(kernel, topic, profile, shifts):
    mode = policy(kernel, topic)
    rows, plans = prepare(shifts)
    index = rule_choice(profile, rows, plans) if mode=='rules' else 0
    result = schedule(rows, plans[index])
    if any(expected(result).values()):
        raise ValueError('selected schedule failed independent verification')
    return dict(selector=mode, candidate=index, schedule=result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('topic')
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('enable','withdraw','status'):
        commands.add_parser(name)
    choose = commands.add_parser('select')
    choose.add_argument('profile', type=int, choices=range(3))
    choose.add_argument('shifts', nargs='+')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('provide an existing experiment database')
    with closing(Kernel(args.database)) as kernel:
        result = (select(kernel,args.topic,args.profile,args.shifts) if args.action=='select' else
                  policy(kernel,args.topic,None if args.action=='status' else args.action))
        print(json.dumps(result))
