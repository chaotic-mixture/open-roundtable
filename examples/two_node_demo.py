"""Exercise real peers through a lost receipt and audit the resulting evidence."""
import argparse
from contextlib import closing, ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode

from examples.peer_node import packed,digest,verify_offer
from open_roundtable.data import initialize, ingest, catalog, share_public


SOURCE='https://roundtable.example/topics/weekend'


def cli(root,*args,ok=True):
    return subprocess.run([sys.executable,'-m','examples.peer_node',str(root),*args],
                          capture_output=True,timeout=320,check=ok)


def start(stack,root,port,interrupt=False):
    ready=root/'ready'
    ready.unlink(missing_ok=True)
    log=stack.enter_context((root/'server.log').open('ab'))
    cmd=[sys.executable,'-m','examples.peer_node',str(root),'serve',str(port)]
    if interrupt:
        cmd.append('--interrupt-before-receipt' if interrupt=='before-receipt' else '--interrupt-after-commit')
    proc=subprocess.Popen(cmd,stdout=log,stderr=log)
    def stop():
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
    stack.callback(stop)
    deadline=time.monotonic()+15
    while not ready.exists() or not ready.read_text():
        if proc.poll() is not None or time.monotonic()>deadline:
            raise RuntimeError('node startup failed')
        time.sleep(.05)
    return proc,int(ready.read_text())


def audit(folder):
    def require(condition,message):
        if not condition:
            raise ValueError(message)
    def read(root,filename,sql):
        path=(root/filename).resolve().as_uri()+'?mode=ro'
        with closing(sqlite3.connect(path,uri=True)) as db:
            require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'SQLite integrity')
            return db.execute(sql).fetchall()
    a,b=folder/'A',folder/'B'
    jobs='SELECT id,request,result,credits FROM jobs'
    sender=read(a,'network.sqlite3',jobs)
    require(len(sender)==1 and sender==read(b,'network.sqlite3',jobs),'endpoints disagree')
    key,request,result,cost=sender[0]
    req,res=json.loads(request),json.loads(result)
    require(key==digest({k:v for k,v in req.items() if k!='id'})==res['id'],'receipt ID')
    require(req['sender']=='A' and req['recipient']=='B' and res['recipient']=='A' and res['sender']=='B','roles')
    calls=read(b,key+'.sqlite3','SELECT status,request,response FROM attempts')
    charges=read(b,key+'.sqlite3','SELECT member,body,cost FROM messages ORDER BY id')
    require(len(calls)==1 and calls[0][0]=='returned','model dispatch count')
    model=json.loads(calls[0][2])
    require(model['done'] and model['eval_count']>0 and model['prompt_eval_count']>0,'missing inference usage')
    require([(r[0],r[2]) for r in charges]==[('expert',1),('model',1)] and cost==res['credits']==2,'charge mismatch')
    require(json.loads(charges[1][1])==model,'model receipt')
    topic,state,balance=read(b,key+'.sqlite3','SELECT id,state,credits FROM topics')[0]
    body=json.loads(read(b,key+'.sqlite3','SELECT body FROM proposals ORDER BY version DESC LIMIT 1')[0][0])
    require(all(res['outcome'][k]==v for k,v in body.items()),'proposal mismatch')
    expert_agrees = True
    preferences = {}
    if req['spec'].get('criteria'):
        from examples.user_consult import validate_roster
        from examples.roster_criteria import evaluate
        rows, rules = validate_roster(req['spec'])
        def rank(schedule):
            starts = [shift['start'] for shift in schedule]
            return evaluate(rules, rows, starts), -sum(h != row[1] for h,row in zip(starts,rows))
        candidates = json.loads(charges[0][1])['candidates']
        require(body['schedule'] == candidates[body['selected']], 'selected schedule mismatch')
        score = rank(body['schedule'])
        require(list(score[0]) == body['criteria_met'], 'preference score mismatch')
        best_score = max(map(rank, candidates))
        expert_agrees = score == best_score
        preferences = dict(criteria_met=list(score[0]), best_candidate_criteria_met=list(best_score[0]),
                           changed_shifts=-score[1], best_candidate_changed_shifts=-best_score[1])
    expected_state = 'RESOLVED' if expert_agrees else 'PAUSED'
    require((topic,state,balance)==(res['outcome']['topic'],expected_state,0)
            and res['outcome']['state']==state,'consultation state mismatch')
    require(dict(read(b,key+'.sqlite3','SELECT member,agree FROM votes'))=={'expert':int(expert_agrees),'model':1},'ballots')
    shared=catalog(a)
    require(any(r['source']==req['source'] and json.loads(r['body'])==req['spec'] for r in shared),
            'unpublished request')
    require(req['spec']==json.loads(charges[0][1])['input'],'worker received different topic')
    endpoint_traffic = {}
    for root,other in [(a,'B'),(b,'A')]:
        require(other in dict(read(root,'network.sqlite3','SELECT name,url FROM peers')),'discovery')
        endpoint_traffic[root]=read(root,'network.sqlite3','SELECT direction,route,body FROM traffic ORDER BY rowid')
    traffic = endpoint_traffic[a] + endpoint_traffic[b]
    fault=json.loads((folder/'interruption.json').read_text())
    allowance = {}
    if 'remaining_at_disconnect' in fault:
        remaining = read(b, 'network.sqlite3', "SELECT remaining FROM grant_limits WHERE name='A'")
        require(type(fault['remaining_at_disconnect']) is int and fault['remaining_at_disconnect'] == 0
                and remaining == [(0,)], 'receiver allowance mismatch')
        allowance = dict(remaining_at_disconnect=fault['remaining_at_disconnect'], remaining_after_replay=remaining[0][0])
    directory_snapshot = json.loads((folder/'directory.json').read_text())
    registrations = [json.loads(r['body']) for r in directory_snapshot]
    recipient = req['recipient']
    origins = [r['url'] for r in registrations if r['name'] == recipient]
    require(len(origins) == 1, 'missing unique recipient registration')
    origin = origins[0]
    route = origin + '/consult'
    def consultation_events(root):
        return [(d, r, json.loads(v)) for d, r, v in endpoint_traffic[root] if r.endswith('/consult')]
    require(consultation_events(a) == [('send',route,req), ('send',route,req), ('receive',route,res)],
            'sender replay sequence')
    require(consultation_events(b) == [('accept','/consult',req), ('accept','/consult',req), ('reply','/consult',res)],
            'receiver replay sequence')
    if 'offer' in req:
        observed = []
        for direction, route, value in endpoint_traffic[a]:
            if direction == 'send' and route == origin + '/consult' and json.loads(value) == req:
                break
            if direction == 'receive' and route in (origin + '/offers', origin + '/catalog'):
                observed.extend(json.loads(value))
        else:
            raise ValueError('missing dispatch to registered offer owner')
        verify_offer(observed, recipient, req['offer'], repair_only=True)
        require(all(r['source'] != req['offer'][0] for r in catalog(b)), 'offer still published after interruption')
    directory = folder/'directory'
    if fault.get('directory_ledger') or directory.exists():
        published = read(directory,'public.sqlite3','SELECT source,body FROM records WHERE shared=1 ORDER BY source')
        require([dict(source=s,body=v) for s,v in published] == directory_snapshot, 'directory snapshot mismatch')
        directory_traffic = read(directory,'network.sqlite3','SELECT direction,route,body FROM traffic ORDER BY rowid')
        imports = {route: json.loads(body) for direction,route,body in directory_traffic if direction == 'receive'}
        require(len(directory_snapshot) == 2 and len(imports) == 2, 'directory registration count')
        for record in directory_snapshot:
            origin = json.loads(record['body'])['url']
            route = origin + '/record?' + urlencode({'source': record['source']})
            require(imports.get(route) == [record], 'directory registration import mismatch')
        served = [json.loads(body) for direction,route,body in directory_traffic if direction == 'serve' and route == '/nodes']
        discovery_snapshot = [record.copy() for record in directory_snapshot]
        if read(directory,'public.sqlite3',"SELECT 1 FROM sqlite_master WHERE type='table' AND name='node_withdrawals'"):
            tokens = dict(read(directory,'public.sqlite3','SELECT source,token FROM node_withdrawals'))
            for record in discovery_snapshot:
                if record['source'] in tokens:
                    record['publication'] = tokens[record['source']]
        require(served == [discovery_snapshot, discovery_snapshot], 'directory discovery responses mismatch')
        discovery_routes = set()
        for root in (a, b):
            receipts = read(root, 'network.sqlite3',
                            "SELECT route,body FROM traffic WHERE direction='receive' AND route LIKE '%/nodes'")
            require(len(receipts) == 1 and json.loads(receipts[0][1]) == discovery_snapshot,
                    f'{root.name} directory receipt mismatch')
            discovery_routes.add(receipts[0][0])
        require(len(discovery_routes) == 1, 'nodes used different discovery endpoints')
        traffic += directory_traffic
    wire=packed([traffic,calls,charges,shared,catalog(b),directory_snapshot])
    marker_roots = [a, b]
    if fault.get('directory_private_control'):
        marker_roots.append(directory)
    for root in marker_roots:
        markers=read(root,'private.sqlite3','SELECT source,body FROM records')
        require(bool(markers),'missing private control')
        for source,value in markers:
            require(source.encode() not in wire and value.encode() not in wire,'private marker crossed boundary')
    require(type(fault['exit']) is int and type(fault['receiver_credits']) is int
            and (fault['exit'],fault['receiver_credits']) in ((75,2),(76,0)) and fault['sender_receipt'] is None
            and fault['before_pid']!=fault['after_pid'],'restart checkpoint')
    if 'kernel_receipt_sha256' in fault:
        require(fault['kernel_receipt_sha256'] == digest(res), 'recovered receipt differs from pre-restart checkpoint')
    return dict(discovered=['A','B'],deliveries=2,model_calls=1,expert_calls=1,credits=cost,
                fault_window={75:'after-commit', 76:'before-receipt'}[fault['exit']],
                receiver_credits_at_disconnect=fault['receiver_credits'],
                prompt_tokens=model['prompt_eval_count'],output_tokens=model['eval_count'],
                state=state,private_markers_found=0,private_stores_checked=[root.name for root in marker_roots],
                request_id=key, **allowance, **preferences)


def run(folder,model,fault='after-commit', *, credentials=None, bind_offer=False, language='zh', with_criteria=False):
    fixture = {'en': 'consult_input.en.json', 'zh': 'consult_input.json'}[language]
    if fault not in ('before-receipt','after-commit'):
        raise ValueError('unknown fault window')
    folder.mkdir(parents=True,exist_ok=False)
    a,b=folder/'A',folder/'B'
    with ExitStack() as stack:
        scheme = 'https' if credentials else 'http'
        directory = folder/'directory'
        initialize(directory)
        ingest(directory,'private',[dict(source='private-key-'+uuid.uuid4().hex,body='secret-'+uuid.uuid4().hex)])
        directory_config = dict(name='directory')
        if credentials:
            directory_config.update(transport='mtls', credentials=str((credentials/'directory').resolve()))
        (directory/'node.json').write_bytes(packed(directory_config))
        for name,root in [('A',a),('B',b)]:
            initialize(root)
            ingest(root,'private',[dict(source='private-key-'+uuid.uuid4().hex,body='secret-'+uuid.uuid4().hex)])
            config = dict(name=name,model=model)
            if credentials:
                config.update(transport='mtls', credentials=str((credentials/name).resolve()))
            (root/'node.json').write_bytes(packed(config))
            proc,port=start(stack,root,0,interrupt=fault if name=='B' else False)
            source = f'https://{name.lower()}.example/node'
            registration = json.loads(cli(root,'register',source,f'{scheme}://127.0.0.1:{port}').stdout)
            share_public(root,source,True,registration['sha256'])
            cli(directory,'import-registration',name,source,f'{scheme}://127.0.0.1:{port}')
            share_public(directory,source,True,registration['sha256'])
        (folder/'directory.json').write_bytes(packed(catalog(directory)))
        _, directory_port = start(stack,directory,0)
        bootstrap=f'{scheme}://127.0.0.1:{directory_port}'
        discovery = ['discover', bootstrap]
        if credentials:
            discovery.extend(['--directory-name', directory_config['name']])
        for root in (a,b):
            cli(root, *discovery)
        cli(b,'permission','A','allow','--remaining','1')
        spec=json.loads(Path(__file__).with_name(fixture).read_text(encoding='utf8'))
        if not with_criteria:
            spec.pop('criteria',None)
        ingest(a,'public',[dict(source=SOURCE,body=packed(spec).decode())])
        share_public(a,SOURCE,True,digest(spec))
        offer_args = []
        recipient = 'B'
        if bind_offer:
            published = json.loads(cli(b,'offer','https://b.example/offers/weekend','repair','Repair weekly shifts').stdout)
            share_public(b,published['source'],True,published['sha256'])
            candidates = json.loads(cli(a,'find-capability','repair','reschedule').stdout)['matches']
            selected, = candidates
            recipient = selected['node']
            offer_args = ['--offer', selected['source'], selected['sha256']]
        failed=cli(a,'send',recipient,SOURCE,*offer_args,ok=False)
        exit_code = proc.wait(timeout=10)
        if failed.returncode==0 or exit_code!=(76 if fault=='before-receipt' else 75):
            raise RuntimeError('forced disconnect did not occur')
        with closing(sqlite3.connect(a/'network.sqlite3')) as db:
            receipt=db.execute('SELECT result FROM jobs').fetchone()[0]
        with closing(sqlite3.connect(b/'network.sqlite3')) as db:
            key,credits=db.execute('SELECT id,credits FROM jobs').fetchone()
            remaining, = db.execute("SELECT remaining FROM grant_limits WHERE name='A'").fetchone()
        with closing(sqlite3.connect((b/(key+'.sqlite3')).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            checkpoint = json.loads(db.execute('SELECT result FROM peer_receipt WHERE id=?',(key,)).fetchone()[0])
        if bind_offer:
            share_public(b,published['source'],False)
        restarted,_=start(stack,b,port)
        (folder/'interruption.json').write_bytes(packed(dict(exit=exit_code,before_pid=proc.pid,
                after_pid=restarted.pid,sender_receipt=receipt,receiver_credits=credits,directory_ledger=True,
                directory_private_control=True,remaining_at_disconnect=remaining,
                kernel_receipt_sha256=digest(checkpoint))))
        cli(a,'resume')
        if json.loads(cli(a,'resume').stdout) != {}:
            raise RuntimeError('completed job still pending')
    result=audit(folder)
    (folder/'audit.json').write_bytes(packed(result))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','verify'])
    parser.add_argument('directory',type=Path)
    parser.add_argument('--model',default='qwen3.5:9b')
    parser.add_argument('--language', choices=['en', 'zh'], default='zh', help='language of the synthetic scheduling request')
    parser.add_argument('--with-criteria', action='store_true', help='include the fixture’s ordered preference criteria')
    parser.add_argument('--fault',choices=['after-commit','before-receipt'],default='after-commit')
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument('--temporary-tls', metavar='OPENSSL', help='issue disposable loopback credentials using this OpenSSL executable')
    tls.add_argument('--credentials', type=Path, help='TLS credentials directory containing A, B and directory')
    parser.add_argument('--bind-offer',action='store_true',help='select a published offer and withdraw it before replay')
    args=parser.parse_args()
    if args.action=='verify':
        result = audit(args.directory)
    else:
        with ExitStack() as stack:
            credentials = args.credentials
            if args.temporary_tls:
                from examples.demo_certificates import issue_certificates
                credentials = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                issue_certificates(credentials,args.temporary_tls)
            result = run(args.directory,args.model,args.fault,credentials=credentials,bind_offer=args.bind_offer,
                         language=args.language,with_criteria=args.with_criteria)
    print(json.dumps(result))
