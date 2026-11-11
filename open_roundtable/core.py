"""SQLite state transitions using local test credits, not tokens or money."""
import sqlite3
import uuid


MAX_CREDITS = 2**63 - 1


def _credits(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_CREDITS:
        raise ValueError('credits must be bounded integer local test units')


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonempty text')


class Kernel:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('''CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            state TEXT NOT NULL, credits INTEGER NOT NULL,
            kind TEXT NOT NULL, priority TEXT NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS members (
            topic TEXT NOT NULL REFERENCES topics(id), id TEXT NOT NULL,
            kind TEXT NOT NULL, source TEXT, PRIMARY KEY(topic, id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, topic TEXT NOT NULL, member TEXT NOT NULL,
            body TEXT NOT NULL, cost INTEGER NOT NULL,
            FOREIGN KEY(topic, member) REFERENCES members(topic, id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS votes (
            topic TEXT NOT NULL, member TEXT NOT NULL, agree INTEGER NOT NULL,
            PRIMARY KEY(topic, member),
            FOREIGN KEY(topic, member) REFERENCES members(topic, id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY, topic TEXT NOT NULL, member TEXT NOT NULL,
            body TEXT NOT NULL, source TEXT NOT NULL,
            FOREIGN KEY(topic, member) REFERENCES members(topic, id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS tags (
            topic TEXT NOT NULL, member TEXT NOT NULL, label TEXT NOT NULL,
            PRIMARY KEY(topic, member, label),
            FOREIGN KEY(topic, member) REFERENCES members(topic, id))''')
        self.db.commit()

    def close(self):
        self.db.close()

    def create(self, title, credits=0, *, kind='discussion'):
        _text(title, 'title')
        _credits(credits)
        if kind not in ('discussion', 'meta'):
            raise ValueError('unknown topic kind')
        topic = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO topics VALUES (?, ?, ?, ?, ?, ?)',
                            (topic, title, 'DRAFT', credits, kind,
                             'lowest' if kind == 'meta' else 'normal'))
        return topic

    def get(self, topic):
        """Read topic and child records from one consistent SQLite snapshot."""
        if self.db.in_transaction:
            return self._snapshot(topic)
        with self.db:
            self.db.execute('BEGIN')
            return self._snapshot(topic)

    def _snapshot(self, topic):
        row = self.db.execute('SELECT * FROM topics WHERE id=?', (topic,)).fetchone()
        if row is None:
            raise ValueError('unknown topic')
        result = dict(row)
        result['members'] = [dict(r) for r in self.db.execute(
            'SELECT * FROM members WHERE topic=? ORDER BY id', (topic,))]
        result['messages'] = [dict(r) for r in self.db.execute(
            'SELECT * FROM messages WHERE topic=? ORDER BY id', (topic,))]
        result['votes'] = [dict(r) for r in self.db.execute(
            'SELECT * FROM votes WHERE topic=? ORDER BY member', (topic,))]
        result['feedback'] = [dict(r) for r in self.db.execute(
            'SELECT * FROM feedback WHERE topic=? ORDER BY id', (topic,))]
        result['tags'] = [dict(r) for r in self.db.execute(
            'SELECT * FROM tags WHERE topic=? ORDER BY member, label', (topic,))]
        return result

    def tag(self, topic, member, label):
        _text(label, 'label')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.get(topic)
            self._member(topic, member)
            self.db.execute('INSERT OR IGNORE INTO tags VALUES (?, ?, ?)', (topic, member, label))

    def feedback(self, topic, member, body, *, source):
        _text(body, 'body')
        _text(source, 'source')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.get(topic)
            self._member(topic, member)
            self.db.execute('INSERT INTO feedback(topic, member, body, source) VALUES (?, ?, ?, ?)',
                            (topic, member, body, source))

    def vote(self, topic, member, agree):
        if type(agree) is not bool:
            raise ValueError('agree must be a bool')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get(topic)
            self._member(topic, member)
            if current['state'] not in ('ACTIVE', 'PAUSED'):
                raise ValueError('vote requires an active or paused topic')
            if self.db.execute('SELECT 1 FROM votes WHERE topic=? AND member=?',
                               (topic, member)).fetchone():
                raise ValueError('duplicate vote; ballots cannot be changed')
            self.db.execute('INSERT INTO votes VALUES (?, ?, ?)', (topic, member, agree))
            yes = self.db.execute('SELECT COUNT(*) FROM votes WHERE topic=? AND agree=1',
                                  (topic,)).fetchone()[0]
            if yes * 2 > len(current['members']):
                self.db.execute("UPDATE topics SET state='RESOLVED' WHERE id=?", (topic,))

    def _member(self, topic, member):
        _text(member, 'member')
        if not self.db.execute('SELECT 1 FROM members WHERE topic=? AND id=?',
                               (topic, member)).fetchone():
            raise ValueError('unknown member in this topic')

    def post(self, topic, member, body, *, cost=1):
        _text(body, 'body')
        _credits(cost, minimum=1)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get(topic)
            self._member(topic, member)
            if current['state'] != 'ACTIVE':
                raise ValueError('post requires an active topic')
            if cost > current['credits']:
                self.db.execute("UPDATE topics SET state='PAUSED' WHERE id=?", (topic,))
                return False
            self.db.execute('INSERT INTO messages(topic, member, body, cost) VALUES (?, ?, ?, ?)',
                            (topic, member, body, cost))
            remaining = current['credits'] - cost
            self.db.execute('UPDATE topics SET credits=?, state=? WHERE id=?',
                            (remaining, 'ACTIVE' if remaining else 'PAUSED', topic))
        return True

    def top_up(self, topic, amount):
        _credits(amount, minimum=1)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get(topic)
            if current['state'] == 'RESOLVED':
                raise ValueError('resolved budget is closed')
            _credits(current['credits'] + amount)
            state = 'ACTIVE' if current['state'] == 'PAUSED' else current['state']
            self.db.execute('UPDATE topics SET credits=?, state=? WHERE id=?',
                            (current['credits'] + amount, state, topic))

    def start(self, topic):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get(topic)
            if current['state'] != 'DRAFT' or not current['members']:
                raise ValueError('start requires a draft with members')
            state = 'ACTIVE' if current['credits'] else 'PAUSED'
            self.db.execute('UPDATE topics SET state=? WHERE id=?', (state, topic))

    def join(self, topic, member, *, kind, source=None):
        _text(member, 'member')
        if kind not in ('model', 'expert', 'human', 'roundtable'):
            raise ValueError('unknown member kind')
        if source is not None or kind == 'roundtable':
            _text(source, 'source')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.get(topic)['state'] != 'DRAFT':
                raise ValueError('roster is frozen after start')
            if self.db.execute('SELECT 1 FROM members WHERE topic=? AND id=?',
                               (topic, member)).fetchone():
                raise ValueError('member already joined')
            self.db.execute('INSERT INTO members VALUES (?, ?, ?, ?)',
                            (topic, member, kind, source))
