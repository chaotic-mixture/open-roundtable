"""Interval-based scheduling expert for the synthetic overnight input format."""
from collections import defaultdict
from itertools import pairwise


def check(tokens):
    workers = defaultdict(list)
    rooms = defaultdict(list)
    for token in tokens:
        (shift, worker, room), span = token.split(':')
        start, end = map(int, span.split('-'))
        if start >= end:
            raise ValueError('shift must have positive duration')
        workers[worker].append((start, end, shift))
        rooms[room].append((start, end, shift))

    findings = {rule: set() for rule in 'PVRL'}
    for groups, rule in [(workers, 'P'), (rooms, 'V')]:
        for shifts in groups.values():
            active = []
            for start, end, shift in sorted(shifts):
                active = [(stop, other) for stop, other in active if stop > start]
                findings[rule].update(''.join(sorted((other, shift))) for _, other in active)
                active.append((end, shift))

    for worker, shifts in workers.items():
        blocks = []
        for start, end, _ in sorted(shifts):
            if blocks and start <= blocks[-1][1]:
                blocks[-1][1] = max(end, blocks[-1][1])
            else:
                blocks.append([start, end])
        for start, end in blocks:
            if end - start > 8:
                findings['L'].add(f'{worker}@{start}-{end}')
        for (_, end), (start, _) in pairwise(blocks):
            if start - end < 8:
                findings['R'].add(f'{worker}@{end}-{start}')
    return findings
