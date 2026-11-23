"""Two-node consultation transport with optional mutually authenticated remote endpoints."""
import argparse
from contextlib import closing
import hashlib
from ipaddress import IPv4Address
from functools import partial
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import ssl
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from open_roundtable.data import catalog, connect, NoRedirect
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
    db.execute('CREATE TABLE IF NOT EXISTS consult_grants (name TEXT PRIMARY KEY, url TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS grant_limits (name TEXT PRIMARY KEY, remaining INTEGER NOT NULL CHECK(remaining>=0))')
    db.execute('CREATE TABLE IF NOT EXISTS traffic (direction TEXT, route TEXT, body TEXT)')
    db.commit()
    return db


def trace(db,direction,route,body):
    with db:
        db.execute('INSERT INTO traffic VALUES (?,?,?)',(direction,route,packed(body).decode()))


def tls_context(root, *, server=False):
    config = json.loads((root/'node.json').read_text())
    if config.get('transport', 'http') == 'http':
        return None
    if config['transport'] != 'mtls':
        raise ValueError('unknown node transport')
    root = Path(config.get('credentials', root))
    purpose = ssl.Purpose.CLIENT_AUTH if server else ssl.Purpose.SERVER_AUTH
    context = ssl.create_default_context(purpose, cafile=str(root/'ca.pem'))
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(root/'node.pem', root/'node.key')
    return context


def certificate_matches(certificate, peer):
    names = [value for kind,value in certificate.get('subjectAltName', ())
             if kind == 'URI' and value.startswith('urn:roundtable:peer:')]
    return names == ['urn:roundtable:peer:' + peer]


class PeerHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, peer, **kwargs):
        super().__init__(*args, **kwargs)
        self.peer = peer

    def connect(self):
        super().connect()
        if not certificate_matches(self.sock.getpeercert(), self.peer):
            self.close()
            raise ssl.SSLCertVerificationError('recipient certificate identity mismatch')


class PeerHTTPSHandler(HTTPSHandler):
    def __init__(self, context, peer):
        super().__init__(context=context)
        self.connection = partial(PeerHTTPSConnection, context=context, peer=peer)

    def https_open(self, request):
        return self.do_open(self.connection, request)


def transfer(db,url,data=None,*,peer_name=None):
    u=urlsplit(url)
    location = db.execute('PRAGMA database_list').fetchone()[2]
    root = Path(location).parent
    config = json.loads((root/'node.json').read_text())
    context = tls_context(root)
    scheme = 'https' if context else 'http'
    if u.scheme!=scheme or not u.hostname or u.username is not None or u.password is not None or not u.port:
        raise ValueError('invalid transport endpoint')
    if u.hostname != '127.0.0.1':
        origins = config.get('allowed_origins', [])
        if not context or not isinstance(origins, list) or f'https://{u.netloc}' not in origins:
            raise ValueError('remote origin is not authorized')
    trace(db,'send',url,data)
    request=Request(url,data=None if data is None else packed(data),headers={'Content-Type':'application/json'})
    handler = PeerHTTPSHandler(context,peer_name) if context and peer_name is not None else HTTPSHandler(context=context)
    with build_opener(ProxyHandler({}),NoRedirect(),handler).open(request,timeout=300) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000:
        raise ValueError('oversize response')
    result=json.loads(raw)
    trace(db,'receive',url,result)
    return result


def discover(db,bootstrap):
    feed=transfer(db,bootstrap+'/catalog')
    descriptors=[json.loads(r['body']) for r in feed if r['source'].endswith('/node')]
    if not isinstance(feed, list) or len({p['name'] for p in descriptors})!=len(descriptors):
        raise ValueError('invalid public directory snapshot')
    for peer in descriptors:
        published=transfer(db,peer['url']+'/catalog',peer_name=peer['name'])
        if not any(r['source'].endswith('/node') and json.loads(r['body'])==peer for r in published):
            raise ValueError('advertised node did not confirm registration')
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM peers')
        db.executemany('INSERT INTO peers VALUES (?,?)', [(p['name'], p['url']) for p in descriptors])
        db.execute('''DELETE FROM consult_grants WHERE NOT EXISTS (
            SELECT 1 FROM peers WHERE peers.name=consult_grants.name AND peers.url=consult_grants.url)''')
        db.execute('DELETE FROM grant_limits WHERE name NOT IN (SELECT name FROM consult_grants)')
    return descriptors


def set_permission(db, peer, allow, remaining=None):
    if remaining is not None and (not allow or type(remaining) is not int or not 0 <= remaining < 2**63):
        raise ValueError('remaining consultations must be a nonnegative integer on an active grant')
    with db:
        if allow:
            row = db.execute('SELECT url FROM peers WHERE name=?', (peer,)).fetchone()
            if row is None:
                raise ValueError('discover peer before granting consultations')
            db.execute('INSERT OR REPLACE INTO consult_grants VALUES (?,?)', (peer, row[0]))
        else:
            db.execute('DELETE FROM consult_grants WHERE name=?', (peer,))
        db.execute('DELETE FROM grant_limits WHERE name=?', (peer,))
        if remaining is not None:
            db.execute('INSERT INTO grant_limits VALUES (?,?)', (peer, remaining))
    return dict(peer=peer, allowed=allow, remaining=remaining)


def envelope(root,recipient,source):
    config=json.loads((root/'node.json').read_text())
    record=next((r for r in catalog(root) if r['source']==source),None)
    if record is None:
        raise ValueError('topic must be explicitly shared')
    body=dict(sender=config['name'],recipient=recipient,source=source,spec=json.loads(record['body']))
    return dict(body,id=digest(body))


def deliver(root,db,request,*,interrupt_before_receipt=False):
    key=request['id']
    body={k:request[k] for k in ('sender','recipient','source','spec')}
    if set(request)!=set(body)|{'id'} or key!=digest(body):
        raise ValueError('invalid envelope')
    config=json.loads((root/'node.json').read_text())
    if request['recipient']!=config['name']:
        raise ValueError('wrong recipient')
    row = db.execute('''SELECT peers.url FROM peers JOIN consult_grants USING(name,url)
                        WHERE name=?''', (request['sender'],)).fetchone()
    if not row:
        raise ValueError('sender has no consultation grant')
    published=transfer(db,row[0]+'/catalog',peer_name=request['sender'])
    if not any(r['source']==request['source'] and json.loads(r['body'])==request['spec'] for r in published):
        raise ValueError('topic differs from shared source')
    database=root/(key+'.sqlite3')
    with db:
        db.execute('BEGIN IMMEDIATE')
        still_allowed = db.execute('SELECT 1 FROM peers JOIN consult_grants USING(name,url) WHERE name=? AND url=?',
                                   (request['sender'], row[0])).fetchone()
        if not still_allowed:
            raise ValueError('consultation grant changed')
        claimed = db.execute('INSERT OR IGNORE INTO jobs(id,request) VALUES (?,?)',
                             (key, packed(request).decode())).rowcount
        if not claimed:
            saved, result = db.execute('SELECT request,result FROM jobs WHERE id=?', (key,)).fetchone()
            if json.loads(saved) != request:
                raise ValueError('stored request mismatch')
            if result is not None:
                return json.loads(result), False
            if not database.exists():
                raise ValueError('job in progress or awaiting local reconciliation')
            uri = database.resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True)) as kernel:
                exists = kernel.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='peer_receipt'").fetchone()
                checkpoint = kernel.execute('SELECT result FROM peer_receipt WHERE id=?', (key,)).fetchone() if exists else None
            if checkpoint is None:
                raise ValueError('job in progress or awaiting local reconciliation')
            result = json.loads(checkpoint[0])
            db.execute('UPDATE jobs SET result=?,credits=? WHERE id=?', (checkpoint[0], result['credits'], key))
            return result, False
        limit = db.execute('SELECT remaining FROM grant_limits WHERE name=?', (request['sender'],)).fetchone()
        if limit is not None:
            if limit[0] == 0:
                raise ValueError('consultation allowance exhausted')
            db.execute('UPDATE grant_limits SET remaining=remaining-1 WHERE name=?', (request['sender'],))
    source=root/(key+'.json')
    source.write_bytes(packed(request['spec']))
    outcome=consult(source,database,config['model'])
    with closing(sqlite3.connect(database)) as k, k:
        credits=k.execute('SELECT sum(cost) FROM messages').fetchone()[0]
        result=dict(id=key,sender=config['name'],recipient=request['sender'],outcome=outcome,credits=credits)
        k.execute('CREATE TABLE peer_receipt (id TEXT PRIMARY KEY, result TEXT NOT NULL)')
        k.execute('INSERT INTO peer_receipt VALUES (?,?)', (key, packed(result).decode()))
    if interrupt_before_receipt:
        os._exit(76)  # Durable kernel checkpoint, network ledger still pending.
    with db:
        db.execute('UPDATE jobs SET result=?,credits=? WHERE id=?',(packed(result).decode(),credits,key))
    return result,True


def validate_receipt(request, receipt):
    if not isinstance(receipt, dict) or (receipt.get('id'),receipt.get('sender'),receipt.get('recipient')) != (
            request['id'],request['recipient'],request['sender']):
        raise ValueError('service receipt identity mismatch')
    charge = receipt.get('credits')
    if type(charge) is not int or not 0 <= charge <= 2:
        raise ValueError('invalid service charge')
    outcome = receipt.get('outcome')
    if not isinstance(outcome, dict) or outcome.get('state') not in ('ACTIVE','PAUSED','RESOLVED'):
        raise ValueError('invalid service outcome')


def send(root,db,recipient,source,*,original=None):
    request=envelope(root,recipient,source)
    if original is not None and original != request:
        raise ValueError('published topic changed')
    peer=db.execute('SELECT url FROM peers WHERE name=?',(recipient,)).fetchone()
    if not peer:
        raise ValueError('discover recipient first')
    with db:
        db.execute('INSERT OR IGNORE INTO jobs(id,request) VALUES (?,?)',(request['id'],packed(request).decode()))
    result=transfer(db,peer[0]+'/consult',request,peer_name=recipient)
    validate_receipt(request, result)
    encoded = packed(result).decode()
    with db:
        posted = db.execute('UPDATE jobs SET result=?,credits=? WHERE id=? AND result IS NULL',
                            (encoded,result['credits'],result['id'])).rowcount
        if not posted:
            saved = db.execute('SELECT result,credits FROM jobs WHERE id=?',(result['id'],)).fetchone()
            if saved != (encoded,result['credits']):
                raise ValueError('conflicting receipt for a settled job')
    return result


def service_account(root, db):
    """Summarize each durable service receipt once from this node's perspective."""
    node = json.loads((root / 'node.json').read_text())['name']
    account = dict(node=node, contributed=0, consumed=0, pending=0, entries=[])
    for key, raw, saved, credits in db.execute('SELECT id,request,result,credits FROM jobs ORDER BY id'):
        request = json.loads(raw)
        sender, provider = request['sender'], request['recipient']
        if key != request['id'] or key != digest({k:v for k,v in request.items() if k!='id'}):
            raise ValueError('job identity mismatch')
        if node not in (sender, provider) or sender == provider:
            raise ValueError('service account requires two distinct participants')
        if saved is None:
            if credits != 0:
                raise ValueError('pending job has a posted charge')
            account['pending'] += 1
            continue
        receipt = json.loads(saved)
        validate_receipt(request, receipt)
        if credits != receipt['credits']:
            raise ValueError('invalid service charge')
        side = 'contributed' if node == provider else 'consumed'
        account[side] += credits
        account['entries'].append(dict(job=key, provider=provider, consumer=sender, credits=credits,
                                       state=receipt['outcome']['state']))
    return account


def partner_statement(root, db, partner):
    node = json.loads((root/'node.json').read_text())['name']
    if node == partner or not partner.strip():
        raise ValueError('choose a distinct partner')
    entries = []
    for key, raw, saved, credits in db.execute('SELECT id,request,result,credits FROM jobs ORDER BY id'):
        request = json.loads(raw)
        if {request['sender'], request['recipient']} != {node,partner}:
            continue
        if request['id'] != key or digest({k:v for k,v in request.items() if k!='id'}) != key:
            raise ValueError('job identity mismatch')
        receipt_hash = None
        if saved is not None:
            receipt = json.loads(saved)
            validate_receipt(request,receipt)
            if credits != receipt['credits']:
                raise ValueError('invalid service charge')
            receipt_hash = digest(receipt)
        elif credits:
            raise ValueError('pending job has a posted charge')
        entries.append(dict(job=key, provider=request['recipient'], credits=credits, receipt=receipt_hash))
    return dict(format=1, node=node, partner=partner, entries=entries)


def reconcile(local, remote):
    if (remote.get('format'),remote.get('node'),remote.get('partner')) != (1,local['partner'],local['node']):
        raise ValueError('statement parties or format mismatch')
    expected = {entry['job']:entry for entry in local['entries']}
    reported = {entry['job']:entry for entry in remote['entries']}
    if len(reported) != len(remote['entries']):
        raise ValueError('duplicate statement job')
    differences = []
    for key in sorted(expected.keys() | reported.keys()):
        if key not in expected:
            reason = 'missing locally'
        elif key not in reported:
            reason = 'missing at partner'
        elif packed(expected[key]) != packed(reported[key]):
            reason = 'receipt or charge differs'
        elif expected[key]['receipt'] is None:
            reason = 'pending at both nodes'
        else:
            continue
        differences.append(dict(job=key, reason=reason))
    return dict(matched=len(expected.keys() | reported.keys())-len(differences), differences=differences)


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


def practice_feedback(root, network, *, job=None, useful=None, observation=None, provider=None):
    account = service_account(root, network)
    consumed = {e['job']: e for e in account['entries'] if e['consumer'] == account['node']}
    with closing(connect(root, 'private')) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS practice_feedback (
            job TEXT PRIMARY KEY, useful INTEGER NOT NULL CHECK(useful IN (0,1)), observation TEXT NOT NULL)""")
        if job is not None:
            if job not in consumed:
                raise ValueError('feedback requires a received consultation at its consumer')
            if type(useful) is not bool or not isinstance(observation, str) or not observation.strip():
                raise ValueError('record a practical observation and its outcome')
            db.execute('INSERT OR IGNORE INTO practice_feedback VALUES (?,?,?)', (job,useful,observation))
            saved = db.execute('SELECT useful,observation FROM practice_feedback WHERE job=?', (job,)).fetchone()
            if saved != (useful,observation):
                raise ValueError('different feedback already recorded for this receipt')
            provider = consumed[job]['provider']
        jobs = {key for key,entry in consumed.items() if entry['provider'] == provider}
        ratings = [value for key,value in db.execute('SELECT job,useful FROM practice_feedback') if key in jobs]
        return dict(provider=provider, received=len(jobs), reviewed=len(ratings), useful=sum(ratings),
                    useful_fraction=sum(ratings)/len(ratings) if ratings else None)


def local_history(root, network, label, job=None):
    label = label.strip()
    if not label:
        raise ValueError('provide a nonempty label')
    with closing(connect(root, 'private')) as history, history:
        history.execute("""CREATE TABLE IF NOT EXISTS job_labels (
            label TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(label,job))""")
        if job is not None:
            row = network.execute('SELECT result FROM jobs WHERE id=?', (job,)).fetchone()
            if not row or row[0] is None:
                raise ValueError('a completed local consultation is required')
            history.execute('INSERT OR IGNORE INTO job_labels VALUES (?,?)', (label, job))
            return dict(job=job, label=label)
        matches = []
        for key, in history.execute('SELECT job FROM job_labels WHERE label=? ORDER BY job', (label,)):
            row = network.execute('SELECT result FROM jobs WHERE id=?', (key,)).fetchone()
            if row and row[0]:
                matches.append(json.loads(row[0]))
        return matches


def serve(root,port,interrupt,interrupt_before_receipt=False):
    context = tls_context(root, server=True)
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
                if context:
                    if not certificate_matches(self.connection.getpeercert(), request['sender']):
                        return self.reply(403, {'error':'sender certificate mismatch'})
                with closing(ledger(root)) as db:
                    trace(db,'accept',self.path,request)
                    result,fresh=deliver(root,db,request,interrupt_before_receipt=interrupt_before_receipt)
                    if fresh and interrupt:
                        os._exit(75)  # Fault injection: durable result, no receipt delivered.
                    trace(db,'reply',self.path,result)
                self.reply(200,result)
            except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):
                self.reply(409,{'error':'request could not be completed'})

    config = json.loads((root/'node.json').read_text())
    address = IPv4Address(config.get('listen_address', '127.0.0.1'))
    if address.is_unspecified or address.is_multicast or (not context and str(address) != '127.0.0.1'):
        raise ValueError('an explicit interface and mutual TLS are required for remote service')
    with ThreadingHTTPServer((str(address),port),Handler) as server:
        if context:
            server.socket = context.wrap_socket(server.socket, server_side=True)
        (root/'ready').write_text(str(server.server_port))
        server.serve_forever()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    commands=parser.add_subparsers(dest='action',required=True)
    server=commands.add_parser('serve')
    server.add_argument('port',type=int)
    server.add_argument('--interrupt-after-commit',action='store_true')
    server.add_argument('--interrupt-before-receipt',action='store_true')
    commands.add_parser('discover').add_argument('bootstrap')
    commands.add_parser('resume')
    commands.add_parser('account')
    for action in ('statement','reconcile'):
        report = commands.add_parser(action)
        report.add_argument('partner')
        report.add_argument('file',type=Path)
    feedback = commands.add_parser('feedback')
    feedback.add_argument('job')
    feedback.add_argument('outcome', choices=['useful','not-useful'])
    feedback.add_argument('observation_file', type=Path)
    commands.add_parser('quality').add_argument('provider')
    access = commands.add_parser('permission')
    access.add_argument('peer')
    access.add_argument('mode', choices=['allow', 'revoke'])
    access.add_argument('--remaining', type=int)
    mark = commands.add_parser('mark')
    mark.add_argument('job')
    mark.add_argument('label')
    commands.add_parser('revisit').add_argument('label')
    submit=commands.add_parser('send')
    submit.add_argument('recipient')
    submit.add_argument('source')
    args=parser.parse_args()
    if args.action=='serve':
        serve(args.directory,args.port,args.interrupt_after_commit,args.interrupt_before_receipt)
    else:
        with closing(ledger(args.directory)) as db:
            if args.action == 'feedback':
                result = practice_feedback(args.directory, db, job=args.job, useful=args.outcome=='useful',
                                           observation=args.observation_file.read_text(encoding='utf-8-sig'))
            elif args.action == 'quality':
                result = practice_feedback(args.directory, db, provider=args.provider)
            elif args.action in ('statement','reconcile'):
                local = partner_statement(args.directory,db,args.partner)
                if args.action == 'statement':
                    with args.file.open('xb') as output:
                        output.write(packed(local))
                    result = dict(entries=len(local['entries']))
                else:
                    result = reconcile(local,json.loads(args.file.read_bytes()))
            elif args.action == 'account':
                result = service_account(args.directory, db)
            elif args.action == 'permission':
                result = set_permission(db, args.peer, args.mode == 'allow', args.remaining)
            elif args.action in ('mark', 'revisit'):
                result = local_history(args.directory, db, args.label, getattr(args, 'job', None))
            else:
                result=(resume(args.directory,db) if args.action=='resume' else
                    discover(db,args.bootstrap) if args.action=='discover' else
                    send(args.directory,db,args.recipient,args.source))
        print(packed(result).decode())
        if args.action == 'reconcile' and result['differences']:
            raise SystemExit(1)
        if args.action == 'resume' and any(v != 'received' for v in result.values()):
            raise SystemExit(1)
