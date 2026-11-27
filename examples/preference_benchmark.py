"""Three paired selectors for natural-language priorities, conflicts and exceptions."""
import argparse
import csv
import hashlib
import json
from contextlib import closing
from pathlib import Path
from time import perf_counter
from urllib.error import URLError
from open_roundtable.run_journal import single_writer, initialize, dispatch

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


ENGLISH_BRIEFS = [
    "A needs to pick up a child. Most importantly, finish by 18:00 inclusive on the day the shift starts. "
    "If A's original shift crossed midnight, pickup is already covered: keep A's original start instead. "
    "Next, the night coordinator wants A to start at or after 18:00. Finally, keep B on its original date.",
    "B takes a shuttle. First, start at or after 06:00 and before 10:00. If B originally started before "
    "06:00, a car is already booked: keep the original date and start time instead. Next, the evening team "
    "wants B to start at or after 14:00 and before 22:00. Finally, C would like to start no earlier than originally planned.",
    "C has an appointment. First, keep C's original date and start time. If C's original shift lasted at "
    "least eight hours, cover is available that day: keeping the date alone is enough. Next, delay C by "
    "at least two hours for handover. Finally, keep A's start within two hours of its original start.",
]
BRIEFS_BY_LANGUAGE = {'zh': BRIEFS, 'en': ENGLISH_BRIEFS}


def run(folder, model, *, expand=False, language='zh'):
    with single_writer(folder):
        _run(folder, model, expand, language)


def _run(folder, model, expand, language):
    design = dict(expand=expand, language=language, briefs=BRIEFS_BY_LANGUAGE[language], seeds=list(range(501,510)), profiles='rotate 0,1,2',
                  weights=[4,2,1], ties='fewest changed shifts, then lowest ID',
                  rubric_sha256=hashlib.sha256(Path(__file__).with_name('preference_rubric.py').read_bytes()).hexdigest(),
                  model={'name':model})
    root=Path(__file__).resolve().parents[1]
    design['implementation']={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                              for name in ('examples/preference_benchmark.py','examples/refine_schedule.py',
                                           'examples/repair_benchmark.py','examples/generate_overnight.py',
                                           'open_roundtable/schedule_expert.py','open_roundtable/run_journal.py')}
    if (folder/'design.json').exists():
        prior=json.loads((folder/'design.json').read_text(encoding='utf8'))
        if prior.get('format') != 2 or prior['model']['name']!=model or any(
                prior.get(key)!=value for key,value in design.items() if key!='model'):
            raise ValueError('incompatible run directory')
        design=prior
    else:
        if (folder/'ledger.sqlite3').exists():
            raise ValueError('existing ledger without matching design')
        try:
            design['model']=next((m for m in api('/api/tags')['models'] if m['name']==model),design['model'])
            design['ollama']=api('/api/version')
        except (URLError,TimeoutError,ConnectionError) as exc:
            design['ollama']={'unavailable':repr(exc)}
        design['format']=2
        save(folder,'design.json',design)
    with closing(Kernel(folder/'ledger.sqlite3')) as k:
        initialize(k.db)
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
                title=f'{seed}/{arm}'
                existing=k.db.execute('SELECT id FROM topics WHERE title=?',(title,)).fetchone()
                topic=existing[0] if existing else k.create(title,credits=2)
                outcome=k.db.execute('SELECT metrics FROM outcomes WHERE topic=?',(topic,)).fetchone()
                if outcome and not json.loads(outcome[0])['retryable']:
                    continue
                current=k.get(topic)
                joined={m['id'] for m in current['members']}
                if 'expert' not in joined:
                    k.join(topic,'expert',kind='expert',source='verified candidate generator')
                if arm=='model' and 'model' not in joined:
                    k.join(topic,'model',kind='model',source=model)
                if current['state']=='DRAFT':
                    k.start(topic)
                posted={m['member'] for m in current['messages']}
                t=perf_counter()
                plans=candidates(rows)
                if expand:
                    from examples.refine_schedule import refine
                    def rank(plan):
                        moved = sum(s!=o for s,o in zip(plan,old))
                        return rule_key(profile,facts(plan,rows),moved,0)
                    plans=refine(rows,plans,rank,range(3))
                if not plans or any(any(check(schedule(rows,p)).values()) or any(
                        not 0<=s<=168-d for s,d in zip(p,durations)) for p in plans):
                    raise ValueError('candidate verification failed')
                changed=[sum(s!=o for s,o in zip(p,old)) for p in plans]
                menu=[dict(id=j,shifts=facts(p,rows),changed=changed[j]) for j,p in enumerate(plans)]
                idx=0
                if arm=='rules':
                    idx=max(range(len(plans)),key=lambda j:rule_key(profile,menu[j]['shifts'],changed[j],j))
                elapsed=perf_counter()-t
                if 'expert' not in posted:
                    assert_post(k,topic,'expert',encoded(dict(plans=plans,tool_choice=idx)))
                request=response=None
                fallback=''
                if arm=='model':
                    instructions = (' Compare priorities from highest to lowest. Break ties by fewer changed shifts, '
                         'then lower candidate ID. Crossing midnight requires working after midnight. A shift ending '
                         'exactly at midnight does not cross it. All candidates are feasible. Dates start at day 0; '
                         'absolute start times count hours from midnight on day 0. Return only the candidate ID. Candidates: '
                         if language == 'en' else '。请按优先级从高到低逐项比较；相同则少改班优先，再选编号小的。'
                         '跨日指午夜之后仍在工作，午夜恰好下班不算。18点下班指开工当天。'
                         '候选均已验证可行。日期从0计，absolute start从第0天午夜计时。'
                         '仅返回候选编号。候选数据：')
                    msg = design['briefs'][profile] + instructions + encoded(menu)
                    request=dict(model=model,stream=False,think=False,options={'temperature':0},
                                 messages=[dict(role='user',content=msg)],format={'type':'object',
                                 'required':['plan'],'additionalProperties':False,'properties':{
                                 'plan':{'type':'integer','enum':list(range(len(plans)))}}})
                    response,fallback,duration=dispatch(k,topic,request,api)
                    elapsed+=k.db.execute('SELECT coalesce(sum(seconds),0) FROM attempts WHERE topic=?',
                                          (topic,)).fetchone()[0]
                    if response is not None:
                        try:
                            idx=choice(response,len(plans))
                        except (TypeError,ValueError,KeyError) as err:
                            fallback=repr(err)
                        if 'model' not in posted:
                            assert_post(k,topic,'model',encoded(response))
                decisions=assess(profile,old,durations,plans[idx])
                score=sum(w*int(b) for w,b in zip(design['weights'],decisions))
                valid=not any(expected(schedule(rows,plans[idx])).values())
                result=dict(id=idx,priorities=decisions,score=score,hard_pass=valid,changed=changed[idx])
                evidence=dict(original=original,plans=plans,topic=topic,
                     request=request,response=response,fallback=fallback,result=result)
                messages=k.get(topic)['messages']
                usage=response or {}
                row=dict(seed=seed,size=size,brief=profile+1,arm=arm,selected=idx,hard_pass=int(valid),
                         high=int(decisions[0]),medium=int(decisions[1]),low=int(decisions[2]),
                         points=score,changed=changed[idx],calls=1+k.db.execute(
                             'SELECT count(*) FROM attempts WHERE topic=?',(topic,)).fetchone()[0],credits=sum(m['cost'] for m in messages),
                         input_tokens=usage.get('prompt_eval_count',0),output_tokens=usage.get('eval_count',0),
                         seconds=elapsed,fallback=int(bool(fallback)))
                row['retryable']=int(arm=='model' and response is None)
                with k.db:
                    k.db.execute('INSERT INTO feedback(topic,member,body,source) VALUES (?,?,?,?)',
                                 (topic,'expert',encoded(result),'independent hourly check and priority rubric'))
                    k.db.execute('INSERT OR REPLACE INTO outcomes VALUES (?,?,?)',
                                 (topic,encoded(evidence),json.dumps(row)))
                export(folder,k)
        export(folder,k)


def export(folder,k):
    items=k.db.execute('SELECT evidence,metrics FROM outcomes ORDER BY rowid').fetchall()
    with (folder/'results.tmp').open('w',newline='') as out:
        writer=None
        for item in items:
            row=json.loads(item['metrics'])
            if writer is None:
                writer=csv.DictWriter(out,fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            save(folder,f"{row['seed']}-{row['arm']}.json",json.loads(item['evidence']))
    (folder/'results.tmp').replace(folder/'results.csv')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--model',default='qwen3.5:9b')
    parser.add_argument('--expand',action='store_true',help='search additional feasible schedules')
    parser.add_argument('--language', choices=BRIEFS_BY_LANGUAGE, default='zh', help='model request language (default: zh)')
    args=parser.parse_args()
    run(args.directory,args.model,expand=args.expand,language=args.language)
