"""Paired schedule repair experiment on the v0.0.3 benchmark inputs."""
import argparse
import csv
import json
from contextlib import closing
from pathlib import Path
from time import perf_counter

from examples.auto_consult import api, assert_post, encoded, lists, save
from examples.generate_overnight import generate
from examples.overnight_score import expected
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


def schedule(rows, starts):
    return [f'{key}:{start}-{start+duration}' for (key, _, duration), start in zip(rows, starts, strict=True)]


def candidates(rows):
    """Try four insertion orders; rank feasible repairs by disruption."""
    plans = []
    for order in [range(len(rows)), reversed(range(len(rows))),
                  sorted(range(len(rows)), key=lambda i: rows[i][1]),
                  sorted(range(len(rows)), key=lambda i: -rows[i][1])]:
        placed = []
        starts = [None]*len(rows)
        for i in order:
            key, original, duration = rows[i]
            for hour in sorted(range(169-duration), key=lambda h: (abs(h-original), h)):
                token = f'{key}:{hour}-{hour+duration}'
                if not any(check(placed+[token]).values()):
                    starts[i] = hour
                    placed.append(token)
                    break
            else:
                break
        if None not in starts and starts not in plans:
            plans.append(starts)
    return sorted(plans, key=lambda p: (sum(h!=r[1] for h,r in zip(p,rows)),
                                       sum(abs(h-r[1]) for h,r in zip(p,rows))))


PREF = ('A would like to keep its original start. B prefers to begin in the morning '
        '(06:00 inclusive to 12:00 exclusive). C prefers an afternoon start '
        '(12:00 inclusive to 18:00 exclusive). Each wish has equal weight.')


def ask(model, rows, plans, violations):
    ids = [r[0][0] for r in rows]
    text = ('Repair a weekly roster. Rows are [shiftID+worker+room,originalStart,duration]. '
            'Only move starts, integer hours, within [0,168] including end. Keep all shifts. '
            'No overlapping same worker or room. Merge touching shifts per worker: blocks <=8h, '
            'positive rest gaps >=8h. First meet all hard constraints, then maximize wishes, '
            'then minimize number of moved shifts. Hour of day = hour modulo 24. '+PREF+
            '\nRows '+encoded(rows)+'\nBase start vectors in shift order (zero-based plan index): '+
            encoded(plans)+'. Pick a base and optionally override individual start hours. '
            'Return {"plan":integer,"moves":{shiftID:newStart,...}}. Empty moves preserves base.')
    if violations is not None:
        text += '\nExpert findings on ORIGINAL roster: '+encoded(violations)
    schema = {'type':'object','required':['plan','moves'],'properties':{
        'plan':{'type':'integer','enum':list(range(len(plans)))},
        'moves':{'type':'object','additionalProperties':False,
                 'properties':{i:{'type':'integer'} for i in ids}}}}
    payload = dict(model=model, messages=[dict(role='user',content=text)], format=schema,
                   stream=False, think=False, options={'temperature':0})
    return payload, api('/api/chat',payload)


def run(folder, model):
    folder.mkdir(parents=True, exist_ok=False)
    save(folder,'design.json',dict(preference=PREF, horizon=168, seeds=list(range(501,510)),
         model=next(m for m in api('/api/tags')['models'] if m['name']==model), ollama=api('/api/version')))
    with closing(Kernel(folder/'ledger.sqlite3')) as k, (folder/'results.csv').open('x',newline='') as out:
        writer = None
        for index, seed in enumerate(range(501,510)):
            size = (7,11,22)[index//3]
            original = generate(seed,size)
            rows = []
            for token in original:
                key, span = token.split(':')
                start,end = map(int,span.split('-'))
                rows.append((key,start,end-start))
            old = [r[1] for r in rows]
            order = ['solo','tool','table'] if index%2==0 else ['table','tool','solo']
            for arm in order:
                topic = k.create(f'repair/{seed}/{arm}',credits=3)
                for m in (['model'] if arm=='solo' else ['expert'] if arm=='tool' else ['expert','model']):
                    k.join(topic,m,kind=m,source=model if m=='model' else 'schedule_expert')
                k.start(topic)
                events = []

                def record(member, stage, data, elapsed):
                    assert_post(k,topic,member,encoded(data))
                    events.append(dict(member=member,stage=stage,seconds=elapsed,data=data))
                    save(folder,f'{seed}-{arm}.json',dict(topic=topic,original=original,events=events))

                plans, findings = [old], None
                if arm!='solo':
                    t = perf_counter()
                    findings = lists(check(original))
                    plans = candidates(rows) or [old]
                    record('expert','plan',dict(findings=findings,candidates=plans),perf_counter()-t)
                starts = plans[0].copy()
                invalid = ''
                inp = output = 0
                if arm!='tool':
                    t = perf_counter()
                    payload,response = ask(model,rows,plans,findings)
                    record('model','propose',dict(request=payload,response=response),perf_counter()-t)
                    inp,output = response['prompt_eval_count'],response['eval_count']
                    try:
                        proposal = json.loads(response['message']['content'])
                        if response.get('done_reason')!='stop' or type(proposal['plan'])!=int or not 0<=proposal['plan']<len(plans):
                            raise ValueError('invalid or unfinished proposal')
                        starts = plans[proposal['plan']].copy()
                        ids = [r[0][0] for r in rows]
                        for shift,h in proposal['moves'].items():
                            if type(h)!=int:
                                raise ValueError('noninteger hour')
                            pos = ids.index(shift)
                            if not 0 <= h <= 168 - rows[pos][2]:
                                raise ValueError('move outside the scheduling week')
                            starts[pos] = h
                    except (ValueError, KeyError, TypeError, AttributeError) as err:
                        invalid = str(err)
                repaired = schedule(rows, starts)
                window = all(0<=h<=168-r[2] for h,r in zip(starts,rows))
                if arm=='table':
                    t = perf_counter()
                    review = dict(violations=lists(check(repaired)),within_week=window,invalid=invalid)
                    record('expert','review',review,perf_counter()-t)
                truth = lists(expected(repaired))
                success = not invalid and window and not any(truth.values())
                if arm=='table' and truth!=review['violations']:
                    raise RuntimeError('expert and independent scorer disagree')
                prefs = [starts[0]==old[0], 6<=starts[1]%24<12, 12<=starts[2]%24<18]
                result = dict(repaired=repaired,violations=truth,within_week=window,invalid=invalid,
                              success=success,preferences=prefs)
                k.feedback(topic,events[-1]['member'],encoded(result),source='independent scoring')
                save(folder,f'{seed}-{arm}.json',dict(topic=topic,original=original,events=events,result=result))
                row = dict(seed=seed,size=size,arm=arm,success=int(success),changed=sum(x!=y for x,y in zip(old,starts)),
                           wishes=sum(prefs),possible=3,calls=len(events),input_tokens=inp,output_tokens=output,
                           credits=sum(m['cost'] for m in k.get(topic)['messages']),
                           seconds=sum(e['seconds'] for e in events))
                if writer is None:
                    writer = csv.DictWriter(out,fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                out.flush()
                print(seed,arm,row['success'],row['changed'],row['wishes'],flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--model',default='qwen3.5:9b')
    args = parser.parse_args()
    run(args.directory,args.model)
