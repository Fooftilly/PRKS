"""Static checks of ``docker-compose.postgres.yml`` (§12.3; L-D-1 #630).

The running stack is exercised by ``scripts/ci/test_postgres_compose.sh``
(the "PostgreSQL Compose acceptance" workflow); these pin the rules a
careless edit could drop without that workflow running.
"""

import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name: str) -> str:
    with open(os.path.join(_PROJECT_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _service(text: str, name: str) -> str:
    """The block of one top-level service, up to the next service or section."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z]+:\n|^[a-z]+:)", text, re.S | re.M)
    assert match is not None, name
    return match.group(1)


class PostgresComposeFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read("docker-compose.postgres.yml")

    def test_the_four_services(self) -> None:
        for name in ("db", "migrate", "web", "worker"):
            with self.subTest(service=name):
                _service(self.text, name)

    def test_the_database_volume_is_the_postgresql_18_location(self) -> None:
        db = _service(self.text, "db")
        self.assertIn("- pgdata:/var/lib/postgresql\n", db)
        self.assertNotIn("/var/lib/postgresql/data", db.replace("Never mount at /var/lib/postgresql/data", ""))
        self.assertNotIn("ports:", db)

    def test_migrate_is_the_only_schema_runner_and_runs_first(self) -> None:
        self.assertIn('PRKS_MIGRATE_ON_START: "0"', self.text)
        for name in ("web", "worker"):
            with self.subTest(service=name):
                block = _service(self.text, name)
                self.assertIn("<<: *prks-env", block)
                self.assertRegex(block, r"migrate:\n\s+condition: service_completed_successfully")
        self.assertIn('restart: "no"', _service(self.text, "migrate"))

    def test_every_prks_service_reads_the_url_secret_from_a_file(self) -> None:
        self.assertIn("PRKS_DATABASE_URL_FILE: /run/secrets/prks_database_url", self.text)
        self.assertNotRegex(self.text, r"PRKS_DATABASE_URL:")
        anchor = self.text[self.text.index("x-prks: &prks"):self.text.index("x-prks-env:")]
        self.assertIn("secrets:\n    - prks_database_url", anchor)
        for name in ("migrate", "web", "worker"):
            with self.subTest(service=name):
                self.assertIn("<<: *prks\n", _service(self.text, name))
        self.assertNotIn("prks_database_url", _service(self.text, "db"))

    def test_web_signs_in_publishes_loopback_and_checks_readiness(self) -> None:
        web = _service(self.text, "web")
        self.assertIn("PRKS_AUTH: session", web)
        self.assertIn('"${PRKS_PUBLISH_HOST:-127.0.0.1}:${PRKS_PUBLISH_PORT:-8080}:8080"', web)
        self.assertIn("/readyz", web)
        self.assertIn('PRKS_EMBEDDED_WORKER: "0"', web)

    def test_roles_run_through_prks_app(self) -> None:
        self.assertIn('entrypoint: ["python", "/app/prks_app.py"]', self.text)
        self.assertIn('command: ["serve", "--host", "0.0.0.0"]', _service(self.text, "web"))
        self.assertIn('command: ["migrate"]', _service(self.text, "migrate"))
        self.assertIn('command: ["worker"]', _service(self.text, "worker"))

    def test_env_example_holds_no_secret(self) -> None:
        example = _read(".env.example")
        self.assertNotRegex(example, r"postgresql://prks:[^<]")
        self.assertNotRegex(example, r"(?im)^\w*PASSWORD\s*=")


if __name__ == "__main__":
    unittest.main()
