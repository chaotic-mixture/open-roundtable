"""Paired overnight scheduling benchmark using a local Ollama model."""
import argparse
import csv
import json
from contextlib import closing
from pathlib import Path
from time import perf_counter

from examples.auto_consult import RULES, api, assert_post, encoded, grade, lists, save
from examples.generate_overnight import generate
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


ARMS = {'solo': ('model',), 'tool': ('expert',), 'table': ('expert', 'model')}
SCHEMA = {'type': 'object', 'required': [*RULES, 'approve_final'],
          'properties': {**{r: {'type': 'array', 'items': {'type': 'string'}} for r in RULES},
                         'approve_final': {'type': 'boolean'}}}


def request(model, shifts, proposal):
    rows = []
    for token in shifts:
        (shift, worker, room), interval = token.split(':')
        rows.append([shift, worker, room, *map(int, interval.split('-'))])
    instructions = ('Find all overnight scheduling violations. Rows are [shift ID,worker,room,start,end], '
                    'in absolute hours. P: all overlapping shift pairs sharing a worker. V: all '
                    'overlapping pairs sharing a room. Use alphabetical two-letter IDs, exclude '
                    'touching endpoints. Per worker merge touching or overlapping shifts into '
                    'blocks. R: consecutive block gaps strictly below 8h as worker@end-start. '
                    'L: blocks strictly longer than 8h as worker@start-end. Use empty arrays for '
                    'no findings. approve_final: do you endorse your final list?\nRows: '+encoded(rows))
    if proposal is not None:
        instructions += '\nReview this scheduling expert proposal and provide your final list: '+encoded(proposal)
    return {'model': model, 'messages': [{'role':'user','content':instructions}],
            'format': SCHEMA, 'stream': False, 'think': False, 'options': {'temperature': 0}}


def evaluate(k, folder, seed, size, shifts, arm, model):
    members = ARMS[arm]
    topic = k.create(f'{seed}/{arm}', credits=2)
    for member in members:
        k.join(topic, member, kind=member, source=model if member == 'model' else 'schedule_expert.check')
    k.start(topic)
    calls = []
    proposal = None
    for member in members:
        if member == 'expert':
            start = perf_counter()
            proposal = lists(check(shifts))
            elapsed = perf_counter()-start
            final = proposal
            body = encoded(final)
            event = {'input': shifts, 'response': proposal}
        else:
            payload = request(model, shifts, proposal)
            start = perf_counter()
            try:
                response = api('/api/chat', payload)
            except Exception as error:
                elapsed = perf_counter() - start
                posted = k.get(topic)['messages']
                failed_attempt = dict(member=member, input=payload, error_type=type(error).__name__,
                                      seconds=elapsed, input_tokens=None, output_tokens=None)
                save(folder, f'{seed}-{arm}.json',
                     dict(topic=topic, shifts=shifts, calls=calls, failed_attempt=failed_attempt,
                          credits_spent=sum(message['cost'] for message in posted)))
                raise
            elapsed = perf_counter()-start
            event = {'input': payload, 'response': response}
            body = response['message']['content']
        assert_post(k, topic, member, body)
        calls.append(dict(event, member=member, seconds=elapsed))
        evidence = dict(topic=topic, shifts=shifts, calls=calls,
                        credits_spent=sum(message['cost'] for message in k.get(topic)['messages']))
        save(folder, f'{seed}-{arm}.json', evidence)
        if member == 'model':
            try:
                if not response['done'] or response['done_reason'] != 'stop':
                    raise ValueError('incomplete generation')
                final = json.loads(body)
                if not isinstance(final, dict) or type(final.get('approve_final')) is not bool:
                    raise ValueError('expected a boolean approve_final verdict')
                scores = grade(shifts, final)
            except (ValueError, KeyError, TypeError) as error:
                evidence['scoring_error'] = str(error)
                save(folder, f'{seed}-{arm}.json', evidence)
                raise
    if arm == 'tool':
        scores = grade(shifts, final)
    if arm == 'table':
        k.vote(topic, 'model', final['approve_final'])
        k.vote(topic, 'expert', all(set(final[r]) == set(proposal[r]) for r in RULES))
    k.feedback(topic, members[-1], encoded(scores), source='examples.overnight_score.expected')
    state = k.get(topic)
    row = dict(seed=seed, shifts=size, arm=arm,
               missed=sum(len(s['missing']) for s in scores.values()),
               extra=sum(len(s['extra']) for s in scores.values()), calls=len(calls),
               input_tokens=sum(c['response']['prompt_eval_count'] for c in calls if c['member']=='model'),
               output_tokens=sum(c['response']['eval_count'] for c in calls if c['member']=='model'),
               credits=sum(m['cost'] for m in state['messages']),
               seconds=sum(c['seconds'] for c in calls))
    for r in RULES:
        row.update({r+'_missed':len(scores[r]['missing']), r+'_extra':len(scores[r]['extra'])})
    return row


def run(folder, model, repetitions, first_seed):
    if repetitions < 1:
        raise ValueError('at least one schedule per difficulty')
    folder.mkdir(parents=True, exist_ok=False)
    installed = next(m for m in api('/api/tags')['models'] if m['name']==model)
    save(folder,'design.json',dict(model=installed, ollama=api('/api/version'), sizes=[7,11,22],
                                   repetitions=repetitions, first_seed=first_seed, budget=2))
    with closing(Kernel(folder/'ledger.sqlite3')) as k, (folder/'results.csv').open('x', newline='') as out:
        writer = None
        for level, size in enumerate((7,11,22)):
            for trial in range(repetitions):
                index = level*repetitions+trial
                seed = first_seed+index
                shifts = generate(seed, size)
                order = ('solo','tool','table') if index%2==0 else ('table','tool','solo')
                for arm in order:
                    row = evaluate(k, folder, seed, size, shifts, arm, model)
                    if writer is None:
                        writer = csv.DictWriter(out, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    out.flush()
                    print(seed,size,arm,row['missed'],row['extra'],flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--model', default='qwen3.5:9b')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--first-seed', type=int, default=501)
    args = parser.parse_args()
    run(args.directory, args.model, args.repetitions, args.first_seed)
