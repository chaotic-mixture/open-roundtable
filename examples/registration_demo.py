"""Publish, discover and withdraw a synthetic registration on loopback."""
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.two_node_demo import cli, start
from open_roundtable.data import initialize, share_public


def run():
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        directory, provider, consumer = (Path(tmp)/name for name in ('directory', 'B', 'A'))
        for root in (directory, provider, consumer):
            initialize(root)
            (root/'node.json').write_text(json.dumps(dict(name=root.name)))
        _, provider_port = start(stack, provider, 0)
        _, directory_port = start(stack, directory, 0)
        source = 'https://b.example/node'
        endpoint = f'http://127.0.0.1:{provider_port}'
        bootstrap = f'http://127.0.0.1:{directory_port}'
        registration = json.loads(cli(provider, 'register', source, endpoint).stdout)
        share_public(provider, source, True, registration['sha256'])
        imported = json.loads(cli(directory, 'import-registration', 'B', source, endpoint).stdout)
        if imported != registration:
            raise ValueError('directory imported a different registration')
        before = json.loads(cli(consumer, 'discover', bootstrap).stdout)
        share_public(directory, source, True, imported['sha256'])
        published = json.loads(cli(consumer, 'discover', bootstrap).stdout)
        share_public(directory, source, False)
        withdrawn = json.loads(cli(consumer, 'discover', bootstrap).stdout)
        if before or published != [dict(name='B', url=endpoint)] or withdrawn:
            raise ValueError('unexpected discovery result')
        return dict(before_publication=before, published=published, after_withdrawal=withdrawn)


if __name__ == '__main__':
    print(json.dumps(run()))
