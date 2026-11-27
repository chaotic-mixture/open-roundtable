"""Offline reconciliation of both original and resumable preference benchmarks."""
import csv
import json
import hashlib
import sqlite3
from pathlib import Path
from contextlib import closing
from examples.generate_overnight import generate
from examples.overnight_score import expected
from examples.preference_rubric import assess
from examples.preference_benchmark import BRIEFS_BY_LANGUAGE, facts
from examples.repair_benchmark import schedule
from examples.select_repair import choice


def verify(folder):
    design=json.loads((folder/'design.json').read_text(encoding='utf8'))
    briefs = BRIEFS_BY_LANGUAGE.get(design.get('language', 'zh'))
    if briefs is None or design['briefs'] != briefs:
        raise ValueError('preference briefs differ from the declared language')
    source=Path(__file__).with_name('preference_rubric.py').read_bytes()
    # A published Git checkout may have LF or CRLF line endings.
    lf=source.replace(b'\r\n',b'\n')
    if design['rubric_sha256'] not in {hashlib.sha256(b).hexdigest() for b in (lf,lf.replace(b'\n',b'\r\n'))}:
        raise ValueError('scoring rubric fingerprint differs')
    with (folder/'results.csv').open() as stream:
        rows=list(csv.DictReader(stream))
    keys={(int(r['seed']),r['arm']) for r in rows}
    if len(rows)!=27 or keys!={(s,a) for s in range(501,510) for a in ('default','rules','model')}:
        raise ValueError('incomplete or duplicate batch rows')
    with closing(sqlite3.connect((folder/'ledger.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()!=('ok',) or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('ledger integrity failure')
        menus_by_seed = {}
        for row in rows:
            seed,arm=int(row['seed']),row['arm']
            size=(7,11,22)[(seed-501)//3]
            profile=(seed-501)%3
            data=json.loads((folder/f'{seed}-{arm}.json').read_text(encoding='utf8'))
            topic=data['topic']
            original=generate(seed,size)
            shifts=[]
            for token in original:
                key,span=token.split(':')
                start,end=map(int,span.split('-'))
                shifts.append((key,start,end-start))
            old=[r[1] for r in shifts]
            durations=[r[2] for r in shifts]
            messages=db.execute('SELECT member,body,cost FROM messages WHERE topic=? ORDER BY id',(topic,)).fetchall()
            expert=json.loads(messages[0][1])
            plans=data['plans']
            if menus_by_seed.setdefault(seed, plans) != plans:
                raise ValueError(f'candidate lists differ between arms for seed {seed}')
            if any(len(p)!=len(old) or any(type(h)!=int or not 0<=h<=168-d for h,d in zip(p,durations))
                   or any(expected(schedule(shifts,p)).values()) for p in plans):
                raise ValueError('invalid candidate menu')
            if arm == 'model':
                request = data['request']
                marker = 'Candidates: ' if design.get('language') == 'en' else '候选数据：'
                prompt = request['messages'][0]['content']
                _, separator, menu = prompt.partition(marker)
                expected_menu = [
                    dict(id=i, shifts=facts(plan, shifts), changed=sum(h != o for h, o in zip(plan, old)))
                    for i, plan in enumerate(plans)
                ]
                if not separator or json.loads(menu) != expected_menu:
                    raise ValueError(f'model request candidate facts differ for seed {seed}')
                if request['format']['properties']['plan']['enum'] != list(range(len(plans))):
                    raise ValueError(f'model request candidate IDs differ for seed {seed}')
            idx=expert['tool_choice']
            if arm=='rules':
                def rank(j):
                    return (assess(profile,old,durations,plans[j]),
                            -sum(h!=o for h,o in zip(plans[j],old)),-j)
                if idx!=max(range(len(plans)),key=rank):
                    raise ValueError('incorrect rule ranking')
            elif idx!=0:
                raise ValueError('invalid default plan')
            response=data['response']
            invalid=''
            if response is not None:
                try:
                    idx=choice(response,len(plans))
                except (TypeError,ValueError,KeyError) as exc:
                    invalid=repr(exc)
            plan=plans[idx]
            flags=assess(profile,old,durations,plan)
            score=sum(w*v for w,v in zip((4,2,1),flags))
            moved=sum(x!=y for x,y in zip(plan,old))
            valid=all(0<=h<=168-d for h,d in zip(plan,durations)) and not any(expected(schedule(shifts,plan)).values())
            result=dict(id=idx,priorities=list(flags),score=score,hard_pass=valid,changed=moved)
            usage=response or {}
            counts=dict(size=size,brief=profile+1,selected=idx,high=flags[0],medium=flags[1],low=flags[2],
                        points=score,changed=moved,hard_pass=valid,credits=sum(m[2] for m in messages),
                        input_tokens=usage.get('prompt_eval_count',0),output_tokens=usage.get('eval_count',0),
                        fallback=bool(data['fallback']),calls=len(messages))
            if design.get('format')==2:
                stored=db.execute('SELECT evidence,metrics FROM outcomes WHERE topic=?',(topic,)).fetchone()
                attempts=db.execute('SELECT status,request,response,error FROM attempts WHERE topic=? ORDER BY id',(topic,)).fetchall()
                counts['calls']=1+len(attempts)
                if json.loads(stored[0])!=data or any(str(v)!=row[k] for k,v in json.loads(stored[1]).items()):
                    raise ValueError('export differs from journal')
                if attempts:
                    status,request,receipt,error=attempts[-1]
                    if json.loads(request)!=data['request'] or (json.loads(receipt) if receipt else None)!=response:
                        raise ValueError('dispatch receipt mismatch')
                    if status=='failed' and error!=data['fallback']:
                        raise ValueError('failure evidence mismatch')
            feedback=db.execute('SELECT body FROM feedback WHERE topic=? ORDER BY id DESC LIMIT 1',(topic,)).fetchone()
            title,balance=db.execute('SELECT title,credits FROM topics WHERE id=?',(topic,)).fetchone()
            if (original!=data['original'] or expert['plans']!=plans or data['result']!=result
                    or json.loads(feedback[0])!=result or title!=f'{seed}/{arm}' or balance!=2-counts['credits']
                    or [m[0] for m in messages] != (['expert','model'] if response is not None else ['expert'])
                    or any(m[2]!=1 for m in messages) or any(int(row[k])!=v for k,v in counts.items())
                    or (response is not None and (messages[-1][0]!='model' or json.loads(messages[-1][1])!=response))
                    or (arm=='model' and briefs[profile] not in data['request']['messages'][0]['content'])
                    or (response is not None and data['fallback']!=invalid)):
                raise ValueError(f'evidence mismatch for {seed}/{arm}')
    return {'verified_rows':len(rows),'retryable':sum(int(r.get('retryable',0)) for r in rows)}
