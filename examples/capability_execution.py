"""Route two public needs once, then execute each recommendation on nine local schedules."""
import csv
import sys

from examples.generate_overnight import generate
from examples.meta_consult import prepare
from examples.overnight_score import expected
from examples.peer_node import match_offers
from examples.repair_benchmark import schedule
from open_roundtable.schedule_expert import check


def run():
    offers = [
        dict(tag='repair', description='Change shift times to resolve conflicts.'),
        dict(tag='audit', description='Report shift overlaps without changing the roster.'),
    ]
    writer = csv.writer(sys.stdout, lineterminator='\n')
    writer.writerow(['seed', 'need', 'router', 'selected', 'fulfilled', 'changed'])
    for tag, need in [('repair', 'Fix overlapping shifts and return an adjusted roster.'),
                      ('audit', 'Identify overlapping shifts; keep every shift unchanged.')]:
        result = match_offers(need, offers, 'qwen3.5:9b')
        print(tag, {k:v for k,v in result.items() if k != 'matches'}, file=sys.stderr)
        for seed in range(501, 510):
            original = generate(seed, (7, 11, 22)[(seed-501)//3])
            for router, selected in [('rule', [tag]), ('model', [o['tag'] for o in result['matches']])]:
                repaired = original
                findings = None
                if 'audit' in selected:
                    findings = check(original)
                if 'repair' in selected:
                    rows, plans = prepare(original)
                    repaired = schedule(rows, plans[0])
                changed = sum(a != b for a,b in zip(original, repaired, strict=True))
                if tag == 'audit':
                    passed = changed == 0 and findings == expected(original)
                else:
                    passed = 'repair' in selected and not any(expected(repaired).values())
                writer.writerow([seed, tag, router, '+'.join(selected), int(passed), changed])


if __name__ == '__main__':
    run()
