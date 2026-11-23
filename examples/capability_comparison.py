"""Smoke comparison on fixed public offers, including a need with no provider."""
from examples.peer_node import match_offers


def run(model):
    offers = [dict(tag='repair',description='Change shift times to resolve conflicts.'),
              dict(tag='reschedule',description='Reassign weekly shift times to produce a conflict-free roster.'),
              dict(tag='audit',description='Report shift overlaps without changing the roster.')]
    cases = [('repair','Fix overlapping shifts and return an adjusted roster.',{0,1}),
             ('audit','Identify overlapping shifts; keep every shift unchanged.',{2}),
             ('payroll','Calculate monthly payroll taxes.',set())]
    for tag,need,expected in cases:
        result = match_offers(need,offers,model)
        selected = {offers.index(o) for o in result['matches']}
        exact = {i for i,o in enumerate(offers) if o['tag']==tag}
        print(dict(tag=tag, exact_missed=len(expected-exact),exact_extra=len(exact-expected),
                   model_missed=len(expected-selected),model_extra=len(selected-expected),
                   **{k:v for k,v in result.items() if k!='matches'}))


if __name__ == '__main__':
    run('qwen3.5:9b')
