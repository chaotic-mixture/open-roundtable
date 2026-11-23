"""Issue temporary test credentials and exercise the actual TLS listener."""
from contextlib import closing, ExitStack
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from examples.peer_node import ledger, transfer
from examples.two_node_demo import start
from open_roundtable.data import initialize


class PeerTLSTests(unittest.TestCase):
    def test_mutual_auth_and_sender_binding(self):
        openssl = os.environ.get('ROUNDTABLE_TEST_OPENSSL') or shutil.which('openssl')
        if not openssl:
            self.skipTest('set ROUNDTABLE_TEST_OPENSSL to run temporary certificate issuance')
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            base = Path(tmp)

            def issue(*args):
                subprocess.run([openssl,*args],cwd=base,check=True,capture_output=True)

            issue('req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=Test CA',
                  '-addext','basicConstraints=critical,CA:TRUE','-keyout','ca.key','-out','ca.pem')
            for name in ('A','B'):
                root = base/name
                initialize(root)
                (root/'node.json').write_text(json.dumps(dict(name=name,transport='mtls')))
                shutil.copyfile(base/'ca.pem',root/'ca.pem')
                issue('req','-new','-newkey','rsa:2048','-nodes','-subj','/CN='+name,
                      '-keyout',str(root/'node.key'),'-out','node.csr')
                (base/'extensions').write_text('subjectAltName=IP:127.0.0.1,URI:urn:roundtable:peer:'+name+
                    '\nextendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=CA:FALSE\n')
                issue('x509','-req','-in','node.csr','-CA','ca.pem','-CAkey','ca.key','-CAcreateserial',
                      '-days','1','-extfile','extensions','-out',str(root/'node.pem'))
            _, port = start(stack,base/'B',0)
            url = f'https://127.0.0.1:{port}'
            with closing(ledger(base/'A')) as db:
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
            client = ssl.create_default_context(cafile=str(base/'ca.pem'))
            with self.assertRaises((OSError,HTTPException)):
                urlopen(url+'/catalog',context=client,timeout=3)
