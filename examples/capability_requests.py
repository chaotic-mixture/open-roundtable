"""Score public offer selections per request, independently of roster execution."""
import json

from examples.capability_execution import literal_matches
from examples.peer_node import match_offers


OFFERS = [
    dict(tag='repair', description='Change shift times to resolve conflicts.'),
    dict(tag='reschedule', description='Reassign weekly shift times to produce a conflict-free roster.'),
    dict(tag='audit', description='Report shift overlaps without changing the roster.'),
]
CASES = [
    ('repair', 'Move duties until no shifts collide.'),
    ('audit', 'List double bookings for the supervisor to review.'),
    ('none', 'Compute the overtime wages owed for this roster.'),
    ('audit', 'Do not move anyone. Tell me which assignments clash.'),
    ('repair', 'A list of overlaps alone is insufficient. Give me revised shift times.'),
    ('none', 'Email every worker a copy of the unchanged roster.'),
    ('repair', 'Return a weekly rota with adjusted hours that eliminate overlaps.'),
    ('audit', 'Preserve every start time and identify overlapping shifts.'),
    ('none', 'Predict how many staff will call in sick next week.'),
]


def run():
    answers = {'repair': {'repair', 'reschedule'}, 'audit': {'audit'}, 'none': set()}
    totals = {}
    for kind, need in CASES:
        result = match_offers(need, OFFERS, 'qwen3.5:9b')
        for router, tags in [('literal', literal_matches(need, OFFERS)),
                             ('model', [offer['tag'] for offer in result['matches']])]:
            selected = set(tags)
            expected = answers[kind]
            metrics = dict(exact=int(expected == selected), missed=len(expected - selected),
                           extra=len(selected - expected))
            for field in ('calls', 'input_tokens', 'output_tokens'):
                metrics[field] = result[field] if router == 'model' else 0
            print(json.dumps(dict(need=need, router=router, expected=sorted(expected),
                                  selected=sorted(selected), **metrics)))
            summary = totals.setdefault(router, {})
            for field, value in metrics.items():
                summary[field] = summary.get(field, 0) + value
    print(json.dumps(dict(requests=len(CASES), totals=totals)))


if __name__ == '__main__':
    run()
