"""Publish, discover and withdraw a synthetic registration on loopback."""
import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.demo_certificates import issue_certificates
from examples.peer_node import ledger
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
        discovery = ['discover', bootstrap]
        if openssl:
            discovery.extend(['--directory-name', 'directory'])
        registration = json.loads(cli(provider, 'register', source, endpoint).stdout)
        share_public(provider, source, True, registration['sha256'])
        imported = json.loads(cli(directory, 'import-registration', 'B', source, endpoint).stdout)
        if imported != registration:
            raise ValueError('directory imported a different registration')
        before = json.loads(cli(consumer, *discovery).stdout)
        share_public(directory, source, True, imported['sha256'])
        published = json.loads(cli(consumer, *discovery).stdout)
        cli(consumer, 'permission', 'B', 'allow', '--remaining', '1')
        with closing(ledger(consumer)) as db:
            granted = db.execute('''SELECT url,remaining FROM peers JOIN consult_grants USING(name,url)
                                    JOIN grant_limits USING(name) WHERE name='B' ''').fetchall()
        if granted != [(endpoint, 1)]:
            raise ValueError('consumer did not persist the bounded grant')
        # Skip discovery during one withdrawal, as an offline consumer would.
        share_public(directory, source, False)
        share_public(directory, source, True, imported['sha256'])
        if json.loads(cli(consumer, *discovery).stdout) != published:
            raise ValueError('unexpected snapshot after a missed withdrawal')
        with closing(ledger(consumer)) as db:
            quota = db.execute('''SELECT remaining FROM peers JOIN consult_grants USING(name,url)
                                 JOIN grant_limits USING(name) WHERE name=?''', ('B',)).fetchone()
        share_public(directory, source, False)
        withdrawn = json.loads(cli(consumer, *discovery).stdout)
        if before or published != [dict(name='B', url=endpoint)] or withdrawn:
            raise ValueError('unexpected discovery result')
        with closing(ledger(consumer)) as db:
            for table in ('peers', 'consult_grants', 'grant_limits'):
                if db.execute(f'SELECT 1 FROM {table} WHERE name=?', ('B',)).fetchone():
                    raise ValueError(f'withdrawn peer remains in {table}')
        share_public(directory, source, True, imported['sha256'])
        republished = json.loads(cli(consumer, *discovery).stdout)
        status = json.loads(cli(consumer, 'permission-status', 'B').stdout)
        if status != dict(peer='B', discovered=True, granted=False, remaining=None, can_submit=False):
            raise ValueError('unexpected permission status after republication')
        if republished != published:
            raise ValueError('consumer did not rediscover the republished node')
        with closing(ledger(consumer)) as db:
            if db.execute('SELECT url FROM peers WHERE name=?', ('B',)).fetchall() != [(endpoint,)]:
                raise ValueError('rediscovered endpoint missing from consumer ledger')
            for table in ('consult_grants', 'grant_limits'):
                if db.execute(f'SELECT 1 FROM {table} WHERE name=?', ('B',)).fetchone():
                    raise ValueError(f'republication restored a stale entry in {table}')
        return dict(before_publication=before, published=published, after_withdrawal=withdrawn,
                    after_republication=republished, remaining_after_missed_withdrawal=quota[0] if quota else None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openssl', help='enable mutual TLS using this OpenSSL executable')
    print(json.dumps(run(parser.parse_args().openssl)))
