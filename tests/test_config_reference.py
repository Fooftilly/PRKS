"""The configuration contract (#612 Phase A): ``backend/config_reference.py``.

Every ``PRKS_*`` name the backend reads is listed, with its scope; Compose
variables match ``.env.example`` and ``docker-compose.yml``; the generated
reference is current; an invalid or retired value stops every role before it
touches anything, without echoing the value.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import unittest
from pathlib import Path

import prks_app
from backend import config_reference
from backend.config_reference import BY_KEY, Scope, effective, problems, render_reference

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^PRKS_[A-Z0-9_]+$")


def literal_names() -> set[str]:
    """Every string literal that is exactly a ``PRKS_*`` name in the backend and prks_app.py."""
    names: set[str] = set()
    for path in [ROOT / "prks_app.py", *sorted((ROOT / "backend").rglob("*.py"))]:
        if path.name == "config_reference.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _NAME.match(node.value):
                names.add(node.value)
    return names


class InventoryTests(unittest.TestCase):
    def test_every_name_the_code_reads_is_listed(self) -> None:
        unlisted = sorted(literal_names() - set(BY_KEY))
        self.assertEqual(unlisted, [], "add these to backend/config_reference.py SETTINGS")

    def test_every_listed_process_setting_is_still_read(self) -> None:
        read = literal_names()
        stale = sorted(
            s.key for s in config_reference.SETTINGS
            if s.scope in (Scope.DEPLOYMENT, Scope.INTERNAL) and s.key not in read
        )
        self.assertEqual(stale, [], "these are listed but no longer read; retire or remove them")

    def test_compose_variables_match_env_example_and_the_compose_file(self) -> None:
        example = {
            line.split("=", 1)[0]
            for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        compose = set(re.findall(r"\$\{([A-Z0-9_]+)", (ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
        for name in sorted(example | compose):
            with self.subTest(name=name):
                self.assertIn(name, BY_KEY)
                self.assertIn(BY_KEY[name].scope, (Scope.COMPOSE, Scope.DEPLOYMENT))

    def test_the_library_settings_are_the_ones_the_api_has(self) -> None:
        from backend.domain.settings import app_settings

        api = {app_settings.ANNOTATION_AUTHOR, app_settings.BIBTEX_EXPORT_FIELDS, app_settings.CITATION_STYLE}
        self.assertEqual({s.key for s in config_reference.in_scope(Scope.LIBRARY)}, api)
        self.assertTrue(all(s.editable_in_browser and not s.restart for s in config_reference.in_scope(Scope.LIBRARY)))
        self.assertFalse(any(s.editable_in_browser for s in config_reference.in_scope(Scope.DEPLOYMENT)))

    def test_the_generated_reference_is_current(self) -> None:
        current = Path(config_reference.DOC_PATH).read_text(encoding="utf-8")
        self.assertEqual(current, render_reference(), "run: python -m backend.config_reference --write")


class ValidationTests(unittest.TestCase):
    def test_valid_and_unset_values_pass(self) -> None:
        env = {
            "PRKS_LOG_LEVEL": "debug", "PRKS_LOG_RETENTION_DAYS": "30", "PRKS_PERF_SLOW_MS": "12.5",
            "PRKS_PERF_LOG_SLOW": "on", "PRKS_PDF_LINEARIZE": "0", "PRKS_MIGRATE_ON_START": "0",
            "PRKS_EMBEDDED_WORKER": "1", "PRKS_BACKUP_MAX_UPLOAD_BYTES": "1024", "PRKS_LOG_FILE_LEVEL": "",
        }
        self.assertEqual(problems(env), [])
        self.assertEqual(problems({}), [])

    def test_invalid_values_name_the_variable_never_the_value(self) -> None:
        bad = {
            "PRKS_LOG_LEVEL": "loud-secret", "PRKS_LOG_RETENTION_DAYS": "0", "PRKS_PERF_SLOW_MS": "nan",
            "PRKS_PERF_LOG_SLOW": "maybe", "PRKS_PDF_LINEARIZE": "2", "PRKS_MIGRATE_ON_START": "always",
            "PRKS_BACKUP_MAX_UPLOAD_BYTES": "-1",
        }
        found = problems(bad)
        self.assertEqual(len(found), len(bad))
        for key, value in bad.items():
            with self.subTest(key=key):
                self.assertTrue(any(message.startswith(key + " ") for message in found))
                self.assertFalse(any(value in message for message in found if value not in ("0", "2", "-1")))

    def test_a_retired_key_is_refused_whatever_its_value(self) -> None:
        for value in ("postgres", ""):
            found = problems({"PRKS_LIBRARY_BACKEND": value, "PRKS_FOR_PROCESSING_DIR": "/x"})
            self.assertEqual(len(found), 2)
            self.assertIn("PRKS_LIBRARY_BACKEND is no longer read", found[0])
            self.assertIn("Remove PRKS_LIBRARY_BACKEND", found[0])
            self.assertIn("PRKS_DATABASE_URL", found[0])

    def test_effective_values_hide_secrets_and_invalid_values(self) -> None:
        rows = {row.key: row for row in effective({
            "PRKS_DATABASE_URL": "postgresql://u:hunter2@db/prks", "PRKS_HTTP_THREADS": "8",
            "PRKS_LOG_LEVEL": "hunter2",
        })}
        self.assertEqual((rows["PRKS_DATABASE_URL"].shown, rows["PRKS_DATABASE_URL"].source),
                         ("(set, hidden)", "environment"))
        self.assertEqual(rows["PRKS_LOG_LEVEL"].shown, "(invalid)")
        self.assertEqual((rows["PRKS_HTTP_THREADS"].shown, rows["PRKS_HTTP_THREADS"].source), ("8", "environment"))
        self.assertEqual(rows["PRKS_SESSION_IDLE_DAYS"].source, "default")
        self.assertNotIn("UID", rows)  # only deployment settings


class AppTests(unittest.TestCase):
    def run_check(self, env: dict[str, str], *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = prks_app.run_config(["check", *argv], env, out=out)
        return code, out.getvalue()

    def test_config_check_accepts_a_usable_configuration(self) -> None:
        code, text = self.run_check({"PRKS_DATABASE_URL": "postgresql://u:hunter2@db/prks"})
        self.assertEqual(code, 0, text)
        self.assertIn("configuration: OK", text)
        self.assertNotIn("hunter2", text)

    def test_config_check_reports_every_problem(self) -> None:
        code, text = self.run_check({
            "PRKS_HTTP_THREADS": "0", "PRKS_AUTH": "nobody", "PRKS_LOG_LEVEL": "x",
            "PRKS_TRUSTED_HOSTS": "http://bad/", "PRKS_LIBRARY_BACKEND": "postgres",
        })
        self.assertEqual(code, 2)
        for fragment in ("PRKS_HTTP_THREADS", "PRKS_AUTH", "PRKS_LOG_LEVEL", "PRKS_TRUSTED_HOSTS",
                         "PRKS_LIBRARY_BACKEND", "PRKS_DATABASE_URL"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        self.assertIn("configuration: 6 problem(s)", text)

    def test_config_check_testing_uses_the_testing_defaults(self) -> None:
        code, text = self.run_check({}, "--testing")
        self.assertEqual(code, 0, text)

    def test_every_role_refuses_an_invalid_environment(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            prks_app.refuse_invalid_environment({"PRKS_PERF_LOG_SLOW": "sometimes"})
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("PRKS cannot start: PRKS_PERF_LOG_SLOW must be", stderr.getvalue())
        self.assertIn("python prks_app.py config check", stderr.getvalue())
        prks_app.refuse_invalid_environment({})  # nothing to refuse


if __name__ == "__main__":
    unittest.main()
