"""Exercise discovery, lost receipt recovery and private-data boundaries over HTTP."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from examples.peer_node import deliver, digest, ledger, resume, set_permission
from examples.two_node_demo import SOURCE, audit, cli, run, start
from open_roundtable.data import connect, ingest, initialize


class PeerRecoveryTests(unittest.TestCase):
    def test_simultaneous_consultations_and_duplicate(self):
        both_started = threading.Event()
        release = threading.Event()
        calls = []
        lock = threading.Lock()

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                with lock:
                    calls.append(self.path)
                    if len(calls) == 2:
                        both_started.set()
                release.wait(15)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(dict(model='fixture', done=True, done_reason='stop',
                    prompt_eval_count=19, eval_count=7,
                    message=dict(role='assistant', content='{"plan":0}'))).encode())

        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(3) as pool, ExitStack() as stack:
            server = stack.enter_context(ThreadingHTTPServer(('127.0.0.1', 0), Model))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            stack.callback(server.shutdown)
            addresses = {}
            spec = Path(__file__).resolve().parents[1] / 'examples' / 'consult_input.json'
            roots = {name: Path(tmp) / name for name in ('A', 'B')}
            for name, root in roots.items():
                initialize(root)
                (root / 'node.json').write_text(json.dumps(dict(name=name, model='fixture')))
                ingest(root, 'public', [dict(source=SOURCE, body=spec.read_text(encoding='utf8'))])
                with closing(connect(root, 'public')) as db, db:
                    db.execute('UPDATE records SET shared=1')
                with patch.dict(os.environ, ROUNDTABLE_OLLAMA_PORT=str(server.server_port)):
                    _, port = start(stack, root, 0)
                addresses[name] = f'http://127.0.0.1:{port}'
            for name, other in [('A','B'), ('B','A')]:
                with closing(ledger(roots[name])) as db:
                    db.execute('INSERT INTO peers VALUES (?,?)', (other, addresses[other]))
                    set_permission(db, other, True)
            stack.callback(release.set)
            pending = [pool.submit(cli, roots[name], 'send', other, SOURCE)
                       for name, other in [('A','B'), ('B','A')]]
            self.assertTrue(both_started.wait(10), 'both consultations should reach the model')
            duplicate = pool.submit(cli, roots['A'], 'send', 'B', SOURCE, ok=False)
            self.assertNotEqual(duplicate.result(timeout=5).returncode, 0)
            release.set()
            receipts = [json.loads(task.result(timeout=10).stdout) for task in pending]
            replay = json.loads(cli(roots['A'], 'send', 'B', SOURCE).stdout)
            self.assertEqual(replay, receipts[0])
            self.assertEqual([r['credits'] for r in receipts], [2, 2])
            self.assertEqual(calls, ['/api/chat', '/api/chat'])
            for root in roots.values():
                with closing(ledger(root)) as db:
                    self.assertEqual(db.execute('SELECT count(result),sum(credits) FROM jobs').fetchone(), (2,4))

    def test_receiver_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'node.json').write_text('{"name":"B"}')
            body = dict(sender='A', recipient='B', source='public', spec={})
            request = dict(body, id=digest(body))
            with closing(ledger(root)) as db, patch('examples.peer_node.transfer') as transfer:
                db.execute("INSERT INTO peers VALUES ('A','http://127.0.0.1:8001')")
                for state in ('default', 'moved', 'revoked'):
                    if state == 'moved':
                        set_permission(db, 'A', True)
                        db.execute("UPDATE peers SET url='http://127.0.0.1:8002'")
                    if state == 'revoked':
                        set_permission(db, 'A', True)
                        set_permission(db, 'A', False)
                    with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'no consultation grant'):
                        deliver(root, db, request)
                transfer.assert_not_called()
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone(), (0,))

    def test_replay_and_privacy(self):
        requests = []

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.path, payload))
                body = dict(model='fixture', done=True, done_reason='stop',
                            prompt_eval_count=19, eval_count=7,
                            message=dict(role='assistant', content='{"plan":0}'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        with tempfile.TemporaryDirectory() as tmp, HTTPServer(('127.0.0.1', 0), Model) as server:
            folder = Path(tmp) / 'pair'
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch.dict(os.environ, ROUNDTABLE_OLLAMA_PORT=str(server.server_port)):
                    early = run(Path(tmp) / 'early', 'fixture', 'before-receipt')
                    self.assertEqual((early['credits'], len(requests)), (2, 1))
                    requests.clear()
                    result = run(folder, 'fixture')
            finally:
                server.shutdown()
                thread.join()
            self.assertEqual(result['discovered'], ['A', 'B'])
            self.assertEqual((result['deliveries'], result['credits']), (2, 2))
            self.assertEqual([path for path, _ in requests], ['/api/chat'])
            self.assertEqual(result, audit(folder))
            accounts = [json.loads(cli(folder / node, 'account').stdout) for node in ('A','B')]
            self.assertEqual(accounts[0]['entries'], accounts[1]['entries'])
            self.assertEqual([(a['contributed'],a['consumed'],a['pending']) for a in accounts],
                             [(0,2,0),(2,0,0)])
            key = result['request_id']
            note = folder / 'note.txt'
            note.write_text('private-practice-observation')
            self.assertIsNone(json.loads(cli(folder/'A','quality','B').stdout)['useful_fraction'])
            for _ in range(2):
                rating = json.loads(cli(folder/'A','feedback',key,'useful',str(note)).stdout)
                self.assertEqual((rating['received'],rating['reviewed'],rating['useful']), (1,1,1))
                self.assertNotIn(note.read_text(), str(rating))
            for node, outcome in [('A','not-useful'), ('B','useful')]:
                self.assertNotEqual(cli(folder/node,'feedback',key,outcome,str(note),ok=False).returncode, 0)
            self.assertEqual(json.loads(cli(folder/'A','quality','B').stdout), rating)
            with closing(sqlite3.connect(folder/'A'/'private.sqlite3')) as private:
                self.assertEqual(private.execute('SELECT observation FROM practice_feedback').fetchone(),
                                 (note.read_text(),))
            label = 'personal-revisit-label'
            for _ in range(2):
                cli(folder / 'A', 'mark', key, label)
            receipts = json.loads(cli(folder / 'A', 'revisit', label).stdout)
            self.assertEqual([receipt['id'] for receipt in receipts], [key])
            self.assertEqual(json.loads(cli(folder / 'B', 'revisit', label).stdout), [])
            self.assertNotEqual(cli(folder / 'A', 'mark', 'missing', label, ok=False).returncode, 0)
            self.assertEqual(result, audit(folder))
            for node in ('A', 'B'):
                with closing(sqlite3.connect(folder / node / 'private.sqlite3')) as private:
                    source, body = private.execute('SELECT source,body FROM records').fetchone()
                for value in (source, body):
                    self.assertNotIn(value.encode(), requests[0][1])
            with closing(sqlite3.connect(folder / 'A' / 'network.sqlite3')) as network, network:
                network.execute("UPDATE jobs SET credits=3")
                with self.assertRaisesRegex(ValueError, 'endpoints disagree'):
                    network.commit()
                    audit(folder)
                self.assertNotEqual(cli(folder / 'A', 'account', ok=False).returncode, 0)
                network.execute("UPDATE jobs SET credits=2")
                network.execute("INSERT INTO traffic(direction,route,body) VALUES('send','/check',?)", (body,))
            with self.assertRaisesRegex(ValueError, 'private marker'):
                audit(folder)

            sender = folder / 'A'
            with closing(sqlite3.connect(sender / 'network.sqlite3')) as network, network:
                network.execute('UPDATE jobs SET result=NULL')
                key = result['request_id']
                with closing(sqlite3.connect(sender / 'public.sqlite3')) as public, public:
                    public.execute('UPDATE records SET body=? WHERE source=?', ('{}', SOURCE))
                    public.commit()
                    with patch('examples.peer_node.transfer') as transfer:
                        self.assertEqual(resume(sender, network), {key: 'published topic changed'})
                        public.execute('UPDATE records SET shared=0 WHERE source=?', (SOURCE,))
                        public.commit()
                        self.assertEqual(resume(sender, network), {key: 'topic must be explicitly shared'})
                        transfer.assert_not_called()
                self.assertEqual(network.execute('SELECT result FROM jobs').fetchone(), (None,))
