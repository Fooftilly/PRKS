"""Privacy boundary for PRKS logs.

Logs describe operations and failures, not research-library contents.
Lowering the log level must never unlock raw user/library data.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from urllib.parse import unquote, urlparse

from backend.storage.paths import repo_root

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_ERROR_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_STATIC_RE = re.compile(r"^[A-Za-z0-9/._-]+$")
_SAFE_ASSET_BASENAME_RE = re.compile(
    r"^[A-Za-z0-9._-]+\.(js|css|mjs|map|svg|webmanifest)$"
)
_SAFE_ID_MAX = 64
_SAFE_STATIC_MAX = 256

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ALL_INTERFACES_HOSTS = frozenset({"0.0.0.0", "::"})

KNOWN_CLIENT_ERROR_KINDS = frozenset(
    {
        "window_error",
        "unhandled_rejection",
        "api_client_error",
        "api_http_error",
        "api_parse_error",
    }
)

KNOWN_CLIENT_SOURCES = frozenset(
    {
        "api",
        "client",
        "external",
        "works.fetch",
        "works.details",
        "folders.fetch",
        "folders.details",
        "persons.fetch",
        "persons.details",
        "person-groups.fetch",
        "person-groups.details",
        "recent.fetch",
        "recently-added.fetch",
        "search.fetch",
        "publishers.fetch",
        "tags.fetch",
        "processing-files.fetch",
        "pdf.viewer",
    }
)

_STATIC_API_PATHS = frozenset(
    {
        "/api/works",
        "/api/works/reindex-pdf-text",
        "/api/works/linearize-existing-pdfs",
        "/api/playlists",
        "/api/folders",
        "/api/persons",
        "/api/person-groups",
        "/api/recent",
        "/api/recently-added",
        "/api/search",
        "/api/tags",
        "/api/tags/merge",
        "/api/publishers",
        "/api/settings",
        "/api/processing-files",
        "/api/client-errors",
        "/api/roles",
        "/api/arguments",
        "/api/concepts",
    }
)

_RESOURCE_SLOT = {
    "works": ":id",
    "playlists": ":id",
    "persons": ":id",
    "person-groups": ":id",
    "folders": ":id",
    "tags": ":id",
    "publishers": ":id",
    "processing-files": ":id",
    "pdfs": ":pdf",
    "bibtex": ":id",
}

_NESTED_STATIC = {
    "works": frozenset(
        {
            "roles",
            "thumbnail",
            "annotations",
            "save-confirm",
            "related_folders",
            "pdf",
            "tags",
        }
    ),
    "playlists": frozenset({"items", "reorder"}),
    "persons": frozenset({"profile-image"}),
    "person-groups": frozenset({"members"}),
    "folders": frozenset({"works", "tags"}),
    "tags": frozenset({"aliases"}),
    "publishers": frozenset({"aliases"}),
    "processing-files": frozenset({"pdf", "import"}),
}

_NESTED_ID_AFTER = {
    ("works", "tags"): ":id",
    ("playlists", "items"): ":id",
    ("folders", "tags"): ":id",
}

_FIRST_PARTY_EXACT = frozenset(
    {
        "/",
        "/index.html",
        "/sw.js",
        "/favicon.svg",
        "/manifest.webmanifest",
    }
)
_FIRST_PARTY_PREFIXES = ("/js/", "/css/", "/vendor/", "/icons/")


def _is_first_party_static_route(route: str) -> bool:
    if route in _FIRST_PARTY_EXACT:
        return True
    return route.startswith(_FIRST_PARTY_PREFIXES)


def safe_log_id(value) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text:
        return "unknown"
    if len(text) > _SAFE_ID_MAX or not _SAFE_ID_RE.fullmatch(text):
        return "invalid"
    return text


def safe_log_label(value, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if len(text) > _SAFE_ID_MAX or not _SAFE_ID_RE.fullmatch(text):
        return fallback
    return text


def safe_error_type(exc) -> str:
    if exc is None:
        return "unknown"
    if isinstance(exc, type):
        name = getattr(exc, "__name__", "") or "Exception"
    else:
        name = type(exc).__name__
    return safe_log_label(name, fallback="Exception")


def safe_bind_scope(host: str) -> str:
    h = (host or "").strip().lower()
    if h in _LOOPBACK_HOSTS:
        return "loopback"
    if h in _ALL_INTERFACES_HOSTS:
        return "all_interfaces"
    return "custom"


def safe_client_error_kind(value) -> str:
    label = safe_log_label(value, fallback="")
    if label in KNOWN_CLIENT_ERROR_KINDS:
        return label
    return "client_error"


def safe_error_name(value) -> str:
    if value is None:
        return "Error"
    text = str(value).strip()
    if _SAFE_ERROR_NAME_RE.fullmatch(text):
        return text
    return "Error"


def safe_http_status(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 100 or n > 599:
        return None
    return n


def safe_source_location(value, *, max_value: int = 1_000_000) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 0 or n > max_value:
        return None
    return n


def safe_client_source(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    if raw in KNOWN_CLIENT_SOURCES:
        return raw
    if _SAFE_ASSET_BASENAME_RE.fullmatch(raw):
        return raw

    host = ""
    candidate = raw
    if "://" in raw or raw.startswith("//"):
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        candidate = parsed.path or ""
        if host and host not in _LOOPBACK_HOSTS:
            return "external"
    else:
        candidate = raw.split("?", 1)[0].split("#", 1)[0]

    candidate = unquote(candidate)
    if ".." in candidate or "\n" in candidate or "\r" in candidate:
        return "external"
    if not candidate.startswith("/"):
        return "external"

    if not _is_first_party_static_route(candidate):
        return "external"
    base = os.path.basename(candidate)
    if _SAFE_ASSET_BASENAME_RE.fullmatch(base):
        return base
    return "external"


def client_error_log_fields(data) -> dict:
    """Normalize a client-error POST body. Legacy raw fields are ignored."""
    if not isinstance(data, dict):
        raise ValueError("JSON object body required")
    fields = {
        "kind": safe_client_error_kind(data.get("kind")),
        "error_name": safe_error_name(data.get("error_name")),
        "source": safe_client_source(data.get("source")),
        "client_request_id": safe_log_id(data.get("request_id")),
    }
    line = safe_source_location(data.get("line"))
    column = safe_source_location(data.get("column"))
    http_status = safe_http_status(data.get("http_status"))
    if line is not None:
        fields["line"] = line
    if column is not None:
        fields["column"] = column
    if http_status is not None:
        fields["http_status"] = http_status
    return fields


def format_client_error_log(fields: dict, *, request_id: str) -> str:
    parts = [
        "client_error",
        f"kind={fields.get('kind') or 'client_error'}",
        f"error_name={fields.get('error_name') or 'Error'}",
        f"source={fields.get('source') or 'unknown'}",
    ]
    if "line" in fields:
        parts.append(f"line={fields['line']}")
    if "column" in fields:
        parts.append(f"column={fields['column']}")
    if "http_status" in fields:
        parts.append(f"http_status={fields['http_status']}")
    client_rid = fields.get("client_request_id") or "unknown"
    if client_rid not in ("unknown", "invalid", ""):
        parts.append(f"client_request_id={client_rid}")
    parts.append(f"request_id={safe_log_id(request_id)}")
    return " ".join(parts)


def safe_route(path: str) -> str:
    raw = "" if path is None else str(path)
    parsed = urlparse(raw)
    route = unquote(parsed.path or "/")
    if not route.startswith("/"):
        route = "/" + route
    if route != "/" and route.endswith("/"):
        route = route.rstrip("/") or "/"

    if route == "/api" or not route.startswith("/api/"):
        if route.startswith("/api"):
            return "/api/:unknown"
        return _safe_static_route(route)

    if route in _STATIC_API_PATHS:
        return route

    parts = [p for p in route.split("/") if p]
    if len(parts) < 2 or parts[0] != "api":
        return "/api/:unknown"

    family = parts[1]
    slot = _RESOURCE_SLOT.get(family)
    if slot is None:
        return "/api/:unknown"

    if len(parts) == 2:
        return "/api/:unknown"

    out = ["/api", family, slot]
    rest = parts[3:]
    if not rest:
        return "/".join(out)

    nested_ok = _NESTED_STATIC.get(family)
    if nested_ok is None or rest[0] not in nested_ok:
        return "/api/:unknown"
    out.append(rest[0])
    if len(rest) == 1:
        return "/".join(out)

    nested_slot = _NESTED_ID_AFTER.get((family, rest[0]))
    if nested_slot is not None and len(rest) == 2:
        out.append(nested_slot)
        return "/".join(out)
    return "/api/:unknown"


def _safe_static_route(route: str) -> str:
    if ".." in route or len(route) > _SAFE_STATIC_MAX:
        return "/:static"
    if not _SAFE_STATIC_RE.fullmatch(route):
        return "/:static"
    if _is_first_party_static_route(route):
        return route
    return "/:static"


def _safe_frame_path(filename: str) -> str:
    root = os.path.abspath(repo_root())
    try:
        abs_path = os.path.abspath(filename)
    except (OSError, TypeError, ValueError):
        return "<external>/unknown"
    try:
        rel = os.path.relpath(abs_path, root)
    except ValueError:
        return _external_frame_name(filename)
    if rel.startswith("..") or os.path.isabs(rel):
        return _external_frame_name(filename)
    return rel.replace(os.sep, "/")


def _external_frame_name(filename: str) -> str:
    base = os.path.basename(filename or "") or "unknown"
    if re.fullmatch(r"[A-Za-z0-9._-]+\.(py|so|pyc|pyd)", base):
        return f"<external>/{base}"
    return "<external>/unknown"


class PrivacySafeFormatter(logging.Formatter):
    """Tracebacks keep class + code locations; drop exception values and abs paths."""

    def formatException(self, ei) -> str:
        exc_type, _exc, tb = ei
        lines = ["Traceback:"]
        try:
            for frame, lineno in traceback.walk_tb(tb):
                filename = frame.f_code.co_filename
                name = frame.f_code.co_name or "unknown"
                if name != "<module>" and not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*", name
                ):
                    name = "unknown"
                lines.append(f"  {_safe_frame_path(filename)}:{lineno} in {name}")
        except Exception:
            lines.append("  <external>/unknown:0 in unknown")
        type_name = "Exception"
        if exc_type is not None:
            type_name = safe_log_label(
                getattr(exc_type, "__name__", "") or "Exception",
                fallback="Exception",
            )
        lines.append(type_name)
        return "\n".join(lines) + "\n"

    def formatStack(self, stack_info) -> str:
        return ""
