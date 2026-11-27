"""Interval-based scheduling expert for the synthetic overnight input format."""
import re
from collections import defaultdict
from itertools import pairwise


def audit_rules(spec):
    if not isinstance(spec, dict) or spec.get('operation') != 'schedule-audit-v1':
        return None
    rules = spec.get('rules')
    shifts = spec.get('shifts')
    if not isinstance(rules, list) or not rules:
        raise ValueError('provide a nonempty array of audit rules')
    for index, rule in enumerate(rules):
        if rule not in ('P', 'V', 'R', 'L'):
            raise ValueError(f'rules[{index}] must be P (worker overlap), V (room overlap), '
                             'R (minimum rest), or L (maximum continuous work)')
    if not isinstance(shifts, list) or not 1 <= len(shifts) <= 52:
        raise ValueError('provide 1 to 52 audit shifts')
    ids = set()
    for index, shift in enumerate(shifts):
        match = re.fullmatch(r'([A-Za-z])[A-Za-z][A-Za-z]:(\d{1,3})-(\d{1,3})', shift) if isinstance(shift, str) else None
        if not match:
            raise ValueError(f'shifts[{index}] must contain three letters (shift, worker, room) '
                             'and a start-end hour range, for example Axv:22-26')
        if match[1] in ids:
            raise ValueError(f'shifts[{index}] repeats a shift ID; use a unique first letter')
        if not 0 <= int(match[2]) < int(match[3]) <= 168:
            raise ValueError(f'shifts[{index}] must satisfy 0 <= start < end <= 168 hours')
        ids.add(match[1])
    return rules


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
