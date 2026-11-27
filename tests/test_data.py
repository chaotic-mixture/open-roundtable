from contextlib import closing
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from http.client import BadStatusLine
from io import StringIO
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from open_roundtable import data


class DataTests(unittest.TestCase):
    def test_single_record_cli_redacts_malformed_http_status(self):
        data.configure(self.root, 'local', 'fixture')
        errors = StringIO()
        with patch('sys.argv', ['data', str(self.root), 'maintain-private', 'profile']), redirect_stderr(errors):
            with patch.object(data, 'exchange', side_effect=BadStatusLine('private upstream detail')):
                with self.assertRaises(SystemExit) as stopped:
                    data.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(errors.getvalue(), 'maintain-private failed (BadStatusLine)\n')
        with closing(data.connect(self.root, 'private')) as db:
            self.assertEqual(db.execute('SELECT summary FROM records').fetchall(), [(None,)])

    def test_batch_continues_after_a_record_constraint_and_retries_it(self):
        data.ingest(self.root, 'private', [dict(source='second', body='another record')])
        with closing(data.connect(self.root, 'private')) as db, db:
            db.execute("""CREATE TRIGGER reject_profile BEFORE INSERT ON summary_origin
                WHEN NEW.source='profile' BEGIN SELECT RAISE(ABORT, 'private detail'); END""")
        result = data.maintain_pending(self.root, 2, diagnostics=True)
        self.assertEqual(result, dict(selected=2, completed=1, failed=1, skipped=0,
                                      failure_counts={'storage_constraint': 1}))
        with closing(data.connect(self.root, 'private')) as db, db:
            self.assertEqual(db.execute('SELECT source,summary FROM records ORDER BY source').fetchall(),
                             [('profile', None), ('second', 'another record')])
            db.execute('DROP TRIGGER reject_profile')
        self.assertEqual(data.maintain_pending(self.root, 2), dict(selected=1, completed=1, failed=0, skipped=0))

    def test_failed_origin_write_rolls_back_new_summary(self):
        data.maintain(self.root, 'profile')
        with closing(data.connect(self.root, 'private')) as db, db:
            db.execute("""CREATE TRIGGER reject_origin BEFORE INSERT ON summary_origin
                BEGIN SELECT RAISE(ABORT, 'origin unavailable'); END""")
        data.configure(self.root, 'local', 'new-model')
        with patch.object(data, 'exchange', return_value='{"message":{"content":"replacement"}}'):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'origin unavailable'):
                data.maintain(self.root, 'profile')
        with closing(data.connect(self.root, 'private')) as db:
            row = db.execute("""SELECT summary, mode, model FROM records
                JOIN summary_origin USING(source) WHERE source='profile'""").fetchone()
        self.assertEqual(row, ('private content', 'sealed', None))

    def test_summary_origin_tracks_successful_maintenance_across_policy_changes(self):
        def origin():
            output = StringIO()
            with patch('sys.argv', ['data', str(self.root), 'summary-origin', 'profile']), redirect_stdout(output):
                data.main()
            return json.loads(output.getvalue())

        with closing(data.connect(self.root, 'private')) as db, db:
            db.execute("UPDATE records SET summary='legacy'")
        self.assertEqual(origin(), dict(ready=True, mode=None, model=None))
        data.configure(self.root, 'local', 'model-a')
        with patch.object(data, 'exchange', return_value='{"message":{"content":"summary"}}'):
            data.maintain(self.root, 'profile')
        data.configure(self.root, 'sealed')
        self.assertEqual(origin(), dict(ready=True, mode='local', model='model-a'))
        data.configure(self.root, 'local', 'alias')
        with patch.object(data, 'exchange', return_value='{"model":"alias:latest","message":{"content":"summary"}}'):
            data.maintain(self.root, 'profile')
        self.assertEqual(origin(), dict(ready=True, mode='local', model='alias', reported_model='alias:latest'))
        data.ingest(self.root, 'private', [dict(source='profile', body='changed')])
        self.assertEqual(origin(), dict(ready=False, mode=None, model=None))

    def test_local_service_receives_only_permitted_batch_content(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.path, json.loads(raw)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"done":true,"message":{"content":"local summary"}}')

        data.ingest(self.root, 'private', [dict(source='sealed', body='sealed fixture text')])
        data.configure(self.root, 'local', 'fixture')
        data.limit_record(self.root, 'sealed', 'sealed')
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                with patch.dict('os.environ', ROUNDTABLE_OLLAMA_PORT=str(server.server_port)):
                    preview = data.maintain_pending(self.root, 2, preview=True)
                    self.assertEqual(preview['modes'], dict(sealed=1, local=1, api=0))
                    self.assertEqual(requests, [])
                    self.assertEqual(data.maintain_pending(self.root, 2)['completed'], 2)
                    self.assertEqual(data.maintain_pending(self.root, 2)['selected'], 0)
            finally:
                server.shutdown()
                worker.join()
        (path, payload), = requests
        self.assertEqual(path, '/api/chat')
        self.assertEqual(payload['messages'][-1]['content'], ' private   content ')
        self.assertNotIn('sealed fixture text', json.dumps(payload))
        with closing(data.connect(self.root, 'private')) as db:
            self.assertEqual(db.execute('SELECT summary FROM records ORDER BY source').fetchall(),
                             [('local summary',), ('sealed fixture text',)])

    def test_batch_preview_counts_routes_without_advancing(self):
        data.ingest(self.root, 'private', [dict(source='second', body='local only')])
        data.configure(self.root, 'api', 'remote', 'https://example.org/chat')
        data.limit_record(self.root, 'second', 'sealed')
        with patch.object(data, 'exchange', side_effect=ConnectionError) as api:
            for _ in range(2):
                self.assertEqual(data.maintain_pending(self.root, 1, preview=True),
                                 dict(selected=1, modes=dict(api=1, sealed=0, local=0)))
            api.assert_not_called()
            self.assertEqual(data.maintain_pending(self.root, 1)['failed'], 1)
            api.assert_called_once()
            self.assertEqual(data.maintain_pending(self.root, 1, preview=True),
                             dict(selected=1, modes=dict(api=0, sealed=1, local=0)))

    def test_one_record_batches_progress_past_an_unavailable_provider(self):
        data.ingest(self.root, 'private', [dict(source='second', body='local only')])
        data.configure(self.root, 'api', 'remote', 'https://example.org/chat')
        data.limit_record(self.root, 'second', 'sealed')
        with patch.object(data, 'exchange', side_effect=ConnectionError) as api:
            self.assertEqual(data.maintain_pending(self.root, 1)['failed'], 1)
            self.assertEqual(data.maintain_pending(self.root, 1)['completed'], 1)
            self.assertEqual(data.maintain_pending(self.root, 1)['failed'], 1)
            self.assertEqual(api.call_count, 2)

    def test_batch_finishes_sealed_record_after_api_outage(self):
        data.ingest(self.root, 'private', [dict(source='sealed', body='local only')])
        data.configure(self.root, 'api', 'remote', 'https://example.org/chat')
        data.limit_record(self.root, 'sealed', 'sealed')
        output = StringIO()
        with patch('sys.argv', ['data', str(self.root), 'maintain-pending', '--limit', '2']), redirect_stdout(output):
            with patch.object(data, 'exchange', side_effect=ConnectionError('sensitive detail')) as api:
                with self.assertRaises(SystemExit) as exit_status:
                    data.main()
                api.assert_called_once()
        self.assertEqual(exit_status.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), dict(selected=2, completed=1, skipped=0, failed=1))
        for error, category in [(ConnectionError('sensitive detail'), 'io_or_http'),
                                (ValueError('sensitive detail'), 'validation')]:
            with patch.object(data, 'exchange', side_effect=error):
                report = data.maintain_pending(self.root, 2, diagnostics=True)
            self.assertEqual(report, dict(selected=1, completed=0, skipped=0, failed=1,
                                          failure_counts={category: 1}))
        with closing(data.connect(self.root, 'private')) as db:
            self.assertEqual(db.execute('SELECT source,summary FROM records ORDER BY source').fetchall(),
                             [('profile', None), ('sealed', 'local only')])

    def test_pending_batch_resumes_and_honors_record_permissions(self):
        data.ingest(self.root, 'private', [dict(source='sealed', body='local only')])
        data.configure(self.root, 'api', 'remote', 'https://example.org/chat')
        data.limit_record(self.root, 'sealed', 'sealed')
        with patch.object(data, 'exchange', return_value='{"choices":[{"message":{"content":"ok"}}]}') as api:
            with self.assertRaises(ValueError):
                data.maintain_pending(self.root, 0)
            self.assertEqual(data.maintain_pending(self.root, 1)['completed'], 1)
            self.assertEqual(data.maintain_pending(self.root, 4)['completed'], 1)
            self.assertEqual(data.maintain_pending(self.root, 4)['selected'], 0)
            self.assertFalse(data.maintain(self.root, 'profile', only_pending=True))
            self.assertEqual(api.call_count, 1)
            self.assertEqual(api.call_args.args[1]['messages'][-1]['content'], ' private   content ')

    def test_private_status_tracks_current_summaries_without_exposing_contents(self):
        def status():
            output = StringIO()
            with patch('sys.argv', ['data', str(self.root), 'private-status']), redirect_stdout(output):
                data.main()
            return json.loads(output.getvalue())

        pending = dict(total=1, ready=0, pending=1)
        ready = dict(total=1, ready=1, pending=0)
        with patch.object(data, 'exchange', side_effect=AssertionError):
            self.assertEqual(status(), pending)
            data.maintain(self.root, 'profile')
            self.assertEqual(status(), ready)
            data.ingest(self.root, 'private', [dict(source='profile', body=' private   content ')])
            self.assertEqual(status(), ready)
            data.ingest(self.root, 'private', [dict(source='profile', body='updated')])
            self.assertEqual(status(), pending)

    def setUp(self):
        port = patch.dict('os.environ', ROUNDTABLE_OLLAMA_PORT='11434')
        port.start()
        self.addCleanup(port.stop)
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'owner'
        data.initialize(self.root)
        data.ingest(self.root,'private',[{'source':'profile','body':' private   content '}])

    def test_sealed_maintenance_stays_inside_store(self):
        with patch.object(data,'exchange',side_effect=AssertionError):
            data.maintain(self.root,'profile')
        with closing(data.connect(self.root,'private')) as db:
            self.assertEqual(db.execute('SELECT summary FROM records').fetchone(),('private content',))
        self.assertEqual(data.catalog(self.root),[])

    def test_long_sealed_record_preserves_final_exception(self):
        body = '周末安排待确认。' * 80 + '\n  例外：本周五不接晚班。'
        data.ingest(self.root, 'private', [dict(source='long', body=body)])
        with patch.object(data, 'exchange', side_effect=AssertionError):
            data.maintain(self.root, 'long')
        with closing(data.connect(self.root, 'private')) as db:
            record = db.execute("SELECT body,summary FROM records WHERE source='long'").fetchone()
        self.assertEqual(record, (body, ' '.join(body.split())))

    def test_incomplete_provider_responses_preserve_previous_private_summary(self):
        data.maintain(self.root, 'profile')
        for mode, response in [
                ('local', '{"done":false,"message":{"content":"partial"}}'),
                ('local', '{"done":true,"done_reason":"length","message":{"content":"partial"}}'),
                ('api', '{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}')]:
            with self.subTest(mode=mode, response=response):
                endpoint = 'https://example.org/chat' if mode == 'api' else None
                data.configure(self.root, mode, 'model', endpoint)
                with patch.object(data, 'exchange', return_value=response), self.assertRaisesRegex(ValueError, 'incomplete'):
                    data.maintain(self.root, 'profile')
                with closing(data.connect(self.root, 'private')) as db:
                    self.assertEqual(db.execute('SELECT summary FROM records').fetchone(), ('private content',))

    def test_separate_permission_for_public_distribution(self):
        entry={'source':'https://example.org/document','body':'public'}
        data.ingest(self.root,'public',[entry])
        self.assertEqual(data.catalog(self.root),[])
        with closing(data.connect(self.root,'public')) as db, db:
            db.execute('UPDATE records SET shared=1')
        self.assertEqual(data.catalog(self.root),[entry])
        data.ingest(self.root,'public',[entry])
        self.assertEqual(data.catalog(self.root),[entry])
        data.ingest(self.root,'public',[dict(entry,body='updated')])
        self.assertEqual(data.catalog(self.root),[])

    def test_publish_reviewed_revision(self):
        source = 'https://example.org/review'
        data.ingest(self.root,'public',[dict(source=source,body='first')])
        with closing(data.connect(self.root,'public')) as db:
            original = data.review_public(db,source)
        data.ingest(self.root,'public',[dict(source=source,body='updated')])
        with self.assertRaisesRegex(ValueError,'changed since review'):
            data.share_public(self.root,source,True,original['sha256'])
        self.assertEqual(data.catalog(self.root),[])
        with closing(data.connect(self.root,'public')) as db:
            updated = data.review_public(db,source)
        data.share_public(self.root,source,True,updated['sha256'])
        self.assertEqual(data.catalog(self.root),[dict(source=source,body='updated')])
        data.share_public(self.root,source,False)
        self.assertEqual(data.catalog(self.root),[])

    def test_policy_routes_and_revocation(self):
        data.configure(self.root,'local','local-model')
        with patch.object(data,'exchange',return_value='{"message":{"content":"summary"}}') as send:
            data.maintain(self.root,'profile')
            self.assertEqual(send.call_args.args[0],'http://127.0.0.1:11434/api/chat')
        with self.assertRaises(ValueError):
            data.configure(self.root,'api','remote','http://example.org')
        url='https://example.org/chat/completions'
        data.configure(self.root,'api','remote',url)
        with patch.object(data,'exchange',return_value='{"choices":[{"message":{"content":"summary"}}]}') as send:
            data.maintain(self.root,'profile')
            self.assertEqual(send.call_args.args[0],url)
            self.assertEqual(send.call_args.args[1]['messages'][-1]['content'],' private   content ')
        data.configure(self.root,'sealed')
        with patch.object(data,'exchange',side_effect=AssertionError):
            data.maintain(self.root,'profile')

    def test_record_limits_survive_updates_and_respect_sealed_store(self):
        data.configure(self.root,'api','remote','https://example.org/chat')
        data.limit_record(self.root,'profile','sealed')
        data.ingest(self.root,'private',[dict(source='profile',body='replacement')])
        with patch.object(data,'exchange',side_effect=AssertionError):
            data.maintain(self.root,'profile')
        data.limit_record(self.root,'profile','local','owner-model')
        with patch.object(data,'exchange',return_value='{"message":{"content":"local result"}}') as send:
            data.maintain(self.root,'profile')
            self.assertEqual(send.call_args.args[0],'http://127.0.0.1:11434/api/chat')
            self.assertEqual(send.call_args.args[1]['model'],'owner-model')
        data.configure(self.root,'sealed')
        with patch.object(data,'exchange',side_effect=AssertionError):
            data.maintain(self.root,'profile')
        data.limit_record(self.root,'profile','inherit')
        data.configure(self.root,'api','remote','https://example.org/chat')
        with patch.object(data,'exchange',return_value='{"choices":[{"message":{"content":"summary"}}]}') as send:
            data.maintain(self.root,'profile')
            self.assertEqual(send.call_args.args[0],'https://example.org/chat')

    def test_preview_shows_effective_destination_without_sending(self):
        with patch.object(data,'exchange',side_effect=AssertionError):
            data.configure(self.root,'api','remote','https://example.org/private/path?key=hidden')
            self.assertEqual(data.preview_private(self.root,'profile'),
                dict(store='api',limit='inherit',mode='api',model='remote',destination='https://example.org'))
            data.limit_record(self.root,'profile','local','owner-model')
            self.assertEqual(data.preview_private(self.root,'profile')['destination'],'http://127.0.0.1:11434')
            data.configure(self.root,'sealed')
            self.assertEqual(data.preview_private(self.root,'profile'),
                dict(store='sealed',limit='local',mode='sealed',model=None,destination=None))
            with self.assertRaises(ValueError):
                data.preview_private(self.root,'missing')

    def test_custom_local_model_port(self):
        data.configure(self.root,'local','local-model')
        with patch.dict('os.environ', ROUNDTABLE_OLLAMA_PORT='49001'):
            self.assertEqual(data.preview_private(self.root,'profile')['destination'],'http://127.0.0.1:49001')
            with patch.object(data,'exchange',return_value='{"message":{"content":"summary"}}') as send:
                data.maintain(self.root,'profile')
                self.assertEqual(send.call_args.args[0],'http://127.0.0.1:49001/api/chat')
        with patch.dict('os.environ', ROUNDTABLE_OLLAMA_PORT='65536'), patch.object(data,'exchange') as send:
            with self.assertRaises(ValueError):
                data.maintain(self.root,'profile')
            send.assert_not_called()

    def test_swapped_database_refused(self):
        with closing(data.connect(self.root,'public')) as db, db:
            db.execute("UPDATE metadata SET scope='private'")
        with self.assertRaises(ValueError):
            data.catalog(self.root)
