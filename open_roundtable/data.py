"""Owner-operated public catalog and private personalization store."""
import argparse
from contextlib import closing
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
            db.execute('''INSERT INTO records(source,body) VALUES (?,?)
                ON CONFLICT(source) DO UPDATE SET body=excluded.body,summary=NULL,shared=0
                WHERE records.body<>excluded.body''',(source,body))


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


def maintain(root, source):
    with closing(connect(root,'private')) as db, db:
        # Serialize permission changes with the entire authorized operation.
        db.execute('BEGIN IMMEDIATE')
        mode,model,endpoint=db.execute('SELECT mode,model,endpoint FROM policy WHERE id=1').fetchone()
        record=db.execute('SELECT body FROM records WHERE source=?',(source,)).fetchone()
        if record is None:
            raise ValueError('unknown record')
        body=record[0]
        if mode=='sealed':
            summary=' '.join(body.split())[:500]
        elif mode in ('local','api'):
            messages=[{'role':'system','content':'Summarize this personal record for its owner.'},
                      {'role':'user','content':body}]
            payload=dict(model=model,messages=messages,stream=False)
            if mode=='local':
                answer=json.loads(exchange('http://127.0.0.1:11434/api/chat',payload))
                summary=answer['message']['content']
            else:
                answer=json.loads(exchange(https_url(endpoint),payload,os.getenv('ROUNDTABLE_API_KEY')))
                summary=answer['choices'][0]['message']['content']
        else:
            raise ValueError('invalid stored policy')
        if not isinstance(summary,str) or not summary.strip():
            raise ValueError('empty maintenance result')
        db.execute('UPDATE records SET summary=? WHERE source=?',(summary,source))


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
    share=commands.add_parser('share-public')
    share.add_argument('source')
    share.add_argument('--off',action='store_true')
    commands.add_parser('public-feed')
    policy=commands.add_parser('policy')
    policy.add_argument('mode',choices=['sealed','local','api'])
    policy.add_argument('--model')
    policy.add_argument('--endpoint')
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
        elif args.action=='share-public':
            with closing(connect(root,'public')) as db, db:
                if not db.execute('UPDATE records SET shared=? WHERE source=?',
                                  (not args.off,args.source)).rowcount:
                    raise ValueError('unknown public source')
        elif args.action=='public-feed':
            print(json.dumps(catalog(root),ensure_ascii=True))
        elif args.action=='policy':
            configure(root,args.mode,args.model,args.endpoint)
        elif args.action=='maintain-private':
            maintain(root,args.source)
    except (HTTPError,URLError,sqlite3.Error,OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        # Remote messages and malformed input can contain private content.
        parser.exit(2,f'{args.action} failed ({type(exc).__name__})\n')


if __name__=='__main__':
    main()
