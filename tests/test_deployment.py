"""Contracts for CI and the non-containerized Linux deployment layer."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.verify_postgresql_restore import validate_restore_target


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class TestContinuousIntegrationContracts(unittest.TestCase):
    def test_ci_runs_full_suite_against_postgresql_18(self):
        workflow = project_text(".github/workflows/ci.yml")

        self.assertIn("postgres:18.4-alpine", workflow)
        self.assertIn("POSTGRES_TEST_DATABASE_URL", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn('"3.11"', workflow)
        self.assertIn('"3.12"', workflow)
        self.assertIn("browser-tests:", workflow)
        self.assertIn("PLAYWRIGHT_BROWSER", workflow)
        self.assertIn("Windows Edge smoke", workflow)

    def test_ci_audits_every_dependency_profile(self):
        workflow = project_text(".github/workflows/ci.yml")

        for requirements_file in (
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-ai.txt",
            "requirements-node.txt",
        ):
            self.assertIn(f"-r {requirements_file}", workflow)
        self.assertIn("PYSEC-2026-2132", workflow)
        self.assertIn("docs/SECURITY.md", workflow)

    def test_codeql_uses_python_security_extended_queries(self):
        workflow = project_text(".github/workflows/codeql.yml")

        self.assertIn("github/codeql-action/init@v4", workflow)
        self.assertIn("github/codeql-action/analyze@v4", workflow)
        self.assertIn("languages: python", workflow)
        self.assertIn("queries: security-extended", workflow)


class TestLinuxDeploymentContracts(unittest.TestCase):
    def test_systemd_service_is_unprivileged_and_hardened(self):
        service = project_text("deploy/linux/school-ai.service")

        self.assertIn("User=school-ai", service)
        self.assertNotIn("User=root", service)
        self.assertIn("EnvironmentFile=/opt/school-ai/.env.local", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("/opt/school-ai/.venv/bin/python", service)

    def test_nginx_forwards_websocket_and_tls_context(self):
        nginx = project_text("deploy/linux/nginx-school-ai.conf")

        self.assertIn("proxy_set_header Upgrade $http_upgrade", nginx)
        self.assertIn("proxy_set_header Connection $connection_upgrade", nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme", nginx)
        self.assertIn("listen 443 ssl", nginx)

    def test_production_env_binds_application_to_loopback(self):
        environment = project_text("deploy/linux/env.production.example")

        self.assertIn("SERVER_HOST=127.0.0.1", environment)
        self.assertIn("COOKIE_SECURE=true", environment)
        self.assertIn("DATABASE_URL=postgresql+psycopg://school_ai_app:", environment)
        self.assertNotIn("DATABASE_URL=postgresql+psycopg://postgres:", environment)


class TestRestoreSafetyContracts(unittest.TestCase):
    def test_restore_target_requires_separate_test_database(self):
        runtime_url = "postgresql+psycopg://app:secret@localhost/school_ai_prod"

        with self.assertRaisesRegex(RuntimeError, "_test"):
            validate_restore_target(
                "postgresql+psycopg://app:secret@localhost/school_ai_restore",
                runtime_url,
            )

        target = validate_restore_target(
            "postgresql://app:secret@localhost/school_ai_restore_test",
            runtime_url,
        )
        self.assertEqual(target.drivername, "postgresql+psycopg")

    def test_restore_target_rejects_runtime_database(self):
        runtime_url = "postgresql+psycopg://app:secret@localhost/school_ai_test"

        with self.assertRaisesRegex(RuntimeError, "runtime"):
            validate_restore_target(runtime_url, runtime_url)

    def test_restore_target_requires_postgresql_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "Runtime DATABASE_URL"):
            validate_restore_target(
                "postgresql+psycopg://app:secret@localhost/school_ai_restore_test",
                "sqlite:///data/school_ai.db",
            )


if __name__ == "__main__":
    unittest.main()
