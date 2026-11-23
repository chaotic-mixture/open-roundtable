"""Issue temporary test credentials and exercise the actual TLS listener."""
from contextlib import closing, ExitStack
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from unittest.mock import patch
import json
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

from examples.demo_certificates import issue_certificates
from examples.peer_node import digest, ledger, packed, transfer
from examples.two_node_demo import audit, cli, run, start
from open_roundtable.data import initialize, share_public


class PeerTLSTests(unittest.TestCase):
    def test_mutual_auth_and_sender_binding(self):
        openssl = os.environ.get('ROUNDTABLE_TEST_OPENSSL') or shutil.which('openssl')
        if not openssl:
            self.skipTest('set ROUNDTABLE_TEST_OPENSSL to run temporary certificate issuance')
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            base = Path(tmp)

            credentials = base/'certificates'
            credentials.mkdir()
            issue_certificates(credentials, openssl)
            for name in ('A','B'):
                root = base/name
                initialize(root)
                (root/'node.json').write_text(json.dumps(dict(name=name,transport='mtls',credentials=str(credentials/name))))
            _, port = start(stack,base/'B',0)
            url = f'https://localhost:{port}'
            with closing(ledger(base/'A')) as db:
                with self.assertRaisesRegex(ValueError,'remote origin'):
                    transfer(db,url+'/catalog',peer_name='B')
                (base/'A'/'node.json').write_text(json.dumps(dict(name='A',transport='mtls',credentials=str(credentials/'A'),allowed_origins=[url])))
                self.assertEqual(transfer(db,url+'/catalog',peer_name='B'), [])
                with self.assertRaises(URLError) as wrong_peer:
                    transfer(db,url+'/consult',dict(sender='A'),peer_name='C')
                self.assertIsInstance(wrong_peer.exception.reason, ssl.SSLCertVerificationError)
                with closing(ledger(base/'B')) as receiver:
                    self.assertEqual(receiver.execute("SELECT count(*) FROM traffic WHERE route='/consult'").fetchone(), (0,))
                with self.assertRaises(HTTPError) as failure:
                    transfer(db,url+'/consult',dict(sender='C'))
                failure.exception.close()
                self.assertEqual(failure.exception.code,403)
                with self.assertRaises(ValueError):
                    transfer(db,url.replace('https:', 'http:')+'/catalog')
            client = ssl.create_default_context(cafile=str(credentials/'A'/'ca.pem'))
            with self.assertRaises((OSError,HTTPException)):
                build_opener(ProxyHandler({}),HTTPSHandler(context=client)).open(url+'/catalog',timeout=3)

            calls = []
            class Model(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers['Content-Length']))
                    calls.append(self.path)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(dict(model='fixture', done=True, done_reason='stop',
                        prompt_eval_count=19, eval_count=7,
                        message=dict(role='assistant', content='{"plan":0}'))).encode())

            model = stack.enter_context(HTTPServer(('127.0.0.1',0), Model))
            threading.Thread(target=model.serve_forever,daemon=True).start()
            stack.callback(model.shutdown)
            folder = base/'exercise'
            with patch.dict(os.environ, ROUNDTABLE_OLLAMA_PORT=str(model.server_port)):
                result = run(folder, 'fixture', 'before-receipt', credentials=credentials, bind_offer=True)
            self.assertEqual(audit(folder), result)
            self.assertEqual((result['deliveries'], result['credits'], calls), (2, 2, ['/api/chat']))
            with closing(ledger(folder/'B')) as db:
                fetched = db.execute("SELECT route,body FROM traffic WHERE direction='receive' AND route LIKE '%/record?%'").fetchall()
                sources = set()
                for route, body in fetched:
                    requested = parse_qs(urlsplit(route).query)['source']
                    self.assertEqual([r['source'] for r in json.loads(body)], requested)
                    sources.update(requested)
                self.assertIn('https://roundtable.example/topics/weekend', sources)
                self.assertTrue(any(source.endswith('/node') for source in sources))
            for name in ('A','B'):
                with closing(ledger(folder/name)) as db:
                    routes = db.execute("SELECT route FROM traffic WHERE direction='send'").fetchall()
                    self.assertTrue(routes and all(url.startswith('https://') for url, in routes))

            receiver = folder/'B'
            configuration = json.loads((receiver/'node.json').read_text())
            configuration['statement_partners'] = ['A']
            (receiver/'node.json').write_text(json.dumps(configuration))
            _, new_port = start(stack,receiver,0)
            with closing(ledger(folder/'A')) as db, db:
                db.execute('UPDATE peers SET url=? WHERE name=?',(f'https://127.0.0.1:{new_port}','B'))
            def accepting():
                return json.loads(cli(folder/'A','admission','B').stdout)['accepting']
            self.assertFalse(accepting())  # Earlier consultation exhausted the single grant.
            cli(receiver,'permission','A','allow','--remaining','1')
            self.assertTrue(accepting())
            self.assertTrue(accepting())
            with closing(ledger(receiver)) as db:
                self.assertEqual(db.execute("SELECT remaining FROM grant_limits WHERE name='A'").fetchone(), (1,))
            cli(receiver,'permission','A','revoke')
            self.assertFalse(accepting())
            source = 'https://b.example/offers/repair'
            draft = json.loads(cli(receiver,'offer',source,'repair','Repair weekly shifts').stdout)
            def lookup():
                return json.loads(cli(folder/'A','find-capability','repair').stdout)
            self.assertEqual(lookup(), dict(matches=[],unavailable=[]))
            share_public(receiver,source,True,draft['sha256'])
            self.assertEqual(lookup()['matches'], [dict(node='B',source=source,description='Repair weekly shifts',sha256=draft['sha256'])])
            with closing(ledger(folder/'A')) as db:
                wire = db.execute("SELECT body FROM traffic WHERE direction='receive' AND route LIKE '%/offers' ORDER BY rowid DESC LIMIT 1").fetchone()
                self.assertEqual([r['source'] for r in json.loads(wire[0])], [source])
            cli(receiver,'permission','A','allow','--remaining','0')
            blocked = json.loads(cli(folder/'A','match-capability','repair shifts','--accepting-only').stdout)
            self.assertEqual((blocked['matches'],blocked['declined'],blocked['calls']), ([],['B'],0))
            cli(receiver,'permission','A','allow','--remaining','1')
            eligible = json.loads(cli(folder/'A','find-capability','repair','--accepting-only').stdout)
            self.assertEqual(eligible['matches'],lookup()['matches'])
            self.assertEqual(eligible['declined'],[])
            share_public(receiver,source,False)
            self.assertEqual(lookup()['matches'], [])
            rejected = cli(folder/'A','send','B','https://roundtable.example/topics/weekend',
                           '--offer',source,draft['sha256'],ok=False)
            self.assertIn(b'selected offer is no longer published',rejected.stderr)

            self.assertEqual(json.loads(cli(folder/'A','reconcile-online','B').stdout),
                             dict(matched=1,differences=[]))
            with closing(ledger(folder/'A')) as db:
                with self.assertRaises(HTTPError) as denied:
                    transfer(db,f'https://127.0.0.1:{new_port}/statement',dict(sender='C'),peer_name='B')
                denied.exception.close()
                self.assertEqual(denied.exception.code,403)

            synthetic = []
            for i in range(6000):
                request = dict(sender='A', recipient='B', source='synthetic', spec=dict(case=i))
                key = digest(request)
                receipt = dict(id=key,sender='B',recipient='A',credits=2,outcome=dict(state='RESOLVED'))
                synthetic.append((key,packed(dict(request,id=key)).decode(),packed(receipt).decode(),2))
            for root in (folder/'A',receiver):
                with closing(ledger(root)) as db, db:
                    db.executemany('INSERT INTO jobs VALUES (?,?,?,?)', synthetic)
            self.assertEqual(json.loads(cli(folder/'A','reconcile-online','B').stdout),
                             dict(matched=6001,differences=[]))
            endpoint = f'https://127.0.0.1:{new_port}/statement'
            with closing(ledger(folder/'A')) as db:
                page = transfer(db,endpoint,dict(sender='A',after='',revision=None),peer_name='B')
                self.assertEqual(len(page['entries']),256)
                with closing(ledger(receiver)) as writer, writer:
                    other = dict(sender='B', recipient='C', source='other', spec={})
                    other['id'] = digest(other)
                    writer.execute('INSERT INTO jobs(id,request) VALUES (?,?)',(other['id'],packed(other).decode()))
                following = transfer(db,endpoint,dict(sender='A',after=page['next'],revision=page['revision']),peer_name='B')
                self.assertEqual(following['revision'],page['revision'])
                self.assertNotIn(other['id'], {e['job'] for e in following['entries']})
                with closing(ledger(receiver)) as writer, writer:
                    writer.execute('DELETE FROM jobs WHERE id=?',(synthetic[0][0],))
                with self.assertRaises(HTTPError) as stale:
                    transfer(db,endpoint,dict(sender='A',after=page['next'],revision=page['revision']),peer_name='B')
                stale.exception.close()
                self.assertEqual(stale.exception.code,409)
