"""Interval-based scheduling expert for the synthetic overnight input format."""
from collections import defaultdict
from itertools import pairwise


def check(tokens, *, rules='PVRL'):
    """Check the requested rule families. By default evaluate all four."""
    findings = {rule: set() for rule in rules}
    if findings.keys() - set('PVRL'):
        raise ValueError('unknown scheduling rule')
    workers = defaultdict(list)
    rooms = defaultdict(list)
    for token in tokens:
        (shift, worker, room), span = token.split(':')
        start, end = map(int, span.split('-'))
        if start >= end:
            raise ValueError('shift must have positive duration')
        workers[worker].append((start, end, shift))
        rooms[room].append((start, end, shift))

    for groups, rule in [(workers, 'P'), (rooms, 'V')]:
        if rule not in findings:
            continue
        for shifts in groups.values():
            active = []
            for start, end, shift in sorted(shifts):
                active = [(stop, other) for stop, other in active if stop > start]
                findings[rule].update(''.join(sorted((other, shift))) for _, other in active)
                active.append((end, shift))

    if not findings.keys() & {'R', 'L'}:
        return findings
    for worker, shifts in workers.items():
        blocks = []
        for start, end, _ in sorted(shifts):
            if blocks and start <= blocks[-1][1]:
                blocks[-1][1] = max(end, blocks[-1][1])
            else:
                blocks.append([start, end])
        for start, end in blocks:
            if 'L' in findings and end - start > 8:
                findings['L'].add(f'{worker}@{start}-{end}')
        for (_, end), (start, _) in pairwise(blocks):
            if 'R' in findings and start - end < 8:
                findings['R'].add(f'{worker}@{end}-{start}')
    return findings
