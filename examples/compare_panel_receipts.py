"""Compare a completed model call in each of two saved repair consultation ledgers."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from examples.select_repair import choice


def read_receipt(path, nested):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        attempts = db.execute('SELECT topic,request,status,response FROM attempts').fetchall()
        if len(attempts) != 1 or attempts[0][2] != 'returned':
            raise ValueError('expected one returned model call per ledger')
        topic, request, _, raw = attempts[0]
        response = json.loads(raw)
        links = db.execute('SELECT parent,child,spent,member FROM delegations').fetchall()
        if (nested and (len(links) != 1 or links[0][1] != topic)) or (not nested and links):
            raise ValueError('unexpected delegation structure')
        charges = db.execute('SELECT member,cost,body FROM messages WHERE topic=? ORDER BY id', (topic,)).fetchall()
        if [(member, cost) for member, cost, _ in charges] != [('expert', 1), ('model', 1)]:
            raise ValueError('expected one expert charge and one model charge')
        if json.loads(charges[1][2]) != response:
            raise ValueError('posted model response differs from dispatch receipt')
        if any(type(response.get(field)) is not int or response[field] < 0
               for field in ('prompt_eval_count', 'eval_count')):
            raise ValueError('invalid model token counts')
        credits = sum(cost for _, cost, _ in charges)
        state = db.execute('SELECT state FROM topics WHERE id=?', (topic,)).fetchone()[0]
        body = db.execute('SELECT body FROM proposals WHERE topic=? ORDER BY version DESC LIMIT 1', (topic,)).fetchone()
        if body is None:
            raise ValueError('missing repair proposal')
        proposal = json.loads(body[0])
        candidates = json.loads(charges[0][2])['candidates']
        if not isinstance(candidates, list) or not candidates:
            raise ValueError('missing expert candidates')
        try:
            selected = choice(response, len(candidates))
        except (ValueError, TypeError, KeyError):
            selected = 0
        if (type(proposal.get('selected')) is not int or proposal['selected'] != selected
                or proposal.get('schedule') != candidates[selected]):
            raise ValueError('proposal differs from selected expert candidate')
        result = dict(state=state, selected=proposal['selected'], criteria_met=proposal['criteria_met'],
                      credits=credits, input_tokens=response['prompt_eval_count'], output_tokens=response['eval_count'])
        if nested:
            parent, _, spent, member = links[0]
            deliveries = db.execute('SELECT body,cost FROM messages WHERE topic=? AND member=?',
                                    (parent, member)).fetchall()
            if spent is None:
                if deliveries:
                    raise ValueError('unsettled child already has a parent delivery')
            elif state != 'RESOLVED' or spent != credits or deliveries != [(body[0], credits)]:
                raise ValueError('child spending and parent settlement disagree')
            result['settled_credits'] = spent
            result['parent_state'] = db.execute('SELECT state FROM topics WHERE id=?', (parent,)).fetchone()[0]
        return json.loads(request), proposal['schedule'], result


def compare(direct, nested):
    request, schedule, flat = read_receipt(direct, False)
    other_request, other_schedule, panel = read_receipt(nested, True)
    if request != other_request:
        raise ValueError('model requests differ across paired consultations')
    return dict(same_schedule=schedule == other_schedule, direct=flat, nested=panel)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('direct', type=Path)
    parser.add_argument('nested', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.direct, args.nested), indent=2))
