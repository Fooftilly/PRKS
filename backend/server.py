from backend import work_note_sync, work_source_sync
import http.server
import socketserver
import json
import gzip
import os
import sys
import base64
import binascii
import time
import re
import uuid
import logging
import ipaddress
from contextlib import nullcontext
from typing import TYPE_CHECKING
from email.message import Message
from urllib.parse import urlparse, parse_qs, quote, unquote
from urllib.request import urlopen, Request

# Add the parent directory to sys.path to ensure 'backend' module is resolvable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.derived_caches import (
    prks_delete_pdf_thumbnails_for_work_id,
    prks_thumb_cache_stem,
)
from backend.domain.people.errors import MissingPersonError
from backend.domain.works.rules import BulkWorkError, effective_source_kind
from backend.managed_pdf_names import managed_pdf_filename, safe_pdf_path_under_dir
from backend.api import processing as processing_api
from backend.api import saved_views as saved_views_api
from backend.api import arguments as arguments_api
from backend.api import concepts as concepts_api
from backend.api import folders as folders_api
from backend.api import annotations as annotations_api
from backend.api import person_groups as person_groups_api
from backend.api import persons as persons_api
from backend.api import playlists as playlists_api
from backend.api import positions as positions_api
from backend.api import research_notes as research_notes_api
from backend.api import search as search_api
from backend.api import publishers as publishers_api
from backend.api import settings as settings_api
from backend.api import tags as tags_api
from backend.api import work_roles as work_roles_api
from backend.api import works as works_api
from backend.domain.research.rules import ResearchError
from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.jobs import JobDiagnostics
from backend.api_contract.openapi import (
    job_diagnostics_openapi_document,
    performance_diagnostics_openapi_document,
    positions_openapi_document,
    processing_items_openapi_document,
    publishers_openapi_document,
    saved_views_openapi_document,
)
from backend.api_contract.performance import (
    PerformanceDiagnosticsReset,
    PerformanceDiagnosticsResetRequest,
    PerformanceSnapshot,
)
from backend.research_graph import GraphTooLargeError
from backend.derived_cache_publish import publish_derived_cache_bytes
from backend.storage.config import StorageConfig
from backend.storage.root_binding import assert_config_matches_bound_root
from backend.log_safety import (
    client_error_log_fields,
    format_client_error_log,
    safe_bind_scope,
    safe_error_type,
    safe_log_id,
    safe_log_label,
    safe_route,
)
from backend.performance import (
    begin_request,
    clear_request,
    clock_ns,
    finish_request,
    is_excluded_route,
    record_counter,
    record_span,
    reset as reset_performance,
    server_timing_header,
    set_response_bytes,
    set_status,
    snapshot as performance_snapshot,
)
from backend.domain.maintenance.upload import (
    RestoreError,
    backup_max_upload_bytes,
    stream_upload_to_file,
)
from backend.person_image import identify_cached_portrait_subtype
from backend.concurrency import MODE_BACKUP, LibraryAccessGate, request_access_mode

if TYPE_CHECKING:
    from backend.pg_facade import PostgresLibrary
    from backend.pg_facade.research_index import PostgresResearchIndex
    from backend.pg_facade.text_index import PostgresTextIndex

LOGGER = logging.getLogger("prks.server")

PORT = 8080
DEFAULT_HOST = "127.0.0.1"
# Default for `python prks_app.py --testing`; non-testing bind refuses this port (see _validate_listen_port).
PRKS_TESTING_DEFAULT_PORT = 8070

# Minimum uncompressed JSON size before gzip (Accept-Encoding: gzip).
_PRKS_JSON_GZIP_MIN_BYTES = 1024
_PRKS_MAX_JSON_BODY_BYTES = 50 * 1024 * 1024
# No Content-Length, or a zero-length document. Distinct from {} and from JSON null.
_JSON_BODY_ABSENT = object()
# Distinct from JSON null. _read_json_body already sent the error response.
_JSON_BODY_REJECTED = object()

# Get the path to the frontend directory
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dir = os.path.join(base_dir, "frontend")

# Ensure frontend dir exists so http.server doesn't crash on startup
os.makedirs(frontend_dir, exist_ok=True)

_bound_storage: StorageConfig | None = None
pdfs_dir: str | None = None
thumbs_dir: str | None = None
db: "PostgresLibrary | None" = None
text_index: "PostgresTextIndex | None" = None
research_index: "PostgresResearchIndex | None" = None


def normalize_listen_host(host: str) -> str:
    host = host.strip()
    if not host:
        raise ValueError("host must not be empty")
    return host


_TRUSTED_HOST_NAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _strip_one_trailing_dot(name: str) -> str:
    if name.endswith("."):
        return name[:-1]
    return name


def _normalize_hostname_label(name: str) -> str | None:
    if name != name.strip() or not name:
        return None
    name = _strip_one_trailing_dot(name)
    if not name or name.endswith("."):
        return None
    return name.lower()


def _parse_tcp_port(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    if len(raw) > 1 and raw.startswith("0"):
        return None
    try:
        port = int(raw, 10)
    except ValueError:
        return None
    if port < 0 or port > 65535:
        return None
    return port


def parse_request_host(value: str) -> tuple[str, int | None] | None:
    if value is None or value != value.strip() or not value:
        return None
    if any(ch in value for ch in (",", "/", "@", " ", "\t")):
        return None
    if value.startswith("["):
        close = value.find("]")
        if close < 1:
            return None
        inner = value[1:close]
        rest = value[close + 1 :]
        try:
            ip = ipaddress.ip_address(inner)
        except ValueError:
            return None
        hostname = str(ip)
        if not rest:
            return (hostname, None)
        if not rest.startswith(":") or len(rest) < 2:
            return None
        port = _parse_tcp_port(rest[1:])
        if port is None:
            return None
        return (hostname, port)
    if value.count(":") > 1:
        return None
    if ":" in value:
        hostpart, portpart = value.rsplit(":", 1)
        port = _parse_tcp_port(portpart)
        if port is None:
            return None
    else:
        hostpart = value
        port = None
    try:
        ip = ipaddress.ip_address(hostpart)
        return (str(ip), port)
    except ValueError:
        pass
    hostname = _normalize_hostname_label(hostpart)
    if hostname is None or not _TRUSTED_HOST_NAME_RE.match(hostname):
        return None
    return (hostname, port)


def parse_trusted_hosts(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    names = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        name = _parse_trusted_host_entry(entry)
        if name is None:
            raise ValueError("invalid PRKS_TRUSTED_HOSTS entry")
        names.append(name)
    return frozenset(names)


def _parse_trusted_host_entry(entry: str) -> str | None:
    if any(ch in entry for ch in (":", "/", "@", "*", "?", "#", " ", "\t", "\\")):
        return None
    name = _normalize_hostname_label(entry)
    if name is None or not _TRUSTED_HOST_NAME_RE.match(name):
        return None
    return name


def _is_unspecified_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _normalized_bind_hostname(bind_host: str) -> str | None:
    if _is_unspecified_ip(bind_host):
        return None
    try:
        return str(ipaddress.ip_address(bind_host))
    except ValueError:
        pass
    return _normalize_hostname_label(bind_host)


def is_trusted_request_host(
    hostname: str, *, bind_host: str, extra_hosts: frozenset[str]
) -> bool:
    if hostname == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        if not ip.is_unspecified:
            return True
    except ValueError:
        pass
    bind_name = _normalized_bind_hostname(bind_host)
    if bind_name is not None and hostname == bind_name:
        return True
    return hostname in extra_hosts


def parse_request_origin(value: str) -> tuple[str, str, int | None] | None:
    if value is None or value != value.strip() or not value:
        return None
    if value == "null":
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    _, _, rest = value.partition("://")
    if any(ch in rest for ch in "/?#"):
        return None
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    try:
        hostname = str(ipaddress.ip_address(hostname))
    except ValueError:
        hostname = _normalize_hostname_label(hostname)
        if hostname is None:
            return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return (parsed.scheme, hostname, port)


def origin_matches_request(
    origin: tuple[str, str, int | None], req_hostname: str, req_port: int | None
) -> bool:
    scheme, hostname, port = origin
    if scheme != "http":
        return False
    if hostname != req_hostname:
        return False
    origin_port = 80 if port is None else port
    request_port = 80 if req_port is None else req_port
    return origin_port == request_port


def json_content_type_allowed(header_value: str) -> bool:
    msg = Message()
    msg["content-type"] = header_value
    return msg.get_content_type() == "application/json"


def octet_stream_content_type_allowed(header_value: str) -> bool:
    msg = Message()
    msg["content-type"] = header_value
    return msg.get_content_type() in (
        "application/octet-stream",
        "application/zip",
        "application/x-zip-compressed",
    )


def _validate_listen_port(port: int) -> None:
    """Avoid non-testing server on port used by `prks_app.py --testing` default."""
    if _bound_storage is not None and _bound_storage.mode == "testing":
        return
    if int(port) == int(PRKS_TESTING_DEFAULT_PORT):
        raise RuntimeError(
            f"Port {PRKS_TESTING_DEFAULT_PORT} is reserved for `python prks_app.py --testing` "
            f"(PRKS_TESTING). Use a different --port, or run with --testing when you need that port."
        )


def bind_storage(config: StorageConfig, *, library_database) -> StorageConfig:
    """Bind ``config``'s root and the PostgreSQL Library the routes use.

    ``library_database`` (a ``backend.db.database.Database``) is the
    Library's database: the routes use its facade and its research and text
    index adapters (PG-C2 #641: PostgreSQL is the only Library store).
    """
    # Publishing a binding for a root this process does not hold the
    # single-process lease on would defeat that lease (storage-architecture
    # §12). Only enforced once the process entry opened a root.
    assert_config_matches_bound_root(config.root)
    return _publish_binding(config, *_postgres_library(config, library_database))


def _postgres_library(config: StorageConfig, database):
    """The PostgreSQL facade and its index adapters for ``database`` (PG-C1 #640)."""
    from backend.pg_facade import PostgresLibrary
    from backend.pg_facade.research_index import PostgresResearchIndex
    from backend.pg_facade.text_index import PostgresTextIndex

    os.makedirs(config.thumbs_dir, exist_ok=True)
    return (
        PostgresLibrary(database, storage=config),
        PostgresTextIndex(database),
        PostgresResearchIndex(database),
    )


def _publish_binding(config: StorageConfig, db_local, candidate, research_candidate) -> StorageConfig:
    """Publish a built binding whole: the adapters were all built before this runs."""
    global _bound_storage, pdfs_dir, thumbs_dir, db, text_index, research_index
    _bound_storage = config
    pdfs_dir = config.pdfs_dir
    thumbs_dir = config.thumbs_dir
    db = db_local
    text_index = candidate
    research_index = research_candidate
    return config


def _safe_pdf_path_in_pdfs_dir(url_last_segment: str) -> str | None:
    return safe_pdf_path_under_dir(pdfs_dir, url_last_segment)


_PDF_ROUTE_PREFIX = '/api/pdfs/'


def _safe_pdf_path_for_route(path: str) -> str | None:
    """Resolve a ``/api/pdfs/<filename>`` route to one managed PDF.

    The whole suffix is validated, not just its last ``/``-separated piece.
    Reducing the route with ``split('/')[-1]`` first made
    ``/api/pdfs/nested/victim.pdf`` serve ``victim.pdf``, so the validator's
    rejection of path-shaped names was unreachable from the route it protects.
    """
    if not path.startswith(_PDF_ROUTE_PREFIX):
        return None
    return _safe_pdf_path_in_pdfs_dir(path[len(_PDF_ROUTE_PREFIX):])


def _served_pdf_path(handler, path: str) -> str | None:
    """The managed PDF a ``/api/pdfs/<filename>`` request may read, or ``None``.

    On the PostgreSQL Library the file must also be one of the acting
    Library's Assets: the data root is shared by every Library.
    """
    pdf_path = _safe_pdf_path_for_route(path)
    if pdf_path is None or not os.path.exists(pdf_path):
        return None
    if not works_api.holds_managed_pdf(handler, db, os.path.basename(pdf_path)):
        return None
    return pdf_path


_BACKUP_ROUTE_PREFIX = '/api/backups/'


def _stored_work_file_path(handler, work_id: str) -> str:
    """The Work's stored ``file_path`` (``""`` when none or no such Work).

    On PostgreSQL that is its primary Asset's: there is no ``works.file_path``
    column, and changing it is not ported (``update_work_metadata`` refuses).
    """
    fields = works_api.thumbnail_fields(handler, db, work_id)
    return str((fields or {}).get("file_path") or "")


def _create_postgres_work(file_path: str, upload: tuple[str, bytes] | None, fields: dict) -> str:
    """Create a Work on the PostgreSQL Library, from an upload or an existing managed PDF.

    No adoption lock: the facade checks an existing file for an actor who
    may edit and adopts it in the create's transaction, and the settle that
    removes bytes re-checks every Asset under the per-object lock (§7.4,
    §11.2). An upload is stored by the facade under fresh keys.
    """
    if upload is None:
        return db.add_work(file_path=file_path, **fields)
    name, data = upload

    def write(out) -> None:
        out.write(data)

    return db._add_work_with_pdf(name, write, **fields)


def _update_postgres_work_metadata(work_id: str, fields: dict) -> None:
    """``PATCH /api/works/{id}`` metadata on the PostgreSQL Library.

    Never with ``file_path``: the PATCH refuses a changed one (501) and drops
    an unchanged echo, and the Library refuses it too (``NotPortedError``),
    so no existing managed file is adopted here.
    """
    db.update_work_metadata(work_id, {k: v for k, v in fields.items() if k != "file_path"})


def _delete_work(work_id: str, *, keep_managed_pdf: bool = False) -> None:
    """Delete a Work and its derived state on the bound Library.

    On PostgreSQL the delete records its files' cleanup claims in its own
    transaction and the orphan sweep removes the bytes (§11.2); the text and
    Research indexes are the database's. Only the Work's thumbnails, derived
    files under the root, are removed here.
    """
    deleted = db.delete_work_record(work_id, claim_pdf=not keep_managed_pdf)
    if deleted is None or not thumbs_dir:
        return
    # Log the id the database returned, never the request's own value.
    logged_id = safe_log_id(deleted.work_id)
    try:
        failed = prks_delete_pdf_thumbnails_for_work_id(deleted.work_id, thumbs_dir)
        if failed:
            LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s failed_count=%s",
                           logged_id, len(failed))
    except Exception as e:
        LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s error_type=%s",
                       logged_id, safe_error_type(e))


def _prks_pixmap_to_pil(pix):
    """PyMuPDF pixmap → Pillow Image (RGB/RGBA). None if Pillow missing or conversion fails."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        n = int(getattr(pix, "n", 3))
        w, h = int(pix.width), int(pix.height)
        samples = pix.samples
        mode = "RGB" if n == 3 else "RGBA"
        stride = int(getattr(pix, "stride", w * n))
        row_bytes = w * n
        if stride == row_bytes:
            return Image.frombytes(mode, (w, h), samples)
        return Image.frombytes(mode, (w, h), samples, "raw", mode, stride, 1)
    except Exception:
        return None


def _prks_env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _prks_pil_to_card_webp_bytes(img) -> bytes | None:
    """Lossy WebP from a Pillow image (quality 82, method 4)."""
    from io import BytesIO

    try:
        from PIL import Image
    except Exception:
        return None
    try:
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=82, method=4)
        out = buf.getvalue()
        return out if out else None
    except Exception:
        return None


def _prks_pixmap_to_card_webp_bytes(pix) -> bytes | None:
    """Lossy WebP for library card thumbnails (quality 82, method 4)."""
    img = _prks_pixmap_to_pil(pix)
    if img is None:
        return None
    return _prks_pil_to_card_webp_bytes(img)






def _prks_pixmap_to_jpeg_bytes(pix, quality: int = 82) -> bytes | None:
    """JPEG fallback when WebP encode is unavailable."""
    from io import BytesIO

    img = _prks_pixmap_to_pil(pix)
    if img is None:
        return None
    try:
        from PIL import Image

        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        return out if out else None
    except Exception:
        return None


def _prks_pixmap_to_lossless_webp_bytes(pix) -> bytes | None:
    """
    Lossless WebP from a PyMuPDF pixmap. Usually smaller than PNG for page renders.
    Returns None if Pillow/WebP encode is unavailable.
    """
    from io import BytesIO

    img = _prks_pixmap_to_pil(pix)
    if img is None:
        return None
    try:
        buf = BytesIO()
        # method=6: slowest preset, best lossless compression in libwebp
        img.save(buf, format="WEBP", lossless=True, method=6)
        out = buf.getvalue()
        return out if out else None
    except Exception:
        return None


def _prks_pixmap_to_lossless_png_bytes(pix) -> bytes | None:
    """
    Lossless PNG with maximum DEFLATE (Pillow). Smaller than raw PyMuPDF PNG tobytes().
    """
    from io import BytesIO

    img = _prks_pixmap_to_pil(pix)
    if img is None:
        return None
    try:
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True, compress_level=9)
        out = buf.getvalue()
        return out if out else None
    except Exception:
        return None


def _prks_thumbnail_bytes_from_pixmap(pix) -> tuple[bytes, str]:
    """
    Encode extracted raster for caching/serving.
    Default: lossy WebP (card-sized), then JPEG, then PNG last resort.
    PRKS_THUMB_LOSSLESS=1 restores lossless WebP → PNG chain for debugging.
    Returns (bytes, mime_subtype) for Content-Type image/<subtype>.
    """
    if _prks_env_truthy("PRKS_THUMB_LOSSLESS"):
        webp = _prks_pixmap_to_lossless_webp_bytes(pix)
        if webp is not None:
            return webp, "webp"
        png = _prks_pixmap_to_lossless_png_bytes(pix)
        if png is not None:
            return png, "png"
        return pix.tobytes("png"), "png"
    webp = _prks_pixmap_to_card_webp_bytes(pix)
    if webp is not None:
        return webp, "webp"
    jpeg = _prks_pixmap_to_jpeg_bytes(pix)
    if jpeg is not None:
        return jpeg, "jpeg"
    png = _prks_pixmap_to_lossless_png_bytes(pix)
    if png is not None:
        return png, "png"
    return pix.tobytes("png"), "png"


# Work creation and source synchronization share ONE parser, in the source
# domain module. A second one here would eventually disagree with it, and the
# disagreement would surface as a Work whose stored URL and stored provider_id
# name different videos.
_YOUTUBE_HOSTS = work_source_sync.YOUTUBE_HOSTS
_youtube_host = work_source_sync.youtube_host
_is_youtube_host = work_source_sync.is_youtube_host
_youtube_video_id = work_source_sync.youtube_video_id
_validate_youtube_url = work_source_sync.validate_youtube_url


def _fetch_youtube_oembed(url: str) -> dict | None:
    """
    Best-effort metadata fetch for a supported YouTube URL via oEmbed (no API key).

    The outbound request is built from PRKS's validated provider identity, not
    from the caller's URL spelling. This keeps the network boundary fixed even
    when the source URL contains unrelated query parameters or malformed input.
    """
    if not url or not str(url).strip():
        return None
    try:
        video_id = _validate_youtube_url(str(url).strip())
        if not video_id:
            return None
        canonical_url = "https://www.youtube.com/watch?v=" + quote(video_id, safe="")
        oembed_url = "https://www.youtube.com/oembed?format=json&url=" + quote(canonical_url, safe="")
        req = Request(
            oembed_url,
            headers={
                "User-Agent": "PRKS/1.0 (oEmbed metadata fetch)",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _skip_to(stream, offset: int) -> None:
    """Position a read stream at ``offset``: seek when it can, else read and discard."""
    if stream.seekable():
        stream.seek(offset)
        return
    while offset > 0:
        chunk = stream.read(min(65536, offset))
        if not chunk:
            return
        offset -= len(chunk)


class PRKSHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **getattr(http.server.SimpleHTTPRequestHandler, "extensions_map", {}),
        ".webmanifest": "application/manifest+json",
    }

    # Non-fingerprinted JS/CSS must revalidate so users don't get stale app versions.
    _STATIC_REVALIDATE_EXTS = frozenset({".css", ".js", ".map"})
    _STATIC_LONG_CACHE_EXTS = frozenset(
        {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2"}
    )

    def __init__(self, *args, **kwargs):
        self._prks_request_id = uuid.uuid4().hex[:12]
        super().__init__(*args, directory=frontend_dir, **kwargs)

    def _request_context(self) -> dict:
        parsed = urlparse(getattr(self, "path", "") or "")
        method = (getattr(self, "command", "") or "").strip() or "unknown"
        return {
            "method": safe_log_label(method, fallback="unknown"),
            "route": safe_route(parsed.path or "/"),
            "request_id": safe_log_id(self._prks_request_id),
        }

    def _send_internal_error(self, exc: Exception | None = None):
        if exc is not None and getattr(self, "_prks_request_context", None) is not None:
            # An admitted PostgreSQL request: a refusal or a mapped database
            # error raised in the route keeps its own status (§5.4, §9.4).
            from backend.api import session as session_api
            from backend.domain.identity.authorization import AccessRefused
            from backend.pg_facade.registry import NotPortedError

            if isinstance(exc, NotPortedError):
                # A documented gap of the PostgreSQL Library, never a fault
                # and never a fallback (PG-C1 #640): 501.
                self._send_not_ported(str(exc))
                return

            reply = session_api.route_failure(exc)
            if reply is not None:
                if isinstance(exc, AccessRefused):
                    # Admission's epoch may be stale by now (a restore since);
                    # the client re-reads it from /api/session.
                    self._prks_library_epoch = None
                self._send_session_reply(reply)
                return
        ctx = self._request_context()
        if exc is not None:
            LOGGER.exception(
                "unhandled_api_error method=%s route=%s request_id=%s error_type=%s",
                ctx["method"],
                ctx["route"],
                ctx["request_id"],
                safe_error_type(exc),
                exc_info=exc,
            )
        else:
            LOGGER.error(
                "internal_error method=%s route=%s request_id=%s",
                ctx["method"],
                ctx["route"],
                ctx["request_id"],
            )
        self.send_json(500, {"error": "internal_error", "request_id": self._prks_request_id})

    def _reject_request(self, status: int, error: str, reason: str) -> None:
        LOGGER.info(
            "request_rejected reason=%s request_id=%s",
            safe_log_label(reason),
            safe_log_id(self._prks_request_id),
        )
        self.send_json(status, {"error": error})

    def _library_access(self, parsed_path):
        mode = request_access_mode(
            getattr(self, "command", "") or "GET",
            parsed_path.path,
            parsed_path.query,
        )
        if mode is None:
            return nullcontext()
        if mode == MODE_BACKUP:
            # The worker writes a PostgreSQL backup from one snapshot; nothing
            # here needs writes held off while the browser follows it.
            return nullcontext()
        gate = getattr(self.server, "prks_access_gate", None)
        if gate is None:
            return nullcontext()
        return gate.scope(mode)

    def _acting_for_request(self):
        """Bind the admitted RequestContext for the route (PostgreSQL facade calls act for it).

        Called only after :meth:`_prks_admit` admitted the request, which
        always leaves a context: a route never runs without an actor.
        """
        ctx = getattr(self, "_prks_request_context", None)
        if ctx is None:
            raise RuntimeError("an /api/ route ran without an admitted request context")
        from backend.pg_facade.context import acting_as

        return acting_as(ctx)

    def _validate_request_host(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            self._reject_request(400, "invalid_host", "invalid_host")
            return False
        parsed = parse_request_host(hosts[0])
        if parsed is None:
            self._reject_request(400, "invalid_host", "invalid_host")
            return False
        hostname, port = parsed
        bind_host = getattr(self.server, "prks_bind_host", DEFAULT_HOST)
        extra_hosts = getattr(self.server, "prks_trusted_hosts", frozenset())
        if not is_trusted_request_host(
            hostname, bind_host=bind_host, extra_hosts=extra_hosts
        ):
            self._reject_request(421, "untrusted_host", "untrusted_host")
            return False
        self._prks_request_host = (hostname, port)
        return True

    def _validate_mutation_origin(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return True
        if len(origins) != 1:
            self._reject_request(403, "origin_not_allowed", "origin_not_allowed")
            return False
        parsed = parse_request_origin(origins[0])
        if parsed is None:
            self._reject_request(403, "origin_not_allowed", "origin_not_allowed")
            return False
        hostname, port = self._prks_request_host
        if not origin_matches_request(parsed, hostname, port):
            self._reject_request(403, "origin_not_allowed", "origin_not_allowed")
            return False
        return True

    def _prks_admit(self, parsed_path) -> bool:
        """Authentication admission for an ``/api/`` request (L-ID-1 #626).

        True to dispatch the route; False when the request has been answered.
        ``backend.api.session`` answers the identity routes and admits every
        other route only for a live session with the route's capability and,
        for a Library mutation, the Library's current epoch. A server started
        without identity (``prks_auth`` unset; ``prks_app`` always starts it)
        fails closed: every ``/api/`` request is answered 503, never served
        without an actor (PG-C2 #641).
        """
        self._prks_library_epoch = None
        self._prks_request_context = None
        method = (getattr(self, "command", "") or "").upper()
        auth = getattr(self.server, "prks_auth", None)
        if auth is None:
            self._reject_request(503, "identity_not_started", "identity_not_started")
            return False
        from backend.api import session as session_api

        def read_json():
            data = self._read_json_body()
            if data is _JSON_BODY_REJECTED:
                return session_api.ANSWERED
            return {} if data is _JSON_BODY_ABSENT else data

        outcome = session_api.handle(
            auth,
            session_api.HttpRequest(
                method=method,
                path=parsed_path.path,
                headers=self.headers,
                peer=str(self.client_address[0]),
                request_host=self._prks_request_host,
                parse_origin=parse_request_origin,
                read_json=read_json,
                route_fences_epoch=True,
            ),
        )
        if isinstance(outcome, session_api.Admitted):
            self._prks_library_epoch = outcome.epoch
            self._prks_request_context = outcome.context
            return True
        if isinstance(outcome, session_api.Reply):
            self._send_session_reply(outcome)
        return False

    def _send_session_reply(self, reply) -> None:
        """Answer with a ``backend.api.session.Reply`` (no body for HEAD)."""
        if reply.status >= 400:
            LOGGER.info(
                "request_rejected reason=%s request_id=%s",
                safe_log_label(str(reply.body.get("error", ""))),
                safe_log_id(self._prks_request_id),
            )
        method = (getattr(self, "command", "") or "").upper()
        body = b"" if method == "HEAD" else json.dumps(reply.body).encode("utf-8")
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for name, value in reply.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_json_body(self):
        types = self.headers.get_all("Content-Type") or []
        if len(types) != 1 or not json_content_type_allowed(types[0]):
            self._reject_request(415, "unsupported_media_type", "unsupported_media_type")
            return _JSON_BODY_REJECTED
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return _JSON_BODY_ABSENT
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError):
            self.send_json(400, {"error": "invalid Content-Length"})
            return _JSON_BODY_REJECTED
        if content_length < 0:
            self.send_json(400, {"error": "invalid Content-Length"})
            return _JSON_BODY_REJECTED
        if content_length > _PRKS_MAX_JSON_BODY_BYTES:
            self.send_json(413, {"error": "request_too_large"})
            return _JSON_BODY_REJECTED
        try:
            payload = self.rfile.read(content_length) if content_length else b""
        except Exception:
            self.send_json(400, {"error": "request_read_failed"})
            return _JSON_BODY_REJECTED
        if not payload:
            return _JSON_BODY_ABSENT
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return _JSON_BODY_REJECTED

    def _parse_client_error_payload(self, data):
        return client_error_log_fields(data)

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            try:
                parsed = urlparse(getattr(self, "path", "") or "")
                path = parsed.path or "/"
                if path.startswith("/api/"):
                    method = (getattr(self, "command", "") or "GET").upper()
                    route = safe_route(path)
                    begin_request(
                        method,
                        route,
                        excluded=is_excluded_route(route) or is_excluded_route(path),
                    )
            except Exception:
                pass
        return ok

    def handle_one_request(self):
        # Set by a route that answers before reading its whole body (end_headers).
        self._prks_unread_body = False
        try:
            super().handle_one_request()
        finally:
            try:
                finish_request(request_id=getattr(self, "_prks_request_id", "") or "")
            except Exception:
                pass
            try:
                clear_request()
            except Exception:
                pass

    def send_response(self, code, message=None):
        try:
            set_status(int(code))
        except Exception:
            pass
        super().send_response(code, message)

    def send_header(self, keyword, value):
        try:
            if str(keyword).lower() == "content-length":
                set_response_bytes(int(value))
        except Exception:
            pass
        super().send_header(keyword, value)

    def end_headers(self):
        # Avoid hammering the server: browsers and embedded viewers may revalidate small assets often
        # if Cache-Control is missing (default was heuristic / no-store in some cases).
        try:
            p = urlparse(self.path).path
            if not p.startswith("/api/"):
                leaf = p.rstrip("/").split("/")[-1].lower()
                if p == "/" or p == "/index.html":
                    # SPA shell should never be cached by intermediary proxies/CDNs.
                    self.send_header("Cache-Control", "no-store, max-age=0")
                elif leaf == "sw.js":
                    self.send_header("Cache-Control", "no-cache")
                elif leaf == "manifest.webmanifest":
                    # Browsers re-check the manifest often; no-cache caused a 304 storm in logs.
                    self.send_header("Cache-Control", "public, max-age=3600")
                else:
                    ext = os.path.splitext(p)[1].lower()
                    if ext in self._STATIC_REVALIDATE_EXTS:
                        # App bundles are stable paths (no content hash), so force revalidation.
                        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
                    elif ext in self._STATIC_LONG_CACHE_EXTS:
                        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        except Exception:
            pass
        try:
            timing = server_timing_header()
            if timing:
                self.send_header("Server-Timing", timing)
        except Exception:
            pass
        if self._prks_request_id:
            self.send_header("X-Request-ID", self._prks_request_id)
        self._send_library_epoch_header()
        # Every response declares its own Content-Type; no response may be
        # re-interpreted as HTML by MIME sniffing. This closes the reflected-XSS
        # class for JSON/API bodies that echo library content back to the page.
        self.send_header("X-Content-Type-Options", "nosniff")
        if getattr(self, "_prks_unread_body", False):
            # Unread body bytes must never be parsed as the next request:
            # close the connection after this response (send_header sets
            # close_connection for this header).
            self.send_header("Connection", "close")
        super().end_headers()

    def _send_library_epoch_header(self):
        epoch = getattr(self, "_prks_library_epoch", None)
        if epoch is not None:  # set only by an authenticated process's admission
            from backend.api.session import EPOCH_HEADER

            self.send_header(EPOCH_HEADER, str(epoch))

    def log_request(self, code="-", size="-"):
        ctx = self._request_context()
        LOGGER.debug(
            "request_access method=%s route=%s status=%s request_id=%s",
            ctx["method"],
            ctx["route"],
            code,
            ctx["request_id"],
        )

    def log_error(self, format, *args):
        LOGGER.debug(
            "stdlib_http_error request_id=%s",
            safe_log_id(getattr(self, "_prks_request_id", "")),
        )

    def log_message(self, format, *args):
        LOGGER.debug(
            "stdlib_http_message request_id=%s",
            safe_log_id(getattr(self, "_prks_request_id", "")),
        )

    def _probe(self, path, *, head=False):
        """``/healthz``: no-I/O liveness. ``/readyz``: the PostgreSQL readiness checks.

        This never invokes API session admission or returns configuration.
        ``/readyz`` answers 200 only when the server carries a readiness
        check (``backend.readiness``: pool, schema, setup, paired root and
        reconciled start generation) and it passes; a server started without
        one stays not ready.
        """
        if path == '/healthz':
            ready = True
        else:
            readiness = getattr(self.server, 'prks_readiness', None)
            try:
                ready = readiness is not None and bool(readiness.ready())
            except Exception as exc:  # a probe fails closed, never with a 500
                LOGGER.warning("readiness_check_failed error_type=%s", safe_error_type(exc))
                ready = False
        body = b"ok" if ready else b"not ready"
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        probe_path = unquote(parsed_path.path).rstrip('/')
        if probe_path in ('/healthz', '/readyz'):
            self._probe(probe_path)
            return
        if parsed_path.path.startswith('/api/'):
            if not self._prks_admit(parsed_path):
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_get(parsed_path)
        else:
            requested = unquote(parsed_path.path or '/')
            safe_rel = requested.lstrip('/')
            frontend_root = os.path.realpath(frontend_dir)
            candidate = os.path.realpath(os.path.join(frontend_root, safe_rel))
            if not (candidate == frontend_root or candidate.startswith(frontend_root + os.sep)):
                self.send_error(404, "Not Found")
                return
            # Serve matching files or fallback to index.html for SPA hash routing
            if not os.path.exists(candidate):
                self.path = '/index.html'
            else:
                self.path = parsed_path.path
            super().do_GET()

    def do_HEAD(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        probe_path = unquote(parsed_path.path).rstrip('/')
        if probe_path in ('/healthz', '/readyz'):
            self._probe(probe_path, head=True)
            return
        if parsed_path.path.startswith('/api/'):
            if not self._prks_admit(parsed_path):
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_head(parsed_path)
        else:
            requested = unquote(parsed_path.path or '/')
            safe_rel = requested.lstrip('/')
            frontend_root = os.path.realpath(frontend_dir)
            candidate = os.path.realpath(os.path.join(frontend_root, safe_rel))
            if not (candidate == frontend_root or candidate.startswith(frontend_root + os.sep)):
                self.send_error(404, "Not Found")
                return
            if not os.path.exists(candidate):
                self.path = '/index.html'
            else:
                self.path = parsed_path.path
            super().do_HEAD()

    def do_POST(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            upload = parsed_path.path == processing_api.COLLECTION
            # The upload's body is read by its route, if at all: every answer
            # before that (admission included) closes the connection.
            self._prks_unread_body = upload
            if not self._prks_admit(parsed_path):
                return
            if parsed_path.path.startswith(_BACKUP_ROUTE_PREFIX):
                # A JSON body is read before the Library gate, as every other
                # POST does: a client that stalls mid-body must not hold the
                # restore's exclusive gate. ``stage`` streams its own body.
                backup_body = None
                if parsed_path.path != '/api/backups/stage':
                    backup_body = self._read_json_body()
                    if backup_body is _JSON_BODY_REJECTED:
                        return
                with self._library_access(parsed_path), self._acting_for_request():
                    try:
                        self._handle_postgres_backup_post(parsed_path.path, backup_body)
                    except Exception as exc:
                        self._send_internal_error(exc)
                return
            if upload:
                with self._library_access(parsed_path), self._acting_for_request():
                    try:
                        processing_api.handle_upload(self, db)
                    except Exception as exc:
                        self._send_internal_error(exc)
                return
            data = self._read_json_body()
            if data is _JSON_BODY_REJECTED:
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_post(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PATCH(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._prks_admit(parsed_path):
                return
            data = self._read_json_body()
            if data is _JSON_BODY_REJECTED:
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_patch(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_patch(self, parsed_path, data=_JSON_BODY_REJECTED):
        path = parsed_path.path
        try:
            if data is _JSON_BODY_REJECTED:
                data = self._read_json_body()
                if data is _JSON_BODY_REJECTED:
                    return
            if data is _JSON_BODY_ABSENT:
                data = {}
            if work_roles_api.handle_patch(self, db, path, data):
                return
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                body = dict(data)
                # Adoption lock + existence check only when the stored path
                # would actually change. Echoing the same file_path on a
                # title/status PATCH must not 409 missing_pdf when the bytes
                # are already gone (CodeRabbit Minor on #172); the echo is
                # dropped below so this request cannot rewrite it after a
                # concurrent retarget/cleanup (owner P2 TOCTOU on #172).
                file_path_changing = "file_path" in body and (
                    str(body.get("file_path") or "") != str(_stored_work_file_path(self, w_id) or "")
                )
                if file_path_changing:
                    # Adopting another file is not ported. Refuse before any
                    # part of the PATCH (a folder move, notes) commits, so a
                    # 501 never hides an applied change.
                    self._send_not_ported("Changing a Work's file_path is not ported to PostgreSQL yet.")
                    return
                if 'folder_id' in body:
                    raw_folder = body.pop('folder_id')
                    try:
                        folders_api.move_work(self, db, w_id, raw_folder)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                if body:
                    # An unchanged echo never rewrites the stored path.
                    body.pop("file_path", None)
                    notes_present = "text_content" in body
                    notes_text = body.pop("text_content", None)
                    if notes_present:
                        try:
                            research_notes_api.save(self, db, w_id, notes_text)
                        except ResearchError as e:
                            self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                            return
                    if body:
                        try:
                            _update_postgres_work_metadata(w_id, body)
                        except ValueError as e:
                            # A refused length is the caller's mistake, not a
                            # server fault: say so rather than 500.
                            self.send_json(400, {'error': str(e)})
                            return
                self.send_json(200, {'status': 'updated'})
            elif playlists_api.handle_patch(self, db, path, data):
                return
            elif persons_api.handle_patch(self, db, path, data):
                return
            elif person_groups_api.handle_patch(self, db, path, data):
                return
            elif folders_api.handle_patch(self, db, path, data):
                return
            elif settings_api.handle_patch(self, db, path, data):
                return
            elif saved_views_api.handle_patch(self, db, path, data):
                return
            elif processing_api.handle_patch(self, db, path, data):
                return
            elif concepts_api.handle_patch(self, db, path, data):
                return
            elif positions_api.handle_patch(self, db, path, data):
                return
            elif arguments_api.handle_patch(self, db, path, data):
                return
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def do_PUT(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._prks_admit(parsed_path):
                return
            data = self._read_json_body()
            if data is _JSON_BODY_REJECTED:
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_put(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_put(self, parsed_path, data=_JSON_BODY_REJECTED):
        path = parsed_path.path
        try:
            if data is _JSON_BODY_REJECTED:
                data = self._read_json_body()
                if data is _JSON_BODY_REJECTED:
                    return
            if data is _JSON_BODY_ABSENT:
                data = {}
            if concepts_api.handle_put(self, db, path, data):
                return
            elif arguments_api.handle_put(self, db, path, data):
                return
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def do_DELETE(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._prks_admit(parsed_path):
                return
            with self._library_access(parsed_path), self._acting_for_request():
                self.handle_api_delete(parsed_path)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_delete(self, parsed_path):
        path = parsed_path.path
        query_params = parse_qs(parsed_path.query)
        try:
            if work_roles_api.handle_delete(self, db, path, query_params):
                return
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                _delete_work(w_id)
                self.send_json(200, {'status': 'deleted'})
            elif playlists_api.handle_delete(self, db, path):
                return
            elif folders_api.handle_delete(self, db, path):
                return
            elif tags_api.handle_delete(self, db, path, query_params):
                return
            elif publishers_api.handle_delete(self, db, path, query_params):
                return
            elif persons_api.handle_delete(self, db, path):
                return
            elif person_groups_api.handle_delete(self, db, path):
                return
            elif saved_views_api.handle_delete(self, db, path):
                return
            elif processing_api.handle_delete(self, db, path):
                return
            elif concepts_api.handle_delete(self, db, path):
                return
            elif positions_api.handle_delete(self, db, path):
                return
            elif arguments_api.handle_delete(self, db, path):
                return
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def _send_pdf_bytes(self, pdf_path: str, *, head_only: bool = False) -> None:
        """Serve PDF with Content-Length and Range support (required for HTTP/1.1 + WASM PDF engines)."""
        try:
            file_size = os.path.getsize(pdf_path)
            f = open(pdf_path, "rb")
        except OSError:
            self.send_error(404, "PDF not found")
            return
        with f:
            self._send_pdf_stream(file_size, f, head_only=head_only)

    def _send_pdf_stream(self, file_size: int, f, *, head_only: bool = False) -> None:
        """Serve ``file_size`` PDF bytes read from ``f`` (positioned at 0), with Range support."""
        range_header = (self.headers.get("Range") or self.headers.get("range") or "").strip()
        start = 0
        end = file_size - 1
        use_partial = False

        if range_header.lower().startswith("bytes="):
            try:
                spec = range_header.split("=", 1)[1].strip().split(",", 1)[0].strip()
                if spec.startswith("-"):
                    suffix = int(spec[1:])
                    start = max(0, file_size - suffix)
                    end = file_size - 1
                    use_partial = True
                elif "-" in spec:
                    a, b = spec.split("-", 1)
                    start = int(a) if a.strip() else 0
                    end = int(b) if b.strip() else file_size - 1
                    use_partial = True
                if use_partial:
                    end = min(end, file_size - 1)
                    start = max(0, start)
                    if start > end or start >= file_size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.end_headers()
                        return
            except (ValueError, IndexError):
                start, end = 0, file_size - 1
                use_partial = False

        if use_partial and (start > 0 or end < file_size - 1):
            length = end - start + 1
            ctx = self._request_context()
            LOGGER.debug(
                "pdf_stream mode=partial method=%s route=%s start=%s end=%s file_size=%s request_id=%s",
                ctx["method"],
                ctx["route"],
                start,
                end,
                file_size,
                ctx["request_id"],
            )
            self.send_response(206)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store, max-age=0, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            if head_only:
                return
            _skip_to(f, start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
            return

        ctx = self._request_context()
        LOGGER.debug(
            "pdf_stream mode=full method=%s route=%s file_size=%s request_id=%s",
            ctx["method"],
            ctx["route"],
            file_size,
            ctx["request_id"],
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store, max-age=0, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()
        if head_only:
            return
        remaining = file_size
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def handle_api_head(self, parsed_path):
        path = parsed_path.path
        try:
            if path.startswith('/api/pdfs/'):
                pdf_path = _served_pdf_path(self, path)
                if pdf_path:
                    self._send_pdf_bytes(pdf_path, head_only=True)
                else:
                    self.send_error(404, "PDF not found")
            elif processing_api.handle_head(self, db, path):
                return
            else:
                self.send_error(405, "Method Not Allowed")
        except Exception as exc:
            self._send_internal_error(exc)

    @staticmethod
    def _prks_etag_value_for_compare(raw: str) -> str:
        s = (raw or "").strip()
        if s.upper().startswith("W/"):
            s = s[2:].lstrip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1]
        return s

    def _prks_if_none_match(self, etag: str) -> bool:
        client = (self.headers.get("If-None-Match") or "").strip()
        if not client:
            return False
        if client == "*":
            return True
        want = self._prks_etag_value_for_compare(etag)
        for part in client.split(","):
            if self._prks_etag_value_for_compare(part) == want:
                return True
        return False

    def _send_json_not_modified(self, etag: str) -> None:
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()

    def _send_person_profile_image_bytes(
        self, body: bytes, subtype: str, max_age: int = 86400
    ) -> None:
        actual = identify_cached_portrait_subtype(body) or subtype
        if actual == "webp":
            mime = "image/webp"
        elif actual == "jpeg":
            mime = "image/jpeg"
        else:
            mime = "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", f"private, max-age={max_age}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_job_diagnostics(self):
        """Owner-only job diagnostics (§12.4); the route exists only with ``PRKS_AUTH``.

        ``backend.api.session`` admitted the request for ADMIN_LIBRARY. A
        server started without jobs answers like any unknown route.
        """
        jobs = getattr(self.server, "prks_jobs", None)
        if jobs is None:
            self.send_error(404, "API endpoint not found")
            return
        body = jobs.diagnostics()
        if body is None:
            self.send_json(503, {"error": "unavailable", "code": "unavailable"})
            return
        self.send_json(200, dump_response(JobDiagnostics, body))

    def handle_api_get(self, parsed_path):
        query = parse_qs(parsed_path.query)
        path = parsed_path.path
        
        try:
            if path == '/api/diagnostics/performance':
                self.send_json(200, dump_response(PerformanceSnapshot, performance_snapshot()))
            elif path == '/api/diagnostics/jobs':
                self._handle_job_diagnostics()
            elif works_api.handle_catalog(self, db, path, query):
                return
            elif playlists_api.handle_get(self, db, path):
                return
            elif path.startswith('/api/works/') and path.endswith('/thumbnail'):
                # /api/works/{id}/thumbnail?page=N
                parts = path.split('/')
                if len(parts) != 5:
                    self.send_error(404, "API endpoint not found")
                    return
                w_id = parts[3]
                try:
                    page_q = query.get('page', [''])[0]
                except Exception:
                    page_q = ''
                thumb = works_api.thumbnail_fields(self, db, w_id)
                if not thumb:
                    self.send_error(404, "Work not found")
                    return
                # Ownership identity, not the last '/'-separated piece: a
                # stored path that is not exactly /api/pdfs/<filename> does not
                # own a managed PDF and must not resolve to some other one.
                pdf_filename = managed_pdf_filename(str(thumb.get('file_path') or ''))
                if not pdf_filename:
                    self.send_error(404, "PDF not found")
                    return
                pdf_path = _safe_pdf_path_in_pdfs_dir(pdf_filename)
                if not pdf_path or not os.path.exists(pdf_path):
                    self.send_error(404, "PDF not found")
                    return

                # Choose page (1-based)
                page = None
                if page_q and str(page_q).strip():
                    try:
                        page = int(str(page_q).strip())
                    except Exception:
                        page = None
                if page is None:
                    try:
                        page = int(thumb.get('thumb_page') or 1)
                    except Exception:
                        page = 1
                if page < 1:
                    page = 1

                try:
                    pdf_mtime = float(os.path.getmtime(pdf_path))
                except Exception:
                    pdf_mtime = 0.0

                cache_base = prks_thumb_cache_stem(
                    w_id, page, thumb.get("primary_asset_id")
                )
                path_webp = os.path.join(thumbs_dir, cache_base + ".webp")

                cache_path: str | None = None
                serve_mime = "image/webp"
                if os.path.exists(path_webp):
                    try:
                        if os.path.getmtime(path_webp) >= pdf_mtime:
                            cache_path = path_webp
                    except OSError:
                        pass

                cache_hit = cache_path is not None
                generated_bytes: bytes | None = None
                if cache_hit:
                    try:
                        record_counter("thumbnail_cache_hits")
                    except Exception:
                        pass
                else:
                    try:
                        record_counter("thumbnail_cache_misses")
                    except Exception:
                        pass

                if not cache_hit:
                    if os.path.exists(path_webp):
                        try:
                            os.remove(path_webp)
                        except OSError:
                            pass
                    try:
                        import pymupdf as fitz  # PyMuPDF
                    except Exception as e:
                        # Common when the image/venv omits `pip install -r requirements.txt` (see Dockerfile).
                        LOGGER.warning(
                            "thumbnail_fitz_import_failed error_type=%s request_id=%s",
                            safe_error_type(e),
                            safe_log_id(self._prks_request_id),
                        )
                        self.send_error(404, "Thumbnail unavailable")
                        return

                    try:
                        t_render = clock_ns()
                        doc = fitz.open(pdf_path)
                        try:
                            page_index = page - 1
                            if page_index < 0 or page_index >= doc.page_count:
                                page_index = 0
                            pg = doc.load_page(page_index)
                            rect = pg.rect
                            width = float(rect.width) if rect and rect.width else 612.0
                            target_w = 560.0
                            scale = target_w / width if width > 0 else 1.0
                            if scale > 2.25:
                                scale = 2.25
                            if scale < 0.6:
                                scale = 0.6
                            mat = fitz.Matrix(scale, scale)
                            pix = pg.get_pixmap(matrix=mat, alpha=False)
                            try:
                                record_span("thumbnail_render", clock_ns() - t_render)
                            except Exception:
                                pass
                            t_encode = clock_ns()
                            generated_bytes, thumb_sub = _prks_thumbnail_bytes_from_pixmap(pix)
                            try:
                                record_span("thumbnail_encode", clock_ns() - t_encode)
                            except Exception:
                                pass
                            serve_mime = f"image/{thumb_sub}"
                            # v2 cache: WebP only (lossy default); fallbacks use matching ext.
                            ext = "webp" if thumb_sub == "webp" else thumb_sub
                            cache_path = os.path.join(thumbs_dir, f"{cache_base}.{ext}")
                        finally:
                            try:
                                doc.close()
                            except Exception:
                                pass

                        # Best-effort cache write: if this fails (read-only volume, perms, etc),
                        # still serve the generated image to the client.
                        tmp_name = f"{cache_base}.{ext}.tmp"
                        try:
                            publish_derived_cache_bytes(
                                thumbs_dir, f"{cache_base}.{ext}", generated_bytes
                            )
                        except Exception:
                            try:
                                tmp_path = os.path.join(thumbs_dir, tmp_name)
                                if os.path.exists(tmp_path):
                                    os.remove(tmp_path)
                            except Exception:
                                pass
                    except Exception as exc:
                        # If a particular PDF can't be rendered, don't take down the whole request path.
                        LOGGER.warning(
                            "thumbnail_render_failed work_id=%s page=%s request_id=%s error_type=%s",
                            safe_log_id(w_id),
                            page,
                            safe_log_id(self._prks_request_id),
                            safe_error_type(exc),
                        )
                        self.send_error(404, "Thumbnail unavailable")
                        return

                if not (cache_path and os.path.exists(cache_path)) and not generated_bytes:
                    self.send_error(404, "Thumbnail unavailable")
                    return
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", serve_mime)
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    if cache_path and os.path.exists(cache_path):
                        with open(cache_path, "rb") as f:
                            self.wfile.write(f.read())
                    else:
                        self.wfile.write(generated_bytes)
                except Exception as exc:
                    self._send_internal_error(exc)
            elif works_api.handle_state(self, db, path):
                return
            elif work_roles_api.handle_get(self, db, path):
                return
            elif tags_api.handle_get(self, db, path, query):
                return
            elif works_api.handle_detail(self, db, path, research_index):
                return
            elif annotations_api.handle_get(self, db, path, query):
                return
            elif folders_api.handle_get(self, db, path):
                return
            elif persons_api.handle_get(self, db, path):
                return
            elif person_groups_api.handle_get(self, db, path):
                return
            elif saved_views_api.handle_get(self, db, path):
                return
            elif processing_api.handle_get(self, db, path, parsed_path.query):
                return
            elif path == '/api/research-graph':
                raw_people = (query.get('people') or [''])[0].strip().lower()
                include_people = raw_people in ('1', 'true', 'yes', 'on')
                try:
                    snapshot = research_index.build_graph(db, include_people=include_people)
                except GraphTooLargeError as e:
                    body = {
                        'error': str(e),
                        'code': e.code,
                        'node_count': e.extra.get('node_count', 0),
                        'edge_count': e.extra.get('edge_count', 0),
                    }
                    self.send_json(e.http_status, body)
                    return
                self.send_json(200, snapshot.to_dict())
            elif concepts_api.handle_get(self, db, path):
                return
            elif path == '/api/openapi.json' or path == '/api/openapi/positions.json':
                # Vertical-slice OpenAPI for Positions (#180 / #45). Additive;
                # not yet the full PRKS surface.
                self.send_json(200, positions_openapi_document())
            elif path == '/api/openapi/performance-diagnostics.json':
                self.send_json(200, performance_diagnostics_openapi_document())
            elif path == '/api/openapi/job-diagnostics.json':
                self.send_json(200, job_diagnostics_openapi_document())
            elif path == '/api/openapi/publishers.json':
                self.send_json(200, publishers_openapi_document())
            elif path == '/api/openapi/saved-views.json':
                self.send_json(200, saved_views_openapi_document())
            elif path == '/api/openapi/processing-items.json':
                self.send_json(200, processing_items_openapi_document())
            elif positions_api.handle_get(self, db, path):
                return
            elif arguments_api.handle_get(self, db, path, query):
                return
            elif persons_api.handle_metadata_state(self, db, path):
                return
            elif folders_api.handle_work_state(self, db, path):
                return
            elif positions_api.handle_sync_state(self, db, path):
                return
            elif arguments_api.handle_sync_state(self, db, path):
                return
            elif concepts_api.handle_sync_state(self, db, path):
                return
            elif playlists_api.handle_work_state(self, db, path):
                return
            elif person_groups_api.handle_person_state(self, db, path):
                return
            elif persons_api.handle_detail(self, db, path):
                return
            elif works_api.handle_browse(self, db, path):
                return
            elif search_api.handle_get(self, db, path, query):
                return
            elif publishers_api.handle_get(self, db, path, query):
                return
            elif path.startswith('/api/pdfs/'):
                pdf_path = _served_pdf_path(self, path)
                if pdf_path:
                    self._send_pdf_bytes(pdf_path)
                else:
                    self.send_error(404, "PDF not found")
            elif works_api.handle_bibtex(self, db, path):
                return
            elif settings_api.handle_get(self, db, path):
                return
            elif path == '/api/backups/download':
                self._handle_postgres_backup_download((query.get('token') or [''])[0])
            elif path.startswith(_BACKUP_ROUTE_PREFIX):
                self.send_error(404, "API endpoint not found")
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def handle_api_post(self, parsed_path, data=_JSON_BODY_REJECTED):
        path = parsed_path.path
        try:
            if data is _JSON_BODY_REJECTED:
                data = self._read_json_body()
                if data is _JSON_BODY_REJECTED:
                    return
            json_body_absent = data is _JSON_BODY_ABSENT
            if json_body_absent:
                data = {}
            if path == '/api/sync/operations':
                self._handle_postgres_sync_operation(data)
                return

            if path == '/api/diagnostics/performance/reset':
                if json_body_absent:
                    self.send_json(
                        400,
                        {
                            "error": "JSON object body required",
                            "code": "invalid_request",
                        },
                    )
                    return
                _request, err = parse_request(PerformanceDiagnosticsResetRequest, data)
                if err is not None:
                    self.send_json(400, err)
                    return
                reset_performance()
                self.send_json(
                    200,
                    dump_response(PerformanceDiagnosticsReset, {"status": "reset"}),
                )
                return

            if path == '/api/client-errors':
                try:
                    payload = self._parse_client_error_payload(data)
                except ValueError as e:
                    self.send_json(400, {"error": str(e)})
                    return
                LOGGER.error(
                    format_client_error_log(
                        payload,
                        request_id=self._prks_request_id,
                    )
                )
                self.send_json(200, {"status": "logged", "request_id": self._prks_request_id})
            elif path == '/api/works/bulk':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    result = db.bulk_update_works(data)
                except BulkWorkError as e:
                    self.send_json(int(e.http_status), {'error': str(e)})
                    return
                LOGGER.info(
                    "bulk_work_update action=%s work_count=%s target_count=%s request_id=%s",
                    safe_log_label(result.get("action")),
                    int(result.get("requested") or 0),
                    int(result.get("target_count") or 0),
                    safe_log_id(self._prks_request_id),
                )
                self.send_json(
                    200,
                    {
                        "status": result.get("status"),
                        "action": result.get("action"),
                        "requested": result.get("requested"),
                        "updated": result.get("updated"),
                    },
                )
            elif path == '/api/works/reindex-pdf-text':
                force = bool(data.get("force", False)) if isinstance(data, dict) else False
                summary = text_index.reconcile_all(db, force=force)
                self.send_json(200, {"status": "ok", **summary})
            elif path == '/api/works/linearize-existing-pdfs':
                # Managed PDFs on PostgreSQL are immutable stored objects;
                # rewriting them in place is not ported.
                self._send_not_ported("Linearizing existing PDFs is not ported to PostgreSQL yet.")
                return
            elif path == '/api/works':
                file_path = data.get('file_path', '')
                source_kind = (data.get('source_kind') or '').strip().lower()
                source_url = (data.get('source_url') or '').strip()

                # Video sources are currently supported for YouTube only. Validate
                # authoritatively before any mutation so a malformed/non-YouTube
                # URL can never create a broken Video Work.
                if source_kind == 'video' and not _validate_youtube_url(source_url):
                    self.send_json(400, {'error': 'Invalid YouTube URL'})
                    return

                # A named destination that does not exist is refused before
                # any bytes are stored or a row is created, matching the
                # durable CREATE_WORK boundary. Filing only after the commit
                # used to fail with the Work already created, so a retry
                # duplicated it.
                requested_folder = data.get("folder_id")
                requested_folder = (
                    str(requested_folder).strip() if requested_folder is not None else ""
                )
                if requested_folder and not folders_api.exists(self, db, requested_folder):
                    self.send_json(404, {
                        'error': 'The selected folder no longer exists.',
                        'code': 'FOLDER_NOT_FOUND',
                    })
                    return
                # Same for the People the Work is born with: a Person this
                # server has not heard of yet (e.g. still queued on the
                # device that created it) would fail its foreign key only
                # after the Work was committed.
                # Only roles creation would insert count: one it skips (no
                # role type, unknown type) must not block the Work.
                if work_roles_api.missing_initial_person_ids(self, db, data.get('roles')):
                    self.send_json(409, {
                        'error': 'A person on this file has not finished saving yet.',
                        'code': 'PERSON_NOT_FOUND',
                    })
                    return

                postgres_upload = None
                # Upload: PDF (existing behavior)
                if data.get('file_b64') and data.get('file_name'):
                    try:
                        decoded_pdf = base64.b64decode(data['file_b64'], validate=True)
                    except (binascii.Error, ValueError):
                        self.send_json(400, {'error': 'Invalid file_b64 payload'})
                        return
                    # The facade stores the bytes as objects and adopts
                    # them in its create (objects.ingest, §10, §11.2).
                    postgres_upload = (str(data['file_name']), decoded_pdf)

                provider = (data.get('provider') or '').strip().lower()
                provider_id = (data.get('provider_id') or '').strip()
                thumb_url = (data.get('thumb_url') or '').strip()
                source_mime = (data.get('source_mime') or '').strip()
                urldate = (data.get('urldate') or '').strip()

                # Video ingest: URL + oEmbed metadata.
                #
                # Decided by the EFFECTIVE kind, using the same authoritative
                # function `add_work()` classifies with. A caller that omitted
                # a redundant `source_kind` still created a video -- the
                # inference makes "no file, has a URL" one -- and enriching only
                # the explicitly labelled ones meant two Works with identical
                # canonical identity got different titles, authors and
                # thumbnails purely because one request said so twice.
                # An upload the facade has not stored yet is still a file.
                classified_file = file_path or ("upload" if postgres_upload else "")
                if effective_source_kind(source_kind, source_url, classified_file) == 'video':
                    if not provider:
                        try:
                            host = (urlparse(source_url).netloc or '').lower()
                        except Exception:
                            host = ''
                        if _is_youtube_host(host):
                            provider = 'youtube'
                    if provider == 'youtube' and not provider_id:
                        provider_id = _youtube_video_id(source_url) or ''
                    meta = _fetch_youtube_oembed(source_url) if provider == 'youtube' else None
                    if meta:
                        if not thumb_url and meta.get('thumbnail_url'):
                            thumb_url = str(meta.get('thumbnail_url') or '').strip()
                        # If title not provided, fill from oEmbed.
                        incoming_title = (data.get('title') or '').strip()
                        if not incoming_title and meta.get('title'):
                            data['title'] = str(meta.get('title') or '').strip()
                        # If author_text not provided, fill from oEmbed author_name.
                        incoming_author = (data.get('author_text') or '').strip()
                        if not incoming_author and meta.get('author_name'):
                            data['author_text'] = str(meta.get('author_name') or '').strip()
                    if not urldate:
                        try:
                            urldate = time.strftime("%Y-%m-%d")
                        except Exception:
                            urldate = ""

                # A source identity that contradicts itself is the caller's
                # mistake, not a server fault: `add_work` refuses it at the
                # creation boundary, and that refusal is a 400.
                work_fields = dict(
                    title=data.get('title', 'Untitled'),
                    status=data.get('status', 'Not Started'),
                    abstract=data.get('abstract', ''),
                    text_content=data.get('text_content', ''),
                    published_date=data.get('published_date', ''),
                    author_text=data.get('author_text', ''),
                    year=data.get('year', ''),
                    publisher=data.get('publisher', ''),
                    location=data.get('location', ''),
                    edition=data.get('edition', ''),
                    journal=data.get('journal', ''),
                    volume=data.get('volume', ''),
                    issue=data.get('issue', ''),
                    pages=data.get('pages', ''),
                    isbn=data.get('isbn', ''),
                    doi=data.get('doi', ''),
                    doc_type=data.get('doc_type', 'article'),
                    source_kind=source_kind,
                    source_url=source_url,
                    source_mime=source_mime,
                    thumb_url=thumb_url,
                    provider=provider,
                    provider_id=provider_id,
                    urldate=urldate,
                    thumb_page=data.get('thumb_page'),
                    private_notes=data.get('private_notes', ''),
                )
                # No adoption lock or discard here: the facade checks an
                # adopted file under the actor's edit right, and bytes a
                # refused create stored are the orphan sweep's (§11.2).
                try:
                    w_id = _create_postgres_work(file_path, postgres_upload, work_fields)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                try:
                    text_index.sync_work(w_id, file_path)
                except Exception as e:
                    LOGGER.warning(
                        "work_create_text_index_failed work_id=%s error_type=%s",
                        safe_log_id(w_id),
                        safe_error_type(e),
                    )
                # Undo this create: a PDF this request uploaded goes with it;
                # existing bytes it only adopted were never its to remove.
                def undo_create():
                    _delete_work(w_id, keep_managed_pdf=not postgres_upload)

                folder_id = data.get("folder_id")
                raw_folder = str(folder_id).strip() if folder_id is not None else ""
                if raw_folder:
                    try:
                        folders_api.file_work(self, db, raw_folder, w_id)
                    except ValueError as e:
                        undo_create()
                        self.send_json(409, {'error': str(e)})
                        return
                else:
                    try:
                        unc_id = folders_api.ensure_uncategorized(self, db)
                        folders_api.file_work(self, db, unc_id, w_id)
                    except ValueError as e:
                        undo_create()
                        self.send_json(409, {'error': str(e)})
                        return
                
                # Link persons/roles provided during upload: all of them in one
                # transaction, which also re-checks every Person under the write
                # lock, so a refusal leaves no role and no alias change behind.
                try:
                    work_roles_api.insert_initial(self, db, w_id, data.get('roles', []))
                except MissingPersonError:
                    # Deleted after the up-front check: same outcome as that
                    # check, through the existing compensation.
                    undo_create()
                    self.send_json(409, {
                        'error': 'A person on this file no longer exists.',
                        'code': 'PERSON_NOT_FOUND',
                    })
                    return

                # Optionally attach to playlist. Last: it writes playlist
                # revisions that the refusals above could not undo.
                playlist_id = (data.get('playlist_id') or '').strip()
                if playlist_id:
                    try:
                        playlists_api.add_item(self, db, playlist_id, w_id)
                    except Exception as exc:
                        # best-effort: do not fail work creation if playlist link fails
                        LOGGER.warning(
                            "work_playlist_attach_failed playlist_id=%s work_id=%s request_id=%s error_type=%s",
                            safe_log_id(playlist_id),
                            safe_log_id(w_id),
                            safe_log_id(self._prks_request_id),
                            safe_error_type(exc),
                        )

                self.send_json(200, {'id': w_id})
            elif playlists_api.handle_post(self, db, path, data):
                return
            elif folders_api.handle_post(self, db, path, data):
                return
            elif person_groups_api.handle_post(self, db, path, data):
                return
            elif saved_views_api.handle_post(self, db, path, data):
                return
            elif processing_api.handle_post(self, db, path, data):
                return
            elif concepts_api.handle_post(self, db, path, data):
                return
            elif positions_api.handle_post(self, db, path, data):
                return
            elif arguments_api.handle_post(self, db, path, data):
                return
            elif persons_api.handle_post(self, db, path, data):
                return
            elif tags_api.handle_post(self, db, path, data):
                return
            elif publishers_api.handle_post(self, db, path, data):
                return
            elif path.startswith('/api/works/') and path.endswith('/opened'):
                # Explicit "the user opened this Work" event, with server-now
                # as this caller's event time. GET /api/works/:id is a pure
                # read. PRKS's own UI takes the durable MARK_WORK_OPENED path
                # instead, so that an open made from cache while the server is
                # unreachable is recorded identically; this endpoint remains
                # for other canonical callers and shares the same max-register
                # helper, so the column can never acquire two meanings.
                parts = path.split('/')
                if len(parts) != 5:
                    self.send_error(404, "API endpoint not found")
                    return
                try:
                    marked = db.mark_work_opened(parts[3])
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                if not marked:
                    self.send_error(404, "Work not found")
                    return
                self.send_json(200, {'status': 'opened'})
            elif work_roles_api.handle_post(self, db, path, data):
                return
            elif annotations_api.handle_post(self, db, path, data):
                return
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)


    def _validate_backup_stage_headers(self) -> int | None:
        types = self.headers.get_all("Content-Type") or []
        if len(types) != 1 or not octet_stream_content_type_allowed(types[0]):
            self._reject_request(415, "unsupported_media_type", "unsupported_media_type")
            return None
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError):
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        if content_length < 0:
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        max_bytes = backup_max_upload_bytes()
        if content_length > max_bytes:
            self.send_json(413, {"error": "request_too_large"})
            return None
        return content_length

    def _handle_postgres_sync_operation(self, data) -> None:
        """``POST /api/sync/operations`` on the PostgreSQL Library (§8.4, §11.4).

        ``backend.sync.protocol`` claims, applies and ledgers the operation in
        one transaction for the admitted context; a refusal (epoch, device,
        role) propagates to ``_send_internal_error``, which answers it as
        admission does. The Research index is kept by the database, so a note
        ACK only reads its refs, ``pending`` included (the Research-ACK fence).
        A deleted Work's files are the orphan sweep's (§11.2), never this
        route's.
        """
        from backend.sync.families import Environment
        from backend.sync.protocol import process_operation as process_postgres_operation

        status, result = process_postgres_operation(
            db.database, self._prks_request_context, data, Environment(pdfs_dir=pdfs_dir))
        acknowledged = status == 200 and result.get("code") == "ACKNOWLEDGED"
        operation = data.get("operation") if isinstance(data, dict) else None
        if acknowledged and operation == work_note_sync.RESEARCH_OPERATION:
            # The note and its ledger row are committed; a failed derived read
            # leaves the refs out (the client reads them later) and never turns
            # a saved note into an error.
            try:
                result["research_refs"] = research_index.work_research_refs(data.get("entity_id"), db)
            except Exception as e:
                LOGGER.warning(
                    "research_refs_read_failed request_id=%s error_type=%s",
                    safe_log_id(self._prks_request_id), safe_error_type(e))
        if acknowledged and operation == "DELETE_WORK":
            # Paths are not durable client state; strip before the wire.
            result = {"code": result["code"], "work_id": result["work_id"], "changed": result["changed"]}
        self.send_json(status, result)

    def _handle_postgres_backup_post(self, path: str, data: object = None) -> None:
        """``POST /api/backups/{progress,stage,restore}`` on the PostgreSQL Library (§11.4).

        ``stage`` is dispatched before its body is read and streams the
        archive itself; every earlier answer closes the connection. The other
        routes' JSON ``data`` was read before the Library gate was taken. A
        restore replaces the PostgreSQL Library (``backend.api.maintenance``).
        """
        from backend.api import maintenance as maintenance_api

        ctx = self._prks_request_context
        root = _bound_storage.root if _bound_storage is not None else None
        if root is None:
            self._send_internal_error()
            return
        if path == '/api/backups/stage':
            self._prks_unread_body = True
            content_length = self._validate_backup_stage_headers()
            if content_length is None:
                return

            # The upload holds the Library's read gate, which a restore waits
            # out: read it with the Processing upload's idle timeout and
            # deadline, so a stalled client cannot hold the gate.
            sock = self.connection
            previous_timeout = sock.gettimeout()

            def receive(dest: str) -> None:
                body = processing_api.TimedBody(self.rfile, sock, content_length)
                stream_upload_to_file(body, dest, expected_length=content_length,
                                      max_bytes=backup_max_upload_bytes())
                self._prks_unread_body = False

            try:
                body = maintenance_api.stage_upload(db, ctx, root, receive)
            except processing_api.UploadTimedOut:
                # The body is unread, so this answer closes the connection.
                self.send_json(408, {"error": "The backup upload stalled; try again.",
                                     "reason": "upload_timeout", "request_id": self._prks_request_id})
                return
            except maintenance_api.StageRefused as exc:
                self.send_json(exc.status, {"error": exc.message, "reason": exc.reason,
                                            "request_id": self._prks_request_id})
                return
            except RestoreError as exc:
                self.send_json(exc.http_status, {"error": exc.message, "reason": exc.reason,
                                                 "request_id": self._prks_request_id})
                return
            finally:
                try:
                    sock.settimeout(previous_timeout)
                except OSError:
                    pass
            self.send_json(200, body)
            return
        if data is None or data is _JSON_BODY_ABSENT:
            data = {}
        if path == '/api/backups/progress':
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def write_line(payload: dict) -> None:
                self.wfile.write(maintenance_api.ndjson(payload))
                self.wfile.flush()

            try:
                maintenance_api.stream_backup(db, ctx, write_line)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                LOGGER.error("backup_progress_failed error_type=%s request_id=%s",
                             safe_error_type(exc), safe_log_id(self._prks_request_id))
                write_line({"phase": "failed", "percent": 0, "error": "Backup could not be created."})
            return
        if path == '/api/backups/restore':
            status, body, epoch = maintenance_api.restore_staged(db, ctx, root, data)
            if epoch is not None:
                # The Library the browser knew is gone: it reads the new epoch.
                self._prks_library_epoch = epoch
            if status != 200:
                body = {**body, "request_id": self._prks_request_id}
            self.send_json(status, body)
            return
        self.send_error(404, "API endpoint not found")

    def _handle_postgres_backup_download(self, token: str) -> None:
        """``GET /api/backups/download?token=<run>``: a published archive of the actor's Library."""
        from backend.api import maintenance as maintenance_api

        root = _bound_storage.root if _bound_storage is not None else None
        path = maintenance_api.archive_for_download(db, self._prks_request_context, root or "", token) \
            if root else None
        if path is None:
            self.send_json(404, {"error": "Backup is not available.", "reason": "unknown_token",
                                 "request_id": self._prks_request_id})
            return
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send_not_ported(self, message: str) -> None:
        """501 ``not_ported``: a route the PostgreSQL Library does not serve yet."""
        self.send_json(501, {"error": message, "code": "not_ported", "request_id": self._prks_request_id})



    def send_json(self, status, context, etag=None, precondition_checked=False):
        if etag and status == 200 and not precondition_checked and self._prks_if_none_match(etag):
            self._send_json_not_modified(etag)
            return
        t0 = clock_ns()
        body = json.dumps(context).encode("utf-8")
        try:
            record_span("json_encode", clock_ns() - t0)
        except Exception:
            pass
        ae = (self.headers.get("Accept-Encoding") or "").lower()
        use_gzip = "gzip" in ae and len(body) >= _PRKS_JSON_GZIP_MIN_BYTES
        if use_gzip:
            t1 = clock_ns()
            body = gzip.compress(body, compresslevel=6)
            try:
                record_span("gzip", clock_ns() - t1)
            except Exception:
                pass
        try:
            set_response_bytes(len(body))
        except Exception:
            pass
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if etag and status == 200:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        # A HEAD answer has the GET answer's headers and no body.
        if (getattr(self, "command", "") or "").upper() != "HEAD":
            self.wfile.write(body)

class PRKSThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True




def run_server(port=PORT, host=DEFAULT_HOST, auth=None, jobs=None, readiness=None):
    if _bound_storage is None:
        raise RuntimeError("storage is not bound; call bind_storage() before run_server()")
    host = normalize_listen_host(host)
    trusted_hosts = parse_trusted_hosts(os.environ.get("PRKS_TRUSTED_HOSTS", ""))
    _validate_listen_port(port)
    # Setup for allowing reusing address
    socketserver.TCPServer.allow_reuse_address = True
    with PRKSThreadingTCPServer((host, port), PRKSHandler) as httpd:
        httpd.prks_bind_host = host
        httpd.prks_trusted_hosts = trusted_hosts
        httpd.prks_access_gate = LibraryAccessGate()
        # Authentication (PRKS_AUTH, backend.domain.identity.web_start); None
        # only for test servers (prks_app always starts identity).
        httpd.prks_auth = auth
        # The PRKS_AUTH runtime's jobs (backend.jobs.embedded.WebJobs): the
        # embedded worker and the job diagnostics. None only for test servers.
        httpd.prks_jobs = jobs
        # GET /readyz (backend.readiness.PostgresReadiness); None keeps it 503.
        httpd.prks_readiness = readiness
        if auth is not None and auth.settings.public_origin is not None:
            httpd.prks_trusted_hosts = trusted_hosts | {auth.settings.public_origin[1]}
        LOGGER.info(
            "server_starting bind_scope=%s port=%s",
            safe_bind_scope(host),
            port,
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
