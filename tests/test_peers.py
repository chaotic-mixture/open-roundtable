"""Exercise discovery, lost receipt recovery and private-data boundaries over HTTP."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from examples.peer_node import resume
from examples.two_node_demo import SOURCE, audit, run


class PeerRecoveryTests(unittest.TestCase):
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
                    result = run(folder, 'fixture')
            finally:
                server.shutdown()
                thread.join()
            self.assertEqual(result['discovered'], ['A', 'B'])
            self.assertEqual((result['deliveries'], result['credits']), (2, 2))
            self.assertEqual([path for path, _ in requests], ['/api/chat'])
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
