"""Hardening of the authorization helpers after #695 (L-ID-1 #626, follow-up to PR 4/5).

Each class is a regression test for one review finding on #695 and fails on
the code #695 merged:

- a :class:`LibraryScope`, its transaction handle or a cursor kept past the
  callback cannot run SQL on the pooled connection (finding 2);
- a claimed device cannot be removed or moved to another user while an
  authorized write that checked it is in flight (finding 3);
- a session or job lease that expires while authorization waits on a lock
  never authorizes (finding 4);
- any positive ``bigint`` is a valid epoch (finding 5);
- two commands that change the Library row queue instead of deadlocking
  (finding 6);
- refusal precedence: authorization first, then the Library's state
  (finding 7);
- a context built from anything but a live server-side session has no
  authority (finding 1).
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from backend.db.database import Isolation, Transaction, TransactionClosed
from backend.domain.identity import sessions
from backend.domain.identity.authorization import Capability, Refusal
from backend.domain.identity.library_scope import (
    LibraryScope,
    ScopeClosed,
    instance_command,
    library_command,
    library_query,
)
from backend.domain.identity.request_context import EPOCH_MAX, RequestContext, parse_epoch
from backend.domain.identity.sessions import ValidSession
from tests.pg.scope_fixtures import BUMP_SQL, Paused, ScopeTestCase, bump, read_all

REPO = Path(__file__).resolve().parents[2]

RENAME_SQL = "UPDATE prks.libraries SET name = %(name)s WHERE id = %(library_id)s RETURNING name"


def rename(name: str) -> Any:
    def run(scope: LibraryScope) -> str:
        row = scope.execute(RENAME_SQL, {"name": name}).fetchone()
        assert row is not None
        return str(row["name"])

    return run


class ScopeLifetimeTests(ScopeTestCase):
    """Finding 2: nothing handed to a callback outlives it."""

    def setUp(self) -> None:
        super().setUp()
        self.ctx_a = self.ctx(self.member("Editor", "editor"))

    def kept(self, sink: list[Any], fn: Any = None) -> Any:
        def run(scope: LibraryScope) -> Any:
            sink.append(scope)
            return fn(scope) if fn is not None else None

        return run

    def assert_closed(self, scope: LibraryScope) -> None:
        before = self.revisions()
        with self.assertRaises(ScopeClosed):
            scope.execute(BUMP_SQL, {"scope_id": "a1"})
        with self.assertRaises(ScopeClosed):
            scope.execute_unscoped("SELECT 1")
        with self.assertRaises(TransactionClosed):
            scope._tx.execute("SELECT 1")
        self.assertEqual(self.revisions(), before)

    def test_a_scope_kept_after_a_command_commits(self) -> None:
        kept: list[LibraryScope] = []
        self.assertEqual(library_command(self.db, self.ctx_a, Capability.EDIT_LIBRARY, self.kept(kept, bump("a1"))), 2)
        self.assert_closed(kept[0])

    def test_a_scope_kept_after_a_command_rolls_back(self) -> None:
        kept: list[LibraryScope] = []

        def fail(scope: LibraryScope) -> None:
            bump("a1")(scope)
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            library_command(self.db, self.ctx_a, Capability.EDIT_LIBRARY, self.kept(kept, fail))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)
        self.assert_closed(kept[0])

    def test_a_scope_kept_after_a_query(self) -> None:
        kept: list[LibraryScope] = []
        library_query(self.db, self.ctx_a, self.kept(kept, read_all))
        self.assert_closed(kept[0])

    def test_a_refused_command_hands_out_no_scope(self) -> None:
        kept: list[LibraryScope] = []
        reader = self.ctx(self.member("Reader", "reader"))
        self.assert_refused(
            Refusal.FORBIDDEN, lambda: library_command(self.db, reader, Capability.EDIT_LIBRARY, self.kept(kept))
        )
        self.assertEqual(kept, [])

    def test_a_scope_from_an_earlier_attempt_stays_closed(self) -> None:
        kept: list[LibraryScope] = []

        def run(scope: LibraryScope) -> int:
            kept.append(scope)
            if len(kept) == 1:
                bump("a1")(scope)
                raise psycopg.errors.SerializationFailure("forced retry")
            # The first attempt's scope is refused while the retry runs, too.
            with self.assertRaises(ScopeClosed):
                kept[0].execute(BUMP_SQL, {"scope_id": "a2"})
            value = bump("a1")(scope)
            assert value is not None
            return value

        result = library_command(self.db, self.ctx_a, Capability.EDIT_LIBRARY, run, isolation=Isolation.SERIALIZABLE)
        self.assertEqual((result, len(kept)), (2, 2))  # the rolled-back attempt left nothing
        for scope in kept:
            self.assert_closed(scope)
        self.assertEqual(self.revisions()[(self.lib_a, "a2")], 1)

    def test_a_cursor_kept_after_the_callback_cannot_run_sql(self) -> None:
        kept: list[psycopg.Cursor[Any]] = []

        def run(scope: LibraryScope) -> None:
            kept.append(scope.execute(BUMP_SQL, {"scope_id": "a1"}))

        library_command(self.db, self.ctx_a, Capability.EDIT_LIBRARY, run)
        with self.assertRaises(psycopg.InterfaceError):
            kept[0].execute("UPDATE prks.entity_revisions SET revision = 99")
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 2)

    def test_an_instance_transaction_kept_after_the_callback(self) -> None:
        kept: list[Transaction] = []
        instance_command(self.db, RequestContext.for_admin(), kept.append)
        with self.assertRaises(TransactionClosed):
            kept[0].execute("SELECT 1")
        # The handle never hands out its pooled connection at all.
        self.assertFalse(hasattr(kept[0], "connection"))


class DeviceRaceTests(ScopeTestCase):
    """Finding 3: the claimed device row is locked for the protected write."""

    def setUp(self) -> None:
        super().setUp()
        self.editor = self.member("Editor", "editor")
        self.other = self.member("Other", "editor")
        self.device_id = self.device(self.editor)
        self.claim = self.ctx(self.editor, device_id=self.device_id)

    def assert_change_waits_for_the_write(self, statement: str, params: tuple[Any, ...]) -> None:
        paused = Paused()
        writer = paused.start(
            lambda: library_command(self.db, self.claim, Capability.EDIT_LIBRARY, paused.hold(bump("a1")))
        )
        self.assertTrue(paused.inside.wait(30))
        with self.connect() as other:
            change = Paused()
            changer = change.start(lambda: other.execute(statement, params))
            self.wait_until(self.waiting_on_lock(other), "the device change to wait for the write")
            self.assertFalse(change.result)
            paused.release.set()
            writer.join(30)
            changer.join(30)
        self.assertEqual((paused.error, change.error, paused.result), ([], [], [2]))
        self.assert_refused(
            Refusal.FORBIDDEN, lambda: library_command(self.db, self.claim, Capability.EDIT_LIBRARY, bump("a1"))
        )

    def test_a_removal_waits_for_the_write_in_flight(self) -> None:
        self.assert_change_waits_for_the_write("DELETE FROM prks.devices WHERE id = %s", (self.device_id,))

    def test_a_reassignment_waits_for_the_write_in_flight(self) -> None:
        self.assert_change_waits_for_the_write(
            "UPDATE prks.devices SET user_id = %s WHERE id = %s", (self.other, self.device_id)
        )

    def test_a_removal_committed_first_refuses_the_write(self) -> None:
        self.run_sql("DELETE FROM prks.devices WHERE id = %s", (self.device_id,))
        self.assert_refused(
            Refusal.FORBIDDEN, lambda: library_command(self.db, self.claim, Capability.EDIT_LIBRARY, bump("a1"))
        )
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)

    def test_reads_with_a_device_claim_take_no_lock(self) -> None:
        paused = Paused()
        reader = paused.start(lambda: library_query(self.db, self.claim, paused.hold(read_all)))
        self.assertTrue(paused.inside.wait(30))
        # The device changes while the read is open: nothing waits for a read-only snapshot.
        self.run_sql("UPDATE prks.devices SET user_id = %s WHERE id = %s", (self.other, self.device_id))
        paused.release.set()
        reader.join(30)
        self.assertEqual((paused.error, len(paused.result[0])), ([], 2))  # type: ignore[arg-type]


class ExpiryAfterLockWaitTests(ScopeTestCase):
    """Finding 4: expiry is checked at the time of the check, after every lock wait.

    A second connection locks the credential's row without changing it, so
    PostgreSQL does not re-read the row once the wait ends; the credential
    expires during the wait. The transaction began before the expiry, so a
    check against ``now()`` would still pass.
    """

    def expires_soon(self, table: str, column: str, row_id: Any) -> None:
        self.run_sql(
            f"UPDATE prks.{table} SET {column} = clock_timestamp() + interval '1 second' WHERE id = %s", (row_id,)
        )

    def has_expired(self, table: str, column: str, row_id: Any) -> Any:
        def check() -> bool:
            rows = self.run_sql(f"SELECT {column} < clock_timestamp() AS gone FROM prks.{table} WHERE id = %s", (row_id,))
            return bool(rows[0]["gone"])

        return check

    def run_through_expiry(self, table: str, column: str, row_id: Any, ctx: RequestContext) -> Paused:
        self.expires_soon(table, column, row_id)
        with self.connect() as blocker:
            blocker.execute("BEGIN")
            blocker.execute(f"SELECT 1 FROM prks.{table} WHERE id = %s FOR UPDATE", (row_id,))
            attempt = Paused()
            thread = attempt.start(lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1")))
            self.wait_until(self.any_lock_wait, "authorization to wait on the credential's row")
            self.wait_until(self.has_expired(table, column, row_id), "the credential to expire")
            blocker.execute("ROLLBACK")  # released without changing the row
            thread.join(30)
        return attempt

    def any_lock_wait(self) -> bool:
        return bool(self.run_sql("SELECT 1 FROM pg_locks WHERE NOT granted"))

    def test_a_session_that_expires_during_a_lock_wait_does_not_authorize(self) -> None:
        ctx = self.ctx(self.member("Editor", "editor"))
        attempt = self.run_through_expiry("sessions", "expires_at", ctx.session_id, ctx)
        self.assertEqual(attempt.result, [])
        self.assertEqual([getattr(e, "refusal", e) for e in attempt.error], [Refusal.NOT_AUTHENTICATED])
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)

    def test_a_lease_that_expires_during_a_lock_wait_does_not_authorize(self) -> None:
        job_id, lease_id = self.job(self.lib_a, 1)
        ctx = RequestContext.for_job(self.db, job_id, lease_id)
        assert ctx is not None
        attempt = self.run_through_expiry("jobs", "locked_until", job_id, ctx)
        self.assertEqual(attempt.result, [])
        self.assertEqual([getattr(e, "refusal", e) for e in attempt.error], [Refusal.NOT_AUTHENTICATED])
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)

    def test_a_session_that_expires_during_a_renewal_lock_wait_is_not_renewed(self) -> None:
        """Renewal locks the row and re-checks it, so it never extends an expired session (#708 review)."""
        owner = self.member("Owner", "owner")
        new = self.db.command(lambda tx: sessions.create_session(tx, user_id=owner, library_id=self.lib_a))
        self.run_sql(
            "UPDATE prks.sessions SET last_used_at = clock_timestamp() - interval '2 days', "
            "expires_at = clock_timestamp() + interval '1 second' WHERE id = %s",
            (new.session_id,),
        )
        with self.connect() as blocker:
            blocker.execute("BEGIN")
            blocker.execute("SELECT 1 FROM prks.sessions WHERE id = %s FOR UPDATE", (new.session_id,))
            attempt = Paused()
            thread = attempt.start(lambda: sessions.resolve_session(self.db, new.token.reveal()))
            self.wait_until(self.any_lock_wait, "the renewal to wait on the session row")
            self.wait_until(self.has_expired("sessions", "expires_at", new.session_id), "the session to expire")
            blocker.execute("ROLLBACK")
            thread.join(30)
        self.assertEqual((attempt.error, attempt.result), ([], [None]))
        self.assertTrue(self.has_expired("sessions", "expires_at", new.session_id)())

    def test_valid_credentials_still_authorize_after_a_lock_wait(self) -> None:
        ctx = self.ctx(self.member("Editor", "editor"))
        with self.connect() as blocker:
            blocker.execute("BEGIN")
            blocker.execute("SELECT 1 FROM prks.sessions WHERE id = %s FOR UPDATE", (ctx.session_id,))
            attempt = Paused()
            thread = attempt.start(lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1")))
            self.wait_until(self.any_lock_wait, "authorization to wait on the session row")
            blocker.execute("ROLLBACK")
            thread.join(30)
        self.assertEqual((attempt.error, attempt.result), ([], [2]))


class LibraryRowTests(ScopeTestCase):
    """Finding 6: commands that change the Library row queue on it, never deadlock."""

    def setUp(self) -> None:
        super().setUp()
        self.owner = self.ctx(self.member("Owner", "owner"))

    def library_name(self) -> str:
        return str(self.run_sql("SELECT name FROM prks.libraries WHERE id = %s", (self.lib_a,))[0]["name"])

    def any_lock_wait(self) -> bool:
        return bool(self.run_sql("SELECT 1 FROM pg_locks WHERE NOT granted"))

    def test_two_settings_updates_queue_instead_of_deadlocking(self) -> None:
        # The first command has passed its checks and pauses before its UPDATE. Were the
        # Library row only shared, the second would also pass its checks, both would try to
        # upgrade the lock in their UPDATE, and PostgreSQL would abort one as a deadlock.
        first = Paused()

        def first_rename(scope: LibraryScope) -> str:
            first.inside.set()
            if not first.release.wait(30):
                raise TimeoutError("never released")
            return rename("First")(scope)

        a = first.start(lambda: library_command(
            self.db, self.owner, Capability.ADMIN_LIBRARY, first_rename, updates_library=True
        ))
        self.assertTrue(first.inside.wait(30))
        second = Paused()
        b = second.start(lambda: library_command(
            self.db, self.owner, Capability.ADMIN_LIBRARY, rename("Second"), updates_library=True
        ))
        self.wait_until(self.any_lock_wait, "the second update to queue on the Library row")
        first.release.set()
        a.join(30)
        b.join(30)
        self.assertEqual((first.error, second.error), ([], []))
        self.assertEqual((first.result, second.result), (["First"], ["Second"]))
        self.assertEqual(self.library_name(), "Second")

    def test_a_settings_update_waits_for_content_commands_in_flight(self) -> None:
        editor = self.ctx(self.member("Editor", "editor"))
        content = Paused()
        a = content.start(lambda: library_command(self.db, editor, Capability.EDIT_LIBRARY, content.hold(bump("a1"))))
        self.assertTrue(content.inside.wait(30))
        update = Paused()
        b = update.start(lambda: library_command(
            self.db, self.owner, Capability.ADMIN_LIBRARY, rename("Renamed"), updates_library=True
        ))
        self.wait_until(self.any_lock_wait, "the settings update to wait for the content command")
        content.release.set()
        a.join(30)
        b.join(30)
        self.assertEqual((content.error, update.error, content.result, update.result), ([], [], [2], ["Renamed"]))

    def test_content_commands_still_share_the_library_row(self) -> None:
        editor = self.ctx(self.member("Editor", "editor"))
        first = Paused()
        a = first.start(lambda: library_command(self.db, editor, Capability.EDIT_LIBRARY, first.hold(bump("a1"))))
        self.assertTrue(first.inside.wait(30))
        # A second content command does not wait for the first.
        self.assertEqual(library_command(self.db, editor, Capability.EDIT_LIBRARY, bump("a2")), 2)
        first.release.set()
        a.join(30)
        self.assertEqual(first.error, [])

    def test_a_library_row_write_needs_the_update_mode(self) -> None:
        for statement in (RENAME_SQL, "DELETE FROM prks.libraries WHERE id = %(library_id)s",
                          'update "prks"."libraries" SET name = %(name)s WHERE id = %(library_id)s',
                          "UPDATE prks . libraries SET name = %(name)s WHERE id = %(library_id)s",
                          "UPDATE\n  ONLY prks.\"libraries\" SET name = %(name)s WHERE id = %(library_id)s",
                          "MERGE INTO prks.libraries l USING (SELECT %(library_id)s AS id) s ON l.id = s.id "
                          "WHEN MATCHED THEN UPDATE SET name = %(name)s",
                          "SELECT 1 FROM prks.libraries WHERE id = %(library_id)s FOR NO KEY UPDATE",
                          "SELECT 1 FROM prks.libraries WHERE id = %(library_id)s FOR UPDATE",
                          "UPDATE /* a comment */ prks.libraries SET name = %(name)s WHERE id = %(library_id)s",
                          "INSERT INTO prks.libraries (id, name) VALUES (%(library_id)s, %(name)s) "
                          "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                          "WITH changed AS (UPDATE prks.libraries SET name = %(name)s WHERE id = %(library_id)s "
                          "RETURNING id) SELECT id FROM changed"):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                library_command(self.db, self.owner, Capability.ADMIN_LIBRARY,
                                lambda scope, statement=statement: scope.execute(statement, {"name": "X"}))
        with self.assertRaises(ValueError):
            library_command(self.db, self.owner, Capability.ADMIN_LIBRARY,
                            lambda scope: scope.execute_unscoped("UPDATE prks.libraries SET name = 'X'"))
        with self.assertRaises(ValueError):  # the update mode is Library administration only
            library_command(self.db, self.owner, Capability.EDIT_LIBRARY, rename("X"), updates_library=True)
        self.assertEqual(self.library_name(), "A")
        # Reading the Library row, or a table whose name merely contains the word, is fine.
        for statement in ("SELECT name FROM prks.libraries WHERE id = %(library_id)s",
                          "SELECT role FROM prks.library_memberships WHERE library_id = %(library_id)s"):
            with self.subTest(statement=statement):
                self.assertIsNotNone(library_command(
                    self.db, self.owner, Capability.ADMIN_LIBRARY,
                    lambda scope, statement=statement: scope.execute(statement).fetchone(),
                ))
        self.assertEqual(library_command(self.db, self.owner, Capability.EDIT_LIBRARY, bump("a1")), 2)

    def test_the_update_mode_keeps_the_fence(self) -> None:
        stale = self.ctx(self.member("Owner 2", "owner"), epoch=2)
        self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED, lambda: library_command(
            self.db, stale, Capability.ADMIN_LIBRARY, rename("X"), updates_library=True
        ))
        with self.connect() as restore:
            restore.execute("SELECT pg_advisory_lock(hashtextextended('prks.library:' || %s, 0))", (self.lib_a,))
            self.assert_refused(Refusal.MAINTENANCE, lambda: library_command(
                self.db, self.owner, Capability.ADMIN_LIBRARY, rename("X"), updates_library=True
            ))
        self.assertEqual(self.library_name(), "A")


class EpochRangeTests(ScopeTestCase):
    """Finding 5: every positive ``bigint`` is an epoch; nothing else is."""

    def test_the_whole_positive_bigint_range_parses(self) -> None:
        for value, expected in (("1", 1), ("1000000000000000000", 10**18), (str(EPOCH_MAX), EPOCH_MAX),
                                (EPOCH_MAX, EPOCH_MAX), (10**18, 10**18)):
            with self.subTest(value=value):
                self.assertEqual(parse_epoch(value), expected)
        for bad in (str(EPOCH_MAX + 1), EPOCH_MAX + 1, "9" * 19, "1" + "0" * 19, "0", 0, "-1", -1,
                    "007", "+7", " 7", "7 ", "7.0", "1e3", "٣", "", None, True, 7.0):
            with self.subTest(value=bad):
                self.assertIsNone(parse_epoch(bad))

    def test_a_19_digit_epoch_fences_commands(self) -> None:
        self.run_sql("UPDATE prks.libraries SET epoch = %s WHERE id = %s", (EPOCH_MAX, self.lib_a))
        editor = self.member("Editor", "editor")
        current = self.ctx(editor, epoch=str(EPOCH_MAX))
        self.assertEqual(library_command(self.db, current, Capability.EDIT_LIBRARY, bump("a1")), 2)
        stale = self.ctx(editor, epoch=str(EPOCH_MAX - 1))
        self.assert_refused(
            Refusal.LIBRARY_EPOCH_CHANGED, lambda: library_command(self.db, stale, Capability.EDIT_LIBRARY, bump("a1"))
        )


class PrecedenceTests(ScopeTestCase):
    """Finding 7: authorization first, then the Library's state; only authorized actors learn it."""

    def test_refusal_precedence(self) -> None:
        reader = self.ctx(self.member("Reader", "reader"), epoch=9)
        editor = self.ctx(self.member("Editor", "editor"), epoch=9)
        signed_out = self.ctx(self.member("Gone", "editor"), epoch=9)
        self.run_sql("UPDATE prks.sessions SET revoked_at = now() WHERE id = %s", (signed_out.session_id,))
        no_epoch = self.ctx(self.member("Editor 2", "editor"), epoch=None)

        def command(ctx: RequestContext) -> Any:
            return lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))

        self.assert_refused(Refusal.NOT_AUTHENTICATED, command(signed_out))  # stale epoch, not revealed
        self.assert_refused(Refusal.FORBIDDEN, command(reader))  # stale epoch, not revealed
        self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED, command(editor))
        self.assert_refused(Refusal.LIBRARY_EPOCH_REQUIRED, command(no_epoch))
        self.run_sql("UPDATE prks.libraries SET maintenance = 'restore' WHERE id = %s", (self.lib_a,))
        self.assert_refused(Refusal.FORBIDDEN, command(reader))  # maintenance, not revealed
        self.assert_refused(Refusal.MAINTENANCE, command(editor))  # maintenance before the epoch


class ContextBoundaryTests(ScopeTestCase):
    """Finding 1: authority comes from live server-side rows, never from a context's say-so."""

    def test_a_hand_made_session_has_no_authority(self) -> None:
        owner = self.member("Owner", "owner")
        live = self.session_for(owner)
        later = datetime.now(timezone.utc) + timedelta(days=1)
        forged = (
            ValidSession(uuid.uuid4(), owner, self.lib_a, later),  # no such session
            ValidSession(live.session_id, self.member("Editor", "editor"), self.lib_a, later),  # someone else's
            ValidSession(live.session_id, owner, self.lib_b, later),  # another Library
        )
        for session in forged:
            ctx = RequestContext.for_session(session, epoch=1)
            with self.subTest(session=session):
                self.assert_refused(
                    Refusal.NOT_AUTHENTICATED,
                    lambda ctx=ctx: library_command(self.db, ctx, Capability.ADMIN_LIBRARY, bump("a1")),
                )
                self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda ctx=ctx: library_query(self.db, ctx, read_all))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 1)

    def test_no_browser_context_holds_instance_administration(self) -> None:
        ctx = self.ctx(self.member("Owner", "owner"))
        self.assert_refused(Refusal.FORBIDDEN, lambda: instance_command(self.db, ctx, lambda tx: None))

    def test_only_the_host_admin_boundary_can_build_an_admin_context(self) -> None:
        """No module the web process can import calls ``for_admin`` or names ``Source.ADMIN``.

        The served app (``prks_app``, ``backend.server`` and everything they
        import, inside functions too) builds contexts only with
        ``for_session`` from a resolved session. ``for_admin`` is for the
        host admin CLI; a call from a served module would make a request
        able to reach instance administration.
        """
        from tests.test_e2e_policy import _served_modules

        offenders = []
        for module in sorted(_served_modules(REPO)):
            path = REPO / (module.replace(".", "/") + ".py")
            if not path.exists():
                path = REPO / module.replace(".", "/") / "__init__.py"
            if module == "backend.domain.identity.request_context" or not path.exists():
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and (
                    node.attr == "for_admin"
                    or (node.attr == "ADMIN" and isinstance(node.value, ast.Name) and node.value.id == "Source")
                ):
                    offenders.append(f"{module}:{node.lineno}")
        self.assertEqual(offenders, [])
        self.assertIn("backend.api.session", _served_modules(REPO))  # the trace does reach the HTTP layer
