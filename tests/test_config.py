"""Configuration paths must be deterministic across launch directories."""

import unittest

from utils.config import PROJECT_ROOT, _database_url


class TestConfigPaths(unittest.TestCase):
    def test_relative_sqlite_url_is_anchored_to_project(self):
        expected = (PROJECT_ROOT / "data" / "example.db").resolve().as_posix()
        self.assertEqual(_database_url("sqlite:///data/example.db"), f"sqlite:///{expected}")

    def test_non_sqlite_url_is_unchanged(self):
        url = "mysql://db.example/school"
        self.assertEqual(_database_url(url), url)

    def test_postgresql_url_uses_psycopg_driver(self):
        self.assertEqual(
            _database_url("postgresql://user:pass@localhost/school"),
            "postgresql+psycopg://user:pass@localhost/school",
        )

    def test_legacy_postgres_url_uses_psycopg_driver(self):
        self.assertEqual(
            _database_url("postgres://user:pass@localhost/school"),
            "postgresql+psycopg://user:pass@localhost/school",
        )

    def test_memory_sqlite_url_is_unchanged(self):
        self.assertEqual(_database_url("sqlite:///:memory:"), "sqlite:///:memory:")


if __name__ == "__main__":
    unittest.main()
