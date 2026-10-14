"""``prks admin bootstrap`` (§9.2; L-ID-1 #626): issuance and completion are separate.

``issue-token`` stores a token and delivers it once (terminal, owner-only file
in the configuration directory, or the operator's ``PRKS_BOOTSTRAP_TOKEN_FILE``);
``complete`` consumes it while it creates the owner and Library. No token or
password reaches output or logs except the deliberate terminal delivery.
"""

from __future__ import annotations

import io
import logging
import os
import stat
import threading
from typing import Any, Optional
from unittest import mock

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import admin, bootstrap_tokens, passwords
from backend.domain.identity.bootstrap_tokens import BootstrapToken
from backend.domain.identity.errors import RootPairingRefused
from tests.pg.harness import fetch_one
from tests.pg.test_root_pairing import RootPairingTestCase

PASSWORD = "an owner passphrase"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Run:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


class AdminBootstrapTests(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.name = self.database("admin")
        self.root = self.new_root()
        self.config_dir = os.path.join(self.tmp, "home", ".config")
        self.env: dict[str, str] = {
            "HOME": os.path.join(self.tmp, "home"),
            "XDG_CONFIG_HOME": self.config_dir,
        }
        self.token_file = os.path.join(self.config_dir, "prks", bootstrap_tokens.TOKEN_FILE_NAME)
        self.password_file = self.secret_file("password", PASSWORD + "\n")
        self.logs = io.StringIO()
        handler = logging.StreamHandler(self.logs)
        handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        previous = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        self.addCleanup(root_logger.removeHandler, handler)
        self.addCleanup(root_logger.setLevel, previous)
        self.secrets: list[str] = [PASSWORD]

    def secret_file(self, name: str, text: str, mode: int = 0o600) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, mode)
        return path

    def open_db(self, _env: Any) -> Database:
        return Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="test", pool_min=0))

    def run_cli(self, *argv: str, tty: bool = False, prompts: Optional[list[str]] = None,
                env: Optional[dict[str, str]] = None) -> _Run:
        out = _Tty() if tty else io.StringIO()
        err = io.StringIO()
        answers = list(prompts or [])
        code = admin.main(
            ["bootstrap", *argv],
            environ=env if env is not None else self.env,
            stdin=io.StringIO(),
            stdout=out,
            stderr=err,
            prompt=(lambda _label: answers.pop(0)) if prompts is not None else None,
            open_database=self.open_db,
        )
        return _Run(code, out.getvalue(), err.getvalue())

    def complete(self, *extra: str, **kwargs: Any) -> _Run:
        return self.run_cli(
            "complete", "--root", self.root, "--login", "owner", "--testing", *extra, **kwargs
        )

    def setup_state(self) -> Optional[str]:
        row = self.instance(self.name)
        return None if row is None else row["setup_state"]

    def token_rows(self) -> int:
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            return fetch_one(conn.execute("SELECT count(*) FROM prks.bootstrap_tokens"))[0]

    def tearDown(self) -> None:
        logged = self.logs.getvalue()
        for secret in self.secrets:
            self.assertNotIn(secret, logged)
        super().tearDown()

    def assert_no_secret(self, run: _Run, *, allow_token_in_out: bool = False) -> None:
        for secret in self.secrets:
            self.assertNotIn(secret, run.err)
            if not (allow_token_in_out and secret != PASSWORD):
                self.assertNotIn(secret, run.out)

    # -- non-interactive: file in the configuration directory ----------------

    def test_headless_install_issues_to_the_config_file_then_completes(self) -> None:
        issued = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(issued.code, 0, issued.err)
        self.assertIn(self.token_file, issued.out)
        self.assertEqual(stat.S_IMODE(os.stat(self.token_file).st_mode), 0o600)
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        self.assert_no_secret(issued)
        self.assertFalse(self.token_file.startswith(self.root))

        env = dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=self.token_file)
        done = self.complete("--password-file", self.password_file, env=env)
        self.assertEqual(done.code, 0, done.err)
        self.assert_no_secret(done)
        self.assertIn("used up", done.out)
        self.assertEqual(self.setup_state(), "ready")
        # The spent token is not left behind looking usable.
        self.assertFalse(os.path.lexists(self.token_file))
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            stored = fetch_one(conn.execute("SELECT password_hash FROM prks.users"))[0]
        self.assertTrue(passwords.verify_password(stored, PASSWORD))

        for argv in (("issue-token", "--root", self.root),):
            again = self.run_cli(*argv)
            self.assertEqual(again.code, 1)
            self.assertIn("already set up", again.err)
        again = self.complete("--password-file", self.password_file, env=env)
        self.assertEqual(again.code, 1)
        self.assertIn("already set up", again.err)
        self.assertFalse(os.path.lexists(self.token_file))

    def outstanding_digest(self) -> Optional[bytes]:
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            row = conn.execute(
                "SELECT token_sha256 FROM prks.bootstrap_tokens "
                "WHERE consumed_at IS NULL AND revoked_at IS NULL"
            ).fetchone()
        return None if row is None else bytes(row[0])

    def expire_tokens(self) -> None:
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            conn.execute(
                "UPDATE prks.bootstrap_tokens SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 second'"
            )

    def complete_with_file(self) -> _Run:
        env = dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=self.token_file)
        return self.complete("--password-file", self.password_file, env=env)

    def test_rerunning_keeps_the_existing_token_file_and_its_token(self) -> None:
        first = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(first.code, 0, first.err)
        with open(self.token_file, "rb") as handle:
            before = handle.read()
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        rows = self.token_rows()
        second = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(second.code, 0, second.err)
        self.assertIn("already exists", second.out)
        self.assert_no_secret(second)
        with open(self.token_file, "rb") as handle:
            self.assertEqual(handle.read(), before)
        # Nothing was revoked: the delivered token is still the outstanding one.
        self.assertEqual(self.token_rows(), rows)
        self.assertEqual(self.outstanding_digest(), token.digest)
        self.assertEqual(self.complete_with_file().code, 0)
        self.assertEqual(self.setup_state(), "ready")

    def test_a_token_file_holding_a_spent_token_is_refused_not_replaced(self) -> None:
        self.assertEqual(self.run_cli("issue-token", "--root", self.root).code, 0)
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        self.expire_tokens()
        rows = self.token_rows()
        run = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(run.code, 1)
        self.assertIn("delete the file", run.err)
        self.assert_no_secret(run)
        self.assertEqual(bootstrap_tokens.read_token_file(self.token_file), token)
        self.assertEqual(self.token_rows(), rows)

    def test_concurrent_headless_issuers_both_deliver_the_published_token(self) -> None:
        # A publishes its file and stops before registering; B runs to the end
        # in that gap; then A registers. Neither revokes the published token.
        published = threading.Event()
        resume = threading.Event()
        result: dict[str, int] = {}

        def pause(point: str) -> None:
            if point == admin.FAULT_TOKEN_FILE_PUBLISHED:
                published.set()
                self.assertTrue(resume.wait(30))

        def run_a() -> None:
            result["a"] = admin.main(
                ["bootstrap", "issue-token", "--root", self.root],
                environ=self.env, stdin=io.StringIO(), stdout=io.StringIO(),
                stderr=io.StringIO(), open_database=self.open_db, faults=pause,
            )

        thread = threading.Thread(target=run_a)
        thread.start()
        try:
            self.assertTrue(published.wait(30))
            token = bootstrap_tokens.read_token_file(self.token_file)
            self.secrets.append(token.reveal())
            b = self.run_cli("issue-token", "--root", self.root)
            self.assertEqual(b.code, 0, b.err)
            self.assert_no_secret(b)
            self.assertEqual(self.outstanding_digest(), token.digest)
        finally:
            resume.set()
            thread.join(60)
        self.assertEqual(result.get("a"), 0)
        self.assertEqual(self.token_rows(), 1)
        self.assertEqual(self.outstanding_digest(), token.digest)
        self.assertEqual(bootstrap_tokens.read_token_file(self.token_file), token)
        self.assertEqual(self.complete_with_file().code, 0)

    def test_a_failed_file_delivery_revokes_nothing(self) -> None:
        delivered = self.run_cli("issue-token", "--root", self.root, tty=True)
        value = next(line.strip() for line in delivered.out.splitlines() if len(line.strip()) == 43)
        self.secrets.append(value)
        rows = self.token_rows()
        with mock.patch.object(bootstrap_tokens, "fsync_directory", return_value=False):
            run = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(run.code, 1)
        self.assertIn("durable", run.err)
        self.assertFalse(os.path.lexists(self.token_file))
        # The token delivered earlier is still the outstanding one, and works.
        self.assertEqual(self.token_rows(), rows)
        self.assertEqual(
            self.outstanding_digest(), bootstrap_tokens.parse_token(value).digest
        )
        self.assertEqual(self.complete(prompts=[value, PASSWORD, PASSWORD]).code, 0)

    def test_a_failed_registration_keeps_the_file_and_a_rerun_recovers(self) -> None:
        refusal = RootPairingRefused("database_unavailable", "db down", retryable=True)
        with mock.patch.object(bootstrap_tokens, "issue_token", side_effect=refusal):
            run = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(run.code, 1)
        self.assertIn("run issue-token again", run.err)
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        self.assert_no_secret(run)
        self.assertEqual(self.token_rows(), 0)
        again = self.run_cli("issue-token", "--root", self.root)
        self.assertEqual(again.code, 0, again.err)
        self.assertEqual(self.outstanding_digest(), token.digest)
        self.assertEqual(self.complete_with_file().code, 0)
        self.assertEqual(self.setup_state(), "ready")

    def test_a_config_directory_inside_the_data_root_is_refused(self) -> None:
        env = dict(self.env, XDG_CONFIG_HOME=os.path.join(self.root, "cfg"))
        run = self.run_cli("issue-token", "--root", self.root, env=env)
        self.assertEqual(run.code, 1)
        self.assertIn("outside the data root", run.err)
        self.assertFalse(os.path.lexists(os.path.join(self.root, "cfg")))

    # -- interactive: terminal ------------------------------------------------

    def test_terminal_issue_prints_once_and_complete_asks_for_it(self) -> None:
        issued = self.run_cli("issue-token", "--root", self.root, tty=True)
        self.assertEqual(issued.code, 0, issued.err)
        self.assertFalse(os.path.lexists(self.token_file))
        lines = [line.strip() for line in issued.out.splitlines() if line.strip()]
        value = next(line for line in lines if len(line) == 43 and " " not in line)
        self.secrets.append(value)
        self.assertEqual(issued.out.count(value), 1)
        self.assert_no_secret(issued, allow_token_in_out=True)

        mismatch = self.complete(prompts=[value, PASSWORD, PASSWORD + "x"])
        self.assertEqual(mismatch.code, 1)
        self.assertIn("differ", mismatch.err)
        self.assertIsNone(self.setup_state())

        done = self.complete(prompts=[value, PASSWORD, PASSWORD])
        self.assertEqual(done.code, 0, done.err)
        self.assert_no_secret(done)
        self.assertEqual(self.setup_state(), "ready")

    # -- automated: the operator's PRKS_BOOTSTRAP_TOKEN_FILE ------------------

    def test_operator_supplied_token_file_is_registered_never_printed_and_consumed(self) -> None:
        token = bootstrap_tokens.new_token()
        self.secrets.append(token.reveal())
        supplied = self.secret_file("supplied-token", token.reveal() + "\n")
        env = dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=supplied)
        for _ in range(2):  # re-running the installer is a no-op
            issued = self.run_cli("issue-token", "--root", self.root, env=env, tty=True)
            self.assertEqual(issued.code, 0, issued.err)
            self.assert_no_secret(issued)
        self.assertEqual(self.token_rows(), 1)
        self.assertFalse(os.path.lexists(self.token_file))
        done = self.complete("--password-file", self.password_file, env=env)
        self.assertEqual(done.code, 0, done.err)
        self.assert_no_secret(done)
        self.assertIn("PRKS_BOOTSTRAP_TOKEN_FILE is used up", done.out)
        self.assertEqual(self.setup_state(), "ready")

    def test_an_expired_operator_token_is_refused_with_a_rotate_instruction(self) -> None:
        token = bootstrap_tokens.new_token()
        self.secrets.append(token.reveal())
        supplied = self.secret_file("supplied-token", token.reveal() + "\n")
        env = dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=supplied)
        self.assertEqual(self.run_cli("issue-token", "--root", self.root, env=env).code, 0)
        self.expire_tokens()
        run = self.run_cli("issue-token", "--root", self.root, env=env)
        self.assertEqual(run.code, 1)
        self.assertIn("write a new token to that file", run.err)
        self.assert_no_secret(run)
        self.assertEqual(self.token_rows(), 1)
        # The old lifetime was not extended: setup still refuses the token.
        done = self.complete("--password-file", self.password_file, env=env)
        self.assertEqual(done.code, 1)
        self.assertIn("expired", done.err)
        self.assertIsNone(self.setup_state())

    def test_open_or_missing_secret_files_are_refused_before_setup(self) -> None:
        token = bootstrap_tokens.new_token()
        self.secrets.append(token.reveal())
        # Any group or other bit is too open; execute-only bits keep the fixture
        # from granting real read or write access to anyone else.
        open_token = self.secret_file("open-token", token.reveal(), mode=0o601)
        run = self.run_cli(
            "issue-token", "--root", self.root, env=dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=open_token)
        )
        self.assertEqual(run.code, 1)
        self.assertIn("chmod 600", run.err)
        self.assertEqual(self.token_rows(), 0)

        good = self.secret_file("token", token.reveal())
        env = dict(self.env, PRKS_BOOTSTRAP_TOKEN_FILE=good)
        self.assertEqual(self.run_cli("issue-token", "--root", self.root, env=env).code, 0)
        open_password = self.secret_file("open-password", PASSWORD, mode=0o610)
        run = self.complete("--password-file", open_password, env=env)
        self.assertEqual(run.code, 1)
        self.assertIn("chmod 600", run.err)
        no_terminal = self.complete(env=env)
        self.assertEqual(no_terminal.code, 1)
        self.assertIn("--password-file", no_terminal.err)
        no_token = self.complete("--password-file", self.password_file)
        self.assertEqual(no_token.code, 1)
        self.assertIn("PRKS_BOOTSTRAP_TOKEN_FILE", no_token.err)
        for result in (run, no_terminal, no_token):
            self.assert_no_secret(result)
        self.assertIsNone(self.setup_state())

    def test_wrong_or_expired_token_completes_nothing(self) -> None:
        issued = self.run_cli("issue-token", "--root", self.root, tty=True)
        value = next(
            line.strip() for line in issued.out.splitlines() if len(line.strip()) == 43
        )
        self.secrets.append(value)
        wrong = bootstrap_tokens.new_token().reveal()
        self.secrets.append(wrong)
        run = self.complete(prompts=[wrong, PASSWORD, PASSWORD])
        self.assertEqual(run.code, 1)
        self.assertIn("not valid", run.err)
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            conn.execute(
                "UPDATE prks.bootstrap_tokens SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 second'"
            )
        run = self.complete(prompts=[value, PASSWORD, PASSWORD])
        self.assertEqual(run.code, 1)
        self.assertIn("expired", run.err)
        for result in (run,):
            self.assert_no_secret(result)
        self.assertIsNone(self.setup_state())

    def test_the_real_database_factory_reads_the_environment(self) -> None:
        env = dict(self.env, PRKS_DATABASE_URL=self.harness.conninfo(self.name))
        out, err = io.StringIO(), io.StringIO()
        code = admin.main(
            ["bootstrap", "issue-token", "--root", self.root],
            environ=env, stdin=io.StringIO(), stdout=out, stderr=err,
        )
        self.assertEqual(code, 0, err.getvalue())
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        self.assertNotIn(self.harness.conninfo(self.name), out.getvalue() + err.getvalue())
        missing = admin.main(
            ["bootstrap", "issue-token", "--root", self.root],
            environ=self.env, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
        )
        self.assertEqual(missing, admin.EXIT_USAGE)

    def test_a_token_from_the_token_file_is_equal_to_the_issued_one(self) -> None:
        self.run_cli("issue-token", "--root", self.root)
        token = bootstrap_tokens.read_token_file(self.token_file)
        self.secrets.append(token.reveal())
        self.assertIsInstance(token, BootstrapToken)
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            digest = fetch_one(conn.execute("SELECT token_sha256 FROM prks.bootstrap_tokens"))[0]
        self.assertEqual(bytes(digest), token.digest)
