"""What one PRKS web process serves (#1424).

An :class:`App` holds what the routes need beyond their request: the bound
storage root and the PostgreSQL Library on it, the routes, the trusted Host
names, and the runtime the composition root started (identity, jobs,
readiness, or a first run's setup phase). ``backend.server`` builds it
(:func:`backend.server.bind_storage`) and hands it to the HTTP server; no
module holds it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from backend.web.routing import RouteTable

if TYPE_CHECKING:
    from backend.domain.identity.web_setup import SetupPhase
    from backend.domain.identity.web_start import WebAuth
    from backend.jobs.embedded import WebJobs
    from backend.library import PostgresLibrary
    from backend.runtime.readiness import PostgresReadiness
    from backend.storage.config import StorageConfig

#: The frontend the server serves (``frontend/``).
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")
DEFAULT_HOST = "127.0.0.1"


def served_routes() -> RouteTable:
    """Every route family's routes (``backend/api/<family>.py``)."""
    from backend.api import ROUTES

    return RouteTable(ROUTES)


@dataclass
class App:
    """The served Library and the server's runtime; one per web process.

    ``library`` and ``storage`` are ``None`` only for a first run's setup
    server (``setup`` set), which serves the setup page and ``/api/setup``.
    ``auth`` is ``None`` only for test servers: every ``/api/`` request is
    then answered 503 (``identity_not_started``).
    """

    storage: "Optional[StorageConfig]" = None
    library: "Optional[PostgresLibrary]" = None
    routes: RouteTable = field(default_factory=served_routes)
    bind_host: str = DEFAULT_HOST
    trusted_hosts: frozenset[str] = frozenset()
    auth: "Optional[WebAuth]" = None
    jobs: "Optional[WebJobs]" = None
    readiness: "Optional[PostgresReadiness]" = None
    setup: "Optional[SetupPhase]" = None
    frontend_dir: str = FRONTEND_DIR

    def require_library(self) -> "PostgresLibrary":
        """The served Library; reaching a route without one is a startup bug."""
        if self.library is None:
            raise RuntimeError("no Library is bound; call bind_storage() before serving requests")
        return self.library

    def require_storage(self) -> "StorageConfig":
        if self.storage is None:
            raise RuntimeError("storage is not bound; call bind_storage() before serving requests")
        return self.storage

    @property
    def pdfs_dir(self) -> str:
        return self.require_storage().pdfs_dir

    @property
    def thumbs_dir(self) -> str:
        return self.require_storage().thumbs_dir

    def trust_public_origin(self, public_origin: Optional[tuple[str, str, Optional[int]]]) -> None:
        """Also accept the Host of the configured public origin."""
        if public_origin is not None:
            self.trusted_hosts = self.trusted_hosts | {public_origin[1]}
