"""Compare direct and delegated repair consultations on the nine seeded rosters."""
import argparse
import json
from pathlib import Path

from examples.compare_panel_receipts import compare
from examples.generate_overnight import generate
from examples.user_consult import consult


def run(directory, model):
    directory.mkdir(parents=True, exist_ok=False)
    for index, size in enumerate((7, 7, 7, 11, 11, 11, 22, 22, 22)):
        seed = 501 + index
        shifts = []
        for token in generate(seed, size):
            (shift, worker, room), interval = token.split(':')
            start, end = map(int, interval.split('-'))
            shifts.append(dict(id=shift, worker=worker, room=room, start=start, end=end))
        spec = dict(title=f'Schedule {seed}', shifts=shifts,
                    preference='First preserve the original start of A; next prefer B starting before noon.',
                    criteria=[dict(shift='A', field='delta', min=0, max=0),
                              dict(shift='B', field='hour', min=0, max=11)])
        source = directory/f'{seed}.json'
        source.write_text(json.dumps(spec), encoding='utf8')
        paths = [directory/f'{seed}-{mode}.sqlite3' for mode in ('direct', 'nested')]
        for nested in (bool(index % 2), not bool(index % 2)):
            consult(source, paths[int(nested)], model, nested=nested)
        yield dict(seed=seed, shifts=size, **compare(*paths))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--model', default='qwen3.5:9b')
    args = parser.parse_args()
    for result in run(args.directory, args.model):
        print(json.dumps(result), flush=True)
