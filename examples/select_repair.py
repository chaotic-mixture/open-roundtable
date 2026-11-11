"""Select a verified schedule by ID, with plan zero as the failure fallback."""
import argparse
import csv
import json
from contextlib import closing
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from examples.auto_consult import api, assert_post, encoded, save
from examples.generate_overnight import generate
from examples.overnight_score import expected
from examples.repair_benchmark import PREF, candidates, schedule
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


def choice(response, count):
    if not response['done'] or response.get('done_reason') != 'stop':
        raise ValueError('incomplete reply')
    answer = json.loads(response['message']['content'])
    if not isinstance(answer, dict) or set(answer) != {'plan'}:
        raise ValueError('expected a plan ID only')
    index = answer['plan']
    if type(index) != int or not 0 <= index < count:
        raise ValueError('unknown candidate')
    return index


def run(folder, model):
    folder.mkdir(parents=True, exist_ok=False)
    try:
        installed = next((m for m in api('/api/tags')['models'] if m['name']==model), {'name':model})
        version = api('/api/version')
    except (URLError, TimeoutError) as exc:
        installed, version = {'name':model}, {'unavailable':repr(exc)}
    save(folder,'design.json',dict(model=installed, ollama=version, preference=PREF, fallback=0))
    with closing(Kernel(folder/'ledger.sqlite3')) as k, (folder/'results.csv').open('x',newline='') as out:
        writer = None
        for i, seed in enumerate(range(501,510)):
            size = (7,11,22)[i//3]
            original = generate(seed,size)
            rows = []
            for token in original:
                key, span = token.split(':')
                start,end = map(int,span.split('-'))
                rows.append((key,start,end-start))
            old = [r[1] for r in rows]
            topic = k.create(f'select repair {seed}',credits=2)
            k.join(topic,'expert',kind='expert',source='schedule_expert.check')
            k.join(topic,'model',kind='model',source=model)
            k.start(topic)
            t = perf_counter()
            plans = candidates(rows)
            if not plans or any(any(check(schedule(rows,p)).values()) or
                                any(not 0<=h<=168-r[2] for h,r in zip(p,rows)) for p in plans):
                raise ValueError('unable to establish verified candidates and default')
            expert_seconds = perf_counter()-t
            assert_post(k,topic,'expert',encoded(plans))
            menu = [dict(plan=j,A_start=p[0],B_hour=p[1]%24,C_hour=p[2]%24,
                         moved=sum(x!=y for x,y in zip(p,old))) for j,p in enumerate(plans)]
            msg = (PREF+' Choose the feasible candidate satisfying most wishes, then fewest moved '
                   'shifts; tie: lowest ID. A original start='+str(old[0])+'. B_hour and C_hour '
                   'are already local hours of day. Candidate facts: '+encoded(menu))
            payload = dict(model=model,stream=False,think=False,options={'temperature':0},
                           messages=[dict(role='user',content=msg)],format={'type':'object',
                           'additionalProperties':False,'required':['plan'],'properties':{'plan':
                           {'type':'integer','enum':list(range(len(plans)))}}})
            response, error, selected = None, '', 0
            t = perf_counter()
            try:
                response = api('/api/chat',payload)
                selected = choice(response,len(plans))
            except (URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                error = repr(exc)
            model_seconds = perf_counter()-t
            if response is not None:
                assert_post(k,topic,'model',encoded(response))
            p = plans[selected]
            repaired = schedule(rows,p)
            success = not any(expected(repaired).values())
            wishes = int(p[0]==old[0])+int(6<=p[1]%24<12)+int(12<=p[2]%24<18)
            result = dict(selected=selected,fallback=bool(error),error=error,repaired=repaired,
                          success=success,wishes=wishes)
            k.feedback(topic,'expert',encoded(result),source='independent hourly scoring')
            save(folder,f'{seed}.json',dict(topic=topic,original=original,plans=plans,
                 request=payload,response=response,result=result))
            usage = response or {}
            row = dict(seed=seed,size=size,success=int(success),changed=menu[selected]['moved'],
                       wishes=wishes,selected=selected,fallback=int(bool(error)),calls=2,
                       credits=sum(m['cost'] for m in k.get(topic)['messages']),
                       input_tokens=usage.get('prompt_eval_count'),output_tokens=usage.get('eval_count'),
                       expert_seconds=expert_seconds,model_seconds=model_seconds,seconds=expert_seconds+model_seconds)
            if writer is None:
                writer=csv.DictWriter(out,fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            out.flush()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--model',default='qwen3.5:9b')
    args=parser.parse_args()
    run(args.directory,args.model)
