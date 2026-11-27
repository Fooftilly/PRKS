"""Storage status HTTP controller: ``GET /api/storage-status`` (#720).

The owner's view of where the Library's files are and how that place
behaves (storage-architecture §11.1): the configured and resolved root, the
source that selected it, the marker's identity, filesystem facts, object
counts and the database it is paired with. ``backend/api/session.py`` admits
only the Library owner (``ADMIN_LIBRARY``).

The body carries host paths, so it is sent ``Cache-Control: no-store`` and
never logged; the route itself is logged as itself. Read-only: under
PostgreSQL the root is paired with the database (postgresql-architecture
§11.1), so no route changes it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.api_contract.operation import Header, Operation, error, ok
from backend.api_contract.storage import StorageStatus
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

PATH = "/api/storage-status"

LOGGER = logging.getLogger("prks.storage")


def database_summary(conninfo: Optional[str]) -> dict[str, Any]:
    """Host, port and database name of ``conninfo``; never the user or the password.

    The fallback when the server cannot be asked (``live_database_summary``).
    """
    host = port = name = None
    if conninfo:
        try:
            from psycopg.conninfo import conninfo_to_dict

            parts = conninfo_to_dict(conninfo)
        except Exception:  # an unparsable URL never reaches here: the pool opened with it
            parts = {}
        raw_host = parts.get("host") or parts.get("hostaddr")
        if isinstance(raw_host, str) and raw_host:
            host = "local socket" if raw_host.startswith("/") or raw_host.startswith("@") else raw_host
        raw_port = parts.get("port")
        port = str(raw_port) if raw_port else None
        raw_name = parts.get("dbname")
        name = raw_name if isinstance(raw_name, str) and raw_name else None
    return {"engine": "PostgreSQL", "host": host, "port": port, "name": name}


def live_database_summary(database: Any, conninfo: Optional[str]) -> dict[str, Any]:
    """Where the pool is connected, as the server reports it.

    libpq fills omitted options (a hostless URL means the local socket), so
    the server's answer names the database the configuration alone may not.
    A Unix socket has no server address: it reads ``local socket``. Falls
    back to the configuration when the server cannot be asked.
    """
    fallback = database_summary(conninfo)
    if database is None:
        return fallback
    from backend.domain.objects.status import server_locator

    try:
        locator = server_locator(database)
    except Exception:
        LOGGER.warning("storage_status_database_locator_unavailable")
        return fallback
    host = locator.host or "local socket"
    port = str(locator.port) if locator.port is not None else fallback["port"]
    name = locator.name or fallback["name"]
    return {"engine": "PostgreSQL", "host": host, "port": port, "name": name}


def body(
    *,
    configured_root: str,
    resolved_root: str,
    source: Optional[str],
    leased: bool,
    database: Any = None,
    storage: Any = None,
    conninfo: Optional[str] = None,
) -> dict[str, Any]:
    """The response body. ``database`` and ``storage`` give the object counts."""
    from backend.api_contract.boundary import dump_response
    from backend.storage.resolver import ROOT_SOURCES
    from backend.storage.status import root_status

    status = root_status(
        configured_root=configured_root, resolved_root=resolved_root, source=source, leased=leased
    )
    objects = None
    if database is not None and storage is not None:
        from backend.domain.objects.status import object_counts

        try:
            counts = object_counts(database, storage)
        except Exception:
            LOGGER.warning("storage_status_counts_unavailable")
        else:
            objects = {
                "referenced": counts.referenced,
                "missing": counts.missing,
                "pending_cleanup": counts.pending_cleanup,
                "orphans": counts.orphans,
                "held": counts.held,
            }
    fs = status.filesystem
    payload = {
        "root": {
            "configured": status.configured_root,
            "resolved": status.resolved_root,
            "source": status.source if status.source in ROOT_SOURCES else None,
            "editable_in_app": False,
        },
        "identity": {
            "storage_root_id": status.storage_root_id,
            "layout_version": status.layout_version,
            "state": status.state,
            "relocation_state": status.relocation_state,
            "backend_type": status.backend_type,
        },
        "filesystem": {
            "free_bytes": fs.free_bytes,
            "total_bytes": fs.total_bytes,
            "filesystem_type": fs.filesystem_type,
            "network": fs.network,
            "case_sensitive": fs.case_sensitive,
            "normalization": fs.normalization,
            "directory_fsync": fs.directory_fsync,
        },
        "objects": objects,
        "database": live_database_summary(database, conninfo),
    }
    result = dump_response(StorageStatus, payload)
    assert isinstance(result, dict)
    return result


def storage_status(req: Request) -> Response:
    """``GET /api/storage-status``."""
    config = req.app.storage
    if config is None:
        return json_response(req, 503, {"error": "Storage is not bound.", "code": "unavailable"})
    from backend.domain.objects.jobs import root_storage
    from backend.storage.root_binding import active_bound_root

    bound = active_bound_root()
    if bound is not None:
        configured, resolved, source, leased = bound.root, bound.root_real, bound.source, True
    else:
        configured = config.configured_root or config.root
        resolved, source, leased = config.root, config.root_source, False
    library = req.app.library
    database = library.database if library is not None else None
    conninfo = None
    if database is not None:
        conninfo = getattr(getattr(database, "config", None), "conninfo", None)
    # The body names host paths: no browser, proxy or service worker keeps it.
    return json_response(
        req,
        200,
        body(
            configured_root=configured,
            resolved_root=resolved,
            source=source,
            leased=leased,
            database=database,
            storage=root_storage(resolved) if database is not None else None,
            conninfo=conninfo,
        ),
        cache_control="no-store",
    )


ROUTES = (
    route("GET", PATH, storage_status, contract=Operation(
        "getStorageStatus", "Storage status, read-only", "settings",
        {200: ok(StorageStatus,
                 "Where the Library's files are, the source that chose that place, the root marker's "
                 "identity, filesystem facts, object counts and the paired database. Carries host "
                 "paths, so it is sent Cache-Control: no-store.",
                 headers={"Cache-Control": Header("Always no-store.", {"type": "string", "const": "no-store"}, required=True)}),
         503: error("Storage is not bound (unavailable).")})),
)
