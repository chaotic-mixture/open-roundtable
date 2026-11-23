"""Exercise real peers through a lost receipt and audit the resulting evidence."""
import argparse
import hashlib
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

from examples.peer_node import packed,digest
from open_roundtable.data import initialize, ingest, connect, catalog, share_public


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
    require((topic,state,balance)==(res['outcome']['topic'],'RESOLVED',0),'consultation incomplete')
    body=json.loads(read(b,key+'.sqlite3','SELECT body FROM proposals ORDER BY version DESC LIMIT 1')[0][0])
    require(all(res['outcome'][k]==v for k,v in body.items()),'proposal mismatch')
    require(dict(read(b,key+'.sqlite3','SELECT member,agree FROM votes'))=={'expert':1,'model':1},'ballots')
    shared=catalog(a)
    require(any(r['source']==req['source'] and json.loads(r['body'])==req['spec'] for r in shared),
            'unpublished request')
    require(req['spec']==json.loads(charges[0][1])['input'],'worker received different topic')
    traffic=[]
    for root,other in [(a,'B'),(b,'A')]:
        require(other in dict(read(root,'network.sqlite3','SELECT name,url FROM peers')),'discovery')
        traffic+=read(root,'network.sqlite3','SELECT direction,route,body FROM traffic')
    accepted=[json.loads(v) for d,r,v in traffic if d=='accept' and r=='/consult']
    replies=[json.loads(v) for d,r,v in traffic if d=='reply' and r=='/consult']
    require(accepted==[req,req] and replies==[res],'lost-receipt replay sequence')
    if 'offer' in req:
        source, checksum = req['offer']
        observed = [record for direction,route,value in traffic if direction=='receive' and route.endswith(('/catalog','/offers'))
                    for record in json.loads(value) if record['source']==source]
        require(any(hashlib.sha256(r['body'].encode()).hexdigest()==checksum for r in observed),
                'missing published offer revision')
        require(all(r['source']!=source for r in catalog(b)), 'offer still published after interruption')
    received=[json.loads(v) for d,r,v in traffic if d=='receive' and r.endswith('/consult')]
    require(received==[res],'sender receipt')
    fault=json.loads((folder/'interruption.json').read_text())
    directory_snapshot = json.loads((folder/'directory.json').read_text())
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
        require(served == [directory_snapshot, directory_snapshot], 'directory discovery responses mismatch')
        traffic += directory_traffic
    wire=packed([traffic,calls,charges,shared,catalog(b),directory_snapshot])
    for root in (a,b):
        markers=read(root,'private.sqlite3','SELECT source,body FROM records')
        require(bool(markers),'missing private control')
        for source,value in markers:
            require(source.encode() not in wire and value.encode() not in wire,'private marker crossed boundary')
    require((fault['exit'],fault['receiver_credits']) in ((75,2),(76,0)) and fault['sender_receipt'] is None
            and fault['before_pid']!=fault['after_pid'],'restart checkpoint')
    return dict(discovered=['A','B'],deliveries=2,model_calls=1,expert_calls=1,credits=cost,
                prompt_tokens=model['prompt_eval_count'],output_tokens=model['eval_count'],
                state=state,private_markers_found=0,request_id=key)


def run(folder,model,fault='after-commit', *, credentials=None, bind_offer=False):
    if fault not in ('before-receipt','after-commit'):
        raise ValueError('unknown fault window')
    folder.mkdir(parents=True,exist_ok=False)
    a,b=folder/'A',folder/'B'
    with ExitStack() as stack:
        scheme = 'https' if credentials else 'http'
        directory = folder/'directory'
        initialize(directory)
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
        for root in (a,b):
            cli(root,'discover',bootstrap)
        cli(b,'permission','A','allow','--remaining','1')
        spec=json.loads(Path(__file__).with_name('consult_input.json').read_text(encoding='utf8'))
        spec.pop('criteria',None)
        ingest(a,'public',[dict(source=SOURCE,body=packed(spec).decode())])
        with closing(connect(a,'public')) as db,db:
            db.execute('UPDATE records SET shared=1 WHERE source=?',(SOURCE,))
        offer_args = []
        if bind_offer:
            published = json.loads(cli(b,'offer','https://b.example/offers/weekend','repair','Repair weekly shifts').stdout)
            share_public(b,published['source'],True,published['sha256'])
            candidates = json.loads(cli(a,'find-capability','repair').stdout)['matches']
            selected, = candidates
            offer_args = ['--offer', selected['source'], selected['sha256']]
        failed=cli(a,'send','B',SOURCE,*offer_args,ok=False)
        exit_code = proc.wait(timeout=10)
        if failed.returncode==0 or exit_code!=(76 if fault=='before-receipt' else 75):
            raise RuntimeError('forced disconnect did not occur')
        with closing(sqlite3.connect(a/'network.sqlite3')) as db:
            receipt=db.execute('SELECT result FROM jobs').fetchone()[0]
        with closing(sqlite3.connect(b/'network.sqlite3')) as db:
            credits=db.execute('SELECT credits FROM jobs').fetchone()[0]
        if bind_offer:
            share_public(b,published['source'],False)
        restarted,_=start(stack,b,port)
        (folder/'interruption.json').write_bytes(packed(dict(exit=exit_code,before_pid=proc.pid,
                after_pid=restarted.pid,sender_receipt=receipt,receiver_credits=credits,directory_ledger=True)))
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
            result = run(args.directory,args.model,args.fault,credentials=credentials,bind_offer=args.bind_offer)
    print(json.dumps(result))
