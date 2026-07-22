"""Configuration paths must be deterministic across launch directories."""

import unittest

from utils.config import PROJECT_ROOT, _database_url


class TestConfigPaths(unittest.TestCase):
    def test_relative_sqlite_url_is_anchored_to_project(self):
        expected = (PROJECT_ROOT / "data" / "example.db").resolve().as_posix()
        self.assertEqual(_database_url("sqlite:///data/example.db"), f"sqlite:///{expected}")

    def test_non_sqlite_url_is_unchanged(self):
        url = "postgresql://db.example/school"
        self.assertEqual(_database_url(url), url)

    def test_memory_sqlite_url_is_unchanged(self):
        self.assertEqual(_database_url("sqlite:///:memory:"), "sqlite:///:memory:")


if __name__ == "__main__":
    unittest.main()
