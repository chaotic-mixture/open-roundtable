from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.meta_consult import run
from examples.generate_overnight import generate
from examples.selector_policy import policy, select
from open_roundtable.core import Kernel


class MetaConsultTests(unittest.TestCase):
    def test_failed_hard_constraint_blocks_majority(self):
        row = dict(default=dict(hard_pass=True, preference=0), rules=dict(hard_pass=False, preference=7))
        with tempfile.TemporaryDirectory() as tmp, patch('examples.meta_consult.evaluate', return_value=row):
            database = Path(tmp) / 'experiment.sqlite3'
            result = run(database)
            self.assertFalse(result['passed'])
            with closing(Kernel(database)) as kernel:
                topic = kernel.get(result['topic'])
                self.assertEqual((topic['kind'], topic['state'], topic['credits']), ('meta','PAUSED',0))
                self.assertEqual({v['member']:v['agree'] for v in topic['votes']}, {'proposer':1,'evaluator':0})

                with self.assertRaises(ValueError):
                    policy(kernel, result['topic'], 'enable')

    def test_owner_can_enable_and_withdraw(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / 'experiment.sqlite3'
            topic = run(database)['topic']
            shifts = generate(602,7)
            with closing(Kernel(database)) as kernel:
                baseline = select(kernel, topic, 1, shifts)
                self.assertEqual(baseline['candidate'], 0)
                for _ in range(2):
                    policy(kernel, topic, 'enable')
            with closing(Kernel(database)) as kernel:
                adopted = select(kernel, topic, 1, shifts)
                self.assertEqual(adopted['candidate'], 2)
                self.assertNotEqual(adopted['schedule'], baseline['schedule'])
                policy(kernel, topic, 'withdraw')
                self.assertEqual(select(kernel, topic, 1, shifts), baseline)
                self.assertEqual(kernel.db.execute('SELECT enabled FROM selector_decisions ORDER BY id').fetchall()[0][0], 1)
                self.assertEqual(kernel.db.execute('SELECT count(*) FROM selector_decisions').fetchone()[0], 2)
