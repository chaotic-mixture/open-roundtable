"""Prewritten synthetic participants. These are not adapters or AI responses."""
from pathlib import Path

from .core import Kernel


NOTICE = 'synthetic preset demonstration; no real models; no external API calls'
PARTICIPANTS = (
    ('synthetic-model', 'model', 'synthetic:prewritten-model'),
    ('synthetic-expert', 'expert', 'synthetic:prewritten-expert'),
    ('synthetic-human', 'human', 'synthetic:prewritten-human'),
    ('synthetic-roundtable', 'roundtable', 'synthetic:reference-only-no-nested-call'),
)


def run_demo(path):
    # Exclusive creation protects even empty files and concurrent demo invocations.
    path = Path(path)
    try:
        with path.open('xb'):
            pass
    except FileExistsError as exc:
        raise ValueError('refusing existing database or file; choose a new --db path') from exc
    kernel = Kernel(path)
    events = []
    try:
        topic = kernel.create('Synthetic question: accept this offline state-machine demonstration?', credits=1)
        for member, kind, source in PARTICIPANTS:
            kernel.join(topic, member, kind=kind, source=source)
        kernel.start(topic)
        events.append(kernel.get(topic)['state'])
        kernel.post(topic, 'synthetic-model', 'Synthetic preset: consume one local test credit.', cost=1)
        events.append(kernel.get(topic)['state'])
        kernel.close()
        kernel = Kernel(path)
        kernel.top_up(topic, 3)
        events.append(kernel.get(topic)['state'])
        kernel.post(topic, 'synthetic-expert', 'Synthetic preset: resume from persisted SQLite state.', cost=1)
        for member, _, _ in PARTICIPANTS[:3]:
            kernel.vote(topic, member, True)
        events.append(kernel.get(topic)['state'])
        kernel.feedback(topic, 'synthetic-human',
                        'Synthetic preset practice note, not a measured model evaluation.',
                        source='synthetic:demo-fixture')
        kernel.tag(topic, 'synthetic-human', 'synthetic')
        kernel.tag(topic, 'synthetic-human', 'offline-state-machine')
        meta = kernel.create('Synthetic proposal: improve open-source test coverage', kind='meta')
        return {'notice': NOTICE, 'budget_unit': 'local test credits',
                'events': events, 'topic': kernel.get(topic), 'meta_proposal': kernel.get(meta)}
    finally:
        kernel.close()
