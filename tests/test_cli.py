"""Subprocess checks of the real offline CLI, using disposable SQLite files."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'demo.sqlite3'

    def run_demo(self):
        return subprocess.run([sys.executable, '-m', 'open_roundtable', 'demo', '--db', str(self.path)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_demo_refuses_existing_database_without_touching_bytes(self):
        first = self.run_demo()
        self.assertEqual(first.returncode, 0, first.stderr)
        original = self.path.read_bytes()
        again = self.run_demo()
        self.assertEqual(again.returncode, 2)
        self.assertIn('refusing existing', again.stderr)
        self.assertEqual(self.path.read_bytes(), original)
        self.path.unlink()
        self.path.write_bytes(b'not a database; must not be overwritten')
        original = self.path.read_bytes()
        invalid = self.run_demo()
        self.assertEqual(invalid.returncode, 2)
        self.assertIn('refusing existing', invalid.stderr)
        self.assertEqual(self.path.read_bytes(), original)

    def test_demo_reports_bad_destination_without_traceback(self):
        self.path = self.path.parent / 'missing-parent' / 'demo.sqlite3'
        result = self.run_demo()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.path.exists())

    def test_demo_runs_real_sqlite_and_labels_synthetic(self):
        result = self.run_demo()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn('synthetic', output['notice'])
        self.assertIn('no real models', output['notice'])
        self.assertEqual(output['budget_unit'], 'local test credits')
        self.assertEqual(output['events'], ['ACTIVE', 'PAUSED', 'ACTIVE', 'RESOLVED'])
        self.assertEqual(output['topic']['state'], 'RESOLVED')
        self.assertEqual({m['kind'] for m in output['topic']['members']}, {'model', 'expert', 'human', 'roundtable'})
        self.assertEqual(output['meta_proposal']['state'], 'DRAFT')
        self.assertEqual(output['meta_proposal']['priority'], 'lowest')
        with closing(sqlite3.connect(self.path)) as db:
            topic = output['topic']['id']
            self.assertEqual(db.execute('SELECT state, credits FROM topics WHERE id=?', (topic,)).fetchone(), ('RESOLVED', 2))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE topic=?', (topic,)).fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM votes WHERE topic=?', (topic,)).fetchone()[0], 3)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
