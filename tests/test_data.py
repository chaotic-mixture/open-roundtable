from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from open_roundtable import data


class DataTests(unittest.TestCase):
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
