"""Bootstrap tokens (§9.2; L-ID-1 #626): issuance, expiry, single use, the token file.

The database keeps only a token's SHA-256; one token is outstanding at a time;
step 1 of bootstrap checks it and step 3 consumes it in the transaction that
marks setup ready.
"""

from __future__ import annotations

import os
import pickle
import stat
import threading
from typing import Any

import psycopg

from backend.domain.identity import bootstrap_tokens, root_pairing
from backend.domain.identity.bootstrap_tokens import BootstrapToken
from backend.domain.identity.root_pairing import BootstrapResult, RootPairingRefused
from tests.pg.harness import fetch_one
from tests.pg.test_root_pairing import OWNER, Crash, RootPairingTestCase, crash_at


class TokenValueTests(RootPairingTestCase):
    def test_new_tokens_are_256_bit_and_distinct(self) -> None:
        tokens = {bootstrap_tokens.new_token().reveal() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        for value in tokens:
            self.assertRegex(value, r"^[A-Za-z0-9_-]{43}$")

    def test_a_token_never_prints_or_pickles_its_value(self) -> None:
        token = bootstrap_tokens.new_token()
        for text in (repr(token), str(token), f"{token}", repr([token]), repr({"t": token})):
            self.assertNotIn(token.reveal(), text)
        with self.assertRaises(TypeError):
            pickle.dumps(token)
        self.assertEqual(token, BootstrapToken(token.reveal()))
        self.assertNotEqual(token, bootstrap_tokens.new_token())

    def test_malformed_tokens_are_refused_without_echo(self) -> None:
        for bad in ("", "short", "x" * 42, "a" * 43 + "!", "a" * 129, "é" * 43):
            with self.subTest(length=len(bad)):
                error = self.refused("bootstrap_token_malformed", BootstrapToken, bad)
                if bad:
                    self.assertNotIn(bad, error.message)
        self.assertEqual(
            bootstrap_tokens.parse_token("  " + "a" * 43 + "\n"), BootstrapToken("a" * 43)
        )


class TokenStoreTests(RootPairingTestCase):
    def tokens(self, name: str) -> list[dict[str, Any]]:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(
                    "SELECT row_to_json(t)::text AS json, consumed_at, revoked_at, "
                    "expires_at - created_at AS ttl FROM prks.bootstrap_tokens t ORDER BY created_at"
                )
                return cur.fetchall()

    def outstanding(self, name: str) -> int:
        return sum(1 for row in self.tokens(name) if not row["consumed_at"] and not row["revoked_at"])

    def test_only_the_hash_is_stored_with_a_24_hour_expiry(self) -> None:
        name = self.database()
        issued = bootstrap_tokens.issue_token(self.db(name))
        (row,) = self.tokens(name)
        self.assertNotIn(issued.token.reveal(), row["json"])
        self.assertIn(issued.token.digest.hex(), row["json"])
        self.assertEqual(row["ttl"].total_seconds(), 24 * 3600)

    def test_issuing_revokes_the_previous_token(self) -> None:
        name, root = self.database(), self.new_root()
        first = self.issue(name)
        second = self.issue(name)
        self.assertEqual(self.outstanding(name), 1)
        self.refused("bootstrap_token_invalid", self.bootstrap, name, root, token=first)
        self.assertIsNone(self.instance(name))
        self.assertTrue(self.bootstrap(name, root, token=second).created)

    def test_the_index_allows_one_outstanding_token(self) -> None:
        name = self.database()
        self.issue(name)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO prks.bootstrap_tokens (token_sha256, expires_at) "
                    "VALUES (%s, now() + interval '1 hour')",
                    (bootstrap_tokens.new_token().digest,),
                )

    def test_concurrent_issuers_leave_exactly_one_outstanding_token(self) -> None:
        name = self.database()
        barrier = threading.Barrier(5)
        errors: list[BaseException] = []

        def run() -> None:
            try:
                barrier.wait(30)
                self.issue(name)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.tokens(name)), 5)
        self.assertEqual(self.outstanding(name), 1)

    def test_reissuing_a_supplied_token_is_a_no_op_and_a_spent_one_never_returns(self) -> None:
        name, root = self.database(), self.new_root()
        supplied = bootstrap_tokens.new_token()
        first = bootstrap_tokens.issue_token(self.db(name), token=supplied)
        again = bootstrap_tokens.issue_token(self.db(name), token=supplied)
        self.assertEqual(first.expires_at, again.expires_at)
        self.assertEqual(len(self.tokens(name)), 1)
        self.issue(name)  # revokes it
        self.refused(
            "bootstrap_token_reused", bootstrap_tokens.issue_token, self.db(name), token=supplied
        )
        self.bootstrap(name, root)  # consumes the newest one
        self.refused(
            "already_ready", bootstrap_tokens.issue_token, self.db(name), token=supplied
        )

    def test_an_expired_supplied_token_is_refused_not_reregistered(self) -> None:
        name = self.database()
        supplied = bootstrap_tokens.new_token()
        bootstrap_tokens.issue_token(self.db(name), token=supplied)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute(
                "UPDATE prks.bootstrap_tokens SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 second'"
            )
        before = self.tokens(name)
        error = self.refused(
            "bootstrap_token_expired", bootstrap_tokens.issue_token, self.db(name), token=supplied
        )
        self.assertNotIn(supplied.reveal(), error.message)
        # Its lifetime is not extended, and nothing else changed.
        self.assertEqual(self.tokens(name), before)

    def test_no_token_is_issued_once_ready(self) -> None:
        name, root = self.database(), self.new_root()
        self.bootstrap(name, root)
        count = len(self.tokens(name))
        self.refused("already_ready", bootstrap_tokens.issue_token, self.db(name))
        self.assertEqual(len(self.tokens(name)), count)
        self.assertEqual(self.outstanding(name), 0)


class BootstrapTokenGateTests(RootPairingTestCase):
    def expire(self, name: str) -> None:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute(
                "UPDATE prks.bootstrap_tokens SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 second'"
            )

    def assert_untouched(self, name: str, root: str, before: bytes) -> None:
        self.assertIsNone(self.instance(name))
        self.assertEqual(self.marker_bytes(root), before)

    def test_missing_unknown_expired_and_revoked_tokens_do_nothing(self) -> None:
        name, root = self.database(), self.new_root()
        before = self.marker_bytes(root)
        self.refused(
            "bootstrap_token_invalid", self.bootstrap, name, root, token=bootstrap_tokens.new_token()
        )
        self.assert_untouched(name, root, before)
        token = self.issue(name)
        self.expire(name)
        self.refused("bootstrap_token_expired", self.bootstrap, name, root, token=token)
        self.assert_untouched(name, root, before)
        revoked = self.issue(name)
        self.issue(name)
        self.refused("bootstrap_token_invalid", self.bootstrap, name, root, token=revoked)
        self.assert_untouched(name, root, before)

    def test_the_token_is_consumed_with_ready_and_never_reopens_setup(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        self.bootstrap(name, root, token=token)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            consumed = fetch_one(conn.execute(
                "SELECT consumed_at IS NOT NULL FROM prks.bootstrap_tokens"
            ))[0]
        self.assertTrue(consumed)
        for attempt in (token, bootstrap_tokens.new_token()):
            with self.subTest(same=attempt == token):
                self.refused("already_ready", self.bootstrap, name, root, token=attempt)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            self.assertEqual(fetch_one(conn.execute("SELECT count(*) FROM prks.users"))[0], 1)

    def test_a_retried_final_step_needs_the_token_that_completed_setup(self) -> None:
        name, root = self.database(), self.new_root()
        lease = self.lease(root)
        token = self.issue(name)
        db = self.db(name)
        claim = root_pairing.begin_bootstrap(db, token=token, root=root, lease=lease, testing=True)
        root_pairing.claim_bootstrap_root(root, claim, lease=lease)
        first = root_pairing.finish_bootstrap(db, claim, OWNER, token=token, root=root, lease=lease)
        again = root_pairing.finish_bootstrap(db, claim, OWNER, token=token, root=root, lease=lease)
        self.assertEqual((again.user_id, again.created), (first.user_id, False))
        self.refused(
            "already_ready", root_pairing.finish_bootstrap, db, claim, OWNER,
            token=bootstrap_tokens.new_token(), root=root, lease=lease,
        )

    def test_a_failed_final_step_leaves_the_token_unconsumed(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        # A stray account holding the owner's login makes step 3's INSERT fail.
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute(
                "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, 'OWNER', 'x')",
                ("U-" + "0" * 32,),
            )
        self.refused("database_unavailable", self.bootstrap, name, root, token=token)
        self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            self.assertTrue(fetch_one(conn.execute(
                "SELECT consumed_at IS NULL FROM prks.bootstrap_tokens"
            ))[0])
            conn.execute("DELETE FROM prks.users")
        self.assertTrue(self.bootstrap(name, root, token=token).created)

    def test_a_blank_owner_field_is_refused_before_anything_is_written(self) -> None:
        name, root = self.database(), self.new_root()
        before = self.marker_bytes(root)
        for field in ("login_name", "display_name", "library_name"):
            with self.subTest(field=field):
                values = {"login_name": "owner", "display_name": "Owner", "library_name": "Library"}
                values[field] = "  "
                owner = root_pairing.OwnerSpec(password_hash=OWNER.password_hash, **values)
                self.refused("owner_invalid", self.bootstrap, name, root, owner=owner)
                self.assertIsNone(self.instance(name))
                self.assertEqual(self.marker_bytes(root), before)

    def test_owner_without_an_argon2id_hash_is_refused(self) -> None:
        name, root = self.database(), self.new_root()
        for value in ("", "plaintext", "$argon2i$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$aGFzaGhhc2g"):
            with self.subTest(value=value[:12]):
                owner = root_pairing.OwnerSpec("owner", "Owner", "Library", value)
                error = self.refused(
                    "owner_password_invalid", self.bootstrap, name, root, owner=owner
                )
                self.assertNotIn(value or "\0", error.message)

    def test_a_crash_then_an_expired_token_resumes_with_a_new_one(self) -> None:
        name, root = self.database(), self.new_root()
        with self.assertRaises(Crash):
            self.bootstrap(name, root, faults=crash_at(root_pairing.FAULT_AFTER_CLAIM))
        stale = self.issue(name)
        self.expire(name)
        self.refused("bootstrap_token_expired", self.bootstrap, name, root, token=stale)
        self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
        result = self.bootstrap(name, root)  # a fresh token resumes at step 3
        self.assertTrue(result.created)
        self.assert_paired(name, root, 1)

    def test_a_token_issued_while_setup_runs_supersedes_it(self) -> None:
        name, root = self.database(), self.new_root()
        paused, resume = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}
        token = self.issue(name)

        def hold(point: str) -> None:
            if point == root_pairing.FAULT_AFTER_CLAIM:
                paused.set()
                resume.wait(30)

        def first() -> None:
            try:
                outcome["first"] = self.bootstrap(name, root, token=token, faults=hold)
            except RootPairingRefused as exc:
                outcome["first"] = exc.reason

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(paused.wait(30))
        newer = self.issue(name)
        resume.set()
        thread.join(60)
        self.assertEqual(outcome["first"], "bootstrap_token_invalid")
        self.assertEqual(self.instance(name)["setup_state"], "bootstrapping")  # type: ignore[index]
        self.assertIsInstance(self.bootstrap(name, root, token=newer), BootstrapResult)
        self.assert_paired(name, root, 1)


    def test_a_token_issued_while_setup_commits_waits_and_is_refused(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        paused, resume = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}

        def hold(point: str) -> None:
            if point == root_pairing.FAULT_READY_BEFORE_COMMIT:
                paused.set()
                resume.wait(30)

        def setup() -> None:
            outcome["setup"] = self.bootstrap(name, root, token=token, faults=hold)

        def issue() -> None:
            try:
                outcome["issue"] = self.issue(name)
            except RootPairingRefused as exc:
                outcome["issue"] = exc.reason

        setup_thread = threading.Thread(target=setup)
        setup_thread.start()
        self.assertTrue(paused.wait(30))
        issue_thread = threading.Thread(target=issue)
        issue_thread.start()
        self.assertTrue(self.wait_for_lock_waiter(name))
        resume.set()
        setup_thread.join(60)
        issue_thread.join(60)
        self.assertIsInstance(outcome["setup"], BootstrapResult)
        self.assertEqual(outcome["issue"], "already_ready")
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            self.assertEqual(fetch_one(conn.execute(
                "SELECT count(*) FROM prks.bootstrap_tokens "
                "WHERE consumed_at IS NULL AND revoked_at IS NULL"
            ))[0], 0)

    def wait_for_lock_waiter(self, name: str) -> bool:
        with psycopg.connect(self.harness.conninfo(name), autocommit=True) as conn:
            for _ in range(200):
                waiting = fetch_one(conn.execute(
                    "SELECT count(*) FROM pg_locks WHERE NOT granted AND database = "
                    "(SELECT oid FROM pg_database WHERE datname = current_database())"
                ))[0]
                if waiting:
                    return True
                threading.Event().wait(0.02)
        return False


class TokenFileTests(RootPairingTestCase):
    def test_the_file_is_owner_only_outside_the_root_and_never_overwritten(self) -> None:
        root = self.new_root()
        config = os.path.join(self.tmp, "config", "prks")
        path = os.path.join(config, bootstrap_tokens.TOKEN_FILE_NAME)
        token = bootstrap_tokens.new_token()
        old_umask = os.umask(0)
        try:
            bootstrap_tokens.write_token_file(token, path, data_root=root)
        finally:
            os.umask(old_umask)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(config).st_mode) & 0o077, 0)
        self.assertEqual(bootstrap_tokens.read_token_file(path), token)
        with open(path, "rb") as handle:
            before = handle.read()
        self.refused(
            "token_file_exists", bootstrap_tokens.write_token_file,
            bootstrap_tokens.new_token(), path, data_root=root,
        )
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(sorted(os.listdir(config)), [bootstrap_tokens.TOKEN_FILE_NAME])

    def test_a_link_at_the_path_is_not_written_through(self) -> None:
        root = self.new_root()
        target = os.path.join(self.tmp, "elsewhere")
        link = os.path.join(self.tmp, "cfg", bootstrap_tokens.TOKEN_FILE_NAME)
        os.makedirs(os.path.dirname(link))
        os.symlink(target, link)
        self.refused(
            "token_file_exists", bootstrap_tokens.write_token_file,
            bootstrap_tokens.new_token(), link, data_root=root,
        )
        self.assertFalse(os.path.exists(target))

    def test_a_token_file_inside_the_data_root_is_refused(self) -> None:
        root = self.new_root()
        for path in (
            os.path.join(root, bootstrap_tokens.TOKEN_FILE_NAME),
            os.path.join(root, ".prks-maintenance", "x", bootstrap_tokens.TOKEN_FILE_NAME),
        ):
            with self.subTest(path=os.path.relpath(path, root)):
                self.refused(
                    "token_file_in_data_root", bootstrap_tokens.write_token_file,
                    bootstrap_tokens.new_token(), path, data_root=root,
                )
                self.assertFalse(os.path.lexists(path))

    def test_reading_refuses_open_modes_links_and_non_files(self) -> None:
        token = bootstrap_tokens.new_token()
        path = os.path.join(self.tmp, "token")
        with open(path, "w", encoding="ascii") as handle:
            handle.write(token.reveal() + "\n")
        # Any group or other bit is refused; execute-only bits keep the fixture
        # from granting real read or write access to anyone else.
        for loose in (0o610, 0o601):
            with self.subTest(mode=oct(loose)):
                os.chmod(path, loose)
                self.refused("token_file_too_open", bootstrap_tokens.read_token_file, path)
        os.chmod(path, 0o600)
        self.assertEqual(bootstrap_tokens.read_token_file(path), token)
        link = os.path.join(self.tmp, "link")
        os.symlink(path, link)
        self.refused("token_file_unreadable", bootstrap_tokens.read_token_file, link)
        self.refused("token_file_unreadable", bootstrap_tokens.read_token_file, self.tmp)
        self.refused(
            "token_file_unreadable", bootstrap_tokens.read_token_file, os.path.join(self.tmp, "none")
        )

    def test_a_used_token_file_is_removed_only_when_it_holds_that_token(self) -> None:
        root = self.new_root()
        path = os.path.join(self.tmp, "cfg", bootstrap_tokens.TOKEN_FILE_NAME)
        token = bootstrap_tokens.new_token()
        bootstrap_tokens.write_token_file(token, path, data_root=root)
        self.assertFalse(bootstrap_tokens.remove_token_file_if(path, bootstrap_tokens.new_token()))
        self.assertTrue(os.path.exists(path))
        self.assertTrue(bootstrap_tokens.remove_token_file_if(path, token))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(bootstrap_tokens.remove_token_file_if(path, token))

    def test_a_conditional_removal_never_deletes_a_file_published_meanwhile(self) -> None:
        # Two cleaners and a second deployment share the directory: a
        # removal that read token A must not unlink a B published at the same
        # path afterwards. The directory lock makes check-and-unlink atomic.
        root = self.new_root()
        directory = os.path.join(self.tmp, "cfg")
        path = os.path.join(directory, bootstrap_tokens.TOKEN_FILE_NAME)
        first = bootstrap_tokens.new_token()
        second = bootstrap_tokens.new_token()
        bootstrap_tokens.write_token_file(first, path, data_root=root)
        result: dict[str, bool] = {}
        with bootstrap_tokens._token_file_lock(directory):
            remover = threading.Thread(
                target=lambda: result.update(removed=bootstrap_tokens.remove_token_file_if(path, first))
            )
            remover.start()
            remover.join(0.5)
            self.assertTrue(remover.is_alive(), "the removal ran without the lock")
            # Another process replaces the file while the remover waits.
            os.unlink(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(second.reveal() + "\n")
        remover.join(30)
        self.assertFalse(result["removed"])
        self.assertEqual(bootstrap_tokens.read_token_file(path), second)
