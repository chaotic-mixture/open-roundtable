"""Loopback-only two-node transport for the synthetic Phase 2 experiment."""
import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from open_roundtable.data import catalog, NoRedirect
from examples.user_consult import consult


def packed(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def ledger(root):
    db=sqlite3.connect(root/'network.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS peers (name TEXT PRIMARY KEY, url TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT NOT NULL,
        result TEXT, credits INTEGER NOT NULL DEFAULT 0)''')
    db.execute('CREATE TABLE IF NOT EXISTS traffic (direction TEXT, route TEXT, body TEXT)')
    db.commit()
    return db


def trace(db,direction,route,body):
    with db:
        db.execute('INSERT INTO traffic VALUES (?,?,?)',(direction,route,packed(body).decode()))


def transfer(db,url,data=None):
    u=urlsplit(url)
    if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or not u.port:
        raise ValueError('demo transport requires numeric loopback and an explicit port')
    trace(db,'send',url,data)
    request=Request(url,data=None if data is None else packed(data),headers={'Content-Type':'application/json'})
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=300) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000:
        raise ValueError('oversize response')
    result=json.loads(raw)
    trace(db,'receive',url,result)
    return result


def discover(db,bootstrap):
    feed=transfer(db,bootstrap+'/catalog')
    descriptors=[json.loads(r['body']) for r in feed if r['source'].endswith('/node')]
    if not descriptors or len({p['name'] for p in descriptors})!=len(descriptors):
        raise ValueError('missing or duplicate public registration')
    for peer in descriptors:
        published=transfer(db,peer['url']+'/catalog')
        if not any(r['source'].endswith('/node') and json.loads(r['body'])==peer for r in published):
            raise ValueError('advertised node did not confirm registration')
    with db:
        db.executemany('INSERT OR REPLACE INTO peers VALUES (?,?)',[(p['name'],p['url']) for p in descriptors])
    return descriptors


def envelope(root,recipient,source):
    config=json.loads((root/'node.json').read_text())
    record=next((r for r in catalog(root) if r['source']==source),None)
    if record is None:
        raise ValueError('topic must be explicitly shared')
    body=dict(sender=config['name'],recipient=recipient,source=source,spec=json.loads(record['body']))
    return dict(body,id=digest(body))


def deliver(root,db,request):
    key=request['id']
    body={k:request[k] for k in ('sender','recipient','source','spec')}
    if set(request)!=set(body)|{'id'} or key!=digest(body):
        raise ValueError('invalid envelope')
    config=json.loads((root/'node.json').read_text())
    if request['recipient']!=config['name']:
        raise ValueError('wrong recipient')
    row=db.execute('SELECT url FROM peers WHERE name=?',(request['sender'],)).fetchone()
    if not row:
        raise ValueError('unknown sender')
    published=transfer(db,row[0]+'/catalog')
    if not any(r['source']==request['source'] and json.loads(r['body'])==request['spec'] for r in published):
        raise ValueError('topic differs from shared source')
    cached=db.execute('SELECT request,result FROM jobs WHERE id=?',(key,)).fetchone()
    if cached:
        if json.loads(cached[0])!=request or not cached[1]:
            raise ValueError('incomplete job requires local reconciliation')
        return json.loads(cached[1]),False
    with db:
        db.execute('INSERT INTO jobs(id,request) VALUES (?,?)',(key,packed(request).decode()))
    source=root/(key+'.json')
    source.write_bytes(packed(request['spec']))
    database=root/(key+'.sqlite3')
    outcome=consult(source,database,config['model'])
    with closing(sqlite3.connect(database)) as k:
        credits=k.execute('SELECT sum(cost) FROM messages').fetchone()[0]
    result=dict(id=key,sender=config['name'],recipient=request['sender'],outcome=outcome,credits=credits)
    with db:
        db.execute('UPDATE jobs SET result=?,credits=? WHERE id=?',(packed(result).decode(),credits,key))
    return result,True


def send(root,db,recipient,source,*,original=None):
    request=envelope(root,recipient,source)
    if original is not None and original != request:
        raise ValueError('published topic changed')
    peer=db.execute('SELECT url FROM peers WHERE name=?',(recipient,)).fetchone()
    if not peer:
        raise ValueError('discover recipient first')
    with db:
        db.execute('INSERT OR IGNORE INTO jobs(id,request) VALUES (?,?)',(request['id'],packed(request).decode()))
    result=transfer(db,peer[0]+'/consult',request)
    if (result['id'],result['sender'],result['recipient'])!=(request['id'],recipient,request['sender']):
        raise ValueError('receipt mismatch')
    with db:
        db.execute('UPDATE jobs SET result=?,credits=? WHERE id=?',
                   (packed(result).decode(),result['credits'],result['id']))
    return result


def resume(root, db):
    """Retry pending outbound jobs whose original topic is still shared."""
    name = json.loads((root / 'node.json').read_text())['name']
    rows = db.execute('SELECT id,request FROM jobs WHERE result IS NULL ORDER BY rowid').fetchall()
    outcomes = {}
    for key, raw in rows:
        request = json.loads(raw)
        if request['sender'] != name:
            continue
        try:
            send(root, db, request['recipient'], request['source'], original=request)
        except (ValueError, OSError) as error:
            outcomes[key] = str(error)
        else:
            outcomes[key] = 'received'
    return outcomes


def serve(root,port,interrupt):
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,value):
            raw=packed(value)
            self.send_response(code)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path!='/catalog':
                return self.reply(404,{})
            result=catalog(root)
            with closing(ledger(root)) as db:
                trace(db,'serve',self.path,result)
            self.reply(200,result)

        def do_POST(self):
            if self.path!='/consult':
                return self.reply(404,{})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100_000:
                    raise ValueError('invalid size')
                request=json.loads(self.rfile.read(size))
                with closing(ledger(root)) as db:
                    trace(db,'accept',self.path,request)
                    result,fresh=deliver(root,db,request)
                    if fresh and interrupt:
                        os._exit(75)  # Fault injection: durable result, no receipt delivered.
                    trace(db,'reply',self.path,result)
                self.reply(200,result)
            except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):
                self.reply(409,{'error':'request could not be completed'})

    with HTTPServer(('127.0.0.1',port),Handler) as server:
        (root/'ready').write_text(str(server.server_port))
        server.serve_forever()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    commands=parser.add_subparsers(dest='action',required=True)
    server=commands.add_parser('serve')
    server.add_argument('port',type=int)
    server.add_argument('--interrupt-after-commit',action='store_true')
    commands.add_parser('discover').add_argument('bootstrap')
    commands.add_parser('resume')
    submit=commands.add_parser('send')
    submit.add_argument('recipient')
    submit.add_argument('source')
    args=parser.parse_args()
    if args.action=='serve':
        serve(args.directory,args.port,args.interrupt_after_commit)
    else:
        with closing(ledger(args.directory)) as db:
            result=(resume(args.directory,db) if args.action=='resume' else
                    discover(db,args.bootstrap) if args.action=='discover' else
                    send(args.directory,db,args.recipient,args.source))
        print(packed(result).decode())
        if args.action == 'resume' and any(v != 'received' for v in result.values()):
            raise SystemExit(1)
