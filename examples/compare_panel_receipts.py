"""Compare a completed model call in each of two saved repair consultation ledgers."""
import argparse
import hashlib
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

from examples.select_repair import choice
from examples.roster_criteria import evaluate
from examples.overnight_score import expected
from examples.repair_benchmark import schedule
from examples.user_consult import selection_messages, validate_roster


def candidate_score(chosen, original, rows, rules):
    if not isinstance(chosen, list) or len(chosen) != len(rows):
        raise ValueError('changed shift count in repair')
    starts = []
    for shift, item, (_, _, duration) in zip(chosen, original, rows):
        start = shift.get('start')
        if (type(start) is not int or type(shift.get('end')) is not int
                or not 0 <= start <= 168-duration or shift != dict(item, start=start, end=start+duration)):
            raise ValueError('invalid shift identity, duration or weekly interval in repair')
        starts.append(start)
    if any(expected(schedule(rows, starts)).values()):
        raise ValueError('selected repair violates hard constraints')
    return list(evaluate(rules, rows, starts))


def read_receipt(path, nested, expected_input=None):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        attempts = db.execute('SELECT topic,request,status,response,seconds FROM attempts').fetchall()
        if len(attempts) != 1 or attempts[0][2] != 'returned':
            raise ValueError('expected one returned model call per ledger')
        topic, request, _, raw, seconds = attempts[0]
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('invalid recorded model call duration')
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
        expert = json.loads(charges[0][2])
        if expected_input is not None and expert['input'] != expected_input:
            raise ValueError(f'{path.name}: ledger input differs from seeded roster')
        candidates = expert['candidates']
        if not isinstance(candidates, list) or not candidates:
            raise ValueError('missing expert candidates')
        try:
            selected = choice(response, len(candidates))
            fallback = False
        except (ValueError, TypeError, KeyError):
            selected = 0
            fallback = True
        reason = proposal.get('fallback')
        if not isinstance(reason, str) or bool(reason) != fallback:
            raise ValueError('recorded fallback differs from model response')
        if (type(proposal.get('selected')) is not int or proposal['selected'] != selected
                or proposal.get('schedule') != candidates[selected]):
            raise ValueError('proposal differs from selected expert candidate')
        rows, rules = validate_roster(expert['input'])
        original = expert['input']['shifts']
        scores = [candidate_score(candidate, original, rows, rules) for candidate in candidates]
        flags = scores[selected]
        saved = proposal.get('criteria_met')
        if not isinstance(saved, list) or any(type(flag) is not bool for flag in saved) or saved != flags:
            raise ValueError('recorded preference scores differ from the selected schedule')
        result = dict(state=state, selected=proposal['selected'], used_fallback=fallback, criteria_met=flags,
                      credits=credits, model_seconds=seconds,
                      input_tokens=response['prompt_eval_count'], output_tokens=response['eval_count'])
        result['best_criteria_met'] = max(scores)  # Earlier criteria have higher priority.
        result['can_meet_all'] = any(all(score) for score in scores)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='benchmark_timing'").fetchone():
            [(position, duration)] = db.execute('SELECT position,seconds FROM benchmark_timing').fetchall()
            if position not in (1, 2) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration < seconds:
                raise ValueError('invalid consultation timing')
            result.update(position=position, consultation_seconds=duration)
        changes = [sum(shift['start'] != before['start'] for shift, before in zip(candidate, original, strict=True))
                   for candidate in candidates]
        expert_agrees = not rules or (flags, changes[selected]) == (scores[0], changes[0])
        expected_votes = {'expert': int(expert_agrees)}
        if not fallback:
            expected_votes['model'] = 1
        votes = dict(db.execute('SELECT member,agree FROM votes WHERE topic=?', (topic,)))
        if votes != expected_votes or state != ('RESOLVED' if sum(expected_votes.values()) == 2 else 'PAUSED'):
            raise ValueError('ballots or consultation state differ from selected proposal')
        result['votes'] = votes
        result['changed_shifts'] = changes[selected]
        result['min_changes_at_selected_score'] = min(n for n, score in zip(changes, scores) if score == flags)
        result['min_changes_at_best_score'] = min(n for n, score in zip(changes, scores)
                                                  if score == result['best_criteria_met'])
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
        dispatch = json.loads(request)
        if dispatch.get('messages') != selection_messages(expert['input'], candidates):
            raise ValueError('model menu differs from recorded expert input or candidates')
        return dispatch, proposal['schedule'], result


def compare(direct, nested, *, expected_input=None):
    request, schedule, flat = read_receipt(direct, False, expected_input)
    other_request, other_schedule, panel = read_receipt(nested, True, expected_input)
    if request != other_request:
        raise ValueError('model requests differ across paired consultations')
    if 'position' in flat or 'position' in panel:
        if {flat.get('position'), panel.get('position')} != {1, 2}:
            raise ValueError('inconsistent consultation order')
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    return dict(request_sha256=request_hash, same_schedule=schedule == other_schedule, direct=flat, nested=panel)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('direct', type=Path)
    parser.add_argument('nested', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.direct, args.nested), indent=2))
