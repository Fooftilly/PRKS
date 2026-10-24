"""HTTP sign-in, sessions and logout (§9.3, §13.3; L-ID-1 #626, PR 5/5).

Against a real PRKS server (``tests/pg/http_fixtures.py``): ``POST
/api/login``, ``GET /api/session`` and ``POST /api/logout`` use the PR 3/5
login service and sessions, set the §9.3 cookies, throttle with 429 and
``Retry-After``, and never put a token, a hash or a password in a body,
a header other than its own ``Set-Cookie``, or a log line.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from backend.domain.identity.login_limiter import LoginLimits
from backend.domain.identity.secret_tokens import LoginDeviceToken, SessionToken
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.identity_fixtures import PASSWORD

INVALID = {"error": "invalid_credentials"}


class SessionFlowTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()

    def assert_cookie(self, res: Response, name: str, path: str, *, secure: bool = False) -> dict[str, str]:
        cookie = res.cookies[name]
        self.assertEqual(cookie["path"], path)
        self.assertIn("httponly", cookie)
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual("secure" in cookie, secure)
        return cookie

    def test_login_session_and_logout(self) -> None:
        res = self.sign_in()
        self.assertEqual(res.status, 200, res.raw)
        session = self.assert_cookie(res, "prks_session", "/")
        device = self.assert_cookie(res, "prks_login_device", "/api/login")
        self.assertEqual(int(device["max-age"]), 365 * 24 * 3600)
        self.assertAlmostEqual(int(session["max-age"]), 30 * 24 * 3600, delta=60)
        self.assertEqual(res.header("Cache-Control"), ["no-store"])
        body = res.body
        self.assertTrue(body["signed_in"])
        self.assertEqual(body["session"]["user"]["id"], self.owner)
        self.assertEqual(body["session"]["library"], {"id": self.lib, "name": "Library", "role": "owner", "epoch": 1})
        self.assertEqual(body["session"]["instance_id"], self.instance_id)
        self.assertEqual(res.header("PRKS-Library-Epoch"), ["1"])

        cookies = {"prks_session": session["value"]}
        current = self.call("GET", "/api/session", cookies=cookies)
        self.assertEqual(current.status, 200)
        self.assertEqual(current.body["library"]["epoch"], 1)
        self.assertEqual(current.header("PRKS-Library-Epoch"), ["1"])

        out = self.call("POST", "/api/logout", cookies=cookies)
        self.assertEqual(out.status, 200)
        self.assertEqual(out.cookies["prks_session"]["value"], "")
        self.assertEqual(out.cookies["prks_session"]["max-age"], "0")
        revoked = self.run_sql("SELECT revoked_at IS NOT NULL AS revoked FROM prks.sessions")
        self.assertEqual(revoked, [{"revoked": True}])
        self.assertEqual(self.call("GET", "/api/session", cookies=cookies).status, 401)
        self.assertEqual(self.call("GET", "/api/tags", cookies=cookies).status, 401)

    def test_signing_out_without_a_session_is_not_an_error(self) -> None:
        res = self.call("POST", "/api/logout")
        self.assertEqual(res.status, 200)
        self.assertEqual(res.cookies["prks_session"]["max-age"], "0")

    def test_renewal_moves_the_expiry_and_the_cookie_lifetime(self) -> None:
        cookies = self.session_cookie()
        self.run_sql(
            "UPDATE prks.sessions SET last_used_at = now() - interval '2 days', "
            "expires_at = now() + interval '1 day'"
        )
        res = self.call("GET", "/api/session", cookies=cookies)
        self.assertEqual(res.status, 200)
        left = self.run_sql("SELECT expires_at - now() AS left FROM prks.sessions")[0]["left"]
        self.assertGreater(left, timedelta(days=29))
        refreshed = res.cookies["prks_session"]
        self.assertEqual(refreshed["value"], cookies["prks_session"])
        self.assertAlmostEqual(int(refreshed["max-age"]), 30 * 24 * 3600, delta=60)

    def test_each_login_rotates_the_device_token(self) -> None:
        first = self.sign_in().cookies["prks_login_device"]["value"]
        second = self.sign_in(cookies={"prks_login_device": first})
        self.assertEqual(second.status, 200)
        rotated = second.cookies["prks_login_device"]["value"]
        self.assertNotEqual(rotated, first)
        stored = [bytes(r["token_sha256"]) for r in self.run_sql("SELECT token_sha256 FROM prks.login_device_tokens")]
        self.assertIn(LoginDeviceToken(rotated).digest, stored)
        self.assertNotIn(LoginDeviceToken(first).digest, stored)

    def test_a_new_login_revokes_the_session_the_browser_held(self) -> None:
        old = self.session_cookie()
        self.assertEqual(self.sign_in(cookies=old).status, 200)
        self.assertEqual(self.call("GET", "/api/session", cookies=old).status, 401)

    def test_login_route_shape(self) -> None:
        self.assertEqual(self.call("GET", "/api/login").status, 405)
        self.assertEqual(self.call("POST", "/api/login", body=["Owner", PASSWORD]).status, 400)
        self.assertEqual(self.call("POST", "/api/session", body={}).status, 405)
        res = self.call("POST", "/api/login", headers=[("Content-Type", "text/plain")])
        self.assertEqual(res.status, 415)
        self.assertEqual(self.attempts(), {})

    def test_secure_cookies_behind_an_https_public_origin(self) -> None:
        self.serve(self.settings(public_origin=("https", "prks.example", None)))
        res = self.sign_in(host="prks.example", origin="https://prks.example")
        self.assertEqual(res.status, 200, res.raw)
        self.assert_cookie(res, "prks_session", "/", secure=True)
        self.assert_cookie(res, "prks_login_device", "/api/login", secure=True)
        out = self.call(
            "POST", "/api/logout", host="prks.example", origin="https://prks.example",
            cookies={"prks_session": res.cookies["prks_session"]["value"]},
        )
        self.assert_cookie(out, "prks_session", "/", secure=True)


class NonEnumerationTests(HttpAuthTestCase):
    """Every refused sign-in looks the same and costs one Argon2id check."""

    def setUp(self) -> None:
        super().setUp()
        self.user("Disabled", disabled=True)
        self.user("NoPassword", password=None)
        self.user("Orphan", library=None)
        self.serve()

    def test_refusals_are_indistinguishable(self) -> None:
        cases = [
            ("Owner", "a wrong passphrase here"),
            ("Nobody", PASSWORD),
            ("Disabled", PASSWORD),
            ("NoPassword", PASSWORD),
            ("Orphan", PASSWORD),
        ]
        for login, password in cases:
            with self.subTest(login=login):
                before = self.hasher.verifies
                res = self.sign_in(login, password)
                self.assertEqual((res.status, res.body), (401, INVALID))
                self.assertEqual(res.cookies, {})
                self.assertEqual(self.hasher.verifies - before, 1)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])

    def test_unusable_input_is_refused_like_a_wrong_password(self) -> None:
        for body in ({"login": "Owner"}, {"login": 7, "password": PASSWORD}, {"login": "", "password": ""}):
            with self.subTest(body=body):
                res = self.call("POST", "/api/login", body=body)
                self.assertEqual((res.status, res.body), (401, INVALID))


class ThrottlingTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve(self.settings(limits=LoginLimits(account=2, address=3, device=2)))

    def assert_throttled(self, res: Response) -> None:
        self.assertEqual((res.status, res.body), (429, {"error": "too_many_attempts"}))
        (retry,) = res.header("Retry-After")
        self.assertTrue(1 <= int(retry) <= 15 * 60, retry)
        self.assertEqual(res.cookies, {})

    def test_account_limit(self) -> None:
        for _ in range(2):
            self.assertEqual(self.sign_in(password="wrong passphrase!").status, 401)
        before = self.hasher.verifies
        self.assert_throttled(self.sign_in())  # even the right password
        self.assertEqual(self.hasher.verifies, before, "no password check over the limit")

    def test_address_limit(self) -> None:
        for name in ("a1", "a2", "a3"):
            self.assertEqual(self.sign_in(name).status, 401)
        self.assert_throttled(self.sign_in("a4"))
        self.assertEqual(self.attempts()["ip:127.0.0.1"], 4)

    def test_known_device_signs_in_past_the_account_limit_and_has_its_own(self) -> None:
        device = self.sign_in().cookies["prks_login_device"]["value"]
        for _ in range(3):
            self.sign_in(password="wrong passphrase!")
        self.assert_throttled(self.sign_in())
        known = self.sign_in(cookies={"prks_login_device": device})
        self.assertEqual(known.status, 200, known.raw)
        device = known.cookies["prks_login_device"]["value"]
        for _ in range(2):
            res = self.sign_in(password="wrong passphrase!", cookies={"prks_login_device": device})
            self.assertEqual(res.status, 401)
        self.assert_throttled(self.sign_in(cookies={"prks_login_device": device}))


class DeviceTokenConcurrencyTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.serve()
        self.device = self.sign_in().cookies["prks_login_device"]["value"]
        self.run_sql("DELETE FROM prks.sessions")

    def test_two_logins_with_one_device_token_sign_in_once(self) -> None:
        # Both logins finish their password check before either commits.
        barrier = threading.Barrier(2, timeout=30)
        self.hasher.on_verify = barrier.wait
        results: list[Response] = []
        lock = threading.Lock()

        def attempt() -> None:
            res = self.sign_in(cookies={"prks_login_device": self.device})
            with lock:
                results.append(res)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(r.status for r in results), [200, 401])
        refused = next(r for r in results if r.status == 401)
        self.assertEqual((refused.body, refused.cookies), (INVALID, {}))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 1}])
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.login_device_tokens"), [{"n": 1}])

    def test_a_token_revoked_during_the_password_check_signs_nothing_in(self) -> None:
        def revoke() -> None:
            self.hasher.on_verify = None
            self.run_sql("DELETE FROM prks.login_device_tokens")

        self.hasher.on_verify = revoke
        res = self.sign_in(cookies={"prks_login_device": self.device})
        self.assertEqual((res.status, res.body, res.cookies), (401, INVALID, {}))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])
        # Signing in again is a new browser: it works and gets a new token.
        again = self.sign_in(cookies={"prks_login_device": self.device})
        self.assertEqual(again.status, 200)
        self.assertNotEqual(again.cookies["prks_login_device"]["value"], self.device)


class SessionStateTests(HttpAuthTestCase):
    """A session that is missing, malformed, expired, revoked or disabled is 401 everywhere."""

    def setUp(self) -> None:
        super().setUp()
        self.serve()

    def assert_unauthenticated(self, cookies: Any) -> None:
        for method, path in (("GET", "/api/session"), ("GET", "/api/tags"), ("POST", "/api/tags")):
            with self.subTest(method=method, path=path):
                if method == "POST":
                    res = self.add_tag(cookies)
                else:
                    res = self.call(method, path, cookies=cookies)
                self.assertEqual(res.status, 401, res.raw)
                self.assertEqual(res.body["error"], "not_authenticated")

    def test_missing_and_malformed(self) -> None:
        self.assert_unauthenticated(None)
        self.assert_unauthenticated({"prks_session": "not-a-token"})
        self.assert_unauthenticated({"prks_session": SessionToken.new().reveal()})

    def test_expired(self) -> None:
        cookies = self.session_cookie()
        self.run_sql("UPDATE prks.sessions SET expires_at = now() - interval '1 second'")
        self.assert_unauthenticated(cookies)

    def test_revoked(self) -> None:
        cookies = self.session_cookie()
        self.run_sql("UPDATE prks.sessions SET revoked_at = now()")
        self.assert_unauthenticated(cookies)

    def test_disabled_user(self) -> None:
        cookies = self.session_cookie()
        self.run_sql("UPDATE prks.users SET disabled_at = now()")
        self.assert_unauthenticated(cookies)

    def test_a_cookie_sent_twice_is_no_session(self) -> None:
        token = self.session_cookie()["prks_session"]
        res = self.call("GET", "/api/session", headers=[("Cookie", f"prks_session={token}; prks_session={token}")])
        self.assertEqual(res.status, 401)


class PrivacyTests(HttpAuthTestCase):
    """No secret in a response body, a non-cookie header, or a log line."""

    def test_no_secret_leaves_through_responses_or_logs(self) -> None:
        self.serve(self.settings(limits=LoginLimits(account=2)))
        responses = [self.sign_in()]
        token = responses[0].cookies["prks_session"]["value"]
        device = responses[0].cookies["prks_login_device"]["value"]
        cookies = {"prks_session": token}
        responses += [
            self.call("GET", "/api/session", cookies=cookies),
            self.add_tag(cookies),
            self.sign_in(password="wrong passphrase!"),
            self.sign_in(password="wrong passphrase!"),
            self.sign_in(password="wrong passphrase!"),  # throttled
            self.sign_in("Nobody", "some passphrase!"),
            self.call("POST", "/api/logout", cookies=cookies),
            self.call("GET", "/api/session", cookies=cookies),
        ]
        stored_hash = self.run_sql("SELECT password_hash FROM prks.users")[0]["password_hash"]
        secrets = [PASSWORD, "wrong passphrase!", token, device, stored_hash, "argon2id"]
        logs = self.logs.getvalue()
        for secret in secrets:
            self.assertNotIn(secret, logs)
            for res in responses:
                self.assertNotIn(secret, res.text)
        # Only the sign-in sets the device cookie; the session cookie only ever
        # carries this browser's own token, or is cleared.
        for res in responses[1:]:
            self.assertNotIn("prks_login_device", res.cookies)
            self.assertIn(res.cookies.get("prks_session", {}).get("value", ""), (token, ""))
