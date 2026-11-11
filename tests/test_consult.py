import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from examples.auto_consult import run, verify
from examples.synthetic_consult import MODEL, fixture
from open_roundtable.schedule_expert import check


class ConsultationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'consult'

    def execute(self, provider=fixture):
        output = io.StringIO()
        with redirect_stdout(output):
            run(self.folder, MODEL, 47, provider)
        return json.loads(output.getvalue())

    def test_positive_vote_and_zero_errors(self):
        result = self.execute()
        self.assertEqual((result['state'], result['credits_spent']), ('RESOLVED', 2))
        self.assertEqual(sum(len(row['truth']) for row in result['scores'].values()), 7)
        self.assertTrue(all(not row['extra'] and not row['missing'] for row in result['scores'].values()))
        with self.assertRaises(FileExistsError):
            self.execute()

    def test_disagreement_and_independent_scoring(self):
        def dissent(path, payload=None):
            response = fixture(path, payload)
            if payload:
                answer = json.loads(response['message']['content'])
                answer.update(P=['AB'], approve_final=True)
                response['message']['content'] = json.dumps(answer)
            return response
        result = self.execute(dissent)
        self.assertEqual(result['state'], 'PAUSED')
        self.assertEqual(result['scores']['P'], {'truth':['DE'], 'extra':['AB'], 'missing':['DE']})

    def test_altered_ledger_rejected(self):
        self.execute()
        with closing(sqlite3.connect(self.folder/'ledger.sqlite3')) as db:
            db.execute('UPDATE messages SET cost=2')
            db.commit()
        with self.assertRaisesRegex(ValueError, 'evidence mismatch'):
            verify(self.folder)

    def test_overnight_rest_and_exact_boundaries(self):
        shifts = ['Axu:20-24','Bxv:24-28','Cxu:36-38','Dxu:39-48']
        self.assertEqual(check(shifts), {'P':set(), 'V':set(), 'R':{'x@38-39'}, 'L':{'x@39-48'}})
