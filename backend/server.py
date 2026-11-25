"""Start and stop the PRKS HTTP server (#1329, #1424).

The composition root (``prks_app.py``) binds the storage root and the
PostgreSQL Library into an :class:`~backend.web.app.App`
(:func:`bind_storage`), then serves it with :func:`run_server`. A first
run serves its setup phase with :func:`start_setup_server` until setup is
done. Requests are answered by ``backend.web`` (Uvicorn, the dispatcher and
the route table in ``backend/api/``).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Optional

from backend.runtime.log_safety import safe_bind_scope
from backend.storage.config import StorageConfig
from backend.storage.root_binding import assert_config_matches_bound_root
from backend.web.app import DEFAULT_HOST, App
from backend.web.hosts import normalize_listen_host, parse_trusted_hosts

if TYPE_CHECKING:
    from backend.domain.identity.web_setup import SetupPhase
    from backend.domain.identity.web_start import WebAuth
    from backend.jobs.embedded import WebJobs
    from backend.runtime.readiness import PostgresReadiness
    from backend.web.asgi import UvicornServing
    from backend.web.settings import HttpServerSettings

LOGGER = logging.getLogger("prks.server")

PORT = 8080
# Default for `python prks_app.py --testing`; a non-testing bind refuses this port.
PRKS_TESTING_DEFAULT_PORT = 8070


def validate_listen_port(port: int, *, testing: bool) -> None:
    """Keep a non-testing server off the port ``prks_app.py --testing`` uses by default."""
    if testing:
        return
    if int(port) == int(PRKS_TESTING_DEFAULT_PORT):
        raise RuntimeError(
            f"Port {PRKS_TESTING_DEFAULT_PORT} is reserved for `python prks_app.py --testing` "
            f"(PRKS_TESTING). Use a different --port, or run with --testing when you need that port."
        )


def bind_storage(config: StorageConfig, *, library_database: Any) -> App:
    """The :class:`App` serving ``config``'s root and the PostgreSQL Library on it.

    ``library_database`` (a ``backend.db.database.Database``) is the
    Library's database (PG-C2 #641: PostgreSQL is the only Library store).
    """
    # Serving a root this process does not hold the single-process lease on
    # would defeat that lease (storage-architecture §12). Only enforced once
    # the process entry opened a root.
    assert_config_matches_bound_root(config.root)
    from backend.library import PostgresLibrary

    os.makedirs(config.thumbs_dir, exist_ok=True)
    return App(storage=config, library=PostgresLibrary(library_database, storage=config))


def make_server(address: tuple[str, int], app: App, http: "Optional[HttpServerSettings]" = None) -> "UvicornServing":
    """Bind the Uvicorn HTTP server for ``app`` (#1329)."""
    from backend.web.asgi import UvicornServing
    from backend.web.settings import HttpServerSettings

    http = http or HttpServerSettings()
    return UvicornServing(
        address, app, threads=http.threads, tls_cert_file=http.tls_cert_file, tls_key_file=http.tls_key_file
    )


def run_server(
    app: App,
    port: int = PORT,
    host: str = DEFAULT_HOST,
    auth: "Optional[WebAuth]" = None,
    jobs: "Optional[WebJobs]" = None,
    readiness: "Optional[PostgresReadiness]" = None,
    http: "Optional[HttpServerSettings]" = None,
) -> None:
    """Serve ``app`` until stopped (SIGTERM/SIGINT, or the embedded worker's stop)."""
    host = normalize_listen_host(host)
    storage = app.require_storage()
    validate_listen_port(port, testing=storage.mode == "testing")
    from backend.web.settings import HttpServerSettings

    http = http or HttpServerSettings()
    app.bind_host = host
    app.trusted_hosts = parse_trusted_hosts(os.environ.get("PRKS_TRUSTED_HOSTS", ""))
    # Authentication (PRKS_AUTH, backend.domain.identity.web_start); None
    # only for test servers (prks_app always starts identity).
    app.auth = auth
    # The PRKS_AUTH runtime's jobs (backend.jobs.embedded.WebJobs): the
    # embedded worker and the job diagnostics. None only for test servers.
    app.jobs = jobs
    # GET /readyz (backend.runtime.readiness.PostgresReadiness); None keeps it 503.
    app.readiness = readiness
    if auth is not None:
        app.trust_public_origin(auth.settings.public_origin)
    with make_server((host, port), app, http) as httpd:
        LOGGER.info(
            "server_starting bind_scope=%s port=%s threads=%s tls=%s",
            safe_bind_scope(host),
            port,
            http.threads,
            "on" if http.tls else "off",
        )
        if jobs is not None:
            # Runs the embedded worker while serving and stops it gracefully
            # when the server stops (SIGTERM or SIGINT included).
            jobs.serve(httpd)
            return
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            LOGGER.info("server_stopping reason=keyboard_interrupt")


class SetupServer:
    """The HTTP server of a first run's setup phase (#612), serving on a thread.

    It serves the setup page and ``/api/setup`` only
    (:mod:`backend.api.setup`): no Library, no storage, ``/readyz`` 503.
    :meth:`stop` closes the listening socket, so the normal server binds the
    same address once setup is done.
    """

    def __init__(self, httpd: "UvicornServing") -> None:
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="prks-setup-server", daemon=True)

    @property
    def server_address(self) -> tuple[str, int]:
        return self._httpd.server_address

    def start(self) -> "SetupServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
            self._thread.join(timeout=GRACEFUL_SETUP_STOP_S)
        finally:
            self._httpd.server_close()


# Bounds the wait for the setup server's thread after shutdown() returned.
GRACEFUL_SETUP_STOP_S = 5.0


def start_setup_server(
    phase: "SetupPhase",
    port: int = PORT,
    host: str = DEFAULT_HOST,
    http: "Optional[HttpServerSettings]" = None,
    *,
    testing: bool = False,
) -> SetupServer:
    """Bind and start the setup phase's server (#612); the caller stops it.

    The bind, Host and CSRF rules are the normal server's. Storage is not
    bound yet, so ``testing`` says whether this is a testing process (which
    may use the ``--testing`` port).
    """
    host = normalize_listen_host(host)
    validate_listen_port(port, testing=testing)
    from backend.web.settings import HttpServerSettings

    http = http or HttpServerSettings()
    app = App(
        bind_host=host,
        trusted_hosts=parse_trusted_hosts(os.environ.get("PRKS_TRUSTED_HOSTS", "")),
        setup=phase,
    )
    app.trust_public_origin(phase.settings.public_origin)
    httpd = make_server((host, port), app, http)
    LOGGER.info(
        "setup_server_starting bind_scope=%s port=%s tls=%s",
        safe_bind_scope(host), port, "on" if http.tls else "off",
    )
    return SetupServer(httpd).start()
