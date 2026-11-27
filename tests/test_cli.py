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

    def test_audit_exit_status_and_report(self):
        source = self.path.with_suffix('.json')
        for end, flags, status in [(26, [], 0), (26, ['--fail-on-findings'], 1),
                                   (21, ['--fail-on-findings'], 0), (169, ['--fail-on-findings'], 2)]:
            with self.subTest(end=end, flags=flags):
                source.write_text(json.dumps(dict(operation='schedule-audit-v1', rules=['R'],
                                                  shifts=[f'Axv:18-{end}', 'Bxu:29-33'])))
                result = subprocess.run([sys.executable, '-m', 'open_roundtable', 'audit', str(source), *flags],
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, status)
                if status == 2:
                    self.assertIn('shifts[0]', result.stderr)
                else:
                    self.assertEqual(json.loads(result.stdout)['findings'], {'R': ['x@26-29'] if end == 26 else []})

    def test_audit_consultation_uses_original_labels(self):
        source = ROOT / 'examples/consult_input.en.json'
        output = subprocess.check_output([sys.executable, '-m', 'open_roundtable', 'audit',
                                          str(source), '--consult-input'], cwd=ROOT)
        self.assertEqual(json.loads(output)['findings'], dict(P=[['early', 'late']], V=[['early', 'late']],
                                                            R=[], L=['Alex@8-18']))

    def test_audit_reads_piped_requests(self):
        cmd = [sys.executable, '-m', 'open_roundtable', 'audit', '-', '--fail-on-findings']
        request = dict(operation='schedule-audit-v1', rules=['V'], shifts=['Axv:1-4', 'Byv:3-5'])
        report = subprocess.run(cmd, cwd=ROOT, input=json.dumps(request), capture_output=True, text=True)
        self.assertEqual(report.returncode, 1)
        self.assertEqual(json.loads(report.stdout)['findings'], {'V': ['AB']})
        invalid = subprocess.run(cmd, cwd=ROOT, input='{', capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, '')

    def test_invalid_roster_reports_shift_error_before_creating_ledger(self):
        spec = json.loads((ROOT / 'examples/consult_input.en.json').read_text(encoding='utf8'))
        source = self.path.with_suffix('.json')
        for changes, message in [({'id': 'early'}, 'duplicate shift ID: early'),
                                 ({'end': 169}, 'late: start and end must be integer hours')]:
            with self.subTest(changes=changes):
                shifts = [spec['shifts'][0], dict(spec['shifts'][1], **changes)]
                source.write_text(json.dumps(dict(spec, shifts=shifts)), encoding='utf8')
                result = subprocess.run([sys.executable, '-m', 'open_roundtable', 'consult', str(source),
                                         '--db', str(self.path)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.path.exists())

    def test_delegate_from_new_database(self):
        def cli(command, *args):
            output = subprocess.check_output([sys.executable, '-m', 'open_roundtable', command,
                                              str(self.path), *args], cwd=ROOT)
            return json.loads(output)

        parent = cli('create', 'review', '4')['id']
        child = cli('delegate', parent, 'panel', 'subreview', '3')['child']
        allocation = dict(member='panel', child=child, budget=3, spent=None)
        self.assertEqual(cli('show', parent)['delegations'], [allocation])
        cli('join', child, 'reviewer', 'human')
        cli('start', parent)
        cli('start', child)
        posted = cli('post', child, 'reviewer', 'checked two cases', '--cost', '2')
        self.assertEqual((posted['credits'], posted['messages'][-1]['cost']), (1, 2))
        cli('propose', child, '0', 'approved')
        self.assertEqual(cli('vote', child, '1', 'reviewer', 'yes')['state'], 'RESOLVED')
        self.assertEqual(cli('settle', child)['spent'], 2)
        self.assertEqual(cli('show', parent)['delegations'], [dict(allocation, spent=2)])
        cli('propose', parent, '0', 'adopted')
        result = cli('vote', parent, '1', 'panel', 'yes')
        self.assertEqual((result['state'], result['credits']), ('RESOLVED', 2))

    def test_resume_after_unaffordable_post(self):
        def invoke(command, *args):
            return subprocess.run([sys.executable, '-m', 'open_roundtable', command, str(self.path), *args],
                                  cwd=ROOT, capture_output=True, text=True)

        topic = json.loads(invoke('create', 'review', '1').stdout)['id']
        invoke('join', topic, 'expert', 'expert').check_returncode()
        invoke('start', topic).check_returncode()
        rejected = invoke('post', topic, 'expert', 'inspection', '--cost', '2')
        self.assertEqual(rejected.returncode, 2)
        paused = json.loads(invoke('show', topic).stdout)
        self.assertEqual((paused['state'], paused['credits'], paused['messages']), ('PAUSED', 1, []))
        resumed = json.loads(invoke('top-up', topic, '2').stdout)
        self.assertEqual((resumed['state'], resumed['credits'], resumed['messages']), ('ACTIVE', 3, []))
        posted = json.loads(invoke('post', topic, 'expert', 'inspection', '--cost', '2').stdout)
        self.assertEqual((posted['credits'], len(posted['messages'])), (1, 1))
        self.assertEqual(json.loads(invoke('show', topic).stdout), posted)

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
