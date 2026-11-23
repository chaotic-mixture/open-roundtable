"""Open a second expert proposal in a new copy of a paused repair ledger."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from examples.compare_panel_receipts import candidate_score, read_receipt
from examples.user_consult import validate_roster
from open_roundtable.core import Kernel


def revise(source, destination):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp)/'revision.sqlite3'
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(snapshot)) as copy:
                original.backup(copy)
        with closing(Kernel(snapshot)) as kernel:
            db = kernel.db
            nested = bool(db.execute('SELECT 1 FROM delegations').fetchone())
            _, _, receipt = read_receipt(snapshot, nested)
            if receipt['state'] != 'PAUSED' or receipt['votes'].get('expert') != 0:
                raise ValueError('expected a paused proposal with expert dissent')
            [(topic, expert_body)] = db.execute("SELECT topic,body FROM messages WHERE member='expert'").fetchall()
            expert = json.loads(expert_body)
            original = expert['input']['shifts']
            rows, rules = validate_roster(expert['input'])
            menu = expert['candidates']
            def rank(candidate):
                flags = candidate_score(candidate, original, rows, rules)
                changed = sum(a != b for a,b in zip(candidate,original,strict=True))
                return flags, -changed
            selected = max(range(len(menu)), key=lambda i: rank(menu[i]))
            flags, negative_changes = rank(menu[selected])
            body = dict(selected=selected, schedule=menu[selected], criteria_met=flags,
                        changed_shifts=-negative_changes, proposed_by='expert')
            version = kernel.propose(topic,json.dumps(body),previous_version=1)
            kernel.vote(topic,'expert',True,version=version)
            result = dict(topic=topic, version=version, state=kernel.get(topic)['state'],
                          selected=selected, criteria_met=flags, changed_shifts=-negative_changes)
        with destination.open('xb') as output, snapshot.open('rb') as finished:
            import shutil
            shutil.copyfileobj(finished,output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(revise(args.source,args.destination)))
