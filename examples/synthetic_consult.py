"""Offline seed-47 example. All model metadata and replies here are synthetic."""
import argparse

from examples.auto_consult import encoded, run
from pathlib import Path


MODEL = 'synthetic:seed47'


def fixture(path, payload=None):
    if path == '/api/tags':
        return {'models': [{'name': MODEL}]}
    if path == '/api/version':
        return {'version': 'synthetic'}
    if path != '/api/chat' or payload['model'] != MODEL:
        raise ValueError('unexpected synthetic request')
    reply = {'P': ['DE'], 'V': ['BD', 'BE', 'DE'],
             'R': ['x@36-41', 'y@22-26', 'y@29-36'], 'L': [],
             'approve_final': True, 'reason': 'Synthetic fixture: D and E overlap.'}
    return {'model': MODEL, 'done': True, 'done_reason': 'stop',
            'message': {'role': 'assistant', 'content': encoded(reply)},
            'prompt_eval_count': 0, 'eval_count': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    run(parser.parse_args().directory, MODEL, 47, fixture)
