"""Publish, discover and withdraw a synthetic registration on loopback."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.demo_certificates import issue_certificates
from examples.two_node_demo import cli, start
from open_roundtable.data import initialize, share_public


def run(openssl=None):
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        directory, provider, consumer = (Path(tmp)/name for name in ('directory', 'B', 'A'))
        credentials = Path(tmp)/'credentials'
        if openssl:
            credentials.mkdir()
            issue_certificates(credentials, openssl)
        for root in (directory, provider, consumer):
            initialize(root)
            config = dict(name=root.name)
            if openssl:
                config.update(transport='mtls', credentials=str(credentials/root.name))
            (root/'node.json').write_text(json.dumps(config))
        _, provider_port = start(stack, provider, 0)
        _, directory_port = start(stack, directory, 0)
        source = 'https://b.example/node'
        scheme = 'https' if openssl else 'http'
        endpoint = f'{scheme}://127.0.0.1:{provider_port}'
        bootstrap = f'{scheme}://127.0.0.1:{directory_port}'
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openssl', help='enable mutual TLS using this OpenSSL executable')
    print(json.dumps(run(parser.parse_args().openssl)))
