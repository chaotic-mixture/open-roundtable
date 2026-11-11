"""Capture unedited unittest stdout/stderr for each explicitly run TDD step."""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('label')
parser.add_argument('--expect', choices=['red', 'green'], required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
result = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
report = root / 'reports' / 'core_tdd.md'
report.parent.mkdir(exist_ok=True)
with report.open('a', encoding='utf-8') as stream:
    stream.write(f'\n## {args.label} ({args.expect.upper()})\n\nCommand: `python -m unittest discover -s tests -v`\n\n```text\n{result.stdout}```\nExit code: {result.returncode}\n')
print(result.stdout, end='')
print(f'Captured exit code: {result.returncode}; expected {args.expect}')
if (result.returncode == 0) != (args.expect == 'green'):
    sys.exit('Unexpected TDD result')
