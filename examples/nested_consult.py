"""Run a funded child roundtable and independently verify its schedule findings."""
import argparse
from contextlib import closing
import json
from pathlib import Path

from examples.overnight_score import expected
from open_roundtable.core import Kernel
from open_roundtable.schedule_expert import check


def run(path, shifts):
    path.touch(exist_ok=False)
    with closing(Kernel(path)) as kernel:
        parent = kernel.create('Verify schedule', 4)
        child = kernel.delegate(parent, 'schedule panel', 'Find schedule conflicts', 3)
        kernel.join(parent, 'reviewer', kind='expert')
        kernel.join(child, 'interval expert', kind='expert')
        kernel.start(parent)
        kernel.start(child)
        findings = check(shifts)
        body = json.dumps({rule: sorted(items) for rule, items in findings.items()}, sort_keys=True)
        kernel.post(child, 'interval expert', body)
        version = kernel.propose(child, body, previous_version=0)
        kernel.vote(child, 'interval expert', True, version=version)
        spent = kernel.settle(child)
        report = kernel.get(parent)['messages'][0]['body']
        verified = json.loads(report) == {r: sorted(v) for r, v in expected(shifts).items()}
        kernel.post(parent, 'reviewer', json.dumps({'verified': verified}))
        version = kernel.propose(parent, report, previous_version=0)
        kernel.vote(parent, 'schedule panel', True, version=version)
        kernel.vote(parent, 'reviewer', verified, version=version)
        return dict(parent=parent, child=child, child_spent=spent, state=kernel.get(parent)['state'],
                    remaining=kernel.get(parent)['credits'], verified=verified, findings=json.loads(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('shifts', nargs='+')
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.shifts)))
