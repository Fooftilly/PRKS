"""Admission of every other ``/api/`` route: authorization, epoch, CSRF, proxies, loopback owner.

§9.3, §9.4, §10.4, §13.3; L-ID-1 #626, PR 5/5. Routes are today's SQLite
routes; admission decides with the PR 4/5 helpers before any of them runs.
``POST /api/tags`` stands for a Library mutation, ``GET /api/tags`` for a read.
"""

from __future__ import annotations

from email.message import Message
from typing import Any, Optional

import psycopg

from backend.api import session as session_api
from backend.domain.identity import sessions
from backend.domain.identity.library_scope import LIBRARY_LOCK_KEY_SQL
from backend.domain.identity.login_limiter import TrustedProxies
from backend.domain.identity.web_start import AuthMode
from backend.server import parse_request_origin
from tests.pg.http_fixtures import HttpAuthTestCase, Response


class AdmissionTestCase(HttpAuthTestCase):
    def tags(self) -> int:
        """How many tags the SQLite store holds (it is shared by the class's tests)."""
        return len(self.server_module.db.get_all_tags())

    def assert_refused(self, res: Response, status: int, code: str) -> None:
        self.assertEqual(res.status, status, res.raw)
        self.assertEqual(res.body["error"], code)
        self.assertNotIn(self.lib, res.text)


class AuthorizationTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()

    def test_removed_membership_refuses_the_next_read_and_write(self) -> None:
        cookies = self.session_cookie()
        self.assertEqual(self.add_tag(cookies).status, 200)
        self.run_sql("DELETE FROM prks.library_memberships")
        before = self.tags()
        self.assert_refused(self.call("GET", "/api/tags", cookies=cookies), 401, "not_authenticated")
        self.assert_refused(self.add_tag(cookies), 401, "not_authenticated")
        self.assertEqual(self.tags(), before)

    def test_demoting_an_editor_refuses_the_next_write(self) -> None:
        self.user("Editor", library=self.lib, role="editor")
        cookies = self.session_cookie("Editor")
        self.assertEqual(self.add_tag(cookies).status, 200)
        self.run_sql("UPDATE prks.library_memberships SET role = 'reader' WHERE role = 'editor'")
        before = self.tags()
        self.assert_refused(self.add_tag(cookies), 403, "forbidden")
        self.assertEqual(self.tags(), before)
        self.assertEqual(self.call("GET", "/api/tags", cookies=cookies).status, 200)
        session = self.call("GET", "/api/session", cookies=cookies)
        self.assertEqual(session.body["library"]["role"], "reader")

    def test_library_administration_needs_the_owner(self) -> None:
        self.user("Editor", library=self.lib, role="editor")
        editor = self.session_cookie("Editor")
        owner = self.session_cookie()
        epoch = self.library_epoch()
        self.assert_refused(
            self.call("PATCH", "/api/settings", body={}, cookies=editor, epoch=epoch), 403, "forbidden"
        )
        self.assert_refused(self.call("GET", "/api/backups/download", cookies=editor), 403, "forbidden")
        res = self.call("PATCH", "/api/settings", body={}, cookies=owner, epoch=epoch)
        self.assertNotIn(res.status, (401, 403))

    def test_other_library_stays_invisible(self) -> None:
        other = self.library("Other")
        self.user("Stranger", library=other, role="owner")
        self.run_sql("UPDATE prks.libraries SET epoch = 7 WHERE id = %s", (other,))
        cookies = self.session_cookie()
        res = self.call(
            "GET", f"/api/session?library_id={other}", cookies=cookies,
            headers=[("PRKS-Library-Id", other)],
        )
        self.assertEqual(res.body["library"]["id"], self.lib)
        self.assertNotIn(other, res.text)
        self.assertNotIn("Other", res.text)
        # The other Library's epoch is not this Library's: refused, and nothing says why beyond that.
        stale = self.add_tag(cookies, epoch=7)
        self.assert_refused(stale, 409, "LIBRARY_EPOCH_CHANGED")
        self.assertNotIn(other, stale.text)

    def test_a_session_never_moves_to_another_library(self) -> None:
        other = self.library("Other")
        self.join(self.owner, other, "owner")
        new = self.db.command(lambda tx: sessions.create_session(tx, user_id=self.owner, library_id=self.lib))
        cookies = {"prks_session": new.token.reveal()}
        self.run_sql("DELETE FROM prks.library_memberships WHERE library_id = %s", (self.lib,))
        # Still a member of Other, but this session was for the Library it lost.
        self.assert_refused(self.call("GET", "/api/session", cookies=cookies), 401, "not_authenticated")
        self.assert_refused(self.add_tag(cookies, epoch=1), 401, "not_authenticated")

    def test_a_valid_session_is_not_authorization(self) -> None:
        cookies = self.session_cookie()
        # The membership row is what authorizes: a reader role refuses the write
        # even though the session itself is untouched.
        self.run_sql("UPDATE prks.library_memberships SET role = 'reader'")
        self.assert_refused(self.add_tag(cookies), 403, "forbidden")


class EpochTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()
        self.cookies = self.session_cookie()

    def test_missing_epoch_is_refused_before_writing(self) -> None:
        before = self.tags()
        res = self.call("POST", "/api/tags", body={"name": "no epoch"}, cookies=self.cookies)
        self.assert_refused(res, 400, "LIBRARY_EPOCH_REQUIRED")
        self.assertEqual(self.tags(), before)

    def test_malformed_epochs_are_refused(self) -> None:
        before = self.tags()
        # (Leading and trailing spaces are not part of a header value in HTTP.)
        for value in ("abc", "0", "007", "+1", "-1", "1.0", "1 1", "1e3", "\u00b2", "9" * 19):
            with self.subTest(value=value):
                res = self.call(
                    "POST", "/api/tags", body={"name": "x"}, cookies=self.cookies,
                    headers=[("PRKS-Library-Epoch", value)],
                )
                self.assert_refused(res, 400, "LIBRARY_EPOCH_REQUIRED")
        twice = self.call(
            "POST", "/api/tags", body={"name": "x"}, cookies=self.cookies,
            headers=[("PRKS-Library-Epoch", "1"), ("PRKS-Library-Epoch", "1")],
        )
        self.assert_refused(twice, 400, "LIBRARY_EPOCH_REQUIRED")
        self.assertEqual(self.tags(), before)

    def test_stale_and_current_epochs(self) -> None:
        self.run_sql("UPDATE prks.libraries SET epoch = 2")  # a restore committed
        before = self.tags()
        for stale in (1, 3):
            with self.subTest(epoch=stale):
                self.assert_refused(self.add_tag(self.cookies, epoch=stale), 409, "LIBRARY_EPOCH_CHANGED")
        self.assertEqual(self.tags(), before)
        res = self.add_tag(self.cookies, epoch=2)
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual(res.header("PRKS-Library-Epoch"), ["2"])
        self.assertEqual(self.tags(), before + 1)

    def test_reads_and_non_library_mutations_take_no_epoch(self) -> None:
        res = self.call("GET", "/api/tags", cookies=self.cookies)
        self.assertEqual(res.status, 200)
        self.assertEqual(res.header("PRKS-Library-Epoch"), ["1"])
        report = self.call(
            "POST", "/api/client-errors", body={"message": "x"}, cookies=self.cookies
        )
        self.assertNotIn(report.status, (400, 401, 403, 409))
        self.assertEqual(self.call("POST", "/api/logout", cookies=self.cookies).status, 200)


class MaintenanceTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()
        self.cookies = self.session_cookie()

    def test_maintenance_flag(self) -> None:
        self.run_sql("UPDATE prks.libraries SET maintenance = 'restore'")
        before = self.tags()
        self.assert_refused(self.add_tag(self.cookies), 503, "MAINTENANCE")
        self.assertEqual(self.tags(), before)
        self.assertEqual(self.call("GET", "/api/tags", cookies=self.cookies).status, 200)
        # Only an authorized actor learns about maintenance.
        self.assert_refused(self.add_tag(None), 401, "not_authenticated")

    def test_restore_holding_the_maintenance_lock(self) -> None:
        with psycopg.connect(self.harness.conninfo(self.name), autocommit=True) as conn:
            conn.execute("SELECT pg_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (self.lib,))
            before = self.tags()
            self.assert_refused(self.add_tag(self.cookies), 503, "MAINTENANCE")
            self.assertEqual(self.tags(), before)
            self.assertEqual(self.call("GET", "/api/tags", cookies=self.cookies).status, 200)
            conn.execute("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.lib,))
        # After the restore, the epoch the browser held is stale.
        self.assert_refused(self.add_tag(self.cookies, epoch=1), 409, "LIBRARY_EPOCH_CHANGED")
        self.assertEqual(self.add_tag(self.cookies, epoch=2).status, 200)


class CsrfTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()
        self.cookies = self.session_cookie()

    def tag(self, origin: Any, headers: Optional[list[tuple[str, str]]] = None) -> Response:
        return self.add_tag(self.cookies, origin=origin, headers=headers)

    def test_matching_origin_or_same_origin_fetch(self) -> None:
        self.assertEqual(self.tag(self.origin).status, 200)
        self.assertEqual(self.tag(None, [("Sec-Fetch-Site", "same-origin")]).status, 200)
        self.assertEqual(
            self.tag(self.origin, [("Sec-Fetch-Site", "same-origin")]).status, 200
        )

    def test_cross_origin_and_malformed_requests_are_refused(self) -> None:
        port = self.port
        cases: list[tuple[Any, list[tuple[str, str]]]] = [
            (None, []),
            ("http://evil.example", []),
            (f"http://localhost:{port}", []),  # another origin than the one requested
            ("null", []),
            (f"https://127.0.0.1:{port}", []),  # scheme mismatch
            (f"http://127.0.0.1:{port + 1}", []),
            (f"http://127.0.0.1:{port}/", []),
            (f"http://user@127.0.0.1:{port}", []),
            (self.origin, [("Origin", self.origin)]),  # sent twice
            (self.origin, [("Sec-Fetch-Site", "cross-site")]),
            (self.origin, [("Sec-Fetch-Site", "same-site")]),
            (None, [("Sec-Fetch-Site", "none")]),
            (None, [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "same-origin")]),
        ]
        before = self.tags()
        for origin, headers in cases:
            with self.subTest(origin=origin, headers=headers):
                self.assert_refused(self.tag(origin, headers), 403, "origin_not_allowed")
        self.assertEqual(self.tags(), before)

    def test_identity_routes_are_protected_too(self) -> None:
        attempts = self.attempts()
        res = self.sign_in(origin="http://evil.example")
        self.assert_refused(res, 403, "origin_not_allowed")
        self.assertEqual(self.attempts(), attempts, "a refused cross-origin login is not counted")
        self.assert_refused(
            self.call("POST", "/api/logout", cookies=self.cookies, origin="http://evil.example"),
            403, "origin_not_allowed",
        )
        self.assertEqual(self.call("GET", "/api/session", cookies=self.cookies).status, 200)

    def test_public_https_origin(self) -> None:
        self.serve(self.settings(public_origin=("https", "prks.example", None)))
        login = self.sign_in(host="prks.example", origin="https://prks.example")
        self.assertEqual(login.status, 200, login.raw)
        cookies = {"prks_session": login.cookies["prks_session"]["value"]}
        ok = self.add_tag(cookies, host="prks.example", origin="https://prks.example")
        self.assertEqual(ok.status, 200, ok.raw)
        for origin in ("http://prks.example", "https://prks.example:8443", "https://evil.example"):
            with self.subTest(origin=origin):
                res = self.add_tag(cookies, host="prks.example", origin=origin)
                self.assert_refused(res, 403, "origin_not_allowed")
        # With a public origin, the server's own plain-http origin is not it either.
        direct = self.add_tag(cookies, origin=self.origin)
        self.assert_refused(direct, 403, "origin_not_allowed")


class ProxyTests(AdmissionTestCase):
    SPOOF = [
        ("X-Forwarded-For", "203.0.113.9"),
        ("X-Forwarded-Host", "evil.example"),
        ("X-Forwarded-Proto", "https"),
        ("Forwarded", "for=203.0.113.9;host=evil.example;proto=https"),
    ]

    def test_an_untrusted_peer_cannot_choose_its_address(self) -> None:
        self.serve()
        self.sign_in("Nobody", headers=self.SPOOF)
        self.assertEqual(sorted(self.attempts()), ["acct:nobody", "ip:127.0.0.1"])

    def test_a_trusted_proxy_forwards_the_address(self) -> None:
        self.serve(self.settings(trusted_proxies=TrustedProxies.parse("127.0.0.1")))
        self.sign_in("Nobody", headers=[("X-Forwarded-For", "198.51.100.1, 203.0.113.9")])
        self.assertEqual(sorted(self.attempts()), ["acct:nobody", "ip:203.0.113.9"])

    def test_forwarded_headers_never_set_the_expected_origin(self) -> None:
        for trusted in ("", "127.0.0.1"):
            with self.subTest(trusted=trusted):
                self.serve(self.settings(trusted_proxies=TrustedProxies.parse(trusted)))
                cookies = self.session_cookie()
                for origin in ("https://evil.example", f"https://127.0.0.1:{self.port}"):
                    res = self.add_tag(cookies, origin=origin, headers=self.SPOOF)
                    self.assert_refused(res, 403, "origin_not_allowed")


class LoopbackOwnerTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.auth = self.serve(self.settings(AuthMode.LOOPBACK_OWNER))

    def test_requests_act_as_the_owner_with_the_epoch_fence(self) -> None:
        res = self.call("GET", "/api/session")
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual(res.body["user"]["id"], self.owner)
        self.assertEqual(res.body["auth_mode"], "loopback-owner")
        self.assertEqual(res.cookies, {}, "the owner's token never leaves the process")
        self.assert_refused(self.call("POST", "/api/tags", body={"name": "x"}), 400, "LIBRARY_EPOCH_REQUIRED")
        self.assertEqual(self.add_tag(None).status, 200)
        self.run_sql("UPDATE prks.libraries SET epoch = 2")
        self.assert_refused(self.add_tag(None, epoch=1), 409, "LIBRARY_EPOCH_CHANGED")

    def test_no_sign_in_routes(self) -> None:
        self.assertEqual(self.sign_in().status, 404)
        self.assertEqual(self.call("POST", "/api/logout").status, 404)

    def test_a_revoked_owner_session_is_replaced_and_a_disabled_owner_is_refused(self) -> None:
        self.assertEqual(self.call("GET", "/api/tags").status, 200)
        self.run_sql("UPDATE prks.sessions SET revoked_at = now()")
        self.assertEqual(self.call("GET", "/api/tags").status, 200)
        self.run_sql("UPDATE prks.users SET disabled_at = now()")
        self.assert_refused(self.call("GET", "/api/tags"), 401, "not_authenticated")

    def test_a_non_loopback_peer_is_nobody(self) -> None:
        headers = Message()
        request = session_api.HttpRequest(
            method="GET", path="/api/tags", headers=headers, peer="192.0.2.7",
            request_host=("127.0.0.1", self.port), parse_origin=parse_request_origin,
            read_json=lambda: {},
        )
        outcome = session_api.handle(self.auth, request)
        assert isinstance(outcome, session_api.Reply)
        self.assertEqual((outcome.status, outcome.body["error"]), (401, "not_authenticated"))
        mapped = session_api.handle(self.auth, session_api.HttpRequest(
            method="GET", path="/api/tags", headers=headers, peer="::ffff:127.0.0.1",
            request_host=("127.0.0.1", self.port), parse_origin=parse_request_origin,
            read_json=lambda: {},
        ))
        self.assertIsInstance(mapped, session_api.Admitted)


class SqliteRuntimeTests(AdmissionTestCase):
    """Without PRKS_AUTH the application behaves exactly as before."""

    def test_no_authentication_and_the_old_origin_rule(self) -> None:
        self.serve(auth=None)
        before = self.tags()
        res = self.call("POST", "/api/tags", body={"name": "plain"}, origin=None)
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual(res.header("PRKS-Library-Epoch"), [])
        self.assertEqual(self.tags(), before + 1)
        self.assertEqual(self.call("GET", "/api/tags").status, 200)
        refused = self.call("POST", "/api/tags", body={"name": "x"}, origin="http://evil.example")
        self.assertEqual(refused.status, 403)
        self.assertNotEqual(self.call("POST", "/api/login", body={}).status, 200)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.login_attempts"), [{"n": 0}])
