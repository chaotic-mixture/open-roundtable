"""Re-run local acceptance checks and save actual subprocess output, no fixtures as evidence."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports' / 'core_verification.md'
DB = ROOT / 'reports' / ('core_demo_' + uuid.uuid4().hex + '.sqlite3')
REPORT.parent.mkdir(exist_ok=True)
sections = ['# Core verification — actual execution\n', f'Python: `{sys.version}`\n', f'Demo database: `{DB}`\n']
commands = [
    [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
    [sys.executable, '-m', 'compileall', '-q', 'open_roundtable', 'tests'],
    [sys.executable, '-m', 'open_roundtable', 'demo', '--db', str(DB)],
]
for command in commands:
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sections.append('## Command\n\n`' + subprocess.list2cmdline(command) + '`\n\n```text\n' + result.stdout + '\n```\nExit code: ' + str(result.returncode) + '\n')
    print(result.stdout, end='')
    if result.returncode:
        REPORT.write_text('\n'.join(sections), encoding='utf-8')
        raise SystemExit(result.returncode)

output = json.loads(result.stdout)
(ROOT / 'reports' / 'core_demo_output.json').write_text(result.stdout, encoding='utf-8')
with closing(sqlite3.connect(DB)) as db:
    topic = output['topic']['id']
    state, credits = db.execute('SELECT state, credits FROM topics WHERE id=?', (topic,)).fetchone()
    counts = {table: db.execute(f'SELECT COUNT(*) FROM {table} WHERE topic=?', (topic,)).fetchone()[0]
              for table in ('members', 'messages', 'votes', 'feedback', 'tags')}
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = db.execute('PRAGMA foreign_key_check').fetchall()
    assert (state, credits) == ('RESOLVED', 2)
    assert counts == {'members': 4, 'messages': 2, 'votes': 3, 'feedback': 1, 'tags': 2}
    assert integrity == 'ok' and foreign_keys == []
verification = {'state': state, 'credits': credits, 'counts': counts,
                'integrity_check': integrity, 'foreign_key_check': foreign_keys}
sections.append('## Independent SQLite readback\n\n```json\n' + json.dumps(verification, indent=2) + '\n```\n')
print(json.dumps(verification))
before = hashlib.sha256(DB.read_bytes()).hexdigest()
repeat = subprocess.run(commands[-1], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
after = hashlib.sha256(DB.read_bytes()).hexdigest()
assert repeat.returncode == 2 and 'refusing existing' in repeat.stdout and before == after
sections.append('## Repeat demo rejection\n\n```text\n' + repeat.stdout + '\n```\nExit code: ' + str(repeat.returncode) + '\n\nSHA256 before/after (equal): `' + before + '`\n')
print(repeat.stdout, end='')

files = []
for owned in ('open_roundtable', 'tests', 'examples'):
    for directory, subdirs, names in os.walk(ROOT / owned):
        subdirs[:] = [d for d in subdirs if d != '__pycache__']
        actual = [name for name in names if name != '.gitkeep']
        assert actual, f'Directory has no actual files: {directory}'
        files.extend(str((Path(directory) / name).relative_to(ROOT)) for name in actual)
files.append('pyproject.toml')
sections.append('## Owned source file inventory via os.walk\n\n' + '\n'.join('- `' + path + '`' for path in sorted(files)) + '\n')
sections.append('All checked source directories contain actual files. Private references were not read. No release package was built; publication is deliberately unsupported.\n')
REPORT.write_text('\n'.join(sections), encoding='utf-8')
print(f'Verified {len(files)} owned source/example/test files. Report: {REPORT}')
print(f'Database: {DB}')
