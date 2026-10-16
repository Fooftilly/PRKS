"""RequestContext and authorize() (§9.4; L-ID-1 #626, PR 4/5)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone

from backend.db.database import Transaction
from backend.domain.identity import sessions
from backend.domain.identity.authorization import (
    ROLE_CAPABILITIES,
    AccessRefused,
    Capability,
    Refusal,
    authorize,
)
from backend.domain.identity.library_scope import instance_command, library_command, library_query
from backend.domain.identity.request_context import (
    DeviceClaimInvalid,
    RequestContext,
    Source,
    parse_epoch,
)
from tests.pg.scope_fixtures import Paused, ScopeTestCase, bump, read_all

ALL = list(Capability)


class CapabilityMatrixTests(ScopeTestCase):
    def granted(self, ctx: RequestContext) -> set[Capability]:
        granted = set()
        for capability in ALL:
            def check(tx: Transaction, capability: Capability = capability) -> None:
                authorize(tx, ctx, capability)

            try:
                self.db.command(check)
            except AccessRefused as exc:
                self.assertIs(exc.refusal, Refusal.FORBIDDEN)
            else:
                granted.add(capability)
        return granted

    def test_roles_grant_exactly_their_capabilities(self) -> None:
        expected = {
            "owner": {Capability.READ_LIBRARY, Capability.EDIT_LIBRARY, Capability.ADMIN_LIBRARY},
            "editor": {Capability.READ_LIBRARY, Capability.EDIT_LIBRARY},
            "reader": {Capability.READ_LIBRARY},
        }
        self.assertEqual({role: set(caps) for role, caps in ROLE_CAPABILITIES.items()}, expected)
        for role, capabilities in expected.items():
            with self.subTest(role=role):
                self.assertEqual(self.granted(self.ctx(self.member(role, role))), capabilities)

    def test_writes_follow_the_role(self) -> None:
        for role, allowed in (("owner", True), ("editor", True), ("reader", False)):
            with self.subTest(role=role):
                ctx = self.ctx(self.member(role, role))
                before = self.revisions()
                if allowed:
                    self.assertIsNotNone(library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1")))
                else:
                    self.assert_refused(
                        Refusal.FORBIDDEN,
                        lambda ctx=ctx: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1")),
                    )
                    self.assertEqual(self.revisions(), before)
                self.assertEqual(len(library_query(self.db, ctx, read_all)), 2)

    def test_admin_instance_is_never_granted_to_a_browser_or_a_worker(self) -> None:
        owner = self.ctx(self.member("Owner", "owner"))
        job_id, lease_id = self.job(self.lib_a, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        for ctx in (owner, worker):
            with self.subTest(source=ctx.source):
                self.assert_refused(Refusal.FORBIDDEN, lambda ctx=ctx: instance_command(self.db, ctx, lambda tx: 1))
        self.assertEqual(instance_command(self.db, RequestContext.for_admin(), lambda tx: 1), 1)
        self.assertIn(Capability.ADMIN_INSTANCE, self.granted(RequestContext.for_admin(library_id=self.lib_a)))

    def test_workers_read_and_edit_their_own_library_only(self) -> None:
        job_id, lease_id = self.job(self.lib_a, 1)
        worker = RequestContext.for_job(self.db, job_id, lease_id)
        assert worker is not None
        self.assertEqual((worker.source, worker.library_id, worker.epoch), (Source.WORKER, self.lib_a, 1))
        self.assertEqual(self.granted(worker), {Capability.READ_LIBRARY, Capability.EDIT_LIBRARY})


class IdentityStateTests(ScopeTestCase):
    def test_unknown_disabled_and_deleted_users_fail_closed(self) -> None:
        editor = self.member("Editor", "editor")
        ctx = self.ctx(editor)
        forged = RequestContext.for_session(
            sessions.ValidSession(ctx.session_id or uuid.uuid4(), "U-" + "0" * 32, self.lib_a,
                                  datetime.now(timezone.utc)),
            epoch=1,
        )
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_query(self.db, forged, read_all))

        self.run_sql("UPDATE prks.users SET disabled_at = now() WHERE id = %s", (editor,))
        before = self.revisions()
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_query(self.db, ctx, read_all))
        self.assert_refused(
            Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        )
        self.run_sql("DELETE FROM prks.users WHERE id = %s", (editor,))
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_query(self.db, ctx, read_all))
        self.assertEqual(self.revisions(), before)

    def test_signed_out_and_expired_sessions_fail_closed(self) -> None:
        editor = self.member("Editor", "editor")
        signed_out, expired = self.ctx(editor), self.ctx(editor)
        self.run_sql("UPDATE prks.sessions SET revoked_at = now() WHERE id = %s", (signed_out.session_id,))
        self.run_sql("UPDATE prks.sessions SET expires_at = now() - interval '1 second' WHERE id = %s",
                     (expired.session_id,))
        for ctx in (signed_out, expired):
            self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda ctx=ctx: library_query(self.db, ctx, read_all))

    def test_removing_a_membership_refuses_the_existing_session(self) -> None:
        editor = self.member("Editor", "editor")
        ctx = self.ctx(editor)
        library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        self.run_sql("DELETE FROM prks.library_memberships WHERE user_id = %s", (editor,))
        before = self.revisions()
        self.assert_refused(Refusal.NOT_AUTHENTICATED, lambda: library_query(self.db, ctx, read_all))
        self.assert_refused(
            Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        )
        self.assertEqual(self.revisions(), before)

    def test_demoting_an_editor_refuses_the_next_write_but_not_reads(self) -> None:
        editor = self.member("Editor", "editor")
        ctx = self.ctx(editor)
        library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        self.run_sql("UPDATE prks.library_memberships SET role = 'reader' WHERE user_id = %s", (editor,))
        self.assert_refused(
            Refusal.FORBIDDEN, lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        )
        self.assertEqual(len(library_query(self.db, ctx, read_all)), 2)

    def test_a_sign_out_waits_for_an_authorized_write_in_flight(self) -> None:
        editor = self.member("Editor", "editor")
        ctx = self.ctx(editor)
        paused = Paused()
        writer = paused.start(lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, paused.hold(bump("a1"))))
        self.assertTrue(paused.inside.wait(30))
        with self.connect() as other:
            sign_out = Paused()
            signer = sign_out.start(lambda: other.execute(
                "UPDATE prks.sessions SET revoked_at = now() WHERE id = %s", (ctx.session_id,)
            ))
            self.wait_until(self.waiting_on_lock(other), "the sign-out to wait for the write")
            paused.release.set()
            writer.join(30)
            signer.join(30)
        self.assertEqual((paused.error, sign_out.error, paused.result), ([], [], [2]))
        self.assert_refused(
            Refusal.NOT_AUTHENTICATED, lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        )

    def test_a_demotion_waits_for_an_authorized_write_in_flight(self) -> None:
        editor = self.member("Editor", "editor")
        ctx = self.ctx(editor)
        paused = Paused()
        writer = paused.start(lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, paused.hold(bump("a1"))))
        self.assertTrue(paused.inside.wait(30))
        with self.connect() as admin:
            demote = Paused()
            demoter = demote.start(lambda: admin.execute(
                "UPDATE prks.library_memberships SET role = 'reader' WHERE user_id = %s", (editor,)
            ))
            self.wait_until(self.waiting_on_lock(admin), "the demotion to wait for the write")
            paused.release.set()
            writer.join(30)
            demoter.join(30)
        self.assertEqual((paused.error, demote.error), ([], []))
        self.assertEqual(self.revisions()[(self.lib_a, "a1")], 2)  # the write committed first
        self.assert_refused(
            Refusal.FORBIDDEN, lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
        )


class EscalationTests(ScopeTestCase):
    def test_a_context_cannot_be_assembled_from_request_values(self) -> None:
        with self.assertRaises(TypeError):
            RequestContext(Source.ADMIN, None, self.lib_a, 1)
        ctx = self.ctx(self.member("Reader", "reader"))
        self.assertFalse(hasattr(ctx, "role"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.library_id = self.lib_b  # type: ignore[misc]
        # Nor can a valid context be copied with another source, actor, Library or claim.
        for change in ({"source": Source.ADMIN}, {"source": Source.WORKER}, {"library_id": self.lib_b},
                       {"user_id": "U-" + "0" * 32}, {"epoch": 2}, {"device_id": None if ctx.device_id else uuid.uuid4()},
                       {"session_id": uuid.uuid4()}, {"job_id": uuid.uuid4()}, {"lease_id": uuid.uuid4()}):
            with self.subTest(change=change), self.assertRaises(TypeError):
                dataclasses.replace(ctx, **change)

    def test_a_session_for_one_library_cannot_act_on_another(self) -> None:
        user = self.member("Both", "owner")
        self.join(user, self.lib_b, "owner")
        ctx_a = self.ctx(user, self.lib_a)
        session_b = self.session_for(user, self.lib_b)
        # Session A's id with Library B's claim: the session row names A.
        forged = RequestContext.for_session(dataclasses.replace(session_b, session_id=ctx_a.session_id), epoch=1)
        before = self.revisions()
        self.assert_refused(
            Refusal.NOT_AUTHENTICATED,
            lambda: library_command(self.db, forged, Capability.EDIT_LIBRARY, bump("b1")),
        )
        # Through its own Library, B's record is simply not there.
        self.assertIsNone(library_command(self.db, ctx_a, Capability.EDIT_LIBRARY, bump("b1")))
        self.assertEqual(self.revisions(), before)
        self.assertEqual([scope for scope, _rev in library_query(self.db, ctx_a, read_all)], ["a1", "a2"])

    def test_a_non_member_session_cannot_reach_another_library(self) -> None:
        outsider = self.member("Outsider", "owner", self.lib_b)
        ctx_b = self.ctx(outsider, self.lib_b)
        before = self.revisions()
        for scope_id in ("a1", "a2"):
            self.assertIsNone(library_command(self.db, ctx_b, Capability.EDIT_LIBRARY, bump(scope_id)))
        self.assertEqual([scope for scope, _rev in library_query(self.db, ctx_b, read_all)], ["b1"])
        self.assertEqual({k: v for k, v in self.revisions().items() if k[0] == self.lib_a},
                         {k: v for k, v in before.items() if k[0] == self.lib_a})

    def test_a_device_must_be_the_users_own(self) -> None:
        editor = self.member("Editor", "editor")
        other = self.member("Other", "editor")
        own = self.ctx(editor, device_id=str(self.device(editor)))
        self.assertIsNotNone(library_command(self.db, own, Capability.EDIT_LIBRARY, bump("a1")))
        for device in (self.device(other), uuid.uuid4()):
            ctx = self.ctx(editor, device_id=device)
            self.assert_refused(
                Refusal.FORBIDDEN, lambda ctx=ctx: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))
            )
        with self.assertRaises(DeviceClaimInvalid):
            self.ctx(editor, device_id="not-a-uuid")

    def test_epoch_claims_are_parsed_strictly(self) -> None:
        for value, expected in ((1, 1), ("7", 7), (" 7 ", None), (None, None), (0, None), (-1, None),
                                (True, None), ("1.0", None), ("abc", None), ("", None), ("٣", None),
                                ("9" * 19, None), (1.0, None)):
            with self.subTest(value=value):
                self.assertEqual(parse_epoch(value), expected)

    def test_refusals_leak_nothing(self) -> None:
        reader = self.member("Reader Person", "reader")
        ctx = self.ctx(reader)
        refused = [
            self.assert_refused(Refusal.FORBIDDEN,
                                lambda: library_command(self.db, ctx, Capability.EDIT_LIBRARY, bump("a1"))),
            self.assert_refused(Refusal.LIBRARY_EPOCH_CHANGED,
                                lambda: library_command(self.db, self.ctx(self.member("E", "editor"), epoch=9),
                                                        Capability.EDIT_LIBRARY, bump("a1"))),
        ]
        for exc in refused:
            self.assertNotIn("Reader Person", str(exc))
            self.assertNotIn(str(ctx.session_id), str(exc))
        self.assert_private(self.logs.getvalue())
        self.assertNotIn(str(ctx.session_id), self.logs.getvalue())
