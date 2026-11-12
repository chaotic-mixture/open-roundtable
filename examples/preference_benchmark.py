"""Three paired selectors for natural-language priorities, conflicts and exceptions."""
import argparse
import csv
import hashlib
import json
from contextlib import closing
from pathlib import Path
from time import perf_counter

from examples.auto_consult import api, assert_post, encoded, save
from examples.generate_overnight import generate
from examples.overnight_score import expected
from examples.preference_rubric import BRIEFS, assess
from examples.repair_benchmark import candidates, schedule
from examples.select_repair import choice
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


def facts(starts, rows):
    return {r[0][0]:dict(start=s, day=s//24, hour=s%24, end_hour_on_start_day=s%24+r[2],
                          duration=r[2], original=r[1], original_day=r[1]//24,
                          original_hour=r[1]%24, delta=s-r[1]) for r,s in zip(rows[:3],starts[:3])}


def rule_key(profile, f, moved, index):
    a,b,c = (f[id_] for id_ in 'ABC')
    if profile == 0:
        top = (a['delta']==0 if a['original_hour']+a['duration']>24 else a['end_hour_on_start_day']<=18)
        middle, low = a['hour']>=18, b['day']==b['original_day']
    elif profile == 1:
        top = b['delta']==0 if b['original_hour']<6 else 6<=b['hour']<10
        middle, low = 14<=b['hour']<22, c['delta']>=0
    else:
        top = c['day']==c['original_day'] if c['duration']>=8 else c['delta']==0
        middle, low = c['delta']>=2, -2<=a['delta']<=2
    return top,middle,low,-moved,-index


def run(folder, model):
    folder.mkdir(parents=True,exist_ok=False)
    design = dict(briefs=BRIEFS, seeds=list(range(501,510)), profiles='rotate 0,1,2',
                  weights=[4,2,1], ties='fewest changed shifts, then lowest ID',
                  rubric_sha256=hashlib.sha256(Path(__file__).with_name('preference_rubric.py').read_bytes()).hexdigest(),
                  model=next(m for m in api('/api/tags')['models'] if m['name']==model),
                  ollama=api('/api/version'))
    save(folder,'design.json',design)
    with closing(Kernel(folder/'ledger.sqlite3')) as k, (folder/'results.csv').open('x',newline='') as out:
        writer = None
        for i,seed in enumerate(design['seeds']):
            size,profile=(7,11,22)[i//3],i%3
            original=generate(seed,size)
            rows=[]
            for token in original:
                key,span=token.split(':')
                start,end=map(int,span.split('-'))
                rows.append((key,start,end-start))
            old=[r[1] for r in rows]
            durations=[r[2] for r in rows]
            for arm in ('default','rules','model'):
                topic=k.create(f'{seed}/{arm}',credits=2)
                k.join(topic,'expert',kind='expert',source='verified candidate generator')
                if arm=='model':
                    k.join(topic,'model',kind='model',source=model)
                k.start(topic)
                t=perf_counter()
                plans=candidates(rows)
                if not plans or any(any(check(schedule(rows,p)).values()) or any(
                        not 0<=s<=168-d for s,d in zip(p,durations)) for p in plans):
                    raise ValueError('candidate verification failed')
                changed=[sum(s!=o for s,o in zip(p,old)) for p in plans]
                menu=[dict(id=j,shifts=facts(p,rows),changed=changed[j]) for j,p in enumerate(plans)]
                idx=0
                if arm=='rules':
                    idx=max(range(len(plans)),key=lambda j:rule_key(profile,menu[j]['shifts'],changed[j],j))
                elapsed=perf_counter()-t
                assert_post(k,topic,'expert',encoded(dict(plans=plans,tool_choice=idx)))
                request=response=None
                fallback=''
                if arm=='model':
                    msg=(BRIEFS[profile]+'。请按优先级从高到低逐项比较；相同则少改班优先，再选编号小的。'
                         '跨日指午夜之后仍在工作，午夜恰好下班不算。18点下班指开工当天。'
                         '候选均已验证可行。日期从0计，absolute start从第0天午夜计时。'
                         '仅返回候选编号。候选数据：'+encoded(menu))
                    request=dict(model=model,stream=False,think=False,options={'temperature':0},
                                 messages=[dict(role='user',content=msg)],format={'type':'object',
                                 'required':['plan'],'additionalProperties':False,'properties':{
                                 'plan':{'type':'integer','enum':list(range(len(plans)))}}})
                    t=perf_counter()
                    response=api('/api/chat',request)
                    try:
                        idx=choice(response,len(plans))
                    except (TypeError,ValueError,KeyError) as err:
                        fallback=repr(err)
                    elapsed+=perf_counter()-t
                    assert_post(k,topic,'model',encoded(response))
                decisions=assess(profile,old,durations,plans[idx])
                score=sum(w*int(b) for w,b in zip(design['weights'],decisions))
                valid=not any(expected(schedule(rows,plans[idx])).values())
                result=dict(id=idx,priorities=decisions,score=score,hard_pass=valid,changed=changed[idx])
                k.feedback(topic,'expert',encoded(result),source='independent hourly check and priority rubric')
                save(folder,f'{seed}-{arm}.json',dict(original=original,plans=plans,topic=topic,
                     request=request,response=response,fallback=fallback,result=result))
                messages=k.get(topic)['messages']
                usage=response or {}
                row=dict(seed=seed,size=size,brief=profile+1,arm=arm,selected=idx,hard_pass=int(valid),
                         high=int(decisions[0]),medium=int(decisions[1]),low=int(decisions[2]),
                         points=score,changed=changed[idx],calls=len(messages),credits=sum(m['cost'] for m in messages),
                         input_tokens=usage.get('prompt_eval_count',0),output_tokens=usage.get('eval_count',0),
                         seconds=elapsed,fallback=int(bool(fallback)))
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
