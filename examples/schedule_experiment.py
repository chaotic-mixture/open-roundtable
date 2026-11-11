"""Offline ledger and independent scorer for externally dispatched reviewer calls."""
import argparse
from contextlib import closing
from itertools import combinations
from pathlib import Path

from open_roundtable.core import Kernel


DATA = {
    'small': 'A:233/11-12 B:131/13-14 C:143/10-11 D:232/10-11 E:143/11-13 F:223/14-16',
    'medium': 'A:123/14-15 B:231/13-14 C:121/9-10 D:132/13-14 E:132/10-13 F:132/8-10 G:222/9-12 H:111/12-13 I:133/11-13 J:133/12-13 K:113/13-15 L:221/9-10 M:113/11-12 N:141/9-12',
    'large': 'A:112/13-14 B:123/12-15 C:213/14-16 D:211/14-16 E:213/14-17 F:233/14-17 G:241/11-14 H:133/10-13 I:111/12-15 J:231/14-17 K:142/8-11 L:112/14-16 M:122/8-10 N:211/9-10 O:211/12-14 P:232/8-10 Q:211/8-10 R:141/8-10 S:233/14-15 T:222/14-16 U:242/10-11 V:123/10-12 W:143/8-10 X:241/11-14',
}


def truth(data):
    rows = []
    for entry in data.split():
        name, spec = entry.split(':')
        (day, person, room), hours = spec.split('/')
        start, end = map(int, hours.split('-'))
        rows.append((name, day, person, room, start, end))
    return {a[0] + b[0] for a, b in combinations(rows, 2)
            if a[1] == b[1] and (a[2] == b[2] or a[3] == b[3])
            and max(a[4], b[4]) < min(a[5], b[5])}


def run(kernel, args):
    if args.action == 'init':
        for case in DATA:
            for group, roster in [('solo', ['solo']), ('table', ['person', 'room'])]:
                topic = kernel.create(f'{case}/{group}', credits=2)
                for member in roster:
                    kernel.join(topic, member, kind='model')
                kernel.start(topic)
        return
    for row in kernel.db.execute('SELECT id,title FROM topics ORDER BY rowid').fetchall():
        case, group = row['title'].split('/')
        topic = row['id']
        state = kernel.get(topic)
        messages = state['messages']
        if args.action == 'post' and row['title'] == args.topic:
            if len(messages) >= 2:
                raise ValueError('budget exhausted')
            member = 'solo' if group == 'solo' else ['person', 'room'][len(messages)]
            if not kernel.post(topic, member, args.answer, cost=1):
                raise ValueError('post declined')
            return
        if args.action == 'score':
            if len(messages) != 2:
                raise ValueError(f'incomplete: {row["title"]}')
            used = messages[-1:] if group == 'solo' else messages
            pairs = {pair for msg in used for pair in msg['body'].split() if pair != 'NONE'}
            expected = truth(DATA[case])
            print(row['title'], 'truth', len(expected), 'calls', len(messages),
                  'spent', sum(m['cost'] for m in messages), 'balance', state['credits'],
                  'miss', sorted(expected - pairs), 'extra', sorted(pairs - expected))
    if args.action == 'post':
        raise ValueError('unknown topic')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('db', type=Path)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('init')
    post = commands.add_parser('post')
    post.add_argument('topic')
    post.add_argument('answer')
    commands.add_parser('score')
    args = parser.parse_args()
    if args.action == 'init':
        args.db.touch(exist_ok=False)
    elif not args.db.is_file():
        parser.error('database missing')
    with closing(Kernel(args.db)) as kernel:
        run(kernel, args)
