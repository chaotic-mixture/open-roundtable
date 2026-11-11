"""Publication is deliberately disabled pending a separately audited release process."""
import importlib
from pathlib import Path
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackagingBoundaryTests(unittest.TestCase):
    def test_metadata_whitelists_only_package_and_builds_are_blocked(self):
        config_path = ROOT / 'pyproject.toml'
        self.assertTrue(config_path.is_file(), 'explicit package metadata missing')
        config = tomllib.loads(config_path.read_text(encoding='utf-8'))
        self.assertEqual(config['project']['dependencies'], [])
        self.assertEqual(config['tool']['setuptools']['packages'], ['open_roundtable'])
        self.assertFalse(config['tool']['setuptools']['include-package-data'])
        self.assertEqual(config['build-system']['requires'], [])
        self.assertEqual(config['build-system']['backend-path'], ['.'])
        backend = importlib.import_module(config['build-system']['build-backend'])
        with tempfile.TemporaryDirectory() as directory:
            for hook in [backend.build_wheel, backend.build_sdist]:
                with self.assertRaisesRegex(RuntimeError, 'publishing is not supported'):
                    hook(directory)
                self.assertEqual(list(Path(directory).iterdir()), [])
