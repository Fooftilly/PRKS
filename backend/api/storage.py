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

import json
import logging
from typing import Any, Optional

PATH = "/api/storage-status"

LOGGER = logging.getLogger("prks.storage")


def database_summary(conninfo: Optional[str]) -> dict[str, Any]:
    """Host, port and database name of ``conninfo``; never the user or the password."""
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
    from backend.api_contract.storage import StorageStatus
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
        "database": database_summary(conninfo),
    }
    result = dump_response(StorageStatus, payload)
    assert isinstance(result, dict)
    return result


def _send_no_store(handler: Any, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    # The body names host paths: no browser, proxy or service worker keeps it.
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if (getattr(handler, "command", "") or "").upper() != "HEAD":
        handler.wfile.write(data)


def handle_get(handler: Any, path: str, *, config: Any, library: Any) -> bool:
    """Answer ``GET /api/storage-status``; ``config`` is the bound ``StorageConfig``."""
    if path != PATH:
        return False
    if config is None:
        handler.send_json(503, {"error": "Storage is not bound.", "code": "unavailable"})
        return True
    from backend.domain.objects.jobs import root_storage
    from backend.storage.root_binding import active_bound_root

    bound = active_bound_root()
    if bound is not None:
        configured, resolved, source, leased = bound.root, bound.root_real, bound.source, True
    else:
        configured = config.configured_root or config.root
        resolved, source, leased = config.root, config.root_source, False
    database = getattr(library, "database", None) if library is not None else None
    conninfo = None
    if database is not None:
        conninfo = getattr(getattr(database, "config", None), "conninfo", None)
    _send_no_store(
        handler,
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
    )
    return True
