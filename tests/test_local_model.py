"""The local model client stays at the configured loopback origin."""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from examples.auto_consult import api


class LocalModelTransportTests(unittest.TestCase):
    def test_proxy_and_redirect_cannot_receive_model_requests(self):
        contacted = []
        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                contacted.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            do_POST = do_GET

        with ExitStack() as stack:
            def serve(handler):
                server = stack.enter_context(HTTPServer(('127.0.0.1',0),handler))
                threading.Thread(target=server.serve_forever,daemon=True).start()
                stack.callback(server.shutdown)
                return server.server_port

            target = f'http://127.0.0.1:{serve(Destination)}'
            received = []
            class Local(BaseHTTPRequestHandler):
                def do_POST(self):
                    received.append(self.rfile.read(int(self.headers['Content-Length'])))
                    self.send_response(302)
                    self.send_header('Location',target+'/redirected')
                    self.end_headers()

            with patch.dict('os.environ',ROUNDTABLE_OLLAMA_PORT=str(serve(Local)),http_proxy=target,no_proxy=''):
                with self.assertRaises(HTTPError) as error:
                    api('/api/chat',dict(message='synthetic'))
                error.exception.close()
                self.assertEqual(error.exception.code,302)
            self.assertEqual(received,[b'{"message": "synthetic"}'])
            self.assertEqual(contacted,[])
