"""One local Ollama review of an overnight schedule, with offline evidence replay."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
from time import perf_counter
from urllib.request import Request, urlopen

from examples.generate_overnight import generate
from examples.overnight_score import expected
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


RULES = ('P', 'V', 'R', 'L')
HOST = 'http://127.0.0.1:11434'
SOURCES = ('examples/auto_consult.py', 'examples/generate_overnight.py',
           'examples/overnight_score.py', 'open_roundtable/core.py',
           'open_roundtable/schedule_expert.py')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def lists(findings):
    return {r: sorted(findings[r]) for r in RULES}


def prompt(shifts, findings, version):
    if version == 2:
        rows = []
        for token in shifts:
            (shift, worker, room), span = token.split(':')
            start, end = map(int, span.split('-'))
            rows.append(dict(shift_id=shift, worker=worker, room=room, start=start, end=end))
        shifts = rows
    return ('Review this overnight roster and the expert proposal. Each shift is ID, worker, room, '
            'then absolute start-end hours; 24 is midnight. Report all positive same-worker '
            'overlaps (P) and same-room overlaps (V) as alphabetical shift pairs. Merge touching '
            'or overlapping shifts per worker. Report positive gaps <8h (R) as worker@end-start '
            'and blocks >8h (L) as worker@start-end. Return JSON with final findings P,V,R,L '
            '(arrays), approve_final (boolean: do you endorse your final answer?) and reason '
            '(brief explanation citing at least one actual shift).\nShifts: '+encoded(shifts)
            +'\nExpert: '+encoded(findings))


def api(path, payload=None):
    data = None if payload is None else encoded(payload).encode()
    req = Request(HOST+path, data=data, headers={'Content-Type':'application/json'})
    with urlopen(req, timeout=300) as response:
        return json.load(response)


def grade(shifts, final):
    truth = expected(shifts)
    scores = {}
    for r in RULES:
        values = final[r]
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f'invalid findings for {r}')
        predicted = set(values)
        scores[r] = {'truth': sorted(truth[r]), 'missing': sorted(truth[r]-predicted),
                     'extra': sorted(predicted-truth[r])}
    return scores


def save(folder, name, value):
    (folder/name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def run(folder, model, seed, provider=api):
    folder.mkdir(parents=True, exist_ok=False)
    shifts = generate(seed, 7)
    models = provider('/api/tags')['models']
    installed = next(m for m in models if m['name'] == model)
    audit = {'model': installed, 'ollama': provider('/api/version'), 'python': platform.python_version(),
             'seed': seed, 'shifts': shifts, 'started': datetime.now(timezone.utc).isoformat(),
             'initial_credits': 2, 'prompt_version': 2}
    root = Path(__file__).resolve().parents[1]
    audit['source_sha256'] = {s: hashlib.sha256((root/s).read_bytes()).hexdigest() for s in SOURCES}
    save(folder, 'run.json', audit)
    with closing(Kernel(folder/'ledger.sqlite3')) as k:
        topic = k.create('Overnight schedule consultation', credits=2)
        k.join(topic,'expert',kind='expert',source='open_roundtable.schedule_expert.check')
        k.join(topic,'model',kind='model',source=model)
        k.start(topic)
        start = perf_counter()
        proposal = lists(check(shifts))
        audit['expert_seconds'] = perf_counter()-start
        assert_post(k, topic, 'expert', encoded(proposal))
        payload = {'model':model, 'stream':False, 'think':False, 'format':'json',
                   'options':{'temperature':0}, 'messages':[{'role':'user',
                   'content':prompt(shifts, proposal, audit['prompt_version'])}]}
        save(folder, 'request.json', payload)
        start = perf_counter()
        response = provider('/api/chat', payload)
        audit['model_seconds'] = perf_counter()-start
        save(folder, 'response.json', response)
        raw = response['message']['content']
        assert_post(k, topic, 'model', raw)
        final = json.loads(raw)
        scores = grade(shifts, final)
        if type(final['approve_final']) is not bool or not isinstance(final['reason'], str):
            raise ValueError('missing explicit model verdict')
        k.vote(topic,'model',final['approve_final'])
        k.vote(topic,'expert',all(set(final[r]) == set(proposal[r]) for r in RULES))
        k.feedback(topic,'expert',encoded(scores),source='examples.overnight_score.expected')
        audit.update(topic=topic, finished=datetime.now(timezone.utc).isoformat())
        save(folder, 'run.json', audit)
    verify(folder)


def assert_post(kernel, topic, member, body):
    if not kernel.post(topic, member, body, cost=1):
        raise RuntimeError('insufficient budget')


def verify(folder):
    def read(name):
        return json.loads((folder/name).read_text(encoding='utf-8'))
    audit, request, response = map(read, ('run.json','request.json','response.json'))
    shifts, topic = audit['shifts'], audit['topic']
    proposal = lists(check(shifts))
    final = json.loads(response['message']['content'])
    scores = grade(shifts, final)
    with closing(sqlite3.connect((folder/'ledger.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        state, balance = db.execute('SELECT state,credits FROM topics WHERE id=?',(topic,)).fetchone()
        messages = db.execute('SELECT member,body,cost FROM messages WHERE topic=? ORDER BY id',
                              (topic,)).fetchall()
        votes = dict(db.execute('SELECT member,agree FROM votes WHERE topic=?',(topic,)))
        feedback = db.execute('SELECT body FROM feedback WHERE topic=?',(topic,)).fetchall()
        checks = [db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],
                  not db.execute('PRAGMA foreign_key_check').fetchall(),
                  generate(audit['seed'],7)==shifts,
                  request['messages']==[{'role':'user','content':prompt(shifts,proposal,audit.get('prompt_version',1))}],
                  request['model']==response['model']==audit['model']['name'],
                  response['done'] and response['done_reason']=='stop',
                  messages==[('expert',encoded(proposal),1),('model',response['message']['content'],1)],
                  balance==0 and audit['initial_credits']==sum(m[2] for m in messages),
                  votes=={'model':final['approve_final'],'expert':all(set(final[r])==set(proposal[r]) for r in RULES)},
                  state==('RESOLVED' if all(votes.values()) else 'PAUSED'),
                  feedback==[(encoded(scores),)]]
    if not all(checks):
        raise ValueError('evidence mismatch')
    print(encoded({'state':state,'credits_spent':2,'prompt_tokens':response['prompt_eval_count'],
                   'completion_tokens':response['eval_count'],'scores':scores}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run','verify'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--model',default='qwen3.5:9b')
    parser.add_argument('--seed', type=int, default=31)
    args = parser.parse_args()
    if args.action == 'run':
        run(args.directory, args.model, args.seed)
    else:
        verify(args.directory)
