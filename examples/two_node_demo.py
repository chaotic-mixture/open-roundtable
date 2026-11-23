"""Exercise real peers through a lost receipt and audit the resulting evidence."""
import argparse
from contextlib import closing, ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from examples.peer_node import packed,digest
from open_roundtable.data import initialize, ingest, connect, catalog


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
    received=[json.loads(v) for d,r,v in traffic if d=='receive' and r.endswith('/consult')]
    require(received==[res],'sender receipt')
    wire=packed([traffic,calls,charges,shared,catalog(b),json.loads((folder/'directory.json').read_text())])
    for root in (a,b):
        markers=read(root,'private.sqlite3','SELECT source,body FROM records')
        require(bool(markers),'missing private control')
        for source,value in markers:
            require(source.encode() not in wire and value.encode() not in wire,'private marker crossed boundary')
    fault=json.loads((folder/'interruption.json').read_text())
    require((fault['exit'],fault['receiver_credits']) in ((75,2),(76,0)) and fault['sender_receipt'] is None
            and fault['before_pid']!=fault['after_pid'],'restart checkpoint')
    return dict(discovered=['A','B'],deliveries=2,model_calls=1,expert_calls=1,credits=cost,
                prompt_tokens=model['prompt_eval_count'],output_tokens=model['eval_count'],
                state=state,private_markers_found=0,request_id=key)


def run(folder,model,fault='after-commit'):
    if fault not in ('before-receipt','after-commit'):
        raise ValueError('unknown fault window')
    folder.mkdir(parents=True,exist_ok=False)
    a,b=folder/'A',folder/'B'
    with ExitStack() as stack:
        descriptors=[]
        for name,root in [('A',a),('B',b)]:
            initialize(root)
            ingest(root,'private',[dict(source='private-key-'+uuid.uuid4().hex,body='secret-'+uuid.uuid4().hex)])
            (root/'node.json').write_bytes(packed(dict(name=name,model=model)))
            proc,port=start(stack,root,0,interrupt=fault if name=='B' else False)
            desc=dict(name=name,url=f'http://127.0.0.1:{port}')
            ingest(root,'public',[dict(source=f'https://{name.lower()}.example/node',body=packed(desc).decode())])
            with closing(connect(root,'public')) as db,db:
                db.execute('UPDATE records SET shared=1')
            descriptors.extend(catalog(root))
        (folder/'directory.json').write_bytes(packed(descriptors))
        class Directory(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path!='/catalog':
                    return self.send_error(404)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(packed(descriptors))
        directory=stack.enter_context(HTTPServer(('127.0.0.1',0),Directory))
        thread=threading.Thread(target=directory.serve_forever,daemon=True)
        thread.start()
        stack.callback(directory.shutdown)
        bootstrap=f'http://127.0.0.1:{directory.server_port}'
        for root in (a,b):
            cli(root,'discover',bootstrap)
        cli(b,'permission','A','allow')
        spec=json.loads(Path(__file__).with_name('consult_input.json').read_text(encoding='utf8'))
        spec.pop('criteria',None)
        ingest(a,'public',[dict(source=SOURCE,body=packed(spec).decode())])
        with closing(connect(a,'public')) as db,db:
            db.execute('UPDATE records SET shared=1 WHERE source=?',(SOURCE,))
        failed=cli(a,'send','B',SOURCE,ok=False)
        exit_code = proc.wait(timeout=10)
        if failed.returncode==0 or exit_code!=(76 if fault=='before-receipt' else 75):
            raise RuntimeError('forced disconnect did not occur')
        with closing(sqlite3.connect(a/'network.sqlite3')) as db:
            receipt=db.execute('SELECT result FROM jobs').fetchone()[0]
        with closing(sqlite3.connect(b/'network.sqlite3')) as db:
            credits=db.execute('SELECT credits FROM jobs').fetchone()[0]
        restarted,_=start(stack,b,port)
        (folder/'interruption.json').write_bytes(packed(dict(exit=exit_code,before_pid=proc.pid,
                after_pid=restarted.pid,sender_receipt=receipt,receiver_credits=credits)))
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
    args=parser.parse_args()
    print(json.dumps(run(args.directory,args.model,args.fault) if args.action=='run' else audit(args.directory)))
