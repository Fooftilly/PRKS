"""Browser first-run setup (#612): the setup phase, its HTTP routes and the waiting web process.

A web process that waits for setup serves only the setup page and
``/api/setup`` (``backend.api.setup``). The browser sets the instance up with
the operator's bootstrap token through the same idempotent bootstrap the CLI
runs; without a valid token nothing is written, and once setup is ``ready``
nothing opens it again.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import socket
import stat
import threading
import time
import unittest
from typing import Any, Optional
from unittest import mock

import prks_app
from backend.domain.identity import bootstrap_tokens, root_pairing, sessions
from backend.domain.identity.web_setup import (
    STATE_BOOTSTRAPPING,
    STATE_READY,
    STATE_UNCONFIGURED,
    SetupBusy,
    SetupInputError,
    SetupPhase,
    TokenAttempts,
    TooManyAttempts,
    parse_request,
    read_state,
)
from backend.domain.identity.web_start import settings_from_env
from backend.server import parse_request_origin, start_setup_server
from tests.pg.test_root_pairing import Crash, RootPairingTestCase, crash_at

PASSWORD = "correct horse battery"


def body(token: str, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"token": token, "login": "alice", "password": PASSWORD}
    data.update(extra)
    return data


class ParseRequestTests(unittest.TestCase):
    TOKEN = "a" * 43

    def refused(self, data: Any, reason: str, field_name: str) -> None:
        with self.assertRaises(SetupInputError) as caught:
            parse_request(data)
        self.assertEqual((caught.exception.reason, caught.exception.field), (reason, field_name))

    def test_defaults_follow_the_cli(self) -> None:
        got = parse_request(body(f"  {self.TOKEN}\n", login="  alice "))
        self.assertEqual((got.login, got.display_name, got.library_name), ("alice", "alice", "Library"))
        self.assertEqual(got.token.reveal(), self.TOKEN)
        self.assertNotIn(PASSWORD, repr(got))
        self.assertNotIn(self.TOKEN, repr(got))

    def test_unusable_input_names_its_field(self) -> None:
        self.refused([], "invalid_request", "body")
        self.refused({"login": "a", "password": "p"}, "required", "token")
        self.refused(body("short"), "bootstrap_token_malformed", "token")
        self.refused(body(self.TOKEN, login=" "), "required", "login")
        self.refused(body(self.TOKEN, login=5), "invalid_request", "login")
        self.refused(body(self.TOKEN, password=""), "required", "password")
        self.refused(body(self.TOKEN, password="x" * 1025), "too_long", "password")
        self.refused(body(self.TOKEN, library_name="L" * 201), "too_long", "library_name")
        self.refused(body(self.TOKEN, display_name="a\x00b"), "invalid", "display_name")


class TokenAttemptsTests(unittest.TestCase):
    def test_a_window_of_refusals_then_retry_after(self) -> None:
        now = [100.0]
        attempts = TokenAttempts(limit=2, window_s=60, clock=lambda: now[0])
        attempts.check()
        attempts.refused()
        attempts.refused()
        with self.assertRaises(TooManyAttempts) as caught:
            attempts.check()
        self.assertEqual(caught.exception.retry_after, 60)
        now[0] += 60
        attempts.check()


class AfterSetupTests(unittest.TestCase):
    """``/api/setup`` on the normal server, once the root is claimed (#612)."""

    def answer(self, method: str, path: str = "/api/setup") -> Any:
        from email.message import Message

        from backend.api.session import HttpRequest
        from backend.api.setup import handle_after_setup

        def unread() -> object:
            raise AssertionError("the body is never read after setup")

        return handle_after_setup(HttpRequest(
            method=method, path=path, headers=Message(), peer="127.0.0.1",
            request_host=("127.0.0.1", 8000), scheme="http", parse_origin=parse_request_origin,
            read_json=unread,
        ))

    def test_ready_for_a_read_and_already_ready_for_a_post(self) -> None:
        for method in ("GET", "HEAD"):
            reply = self.answer(method)
            self.assertEqual((reply.status, reply.body), (200, {"state": "ready"}))
        reply = self.answer("POST")
        self.assertEqual((reply.status, reply.body), (409, {"error": "already_ready"}))
        reply = self.answer("DELETE")
        self.assertEqual(reply.status, 405)
        self.assertIsNone(self.answer("GET", "/api/setup/x"))


class SetupTestCase(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        # The configuration directory (bootstrap-token) stays inside the test's tmp.
        self.environ = {"XDG_CONFIG_HOME": os.path.join(self.tmp, "config")}

    def settings(self, env: Optional[dict[str, str]] = None) -> Any:
        return settings_from_env(
            {"PRKS_AUTH": "session", **(env or {})}, testing=True, host="127.0.0.1",
            parse_origin=parse_request_origin,
        )

    def phase(self, name: str, root: str, **kwargs: Any) -> SetupPhase:
        kwargs.setdefault("environ", self.environ)
        return SetupPhase(
            self.db(name), kwargs.pop("settings", None) or self.settings(), root=root,
            lease=self.lease(root), testing=True, **kwargs,
        )


class SetupPhaseTests(SetupTestCase):
    def test_setup_creates_the_owner_library_and_a_session(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        phase = self.phase(name, root)
        self.assertEqual(phase.state(), STATE_UNCONFIGURED)
        done = phase.complete(parse_request(body(token.reveal(), library_name="Research")))
        self.assertTrue(done.created)
        self.assertTrue(phase.done.is_set())
        self.assertEqual(read_state(self.db(name)), STATE_READY)
        owner = self.db(name).query(lambda tx: tx.execute(
            "SELECT u.login_name, u.display_name, l.name, m.role FROM prks.library_memberships m "
            "JOIN prks.users u ON u.id = m.user_id JOIN prks.libraries l ON l.id = m.library_id"
        ).fetchall())
        self.assertEqual([tuple(r.values()) for r in owner], [("alice", "alice", "Research", "owner")])
        assert done.session is not None
        valid = sessions.resolve_session(self.db(name), done.session.token.reveal())
        assert valid is not None
        self.assertEqual(valid.library_id, done.session.library_id)
        # The web process then starts as after a CLI setup.
        self.assertIs(self.start(name, root).status, root_pairing.StartStatus.READY)

    def test_a_wrong_token_writes_nothing_and_is_rate_limited(self) -> None:
        name, root = self.database(), self.new_root()
        self.issue(name)
        phase = self.phase(name, root, attempts=TokenAttempts(limit=2))
        other = bootstrap_tokens.new_token().reveal()
        for _ in range(2):
            self.refused("bootstrap_token_invalid", phase.complete, parse_request(body(other)))
        with self.assertRaises(TooManyAttempts):
            phase.complete(parse_request(body(other)))
        self.assertIsNone(self.instance(name))
        self.assertFalse(phase.done.is_set())

    def test_an_expired_token_is_refused(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        self.db(name).command(lambda tx: tx.execute(
            "UPDATE prks.bootstrap_tokens SET created_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 minute'"))
        self.refused("bootstrap_token_expired", self.phase(name, root).complete,
                     parse_request(body(token.reveal())))

    def test_a_ready_instance_refuses_every_token(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        self.bootstrap(name, root, token=token)
        phase = self.phase(name, root)
        self.assertEqual(phase.state(), STATE_READY)
        # The token that completed setup too: the browser is told it is ready.
        self.refused("already_ready", phase.complete, parse_request(body(token.reveal())))

    def test_one_setup_at_a_time(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        phase = self.phase(name, root)
        phase._running.acquire()
        try:
            with self.assertRaises(SetupBusy):
                phase.complete(parse_request(body(token.reveal())))
        finally:
            phase._running.release()
        self.assertTrue(phase.complete(parse_request(body(token.reveal()))).created)

    def test_an_interrupted_setup_resumes_from_the_browser(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name)
        with self.assertRaises(Crash):
            self.bootstrap(name, root, token=token, faults=crash_at(root_pairing.FAULT_AFTER_CLAIM))
        phase = self.phase(name, root)
        self.assertEqual(phase.state(), STATE_BOOTSTRAPPING)
        self.assertTrue(phase.complete(parse_request(body(token.reveal()))).created)
        self.assertEqual(read_state(self.db(name)), STATE_READY)

    def test_the_used_configuration_directory_token_file_is_deleted(self) -> None:
        name, root = self.database(), self.new_root()
        token = bootstrap_tokens.new_token()
        path = bootstrap_tokens.default_token_file(self.environ)
        os.makedirs(os.path.dirname(path), mode=0o700)
        bootstrap_tokens.write_token_file(token, path, data_root=root)
        bootstrap_tokens.issue_token(self.db(name), token=token)
        self.phase(name, root).complete(parse_request(body(token.reveal())))
        self.assertFalse(os.path.exists(path))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class SetupHttpTests(SetupTestCase):
    def serve(self, phase: SetupPhase) -> int:
        port = free_port()
        server = start_setup_server(phase, port=port, host="127.0.0.1")
        self.addCleanup(server.stop)
        return port

    def request(
        self, port: int, method: str, path: str, data: Any = None, headers: Optional[dict[str, str]] = None
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            payload = None if data is None else json.dumps(data).encode()
            sent = {"Host": f"127.0.0.1:{port}", **(headers or {})}
            if payload is not None:
                sent["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=sent)
            response = conn.getresponse()
            raw = response.read()
            got = {k.lower(): v for k, v in response.getheaders()}
            got["set-cookie"] = "\n".join(response.headers.get_all("Set-Cookie") or [])
            return response.status, got, raw
        finally:
            conn.close()

    def post(self, port: int, data: Any, **headers: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        origin = {"Origin": f"http://127.0.0.1:{port}", **headers}
        status, got, raw = self.request(port, "POST", "/api/setup", data, origin)
        return status, json.loads(raw) if raw else {}, got

    def test_the_setup_phase_serves_the_setup_page_and_setup_routes_only(self) -> None:
        name, root = self.database(), self.new_root()
        port = self.serve(self.phase(name, root))
        for path in ("/", "/index.html", "/works/123"):
            with self.subTest(path=path):
                status, headers, raw = self.request(port, "GET", path)
                self.assertEqual(status, 200)
                self.assertIn(b'id="prks-setup-root"', raw)
                self.assertEqual(headers["cache-control"], "no-store, max-age=0")
        status, _, raw = self.request(port, "GET", "/api/setup")
        self.assertEqual((status, json.loads(raw)), (200, {"state": "unconfigured"}))
        for method, path in (("GET", "/api/session"), ("GET", "/api/works"), ("POST", "/api/login")):
            with self.subTest(path=path):
                status, _, raw = self.request(port, method, path, {} if method == "POST" else None)
                self.assertEqual((status, json.loads(raw)), (503, {"error": "setup_pending"}))
        self.assertEqual(self.request(port, "GET", "/healthz")[0], 200)
        self.assertEqual(self.request(port, "GET", "/readyz")[0], 503)
        # An untrusted Host is refused before anything else.
        self.assertEqual(self.request(port, "GET", "/api/setup", headers={"Host": "evil.example"})[0], 421)

    def test_browser_setup_signs_the_owner_in(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name).reveal()
        phase = self.phase(name, root)
        port = self.serve(phase)
        # A cross-site request is refused before the token is looked at.
        status, got, _ = self.post(port, body(token), Origin="http://evil.example")
        self.assertEqual((status, got), (403, {"error": "origin_not_allowed"}))
        status, got, _ = self.post(port, body(bootstrap_tokens.new_token().reveal()))
        self.assertEqual((status, got), (403, {"error": "bootstrap_token_invalid"}))
        status, got, _ = self.post(port, body("not-a-token"))
        self.assertEqual((status, got["error"]), (400, "bootstrap_token_malformed"))
        status, got, _ = self.post(port, body(token, login=""))
        self.assertEqual((status, got), (400, {"error": "invalid_request", "field": "login", "reason": "required"}))
        self.assertIsNone(self.instance(name))

        status, got, headers = self.post(port, body(token))
        self.assertEqual((status, got), (200, {"state": "ready", "created": True, "signed_in": True}))
        cookie = headers["set-cookie"]
        self.assertIn("prks_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(token, json.dumps(got) + json.dumps({k: v for k, v in headers.items() if k != "set-cookie"}))
        self.assertTrue(phase.done.is_set())
        status, _, raw = self.request(port, "GET", "/api/setup")
        self.assertEqual((status, json.loads(raw)), (200, {"state": "ready"}))
        # Nothing opens it again, not even the token that set it up.
        status, got, _ = self.post(port, body(token))
        self.assertEqual((status, got), (409, {"error": "already_ready"}))

    def test_too_many_wrong_tokens_answer_429(self) -> None:
        name, root = self.database(), self.new_root()
        port = self.serve(self.phase(name, root, attempts=TokenAttempts(limit=1)))
        wrong = body(bootstrap_tokens.new_token().reveal())
        self.assertEqual(self.post(port, wrong)[0], 403)
        status, got, headers = self.post(port, wrong)
        self.assertEqual((status, got), (429, {"error": "too_many_attempts"}))
        self.assertGreaterEqual(int(headers["retry-after"]), 1)

    def test_a_root_of_another_database_is_refused_with_its_reason(self) -> None:
        name, root = self.database(), self.new_root()
        self.bootstrap(name, root)
        other = self.database("other")
        port = self.serve(self.phase(other, root))
        status, got, _ = self.post(port, body(self.issue(other).reveal()))
        self.assertEqual((status, got["error"], got["reason"]), (409, "setup_refused", "root_used"))
        self.assertNotIn(root, got["message"])


class WaitingProcessTests(SetupTestCase):
    """``prks_app.SetupWait`` runs the setup server while the web process waits."""

    def test_a_browser_setup_ends_the_wait_and_frees_the_address(self) -> None:
        name, root = self.database(), self.new_root()
        token = self.issue(name).reveal()
        port = free_port()
        lease = self.lease(root)
        phase = SetupPhase(self.db(name), self.settings(), root=root, lease=lease, testing=True,
                           environ=self.environ)
        started: list[Any] = []

        def setup() -> Any:
            server = start_setup_server(phase, port=port, host="127.0.0.1")
            started.append(server)
            return server, phase

        stderr = io.StringIO()
        wait = prks_app.SetupWait(poll=30, stderr=stderr, step=0.01, setup=setup,
                                  setup_hint=prks_app.setup_url("127.0.0.1", port, tls=False))
        results: list[bool] = []
        with wait.watching():
            thread = threading.Thread(target=lambda: results.append(wait()))
            thread.start()
            deadline = time.monotonic() + 10
            while not started and time.monotonic() < deadline:
                time.sleep(0.01)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/setup", body=json.dumps(body(token)), headers={
                "Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                "Content-Type": "application/json",
            })
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
            thread.join(timeout=10)  # well before the 30 s poll
            self.assertEqual(results, [True])
        self.assertIn(f"http://127.0.0.1:{port}/", stderr.getvalue())
        self.assertIn("admin bootstrap issue-token", stderr.getvalue())
        # Leaving the wait closed the setup server's socket: the normal server can bind.
        with socket.create_server(("127.0.0.1", port)):
            pass

    def test_a_setup_server_that_cannot_bind_leaves_the_cli(self) -> None:
        stderr = io.StringIO()

        def setup() -> Any:
            raise OSError(98, "Address already in use")

        wait = prks_app.SetupWait(poll=0.01, stderr=stderr, step=0.01, setup=setup)
        with wait.watching():
            self.assertTrue(wait())
        self.assertIn("cannot serve its setup page (Address already in use)", stderr.getvalue())
        self.assertIn("admin bootstrap", stderr.getvalue())

    def test_setup_url(self) -> None:
        self.assertEqual(prks_app.setup_url("127.0.0.1", 8080, tls=False), "http://127.0.0.1:8080/")
        self.assertEqual(prks_app.setup_url("127.0.0.1", 8443, tls=True), "https://127.0.0.1:8443/")
        self.assertEqual(prks_app.setup_url("0.0.0.0", 8080, tls=False), "http://this computer's address:8080/")  # nosec B104
        self.assertEqual(
            prks_app.setup_url("0.0.0.0", 8080, tls=False, public_origin=("https", "prks.example.org", None)),  # nosec B104
            "https://prks.example.org/",
        )


if __name__ == "__main__":
    unittest.main()
