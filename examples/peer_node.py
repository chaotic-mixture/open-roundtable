"""Two-node consultation transport with optional mutually authenticated remote endpoints."""
import argparse
from contextlib import closing
import hashlib
from ipaddress import IPv4Address
from functools import partial
from http.client import HTTPException, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import ssl
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from open_roundtable.data import catalog, connect, ingest, NoRedirect
from examples.user_consult import NoFeasibleCandidate, consult, validate_roster


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
    db.execute("""CREATE TABLE IF NOT EXISTS statement_revisions (
        first_node TEXT NOT NULL, second_node TEXT NOT NULL, token TEXT NOT NULL,
        PRIMARY KEY(first_node,second_node))""")
    for event, records in [('INSERT', ('NEW',)), ('UPDATE', ('OLD','NEW')), ('DELETE', ('OLD',))]:
        db.execute(f'DROP TRIGGER IF EXISTS jobs_revision_{event}')
        updates = []
        for record in records:
            sender = f"json_extract({record}.request,'$.sender')"
            recipient = f"json_extract({record}.request,'$.recipient')"
            updates.append(f"""INSERT INTO statement_revisions VALUES (
                min({sender},{recipient}),max({sender},{recipient}),hex(randomblob(16)))
                ON CONFLICT(first_node,second_node) DO UPDATE SET token=excluded.token;""")
        db.execute(f"CREATE TRIGGER IF NOT EXISTS pair_revision_{event} AFTER {event} ON jobs "
                   + "BEGIN " + " ".join(updates) + " END")
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
    feed = fetch_public_subset(db, bootstrap, None, '/nodes')
    if not isinstance(feed, list):
        raise ValueError('invalid public directory snapshot')
    registrations = [(r['source'], json.loads(r['body'])) for r in feed if r['source'].endswith('/node')]
    descriptors = [peer for source, peer in registrations]
    if len({p['name'] for p in descriptors})!=len(descriptors):
        raise ValueError('invalid public directory snapshot')
    for source, peer in registrations:
        route = '/record?' + urlencode({'source': source})
        published = fetch_public_subset(db, peer['url'], peer['name'], route)
        if not any(r['source'] == source and json.loads(r['body']) == peer for r in published):
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


def offer(root, source, tag, description):
    node = json.loads((root/'node.json').read_text())['name']
    if not tag.strip() or not description.strip():
        raise ValueError('provide a capability tag and description')
    body = packed(dict(kind='capability', node=node, tag=tag.strip(), description=description.strip())).decode()
    ingest(root,'public',[dict(source=source,body=body)])
    return dict(source=source,sha256=hashlib.sha256(body.encode()).hexdigest())


def fetch_public_subset(db, url, peer, route):
    try:
        return transfer(db,url+route,peer_name=peer)
    except HTTPError as error:
        if error.code != 404:
            raise
        error.close()
    return transfer(db,url+'/catalog',peer_name=peer)


def valid_offer(item, node):
    return (isinstance(item, dict) and item.get('kind') == 'capability' and item.get('node') == node
            and all(isinstance(item.get(field), str) and item[field].strip() for field in ('tag', 'description')))


def published_offers(root):
    name = json.loads((root/'node.json').read_text())['name']
    offers = []
    for record in catalog(root):
        try:
            body = json.loads(record['body'])
        except ValueError:
            continue
        if valid_offer(body, name):
            offers.append(record)
    return offers


def find_capability(root, db, tag=None, *, accepting_only=False):
    node = json.loads((root/'node.json').read_text())['name']
    matches, unavailable = [], []
    for peer,url in db.execute('SELECT name,url FROM peers WHERE name<>? ORDER BY name',(node,)).fetchall():
        try:
            records = fetch_public_subset(db,url,peer,'/offers')
            if not isinstance(records,list):
                raise ValueError('expected a catalog list')
        except (HTTPException,OSError,ValueError):
            unavailable.append(peer)
            continue
        for record in records:
            if not isinstance(record,dict) or not isinstance(record.get('source'),str) or not record['source'].strip():
                continue
            try:
                item = json.loads(record['body'])
            except (KeyError,TypeError,ValueError):
                continue
            if valid_offer(item, peer) and tag in (None,item['tag']):
                match = dict(node=peer,source=record['source'],description=item['description'],
                             sha256=hashlib.sha256(record['body'].encode()).hexdigest())
                if tag is None:
                    match['tag'] = item['tag']
                matches.append(match)
    if accepting_only:
        if json.loads((root/'node.json').read_text()).get('transport') != 'mtls':
            raise ValueError('admission filtering requires mutual TLS')
        accepting = set()
        declined = []
        for peer in sorted({match['node'] for match in matches}):
            try:
                status = admission(root,db,peer)
            except (HTTPException,OSError,ValueError):
                unavailable.append(peer)
                continue
            if status['accepting']:
                accepting.add(peer)
            else:
                declined.append(peer)
        return dict(matches=[m for m in matches if m['node'] in accepting],
                    unavailable=sorted(unavailable), declined=declined)
    return dict(matches=matches, unavailable=unavailable)


def match_offers(need, offers, model, *, allowed_tags=None):
    from examples.auto_consult import api
    if allowed_tags is not None:
        allowed = set(allowed_tags)
        offers = [offer for offer in offers if offer['tag'] in allowed]
    if not offers:
        return dict(matches=[], calls=0, input_tokens=0, output_tokens=0)
    menu = [dict(id=i, tag=o['tag'], description=o['description']) for i,o in enumerate(offers)]
    prompt = 'Select all offers explicitly able to fulfill the entire need. Return {"ids": [integer IDs]}, empty if none.'
    reply = api('/api/chat',dict(model=model, stream=False, think=False, format='json',options={'temperature':0},
        messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps(dict(need=need,offers=menu),ensure_ascii=False))]))
    ids = json.loads(reply['message']['content'])['ids']
    if not reply.get('done') or not isinstance(ids,list) or any(type(i) is not int or not 0<=i<len(menu) for i in ids):
        raise ValueError('invalid model selection')
    return dict(matches=[offers[i] for i in sorted(set(ids))], calls=1,
                input_tokens=reply.get('prompt_eval_count'), output_tokens=reply.get('eval_count'))


def verify_offer(records, node, reference):
    source, checksum = reference
    for record in records:
        if record['source'] == source and hashlib.sha256(record['body'].encode()).hexdigest() == checksum:
            item = json.loads(record['body'])
            if valid_offer(item, node):
                return
    raise ValueError('selected offer is no longer published at the requested revision')


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
    if 'offer' in request:
        body['offer'] = request['offer']
    if set(request)!=set(body)|{'id'} or key!=digest(body):
        raise ValueError('invalid envelope')
    config=json.loads((root/'node.json').read_text())
    if request['recipient']!=config['name']:
        raise ValueError('wrong recipient')
    row = db.execute('''SELECT peers.url FROM peers JOIN consult_grants USING(name,url)
                        WHERE name=?''', (request['sender'],)).fetchone()
    if not row:
        raise ValueError('sender has no consultation grant')
    route = '/record?' + urlencode({'source':request['source']})
    published = fetch_public_subset(db,row[0],request['sender'],route)
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
        if 'offer' in request:
            verify_offer(catalog(root),config['name'],request['offer'])
        limit = db.execute('SELECT remaining FROM grant_limits WHERE name=?', (request['sender'],)).fetchone()
        if limit is not None:
            if limit[0] == 0:
                raise ValueError('consultation allowance exhausted')
        validate_roster(request['spec'])
        if limit is not None:
            db.execute('UPDATE grant_limits SET remaining=remaining-1 WHERE name=?', (request['sender'],))
    source=root/(key+'.json')
    source.write_bytes(packed(request['spec']))
    try:
        outcome=consult(source,database,config['model'])
        credits = None
    except NoFeasibleCandidate as error:
        outcome = dict(state='NO_PLAN', feasibility=error.status, nodes=error.nodes)
        credits = 0
    with closing(sqlite3.connect(database)) as k, k:
        if credits is None:
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
    if isinstance(outcome, dict) and outcome.get('state') == 'NO_PLAN':
        if (charge != 0 or outcome.get('feasibility') not in ('infeasible', 'unknown')
                or type(outcome.get('nodes')) is not int or outcome['nodes'] < 0):
            raise ValueError('invalid feasibility receipt')
        return
    if not isinstance(outcome, dict) or outcome.get('state') not in ('ACTIVE','PAUSED','RESOLVED'):
        raise ValueError('invalid service outcome')


def send(root,db,recipient,source,*,original=None,offer_ref=None):
    request=envelope(root,recipient,source)
    if offer_ref is not None:
        request['offer'] = list(offer_ref)
        request['id'] = digest({k:v for k,v in request.items() if k!='id'})
    if original is not None and original != request:
        raise ValueError('published topic changed')
    peer=db.execute('SELECT url FROM peers WHERE name=?',(recipient,)).fetchone()
    if not peer:
        raise ValueError('discover recipient first')
    if offer_ref is not None and original is None:
        verify_offer(fetch_public_subset(db,peer[0],recipient,'/offers'),recipient,offer_ref)
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


def partner_statement(root, db, partner, *, after='', limit=None):
    node = json.loads((root/'node.json').read_text())['name']
    if node == partner or not partner.strip():
        raise ValueError('choose a distinct partner')
    entries = []
    for key, raw, saved, credits in db.execute('SELECT id,request,result,credits FROM jobs WHERE id>? ORDER BY id',(after,)):
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
        if limit is not None and len(entries) >= limit:
            break
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
        elif expected[key]['receipt'] is None and reported[key].get('receipt') is not None:
            reason = 'pending locally, settled at partner'
        elif expected[key]['receipt'] is not None and 'receipt' in reported[key] and reported[key]['receipt'] is None:
            reason = 'settled locally, pending at partner'
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
            send(root, db, request['recipient'], request['source'], original=request,offer_ref=request.get('offer'))
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


def admission(root, db, partner):
    config = json.loads((root/'node.json').read_text())
    if config.get('transport') != 'mtls':
        raise ValueError('admission lookup requires mutual TLS')
    row = db.execute('SELECT url FROM peers WHERE name=?',(partner,)).fetchone()
    if row is None:
        raise ValueError('discover partner before checking admission')
    response = transfer(db,row[0]+'/admission',dict(sender=config['name']),peer_name=partner)
    if not isinstance(response,dict) or type(response.get('accepting')) is not bool:
        raise ValueError('invalid admission response')
    return dict(node=partner,accepting=response['accepting'])


def reconcile_online(root, db, partner):
    config = json.loads((root/'node.json').read_text())
    if config.get('transport') != 'mtls':
        raise ValueError('online reconciliation requires mutual TLS')
    row = db.execute('SELECT url FROM peers WHERE name=?', (partner,)).fetchone()
    if not row:
        raise ValueError('discover partner before reconciling')
    request = dict(sender=config['name'], after='', revision=None)
    entries = []
    while True:
        remote = transfer(db,row[0]+'/statement',request,peer_name=partner)
        batch = remote['entries']
        if remote['node'] != partner or remote['partner'] != config['name'] or remote['format'] != 1:
            raise ValueError('statement parties or format mismatch')
        revision = remote.get('revision')
        if request['revision'] is not None and revision != request['revision']:
            raise ValueError('statement changed during reconciliation')
        entries.extend(batch)
        cursor = remote.get('next')
        if cursor is None:
            break
        if revision is None or not batch or cursor != batch[-1]['job'] or cursor <= request['after']:
            raise ValueError('invalid statement continuation')
        request.update(after=cursor, revision=revision)
    return reconcile(partner_statement(root,db,partner), dict(remote,entries=entries))



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
            if urlsplit(self.path).path == '/record':
                query = parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                if set(query) != {'source'} or len(query['source']) != 1 or not query['source'][0]:
                    return self.reply(400, {})
                with closing(connect(root,'public')) as public:
                    rows = public.execute('SELECT source,body FROM records WHERE shared=1 AND source=?',
                                          (query['source'][0],)).fetchall()
                result = [dict(source=source,body=body) for source,body in rows]
            elif self.path == '/nodes':
                result = [record for record in catalog(root) if record['source'].endswith('/node')]
            elif self.path in ('/catalog','/offers'):
                result = published_offers(root) if self.path=='/offers' else catalog(root)
            else:
                return self.reply(404,{})
            with closing(ledger(root)) as db:
                trace(db,'serve',self.path,result)
            self.reply(200,result)

        def do_POST(self):
            if self.path in ('/statement','/admission') and not context:
                return self.reply(403, {'error':'mutual TLS required'})
            if self.path not in ('/consult','/statement','/admission'):
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
                    if self.path == '/admission':
                        eligible = db.execute('''SELECT 1 FROM peers JOIN consult_grants USING(name,url)
                            LEFT JOIN grant_limits USING(name) WHERE name=? AND (remaining IS NULL OR remaining>0)''',
                            (request['sender'],)).fetchone()
                        return self.reply(200,dict(accepting=eligible is not None))
                    if self.path == '/statement':
                        partner = request['sender']
                        permitted = config.get('statement_partners', [])
                        if not isinstance(permitted,list) or partner not in permitted:
                            return self.reply(403, {'error':'statement sharing not enabled'})
                        if 'after' not in request:
                            return self.reply(200,partner_statement(root,db,partner))
                        db.execute('BEGIN')
                        pair = sorted((config['name'],partner))
                        row = db.execute('SELECT token FROM statement_revisions WHERE first_node=? AND second_node=?',
                                         pair).fetchone()
                        revision = row[0] if row else 'initial'
                        if request['revision'] is not None and request['revision'] != revision:
                            return self.reply(409, {'error':'statement changed; retry reconciliation'})
                        page = partner_statement(root,db,partner,after=request['after'],limit=257)
                        more = len(page['entries']) == 257
                        if more:
                            page['entries'].pop()
                        page.update(revision=revision, next=page['entries'][-1]['job'] if more else None)
                        return self.reply(200,page)
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
    capability = commands.add_parser('offer')
    capability.add_argument('source')
    capability.add_argument('tag')
    capability.add_argument('description')
    find = commands.add_parser('find-capability')
    find.add_argument('tag')
    find.add_argument('--accepting-only', action='store_true')
    match = commands.add_parser('match-capability')
    match.add_argument('need')
    match.add_argument('--accepting-only', action='store_true')
    match.add_argument('--allow-tag',action='append',help='only consider this declared operation tag; repeat for alternatives')
    match.add_argument('--model', default='qwen3.5:9b')
    commands.add_parser('resume')
    commands.add_parser('account')
    commands.add_parser('admission').add_argument('partner')
    commands.add_parser('reconcile-online').add_argument('partner')
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
    submit.add_argument('--offer',nargs=2,metavar=('SOURCE','SHA256'))
    args=parser.parse_args()
    if args.action=='serve':
        serve(args.directory,args.port,args.interrupt_after_commit,args.interrupt_before_receipt)
    else:
        with closing(ledger(args.directory)) as db:
            if args.action == 'offer':
                result = offer(args.directory,args.source,args.tag,args.description)
            elif args.action == 'find-capability':
                result = find_capability(args.directory,db,args.tag,accepting_only=args.accepting_only)
            elif args.action == 'match-capability':
                catalog_result = find_capability(args.directory,db,accepting_only=args.accepting_only)
                result = match_offers(args.need,catalog_result['matches'],args.model,allowed_tags=args.allow_tag)
                result['unavailable'] = catalog_result['unavailable']
                if args.accepting_only:
                    result['declined'] = catalog_result['declined']
            elif args.action == 'feedback':
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
            elif args.action == 'admission':
                result = admission(args.directory,db,args.partner)
            elif args.action == 'reconcile-online':
                result = reconcile_online(args.directory,db,args.partner)
            elif args.action == 'account':
                result = service_account(args.directory, db)
            elif args.action == 'permission':
                result = set_permission(db, args.peer, args.mode == 'allow', args.remaining)
            elif args.action in ('mark', 'revisit'):
                result = local_history(args.directory, db, args.label, getattr(args, 'job', None))
            else:
                result=(resume(args.directory,db) if args.action=='resume' else
                    discover(db,args.bootstrap) if args.action=='discover' else
                    send(args.directory,db,args.recipient,args.source,offer_ref=args.offer))
        print(packed(result).decode())
        if args.action in ('reconcile','reconcile-online') and result['differences']:
            raise SystemExit(1)
        if args.action == 'resume' and any(v != 'received' for v in result.values()):
            raise SystemExit(1)
