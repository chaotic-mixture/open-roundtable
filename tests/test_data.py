from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from open_roundtable import data


class DataTests(unittest.TestCase):
    def setUp(self):
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

    def test_swapped_database_refused(self):
        with closing(data.connect(self.root,'public')) as db, db:
            db.execute("UPDATE metadata SET scope='private'")
        with self.assertRaises(ValueError):
            data.catalog(self.root)
