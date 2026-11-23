from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.meta_consult import run
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
