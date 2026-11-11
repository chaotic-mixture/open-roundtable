"""Run the schedule expert and record externally dispatched model replies."""
import argparse
from contextlib import closing
from pathlib import Path

from examples.generate_overnight import generate
from examples.overnight_score import expected
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


CASES = {i: generate(30+i, size) for i, size in enumerate((7, 11, 22), 1)}
MEMBERS = {'model': ['model'], 'tool': ['expert'], 'roundtable': ['expert', 'model']}


def expert_reply():
    lines = []
    for case, shifts in CASES.items():
        findings = check(shifts)
        fields = [' '.join(sorted(findings[r])) or '-' for r in 'PVRL']
        lines.append(' | '.join([str(case), *fields]))
    return '\n'.join(lines)


def parse(reply):
    cases = {}
    for line in reply.strip().splitlines():
        case, *fields = line.split('|')
        case = int(case)
        if case in cases:
            raise ValueError('duplicate case')
        cases[case] = {r: set() if f.strip() == '-' else set(f.split())
                       for r, f in zip('PVRL', fields, strict=True)}
    if cases.keys() != CASES.keys():
        raise ValueError('unexpected cases')
    return cases


def run(kernel, args):
    if args.action == 'init':
        for arm, members in MEMBERS.items():
            topic = kernel.create(arm, credits=2)
            for member in members:
                kernel.join(topic, member, kind=member, source=(
                    'open_roundtable.schedule_expert.check' if member == 'expert' else 'external:model'))
            kernel.start(topic)
            if 'expert' in members:
                reply = expert_reply()
                kernel.post(topic, 'expert', reply, cost=1)
                if arm == 'roundtable':
                    print(reply)
        return
    for topic, arm in kernel.db.execute('SELECT id,title FROM topics ORDER BY rowid').fetchall():
        state = kernel.get(topic)
        messages = state['messages']
        if args.action == 'record' and arm == args.arm:
            reply = args.reply.read_text(encoding='utf-8-sig').strip()
            parse(reply)
            if [m['member'] for m in messages] != MEMBERS[arm][:-1]:
                raise ValueError('unexpected prior messages')
            kernel.post(topic, 'model', reply, cost=1)
        if args.action == 'score':
            if [m['member'] for m in messages] != MEMBERS[arm]:
                raise ValueError(f'incomplete arm {arm}')
            prediction = parse(messages[-1]['body'])
            print(arm, 'calls', [m['member'] for m in messages], 'spent',
                  sum(m['cost'] for m in messages), 'balance', state['credits'])
            if arm == 'roundtable':
                print('Consultation reuses the model baseline. End-to-end: '
                      '2 model calls + 1 expert call, 3 credits including the shared reply.')
            for case, shifts in CASES.items():
                for rule, truth in expected(shifts).items():
                    result = prediction[case][rule]
                    print(case, rule, len(truth), 'missing', sorted(truth-result),
                          'extra', sorted(result-truth))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('db', type=Path)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('init')
    record = commands.add_parser('record')
    record.add_argument('arm', choices=['model', 'roundtable'])
    record.add_argument('reply', type=Path)
    commands.add_parser('score')
    args = parser.parse_args()
    if args.action == 'init':
        args.db.touch(exist_ok=False)
    elif not args.db.is_file():
        parser.error('missing database')
    with closing(Kernel(args.db)) as kernel:
        run(kernel, args)
