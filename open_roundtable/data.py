"""Owner-operated public catalog and private personalization store."""
import argparse
import hashlib
from .local_model import ollama_origin
from contextlib import closing
from http.client import HTTPException
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('provide an HTTPS URL without credentials or fragment')
    return value


def initialize(root):
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for scope in ('public', 'private'):
        with closing(sqlite3.connect(root / (scope + '.sqlite3'))) as db, db:
            db.execute('CREATE TABLE metadata (scope TEXT PRIMARY KEY)')
            db.execute('INSERT INTO metadata VALUES (?)', (scope,))
            db.execute('''CREATE TABLE records (
                source TEXT PRIMARY KEY, body TEXT NOT NULL, summary TEXT,
                shared INTEGER NOT NULL DEFAULT 0)''')
            if scope == 'private':
                db.execute("""CREATE TABLE policy (
                    id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, endpoint TEXT, model TEXT)""")
                db.execute("INSERT INTO policy VALUES (1,'sealed',NULL,NULL)")


def connect(root, scope):
    if scope not in ('public', 'private'):
        raise ValueError('unknown store')
    path = (root / (scope + '.sqlite3')).resolve()
    db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True)
    try:
        if db.execute('SELECT scope FROM metadata').fetchall() != [(scope,)]:
            raise ValueError('store identity mismatch')
        if scope == 'private':
            db.execute("""CREATE TABLE IF NOT EXISTS summary_origin (
                source TEXT PRIMARY KEY, mode TEXT NOT NULL, model TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS summary_reported_model (
                source TEXT PRIMARY KEY, model TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS record_limits (
                source TEXT PRIMARY KEY REFERENCES records(source),
                mode TEXT NOT NULL CHECK(mode IN ('sealed','local')), model TEXT)""")
        return db
    except BaseException:
        db.close()
        raise


def configure(root, mode, model=None, endpoint=None):
    if mode not in ('sealed','local','api'):
        raise ValueError('invalid policy')
    if mode == 'api':
        if not endpoint:
            raise ValueError('API policy requires an explicit endpoint')
        https_url(endpoint)
    elif endpoint:
        raise ValueError('custom endpoint requires API policy')
    if mode != 'sealed' and not (model and model.strip()):
        raise ValueError('choose a model')
    with closing(connect(root,'private')) as db, db:
        db.execute('UPDATE policy SET mode=?,model=?,endpoint=? WHERE id=1',(mode,model,endpoint))


def limit_record(root, source, mode, model=None):
    if mode not in ('inherit','sealed','local'):
        raise ValueError('unknown record limit')
    if mode == 'local' and not (model and model.strip()):
        raise ValueError('choose a local model')
    if mode != 'local' and model is not None:
        raise ValueError('model applies only to local maintenance')
    with closing(connect(root,'private')) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM records WHERE source=?',(source,)).fetchone():
            raise ValueError('unknown private record')
        if mode == 'inherit':
            db.execute('DELETE FROM record_limits WHERE source=?',(source,))
        else:
            db.execute('INSERT OR REPLACE INTO record_limits VALUES (?,?,?)',(source,mode,model))


def ingest(root, scope, records):
    if not isinstance(records,list):
        raise ValueError('expected an array of records')
    with closing(connect(root,scope)) as db, db:
        for item in records:
            if not isinstance(item,dict):
                raise ValueError('expected a record object')
            source, body = item.get('source'), item.get('body')
            if not all(isinstance(x,str) and x.strip() for x in (source,body)):
                raise ValueError('source and body must be nonempty text')
            if scope=='public':
                https_url(source)
            # New and changed material needs a separate owner publication decision.
            changed = db.execute('''INSERT INTO records(source,body) VALUES (?,?)
                ON CONFLICT(source) DO UPDATE SET body=excluded.body,summary=NULL,shared=0
                WHERE records.body<>excluded.body''',(source,body)).rowcount
            if scope == 'private' and changed:
                db.execute('DELETE FROM summary_origin WHERE source=?', (source,))
                db.execute('DELETE FROM summary_reported_model WHERE source=?', (source,))


def exchange(url, payload=None, token=None):
    data = json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type':'application/json'} if data else {}
    if token:
        headers['Authorization']='Bearer '+token
    opener=build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url,data=data,headers=headers),timeout=120) as reply:
        raw=reply.read(4_000_001)
        if len(raw)>4_000_000:
            raise ValueError('response exceeds size limit')
        return raw.decode('utf-8')


def effective_policy(db, source):
    row = db.execute("""SELECT p.mode,p.model,p.endpoint,l.mode,l.model
        FROM records r CROSS JOIN policy p LEFT JOIN record_limits l ON l.source=r.source
        WHERE r.source=? AND p.id=1""",(source,)).fetchone()
    if row is None:
        raise ValueError('unknown record or missing policy')
    store,model,endpoint,limit,local_model = row
    if store not in ('sealed','local','api') or limit not in (None,'sealed','local'):
        raise ValueError('invalid stored policy')
    mode = 'sealed' if store=='sealed' else limit or store
    if mode == 'local':
        model = local_model if limit=='local' else model
        endpoint = ollama_origin() + '/api/chat'
    elif mode == 'sealed':
        model,endpoint = None,None
    else:
        endpoint = https_url(endpoint)
    return dict(store=store,limit=limit or 'inherit',mode=mode,model=model,endpoint=endpoint)


def preview_private(root, source):
    with closing(connect(root,'private')) as db:
        route = effective_policy(db,source)
    endpoint = route.pop('endpoint')
    if endpoint:
        url = urlsplit(endpoint)
        route['destination'] = f'{url.scheme}://{url.netloc}'
    else:
        route['destination'] = None
    return route


def maintain(root, source, *, only_pending=False):
    with closing(connect(root,'private')) as db, db:
        # Serialize permission changes with the entire authorized operation.
        db.execute('BEGIN IMMEDIATE')
        if only_pending and not db.execute(
                'SELECT 1 FROM records WHERE source=? AND summary IS NULL', (source,)).fetchone():
            return False
        route = effective_policy(db,source)
        mode,model,endpoint = route['mode'],route['model'],route['endpoint']
        body = db.execute('SELECT body FROM records WHERE source=?',(source,)).fetchone()[0]
        if mode=='sealed':
            summary=' '.join(body.split())
        elif mode in ('local','api'):
            messages=[{'role':'system','content':(
                'Summarize this record concisely for its owner in the original language. '
                'Preserve qualifications that affect meaning, including fictional or hypothetical status, '
                'negation, exceptions, uncertainty, and whose preference is described. '
                'When a record describes both a current arrangement and a possible future change, '
                'state what applies now in its own sentence. Keep the condition and uncertainty with the future change. '
                'If the record is already a concise sentence, repeat it verbatim.')},
                      {'role':'user','content':body}]
            payload=dict(model=model,messages=messages,stream=False)
            if mode=='local':
                answer=json.loads(exchange(endpoint,payload))
                if (not isinstance(answer, dict) or answer.get('done', True) is not True
                        or answer.get('done_reason', 'stop') != 'stop'):
                    raise ValueError('incomplete maintenance response')
                summary=answer['message']['content']
            else:
                answer=json.loads(exchange(endpoint,payload,os.getenv('ROUNDTABLE_API_KEY')))
                choice=answer['choices'][0]
                if not isinstance(choice, dict) or choice.get('finish_reason', 'stop') != 'stop':
                    raise ValueError('incomplete maintenance response')
                summary=choice['message']['content']
        else:
            raise ValueError('invalid stored policy')
        if not isinstance(summary,str) or not summary.strip():
            raise ValueError('empty maintenance result')
        db.execute('UPDATE records SET summary=? WHERE source=?',(summary,source))
        db.execute('INSERT OR REPLACE INTO summary_origin VALUES (?,?,?)', (source, mode, model))
        reported = answer.get('model') if mode != 'sealed' else None
        if isinstance(reported, str) and reported.strip():
            db.execute('INSERT OR REPLACE INTO summary_reported_model VALUES (?,?)', (source, reported))
        else:
            db.execute('DELETE FROM summary_reported_model WHERE source=?', (source,))
        return True


def maintain_pending(root, limit, *, preview=False, diagnostics=False):
    if limit < 1:
        raise ValueError('limit must be positive')
    with closing(connect(root, 'private')) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS maintenance_cursor (id INTEGER PRIMARY KEY CHECK(id=1), source TEXT NOT NULL)')
        rows = db.execute("""SELECT source FROM records WHERE summary IS NULL
            ORDER BY source <= coalesce((SELECT source FROM maintenance_cursor WHERE id=1), ''), source
            LIMIT ?""", (limit,)).fetchall()
        if preview:
            modes = dict(sealed=0, local=0, api=0)
            for source, in rows:
                modes[effective_policy(db, source)['mode']] += 1
            return dict(selected=len(rows), modes=modes)
        if rows:
            db.execute('INSERT OR REPLACE INTO maintenance_cursor VALUES (1, ?)', rows[-1])
    completed = failed = 0
    failures = {}
    for source, in rows:
        try:
            completed += maintain(root, source, only_pending=True)
        except sqlite3.IntegrityError:
            failed += 1
            if diagnostics:
                failures['storage_constraint'] = failures.get('storage_constraint', 0) + 1
        except (OSError, HTTPException, ValueError, KeyError, IndexError, TypeError) as error:
            failed += 1
            if diagnostics:
                category = 'io_or_http' if isinstance(error, (OSError, HTTPException)) else 'validation'
                failures[category] = failures.get(category, 0) + 1
    result = dict(selected=len(rows), completed=completed, skipped=len(rows)-completed-failed, failed=failed)
    if diagnostics:
        result['failure_counts'] = failures
    return result


def review_public(db, source):
    row = db.execute('SELECT body,shared FROM records WHERE source=?', (source,)).fetchone()
    if row is None:
        raise ValueError('unknown public source')
    return dict(source=source, body=row[0], shared=bool(row[1]),
                sha256=hashlib.sha256(row[0].encode('utf-8')).hexdigest())


def share_public(root, source, enabled, expected=None):
    with closing(connect(root,'public')) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if expected is not None and review_public(db,source)['sha256'] != expected:
            raise ValueError('record changed since review')
        if not db.execute('UPDATE records SET shared=? WHERE source=?',(enabled,source)).rowcount:
            raise ValueError('unknown public source')


def catalog(root):
    with closing(connect(root,'public')) as db:
        return [dict(source=s,body=b) for s,b in db.execute(
            'SELECT source,body FROM records WHERE shared=1 ORDER BY source')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path,help='one owner per directory')
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('init')
    imp=commands.add_parser('import')
    imp.add_argument('scope',choices=['public','private'])
    imp.add_argument('file',type=Path)
    fetch=commands.add_parser('fetch-public')
    fetch.add_argument('url',help='public UTF-8 text URL')
    sync=commands.add_parser('import-feed')
    sync.add_argument('url',help='HTTPS JSON catalog from an owner-selected peer')
    commands.add_parser('review-public').add_argument('source')
    share=commands.add_parser('share-public')
    share.add_argument('source')
    share.add_argument('--if-sha256',help='apply only to the reviewed UTF-8 body')
    share.add_argument('--off',action='store_true')
    commands.add_parser('public-feed')
    policy=commands.add_parser('policy')
    policy.add_argument('mode',choices=['sealed','local','api'])
    policy.add_argument('--model')
    policy.add_argument('--endpoint')
    limit=commands.add_parser('record-limit')
    limit.add_argument('source')
    limit.add_argument('mode', choices=['inherit','sealed','local'])
    limit.add_argument('--model')
    commands.add_parser('preview-private').add_argument('source')
    commands.add_parser('summary-origin').add_argument('source')
    commands.add_parser('private-status', help='count private records with current or pending summaries')
    batch = commands.add_parser('maintain-pending', help='maintain a bounded batch of pending private records')
    batch.add_argument('--limit', required=True, type=int)
    batch.add_argument('--preview', action='store_true', help='count records by maintenance mode without advancing the batch')
    batch.add_argument('--diagnostics', action='store_true', help='include aggregate failure categories during maintenance')
    job=commands.add_parser('maintain-private')
    job.add_argument('source')
    args=parser.parse_args()
    root=args.directory
    try:
        if args.action=='init':
            initialize(root)
        elif args.action=='import':
            ingest(root,args.scope,json.loads(args.file.read_text(encoding='utf-8-sig')))
        elif args.action=='fetch-public':
            url=https_url(args.url)
            ingest(root,'public',[dict(source=url,body=exchange(url))])
        elif args.action=='import-feed':
            ingest(root,'public',json.loads(exchange(https_url(args.url))))
        elif args.action=='review-public':
            with closing(connect(root,'public')) as db:
                print(json.dumps(review_public(db,args.source),ensure_ascii=True))
        elif args.action=='share-public':
            share_public(root,args.source,not args.off,args.if_sha256)
        elif args.action=='public-feed':
            print(json.dumps(catalog(root),ensure_ascii=True))
        elif args.action=='policy':
            configure(root,args.mode,args.model,args.endpoint)
        elif args.action=='record-limit':
            limit_record(root,args.source,args.mode,args.model)
        elif args.action=='preview-private':
            print(json.dumps(preview_private(root,args.source),ensure_ascii=True))
        elif args.action=='summary-origin':
            with closing(connect(root, 'private')) as db:
                row = db.execute("""SELECT r.summary IS NOT NULL, o.mode, o.model, p.model FROM records r
                    LEFT JOIN summary_origin o USING(source) LEFT JOIN summary_reported_model p USING(source)
                    WHERE r.source=?""", (args.source,)).fetchone()
                if row is None:
                    raise ValueError('unknown private record')
                ready, mode, model, reported = row
                origin = dict(ready=bool(ready), mode=mode, model=model)
                if reported is not None:
                    origin['reported_model'] = reported
                print(json.dumps(origin))
        elif args.action=='private-status':
            with closing(connect(root, 'private')) as db:
                total, ready = db.execute('SELECT count(*), count(summary) FROM records').fetchone()
            print(json.dumps(dict(total=total, ready=ready, pending=total-ready)))
        elif args.action=='maintain-private':
            maintain(root,args.source)
        elif args.action=='maintain-pending':
            result = maintain_pending(root, args.limit, preview=args.preview, diagnostics=args.diagnostics)
            print(json.dumps(result))
            if result.get('failed'):
                raise SystemExit(2)
    except (HTTPException,HTTPError,URLError,sqlite3.Error,OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        # Remote messages and malformed input can contain private content.
        parser.exit(2,f'{args.action} failed ({type(exc).__name__})\n')


if __name__=='__main__':
    main()
