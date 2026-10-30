"""A real PRKS HTTP server with PostgreSQL authentication, for the PR 5/5 tests (L-ID-1 #626).

Each test gets a fresh migrated PostgreSQL database (``IdentityTestCase``)
and its own ``PRKSThreadingTCPServer`` on a free loopback port, configured
the way ``run_server`` configures it, in front of a SQLite storage root
bound once per test class in a temporary directory (never ``data/``).
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Optional

from backend.domain.identity.login_limiter import LoginLimits, TrustedProxies
from backend.domain.identity.web_start import AuthMode, WebAuth, WebAuthSettings
from tests.pg.identity_fixtures import PASSWORD, IdentityTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    raw: bytes
    cookies: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def body(self) -> Any:
        return json.loads(self.raw.decode("utf-8")) if self.raw else None

    def header(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]

    @property
    def text(self) -> str:
        """Everything the client received except Set-Cookie (where tokens belong)."""
        lines = [f"{k}: {v}" for k, v in self.headers if k.lower() != "set-cookie"]
        return "\n".join(lines) + "\n" + self.raw.decode("utf-8", "replace")


def _parse_set_cookie(value: str) -> tuple[str, dict[str, str]]:
    first, *attrs = [part.strip() for part in value.split(";")]
    name, _, cookie_value = first.partition("=")
    parsed = {"value": cookie_value}
    for attr in attrs:
        key, _, attr_value = attr.partition("=")
        parsed[key.lower()] = attr_value
    return name, parsed


_SENTINEL: Any = object()


class HttpAuthTestCase(IdentityTestCase):
    """Fresh PostgreSQL identity + a SQLite-backed PRKS server per test."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()  # skips without PRKS_TEST_DATABASE_URL
        from run_tests import apply_isolated_test_env

        apply_isolated_test_env(_PROJECT_DIR)
        import backend.server as server_module
        from backend.storage.config import StorageConfig

        cls.server_module = server_module
        cls._previous = (
            server_module._bound_storage, server_module.pdfs_dir, server_module.thumbs_dir,
            server_module.processing_dir, server_module.db, server_module.text_index,
            server_module.research_index,
        )
        cls._tmpdir = tempfile.mkdtemp(prefix="prks-http-auth-")
        storage = os.path.join(cls._tmpdir, "storage")
        processing = os.path.join(cls._tmpdir, "processing")
        os.makedirs(storage)
        os.makedirs(processing)
        server_module.bind_storage(
            replace(StorageConfig.for_testing(storage), processing_dir=processing)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        module = cls.server_module
        (
            module._bound_storage, module.pdfs_dir, module.thumbs_dir, module.processing_dir,
            module.db, module.text_index, module.research_index,
        ) = cls._previous
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        self.owner = self.user("Owner")
        self.lib = self.run_sql(
            "SELECT library_id FROM prks.library_memberships WHERE user_id = %s", (self.owner,)
        )[0]["library_id"]
        self.instance_id = "IN-" + uuid.uuid4().hex.upper()
        self.port = 0

    # -- the server --------------------------------------------------------------------

    def settings(self, mode: AuthMode = AuthMode.SESSION, **kwargs: Any) -> WebAuthSettings:
        kwargs.setdefault("limits", LoginLimits())
        kwargs.setdefault("idle", timedelta(days=30))
        kwargs.setdefault("trusted_proxies", TrustedProxies())
        return WebAuthSettings(mode=mode, **kwargs)

    def serve(
        self, settings: Optional[WebAuthSettings] = None, *, auth: Any = _SENTINEL, jobs: Any = None
    ) -> WebAuth:
        """Start a server on a free loopback port; ``auth=None`` serves the SQLite runtime.

        ``jobs`` is the PRKS_AUTH runtime's ``WebJobs`` (job diagnostics), as
        ``run_server`` sets it; its worker is not started here.
        """
        module = self.server_module
        if auth is _SENTINEL:
            auth = WebAuth(self.db, settings or self.settings(), self.instance_id, self.lib, hasher=self.hasher)
        httpd = module.PRKSThreadingTCPServer(("127.0.0.1", 0), module.PRKSHandler)
        httpd.prks_bind_host = "127.0.0.1"
        httpd.prks_trusted_hosts = frozenset()
        httpd.prks_access_gate = module.LibraryAccessGate()
        httpd.prks_auth = auth
        httpd.prks_jobs = jobs
        if auth is not None and auth.settings.public_origin is not None:
            httpd.prks_trusted_hosts = frozenset({auth.settings.public_origin[1]})
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]
        self.httpd = httpd
        return auth

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- requests ----------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        cookies: Optional[dict[str, str]] = None,
        headers: Optional[list[tuple[str, str]]] = None,
        origin: Optional[str] = _SENTINEL,
        epoch: Any = None,
        host: Optional[str] = None,
    ) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            for name, value in self._request_headers(method, cookies, headers, origin, epoch):
                conn.putheader(name, value)
            payload = b""
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                conn.putheader("Content-Type", "application/json")
            if body is not None or method in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload or None)
            res = conn.getresponse()
            raw = res.read()
            response = Response(res.status, list(res.getheaders()), raw)
        finally:
            conn.close()
        for name, value in response.headers:
            if name.lower() == "set-cookie":
                cookie_name, parsed = _parse_set_cookie(value)
                response.cookies[cookie_name] = parsed
        return response

    def _request_headers(
        self,
        method: str,
        cookies: Optional[dict[str, str]],
        headers: Optional[list[tuple[str, str]]],
        origin: Optional[str],
        epoch: Any,
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if method not in ("GET", "HEAD") and origin is not None:
            out.append(("Origin", self.origin if origin is _SENTINEL else origin))
        if cookies:
            out.append(("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items())))
        if epoch is not None:
            out.append(("PRKS-Library-Epoch", str(epoch)))
        return out + list(headers or [])

    def sign_in(self, login: str = "Owner", password: str = PASSWORD, **kwargs: Any) -> Response:
        return self.call("POST", "/api/login", body={"login": login, "password": password}, **kwargs)

    def session_cookie(self, login: str = "Owner") -> dict[str, str]:
        res = self.sign_in(login)
        self.assertEqual(res.status, 200, res.raw)
        return {"prks_session": res.cookies["prks_session"]["value"]}

    def library_epoch(self, library_id: Optional[str] = None) -> int:
        return int(
            self.run_sql("SELECT epoch FROM prks.libraries WHERE id = %s", (library_id or self.lib,))[0][
                "epoch"
            ]
        )

    def add_tag(self, cookies: dict[str, str], name: str = "T", **kwargs: Any) -> Response:
        kwargs.setdefault("epoch", self.library_epoch())
        return self.call("POST", "/api/tags", body={"name": f"{name}-{uuid.uuid4().hex[:8]}"},
                         cookies=cookies, **kwargs)
