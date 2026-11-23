"""Route two public needs once, then execute each recommendation on nine local schedules."""
import argparse
import csv
import sys

from examples.generate_overnight import generate
from examples.meta_consult import prepare
from examples.overnight_score import expected
from examples.peer_node import match_offers
from examples.repair_benchmark import schedule
from open_roundtable.schedule_expert import check


def execute_selection(tag, selected, original):
    # Check the whole recommendation before invoking any provider.
    if selected != [tag] or tag not in ('repair', 'audit'):
        return False, 0
    if tag == 'audit':
        return check(original) == expected(original), 0
    rows, plans = prepare(original)
    repaired = schedule(rows, plans[0])
    changed = sum(a != b for a, b in zip(original, repaired, strict=True))
    return not any(expected(repaired).values()), changed


def run(with_synonym=False):
    offers = [
        dict(tag='repair', description='Change shift times to resolve conflicts.'),
        dict(tag='audit', description='Report shift overlaps without changing the roster.'),
    ]
    if with_synonym:
        offers.insert(1, dict(tag='reschedule', description='Reassign weekly shift times to produce a conflict-free roster.'))
    writer = csv.writer(sys.stdout, lineterminator='\n')
    writer.writerow(['seed', 'need', 'router', 'selected', 'fulfilled', 'changed'])
    for tag, need in [('repair', 'Fix overlapping shifts and return an adjusted roster.'),
                      ('audit', 'Identify overlapping shifts; keep every shift unchanged.')]:
        result = match_offers(need, offers, 'qwen3.5:9b')
        print(tag, {k:v for k,v in result.items() if k != 'matches'}, file=sys.stderr)
        for seed in range(501, 510):
            original = generate(seed, (7, 11, 22)[(seed-501)//3])
            for router, selected in [('rule', [tag]), ('model', [o['tag'] for o in result['matches']])]:
                # Both repair declarations refer to the same local tool in this fixture.
                operations = sorted({'repair' if item == 'reschedule' else item for item in selected})
                passed, changed = execute_selection(tag, operations, original)
                writer.writerow([seed, tag, router, '+'.join(selected), int(passed), changed])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-synonym', action='store_true', help='include a second public description of the repair tool')
    run(parser.parse_args().with_synonym)
