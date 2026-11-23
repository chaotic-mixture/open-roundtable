"""Persist model dispatch attempts before I/O and receipts before charging."""
from contextlib import closing, contextmanager
import json
import sqlite3
from time import perf_counter
from urllib.error import URLError


@contextmanager
def single_writer(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder/'writer.lock.sqlite3',timeout=0)) as lock:
        lock.execute('BEGIN EXCLUSIVE')
        yield


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY, topic TEXT NOT NULL REFERENCES topics(id),
        request TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
        error TEXT, seconds REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS outcomes (
        topic TEXT PRIMARY KEY REFERENCES topics(id), evidence TEXT NOT NULL,
        metrics TEXT NOT NULL)''')
    db.commit()


def dispatch(kernel, topic, request, provider):
    db = kernel.db
    previous = db.execute('SELECT * FROM attempts WHERE topic=? ORDER BY id DESC LIMIT 1',
                          (topic,)).fetchone()
    body = json.dumps(request,sort_keys=True,ensure_ascii=False)
    if previous and previous['request'] != body:
        raise ValueError('request changed during resume')
    if previous and previous['status']=='returned':
        return json.loads(previous['response']), '', previous['seconds']
    if kernel.get(topic)['credits'] < 1:
        raise ValueError('insufficient model budget')
    with db:
        db.execute("UPDATE attempts SET status='unknown' WHERE topic=? AND status='dispatching'",(topic,))
        call = db.execute("INSERT INTO attempts(topic,request,status) VALUES (?,?,'dispatching')",
                          (topic,body)).lastrowid
    start = perf_counter()
    try:
        response = provider('/api/chat',request)
    except (URLError,TimeoutError,ConnectionError,ValueError) as exc:
        seconds = perf_counter()-start
        with db:
            db.execute("UPDATE attempts SET status='failed',error=?,seconds=? WHERE id=?",
                       (repr(exc),seconds,call))
        return None, repr(exc), seconds
    seconds = perf_counter()-start
    with db:
        db.execute("UPDATE attempts SET status='returned',response=?,seconds=? WHERE id=?",
                   (json.dumps(response,ensure_ascii=False),seconds,call))
    return response,'',seconds
