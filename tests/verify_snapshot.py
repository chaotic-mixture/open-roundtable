"""Verify a committed source snapshot in a fresh output directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from zipfile import ZipFile


def check_archive(snapshot, expected):
    directories = {parent.as_posix() + '/' for name in expected
                   for parent in PurePosixPath(name).parents if parent != PurePosixPath('.')}
    entries = snapshot.namelist()
    files = {entry for entry in entries if not entry.endswith('/')}
    if len(entries) != len(set(entries)) or files != expected or set(entries) - files - directories:
        raise ValueError('ZIP entries differ from commit inventory')


def verify(output, revision, openssl):
    repo = Path(__file__).resolve().parents[1]
    executable = shutil.which(openssl)
    if not executable:
        raise ValueError('OpenSSL executable not found')
    commit = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', revision + '^{commit}'], cwd=repo, text=True).strip()
    inventory = subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', commit], cwd=repo)
    expected = {os.fsdecode(name) for name in inventory.split(b'\0') if name}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    archive = output/'source.zip'
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), commit], cwd=repo, check=True)
    tree = output/'tree'
    with ZipFile(archive) as snapshot:
        check_archive(snapshot, expected)
        snapshot.extractall(tree)
    actual = {path.relative_to(tree).as_posix() for path in tree.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('snapshot file inventory differs from commit')
    env = dict(os.environ, PYTHONPATH='', ROUNDTABLE_TEST_OPENSSL=str(Path(executable).resolve()))
    subprocess.run([sys.executable, 'tests/verify_core.py'], cwd=tree, env=env, check=True)
    subprocess.run([sys.executable, '-m', 'examples.registration_demo', '--openssl', env['ROUNDTABLE_TEST_OPENSSL']],
                   cwd=tree, env=env, check=True)
    result = dict(commit=commit, files=len(expected), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    (output/'verified.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new local evidence directory')
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--openssl', default=os.getenv('ROUNDTABLE_TEST_OPENSSL') or 'openssl')
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.revision, args.openssl)))
