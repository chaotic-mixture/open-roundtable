import io
import unittest
import warnings
from zipfile import ZipFile

from tests.verify_snapshot import check_archive


class SnapshotInventoryTests(unittest.TestCase):
    def test_commit_files_and_optional_directory_entries(self):
        expected = {'README.md', 'src/core.py'}
        for directories in ([], ['src/']):
            with self.subTest(directories=directories), ZipFile(io.BytesIO(), 'w') as archive:
                for name in directories + sorted(expected):
                    archive.writestr(name, b'')
                check_archive(archive, expected)

    def test_reject_inventory_discrepancies(self):
        for names in (['a', 'a'], ['a', 'extra/'], ['other'], []):
            with self.subTest(names=names), ZipFile(io.BytesIO(), 'w') as archive:
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='Duplicate name:', category=UserWarning)
                    for name in names:
                        archive.writestr(name, b'')
                with self.assertRaisesRegex(ValueError, 'ZIP entries differ'):
                    check_archive(archive, {'a'})
