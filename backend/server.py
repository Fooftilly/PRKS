from backend import work_note_sync, work_role_sync, work_source_sync
from backend.sync_protocol import process_operation
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
import threading
from contextlib import nullcontext
from dataclasses import replace
from email.message import Message
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen, Request

# Add the parent directory to sys.path to ensure 'backend' module is resolvable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db_manager import (
    PRKSDatabase,
    BulkWorkError,
    effective_source_kind,
    SavedViewError,
    safe_pdf_path_under_dir,
    prks_thumb_cache_safe_wid,
    prks_thumb_cache_stem,
    prune_orphan_pdf_thumbnails,
    prks_person_image_cache_path,
    prks_person_image_legacy_bin_path,
    prks_delete_person_image_cache,
)
from backend.text_index import (
    PRKSTextIndex,
    get_text_index,
    reconcile_at_startup,
    replace_text_index,
    reset_text_index,
)
from backend.research_index import (
    PRKSResearchIndex,
    get_research_index,
    reconcile_research_index_at_startup,
    replace_research_index,
    reset_research_index,
)
from backend.research_network import ResearchError
import backend.research_network as research_network
from backend.pdf_annotations import WorkAnnotationError
from backend.research_graph import GraphTooLargeError, ResearchGraphBuilder
from backend.pdf_linearize import maybe_linearize_pdf_in_place, is_pdf_linearized
from backend.storage import paths
from backend.storage.config import StorageConfig
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
from backend.work_deletion import delete_work as delete_library_work
from backend.backup_restore import (
    BackupError,
    RestoreError,
    backup_max_upload_bytes,
    cleanup_expired_backup_jobs,
    cleanup_stale_staging,
    discard_temp_path,
    new_staging_upload_path,
    require_restore_upload_space,
    run_backup_with_progress,
    stage_restore,
    stream_upload_to_file,
    take_ready_backup,
    apply_restore,
)
from backend.person_image import (
    PersonImageUrlError,
    decode_and_transcode,
    fetch_and_prepare,
    identify_cached_portrait_subtype,
    normalize_person_image_url,
    read_legacy_portrait_bytes,
)
from backend.concurrency import LibraryAccessGate, request_access_mode

LOGGER = logging.getLogger("prks.server")

PORT = 8080
DEFAULT_HOST = "127.0.0.1"
# Default for `python prks_app.py --testing`; non-testing bind refuses this port (see _validate_listen_port).
PRKS_TESTING_DEFAULT_PORT = 8070

# Minimum uncompressed JSON size before gzip (Accept-Encoding: gzip).
_PRKS_JSON_GZIP_MIN_BYTES = 1024
_PRKS_MAX_JSON_BODY_BYTES = 50 * 1024 * 1024

# Get the path to the frontend directory
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dir = os.path.join(base_dir, "frontend")

# Ensure frontend dir exists so http.server doesn't crash on startup
os.makedirs(frontend_dir, exist_ok=True)

_bound_storage: StorageConfig | None = None
pdfs_dir: str | None = None
thumbs_dir: str | None = None
processing_dir: str | None = None
db: PRKSDatabase | None = None
text_index: PRKSTextIndex | None = None
research_index: PRKSResearchIndex | None = None


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


def bind_storage(config: StorageConfig) -> StorageConfig:
    processing_local = config.processing_dir
    try:
        os.makedirs(processing_local, exist_ok=True)
    except OSError:
        if not config.processing_fallback_allowed:
            raise
        processing_local = paths.processing_prod_fallback()
        os.makedirs(processing_local, exist_ok=True)
    if processing_local != config.processing_dir:
        config = replace(
            config,
            processing_dir=processing_local,
            processing_fallback_allowed=False,
        )

    os.makedirs(config.pdfs_dir, exist_ok=True)
    os.makedirs(config.thumbs_dir, exist_ok=True)

    db_local = PRKSDatabase(storage=config, schema_path="backend/db_schema.sql")
    candidate = PRKSTextIndex(storage=config)
    research_candidate = PRKSResearchIndex(storage=config)

    global _bound_storage, pdfs_dir, thumbs_dir, processing_dir, db, text_index, research_index
    previous_published = (
        _bound_storage,
        pdfs_dir,
        thumbs_dir,
        processing_dir,
        db,
        text_index,
        research_index,
    )
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    try:
        previous_research = get_research_index()
    except RuntimeError:
        previous_research = None
    try:
        replace_text_index(candidate)
        replace_research_index(research_candidate)
        _bound_storage = config
        pdfs_dir = config.pdfs_dir
        thumbs_dir = config.thumbs_dir
        processing_dir = config.processing_dir
        db = db_local
        text_index = candidate
        research_index = research_candidate
    except Exception:
        if previous_index is not None:
            replace_text_index(previous_index)
        else:
            reset_text_index()
        if previous_research is not None:
            replace_research_index(previous_research)
        else:
            reset_research_index()
        (
            _bound_storage,
            pdfs_dir,
            thumbs_dir,
            processing_dir,
            db,
            text_index,
            research_index,
        ) = previous_published
        raise
    return config


def _safe_pdf_path_in_pdfs_dir(url_last_segment: str) -> str | None:
    return safe_pdf_path_under_dir(pdfs_dir, url_last_segment)


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


def _prks_pil_to_jpeg_bytes(img, quality: int = 82) -> bytes | None:
    """JPEG fallback when WebP encode is unavailable."""
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
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        return out if out else None
    except Exception:
        return None


def _prks_write_person_image_cache(cache_path: str, body: bytes) -> None:
    parent = os.path.dirname(cache_path)
    os.makedirs(parent, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(body)
    os.replace(tmp, cache_path)


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


_PRKS_LAST_PDF_SAVE_TOKEN_BY_WORK: dict[str, str] = {}
_PRKS_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK: dict[str, str] = {}
_SAVE_TOKEN_LOCK = threading.Lock()
_PDF_MATERIALIZATION_LOCKS_GUARD = threading.Lock()
_PDF_MATERIALIZATION_LOCKS = {}


def _pdf_materialization_lock_for(work_id: str) -> threading.Lock:
    """Per-Work lock so validate → replace → mark cannot race another ACK."""
    with _PDF_MATERIALIZATION_LOCKS_GUARD:
        lock = _PDF_MATERIALIZATION_LOCKS.get(work_id)
        if lock is None:
            lock = threading.Lock()
            _PDF_MATERIALIZATION_LOCKS[work_id] = lock
        return lock


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
    Best-effort metadata fetch for YouTube URLs via oEmbed (no API key).
    Returns: {title, author_name, thumbnail_url} subset when successful.
    """
    if not url or not str(url).strip():
        return None
    oembed_url = "https://www.youtube.com/oembed?format=json&url=" + str(url).strip()
    try:
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
        gate = getattr(self.server, "prks_access_gate", None)
        if gate is None:
            return nullcontext()
        return gate.scope(mode)

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

    def _read_json_body(self):
        types = self.headers.get_all("Content-Type") or []
        if len(types) != 1 or not json_content_type_allowed(types[0]):
            self._reject_request(415, "unsupported_media_type", "unsupported_media_type")
            return None
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError):
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        if content_length < 0:
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        if content_length > _PRKS_MAX_JSON_BODY_BYTES:
            self.send_json(413, {"error": "request_too_large"})
            return None
        try:
            payload = self.rfile.read(content_length) if content_length else b""
        except Exception:
            self.send_json(400, {"error": "request_read_failed"})
            return None
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_json"})
            return None

    def _parse_client_error_payload(self, data):
        return client_error_log_fields(data)

    def parse_request(self):
        ok = super().parse_request()
        if not ok:
            return ok
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
        super().end_headers()

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

    def do_GET(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            with self._library_access(parsed_path):
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
        if parsed_path.path.startswith('/api/'):
            with self._library_access(parsed_path):
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
            if not self._validate_mutation_origin():
                return
            if parsed_path.path == '/api/backups/stage':
                self._handle_backup_stage(parsed_path)
                return
            data = self._read_json_body()
            if data is None:
                return
            with self._library_access(parsed_path):
                self.handle_api_post(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PATCH(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._validate_mutation_origin():
                return
            data = self._read_json_body()
            if data is None:
                return
            with self._library_access(parsed_path):
                self.handle_api_patch(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_patch(self, parsed_path, data=None):
        path = parsed_path.path
        try:
            if data is None:
                data = self._read_json_body()
                if data is None:
                    return
            if path.startswith('/api/processing-files/') and len(path.split('/')) == 4:
                pf_id = path.split('/')[-1]
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    row = db.update_processing_file(pf_id, data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, row)
            elif path.startswith('/api/works/') and path.endswith('/roles'):
                parts = path.split('/')
                if len(parts) != 5 or parts[4] != 'roles':
                    self.send_error(404, "API endpoint not found")
                    return
                w_id = parts[3]
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                person_id = (data.get('person_id') or '').strip()
                role_type = (data.get('role_type') or '').strip()
                oi_raw = data.get('order_index', 0)
                try:
                    order_index = int(oi_raw) if oi_raw is not None and str(oi_raw).strip() != '' else 0
                except (TypeError, ValueError):
                    self.send_json(400, {'error': 'order_index must be an integer'})
                    return
                if not person_id or not role_type:
                    self.send_json(400, {'error': 'person_id and role_type are required'})
                    return
                credit_name = data.get('credit_name', '')
                if credit_name is not None and not isinstance(credit_name, str):
                    self.send_json(400, {'error': 'credit_name must be a string'})
                    return
                try:
                    updated = db.update_role_credit_name(
                        w_id, person_id, role_type, order_index, credit_name or ''
                    )
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                if updated:
                    self.send_json(200, {'status': 'updated'})
                else:
                    self.send_json(404, {'error': 'role link not found'})
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                body = dict(data)
                if 'folder_id' in body:
                    raw_folder = body.pop('folder_id')
                    try:
                        db.move_work_to_folder(w_id, raw_folder)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                if body:
                    patched_file_path = "file_path" in body
                    notes_present = "text_content" in body
                    notes_text = body.pop("text_content", None)
                    if notes_present:
                        try:
                            research_network.save_work_notes(db, w_id, notes_text)
                        except ResearchError as e:
                            self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                            return
                        try:
                            research_index.sync_work(w_id, notes_text, db)
                        except Exception as e:
                            LOGGER.warning(
                                "research_index_sync_failed work_id=%s error_type=%s",
                                safe_log_id(w_id),
                                safe_error_type(e),
                            )
                    if body:
                        try:
                            db.update_work_metadata(w_id, body)
                        except ValueError as e:
                            # A refused length is the caller's mistake, not a
                            # server fault: say so rather than 500.
                            self.send_json(400, {'error': str(e)})
                            return
                    if patched_file_path:
                        try:
                            rows = db.execute_query(
                                "SELECT file_path FROM works WHERE id = ?",
                                (w_id,),
                            )
                            fp = (rows[0].get("file_path") or "") if rows else ""
                            text_index.sync_work(w_id, fp)
                        except Exception as e:
                            LOGGER.warning(
                                "work_patch_text_index_failed work_id=%s error_type=%s",
                                safe_log_id(w_id),
                                safe_error_type(e),
                            )
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/playlists/') and len(path.split('/')) == 4:
                pl_id = path.split('/')[-1]
                db.update_playlist(pl_id, data)
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/persons/') and len(path.split('/')) == 4:
                p_id = path.split('/')[-1]
                group_ids = data.pop('group_ids', None)
                image_url_changed = 'image_url' in data
                if image_url_changed:
                    try:
                        data['image_url'] = normalize_person_image_url(data.get('image_url'))
                    except PersonImageUrlError:
                        self.send_json(400, {'error': 'Invalid image_url'})
                        return
                if group_ids is not None and not isinstance(group_ids, list):
                    self.send_json(400, {'error': 'group_ids must be a JSON array'})
                    return
                # Metadata and memberships commit or roll back together, so an
                # unknown group id cannot leave the profile half-updated. A
                # failed request must not have changed canonical state -- that
                # is what makes "failed mutation keeps its cache" sound.
                try:
                    db.update_person_profile(p_id, data, group_ids)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                if image_url_changed:
                    # Disposable portrait cache, cleared only after the canonical
                    # profile change committed; a failure here never fails the PATCH.
                    try:
                        prks_delete_person_image_cache(p_id, _bound_storage.people_dir)
                    except Exception as e:
                        LOGGER.warning(
                            "person_image_cache_cleanup_failed person_id=%s error_type=%s",
                            safe_log_id(p_id),
                            safe_error_type(e),
                        )
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/person-groups/') and len(path.split('/')) == 4:
                g_id = path.split('/')[-1]
                try:
                    db.update_person_group(g_id, data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/folders/') and len(path.split('/')) == 4:
                f_id = path.split('/')[-1]
                # A rename stales every member Work's cached detail (it embeds
                # folder_title), so collect the members BEFORE the write and
                # report them -- membership cannot change in this request.
                renames = isinstance(data, dict) and 'title' in data
                member_ids = db.get_folder_work_ids(f_id) if renames else []
                try:
                    db.update_folder_metadata(f_id, data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'updated', 'member_work_ids': member_ids})
            elif path == '/api/settings':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    db.patch_app_settings(data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, db.get_app_settings_response())
            elif path.startswith('/api/saved-views/') and len(path.split('/')) == 4:
                vid = unquote(path.split('/')[-1])
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                has_name = 'name' in data
                has_search = 'search' in data
                if not has_name and not has_search:
                    self.send_json(400, {'error': 'Nothing to update.'})
                    return
                try:
                    view = db.update_saved_view(
                        vid,
                        name=data.get('name') if has_name else None,
                        search=data.get('search') if has_search else None,
                    )
                except SavedViewError as e:
                    self.send_json(int(e.http_status), {'error': str(e)})
                    return
                LOGGER.info(
                    "saved_view_updated view_id=%s definition_changed=%s",
                    safe_log_id(view.get("id")),
                    "true" if has_search else "false",
                )
                self.send_json(200, view)
            elif path.startswith('/api/concepts/') and len(path.split('/')) == 4:
                cid = unquote(path.split('/')[-1])
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.update_concept(
                        db,
                        cid,
                        name=data.get('name') if 'name' in data else None,
                        description=data.get('description') if 'description' in data else None,
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            elif path.startswith('/api/positions/') and len(path.split('/')) == 4:
                pid = unquote(path.split('/')[-1])
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.update_position(
                        db,
                        pid,
                        name=data.get('name') if 'name' in data else None,
                        description=data.get('description') if 'description' in data else None,
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            elif path.startswith('/api/arguments/') and len(path.split('/')) == 4:
                aid = unquote(path.split('/')[-1])
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.update_argument(
                        db,
                        aid,
                        name=data.get('name') if 'name' in data else None,
                        kind=data.get('kind') if 'kind' in data else None,
                        main_text=data.get('main_text') if 'main_text' in data else None,
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def do_PUT(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._validate_mutation_origin():
                return
            data = self._read_json_body()
            if data is None:
                return
            with self._library_access(parsed_path):
                self.handle_api_put(parsed_path, data)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_put(self, parsed_path, data=None):
        path = parsed_path.path
        try:
            if data is None:
                data = self._read_json_body()
                if data is None:
                    return
            parts = path.split('/')
            if (
                path.startswith('/api/concepts/')
                and len(parts) == 5
                and parts[4] == 'parents'
            ):
                cid = unquote(parts[3])
                parent_ids = data.get('parent_ids') if isinstance(data, dict) else None
                if parent_ids is None and isinstance(data, list):
                    parent_ids = data
                try:
                    item = research_network.replace_concept_parents(db, cid, parent_ids)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            elif (
                path.startswith('/api/concepts/')
                and len(parts) == 5
                and parts[4] == 'aliases'
            ):
                cid = unquote(parts[3])
                aliases = data.get('aliases') if isinstance(data, dict) else None
                if aliases is None and isinstance(data, list):
                    aliases = data
                try:
                    item = research_network.replace_concept_aliases(db, cid, aliases)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            elif (
                path.startswith('/api/arguments/')
                and len(parts) == 5
                and parts[4] == 'sources'
            ):
                aid = unquote(parts[3])
                sources = data.get('sources') if isinstance(data, dict) else None
                if sources is None and isinstance(data, list):
                    sources = data
                try:
                    item = research_network.replace_argument_sources(db, aid, sources)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            elif (
                path.startswith('/api/arguments/')
                and len(parts) == 5
                and parts[4] == 'targets'
            ):
                aid = unquote(parts[3])
                targets = data.get('targets') if isinstance(data, dict) else None
                if targets is None and isinstance(data, list):
                    targets = data
                try:
                    item = research_network.replace_argument_targets(db, aid, targets)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, item)
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def do_DELETE(self):
        if not self._validate_request_host():
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            if not self._validate_mutation_origin():
                return
            with self._library_access(parsed_path):
                self.handle_api_delete(parsed_path)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_delete(self, parsed_path):
        path = parsed_path.path
        query_params = parse_qs(parsed_path.query)
        try:
            if path.startswith('/api/works/') and path.endswith('/roles'):
                # DELETE /api/works/{work_id}/roles?person_id=&role_type=&order_index=
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'roles':
                    w_id = parts[3]
                    person_id = (query_params.get('person_id') or [''])[0].strip()
                    role_type = (query_params.get('role_type') or [''])[0].strip()
                    oi_raw = (query_params.get('order_index') or ['0'])[0].strip()
                    try:
                        order_index = int(oi_raw) if oi_raw != '' else 0
                    except ValueError:
                        self.send_json(400, {'error': 'order_index must be an integer'})
                        return
                    if not person_id or not role_type:
                        self.send_json(400, {'error': 'person_id and role_type are required'})
                        return
                    if db.delete_work_role(w_id, person_id, role_type, order_index):
                        self.send_json(200, {'status': 'removed'})
                    else:
                        self.send_json(404, {'error': 'role link not found'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                delete_library_work(db, text_index, w_id)
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/playlists/') and len(path.split('/')) == 4:
                pl_id = path.split('/')[-1]
                db.delete_playlist(pl_id)
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/playlists/') and '/items/' in path:
                # /api/playlists/{id}/items/{work_id}
                parts = path.split('/')
                if len(parts) == 6 and parts[4] == 'items':
                    pl_id, w_id = parts[3], parts[5]
                    db.remove_work_from_playlist(pl_id, w_id)
                    self.send_json(200, {'status': 'removed'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/folders/') and len(path.split('/')) == 4:
                f_id = path.split('/')[-1]
                try:
                    db.delete_empty_folder(f_id)
                except ValueError as e:
                    self.send_json(409, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/works/') and '/tags/' in path:
                # /api/works/{id}/tags/{tag_id}
                parts = path.split('/')
                if len(parts) < 6:
                    self.send_error(404, "API endpoint not found")
                    return
                db.remove_tag_from_work(parts[3], parts[5])
                self.send_json(200, {'status': 'removed'})
            elif path.startswith('/api/folders/') and '/tags/' in path:
                # /api/folders/{id}/tags/{tag_id}
                parts = path.split('/')
                if len(parts) < 6:
                    self.send_error(404, "API endpoint not found")
                    return
                db.remove_tag_from_folder(parts[3], parts[5])
                self.send_json(200, {'status': 'removed'})
            elif path.startswith('/api/tags/') and path.endswith('/aliases'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'aliases':
                    tag_id = parts[3]
                    alias = (query_params.get('alias') or [''])[0]
                    alias = unquote(alias)
                    if not alias.strip():
                        self.send_json(400, {'error': 'missing alias'})
                        return
                    if db.delete_tag_alias(tag_id, alias):
                        self.send_json(200, {'status': 'deleted'})
                    else:
                        self.send_json(404, {'error': 'alias not found'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/publishers/') and path.endswith('/aliases'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'aliases':
                    publisher_id = parts[3]
                    alias = (query_params.get('alias') or [''])[0]
                    alias = unquote(alias)
                    if not alias.strip():
                        self.send_json(400, {'error': 'missing alias'})
                        return
                    if db.delete_publisher_alias(publisher_id, alias):
                        self.send_json(200, {'status': 'deleted'})
                    else:
                        self.send_json(404, {'error': 'alias not found'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/publishers/') and len(path.split('/')) == 4:
                p_id = path.split('/')[-1]
                db.delete_publisher(p_id)
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/tags/') and len(path.split('/')) == 4:
                t_id = path.split('/')[-1]
                try:
                    result = db.delete_tag(t_id)
                    self.send_json(200, result)
                except ValueError as e:
                    msg = str(e)
                    if 'not found' in msg.lower():
                        self.send_json(404, {'error': msg})
                    else:
                        self.send_json(400, {'error': msg})
            elif path.startswith('/api/persons/') and len(path.split('/')) == 4:
                p_id = path.split('/')[-1]
                try:
                    db.delete_person_if_unlinked(p_id)
                except ValueError as e:
                    msg = str(e)
                    if 'not found' in msg.lower():
                        self.send_json(404, {'error': msg})
                    elif 'linked works' in msg.lower():
                        self.send_json(409, {'error': msg})
                    else:
                        self.send_json(400, {'error': msg})
                    return
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/person-groups/'):
                parts = path.split('/')
                # /api/person-groups/{group_id}/members/{person_id}
                if len(parts) == 6 and parts[4] == 'members':
                    db.remove_person_from_group(parts[5], parts[3])
                    self.send_json(200, {'status': 'removed'})
                elif len(parts) == 4:
                    try:
                        db.delete_person_group(parts[3])
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'deleted'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/saved-views/') and len(path.split('/')) == 4:
                vid = unquote(path.split('/')[-1])
                try:
                    db.delete_saved_view(vid)
                except SavedViewError as e:
                    self.send_json(int(e.http_status), {'error': str(e)})
                    return
                LOGGER.info(
                    "saved_view_deleted view_id=%s",
                    safe_log_id(vid),
                )
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/concepts/') and len(path.split('/')) == 4:
                cid = unquote(path.split('/')[-1])
                try:
                    research_network.delete_concept(db, cid)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                try:
                    research_index.remove_concept_mentions(cid)
                except Exception as e:
                    LOGGER.warning(
                        "research_index_cleanup_failed concept_id=%s error_type=%s",
                        safe_log_id(cid),
                        safe_error_type(e),
                    )
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/positions/') and len(path.split('/')) == 4:
                pid = unquote(path.split('/')[-1])
                try:
                    research_network.delete_position(db, pid)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, {'status': 'deleted'})
            elif path.startswith('/api/arguments/') and len(path.split('/')) == 4:
                aid = unquote(path.split('/')[-1])
                try:
                    research_network.delete_argument(db, aid)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                try:
                    research_index.remove_argument_mentions(aid)
                except Exception as e:
                    LOGGER.warning(
                        "research_index_cleanup_failed argument_id=%s error_type=%s",
                        safe_log_id(aid),
                        safe_error_type(e),
                    )
                self.send_json(200, {'status': 'deleted'})
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def _send_pdf_bytes(self, pdf_path: str, *, head_only: bool = False) -> None:
        """Serve PDF with Content-Length and Range support (required for HTTP/1.1 + WASM PDF engines)."""
        try:
            file_size = os.path.getsize(pdf_path)
        except OSError:
            self.send_error(404, "PDF not found")
            return

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
            with open(pdf_path, "rb") as f:
                f.seek(start)
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
        with open(pdf_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def handle_api_head(self, parsed_path):
        path = parsed_path.path
        try:
            if path.startswith('/api/pdfs/'):
                filename = path.split('/')[-1]
                pdf_path = _safe_pdf_path_in_pdfs_dir(filename)
                if pdf_path and os.path.exists(pdf_path):
                    self._send_pdf_bytes(pdf_path, head_only=True)
                else:
                    self.send_error(404, "PDF not found")
            elif path.startswith('/api/processing-files/') and path.endswith('/pdf'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'pdf':
                    pf_id = parts[3]
                    pdf_path = db.get_processing_file_pdf_path(pf_id)
                    if pdf_path and os.path.exists(pdf_path):
                        self._send_pdf_bytes(pdf_path, head_only=True)
                    else:
                        self.send_error(404, "PDF not found")
                else:
                    self.send_error(404, "API endpoint not found")
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

    def _send_person_profile_image(self, person_id: str) -> None:
        row = db.get_person(person_id)
        if not row:
            self.send_error(404, "Person not found")
            return
        url = (row.get("image_url") or "").strip()
        if not url:
            self.send_error(404, "Profile image not available")
            return

        cache_path = prks_person_image_cache_path(
            person_id, url, _bound_storage.people_dir
        )
        legacy_bin = prks_person_image_legacy_bin_path(
            person_id, _bound_storage.people_dir
        )

        def read_cache_file(path: str) -> bytes:
            try:
                with open(path, "rb") as fp:
                    return fp.read()
            except OSError:
                return b""

        def drop_file(path: str) -> None:
            try:
                os.remove(path)
            except OSError:
                pass

        body = read_cache_file(cache_path)
        if body:
            subtype = identify_cached_portrait_subtype(body)
            if subtype:
                self._send_person_profile_image_bytes(body, subtype)
                return
            drop_file(cache_path)

        if os.path.isfile(legacy_bin):
            legacy_body = read_legacy_portrait_bytes(legacy_bin)
            encoded = (
                decode_and_transcode(legacy_body, None) if legacy_body else None
            )
            if encoded is not None:
                out, subtype = encoded
                try:
                    _prks_write_person_image_cache(cache_path, out)
                    drop_file(legacy_bin)
                except OSError:
                    pass
                self._send_person_profile_image_bytes(out, subtype, max_age=3600)
                return
            drop_file(legacy_bin)

        prepared = fetch_and_prepare(url)
        if prepared is None:
            LOGGER.info(
                "portrait_fetch_failed request_id=%s",
                safe_log_id(self._prks_request_id),
            )
            self.send_error(404, "Profile image not available")
            return
        try:
            _prks_write_person_image_cache(cache_path, prepared.body)
        except OSError:
            pass
        self._send_person_profile_image_bytes(
            prepared.body, prepared.subtype, max_age=3600
        )

    def handle_api_get(self, parsed_path):
        query = parse_qs(parsed_path.query)
        path = parsed_path.path
        
        try:
            if path == '/api/diagnostics/performance':
                self.send_json(200, performance_snapshot())
            elif path == '/api/works':
                # `?projection=browse` is an explicit, additive contract: the
                # compact catalog the browse routes cache. The default response
                # keeps its existing semantics for every other caller.
                if (query.get('projection') or [''])[0] == 'browse':
                    data = db.get_works_browse_catalog()
                    etag = db.etag_for_representation('works-browse', data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
                else:
                    etag = db.etag_works_catalog()
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    data = db.get_all_works()
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/playlists':
                etag = db.etag_playlists_catalog()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_all_playlists()
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/playlists/') and len(path.split('/')) == 4:
                pl_id = path.split('/')[-1]
                data = db.get_playlist(pl_id)
                if data:
                    self.send_json(200, data)
                else:
                    self.send_error(404, "Playlist not found")
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
                row = db.execute_query(
                    "SELECT file_path, thumb_page FROM works WHERE id = ?",
                    (w_id,),
                )
                if not row:
                    self.send_error(404, "Work not found")
                    return
                file_path = (row[0].get('file_path') or '').strip()
                if not file_path or not file_path.startswith('/api/pdfs/'):
                    self.send_error(404, "PDF not found")
                    return
                pdf_filename = file_path.split("/")[-1]
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
                        page = int(row[0].get('thumb_page') or 1)
                    except Exception:
                        page = 1
                if page < 1:
                    page = 1

                try:
                    pdf_mtime = float(os.path.getmtime(pdf_path))
                except Exception:
                    pdf_mtime = 0.0

                cache_base = prks_thumb_cache_stem(w_id, page)
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
                        import fitz  # PyMuPDF
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
                        try:
                            tmp_path = (cache_path or "") + ".tmp"
                            with open(tmp_path, "wb") as f:
                                f.write(generated_bytes)
                            os.replace(tmp_path, cache_path)
                        except Exception:
                            try:
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
            elif path.startswith('/api/works/') and path.endswith('/metadata-state') and len(path.split('/')) == 5:
                data = db.get_work_metadata_state(path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-metadata-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/source-state') and len(path.split('/')) == 5:
                # A REVISION ONLY. The Work record already carries every
                # canonical source value, so duplicating a URL here would
                # double what this endpoint sends and what IndexedDB stores
                # for values the client already holds.
                data = work_source_sync.get_source_state(db, path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                elif data == work_source_sync.INVALID_SOURCE_STATE:
                    # The product reads this row as a video, but its identity
                    # cannot be stated. Said plainly rather than answered with
                    # a revision the mutation would then refuse to act on.
                    self.send_json(409, {
                        "error": "This file's video source cannot be read. Its link is not a "
                                 "supported video URL.",
                        "code": work_source_sync.INVALID_SOURCE_STATE})
                else:
                    etag = db.etag_for_representation("work-source-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/notes-state') and len(path.split('/')) == 5:
                data = db.get_work_notes_state(path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-notes-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/people-state') and len(path.split('/')) == 5:
                # Relationship REVISIONS, including tombstones. The Work detail
                # already carries the linked people themselves; duplicating
                # Person objects here would double what every read costs for
                # values the client already holds. A scope with a revision and
                # no live relationship is what tells an offline device its own
                # pending removal is current rather than stale.
                data = work_role_sync.get_roles_state(db, path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-people-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/tag-options') and len(path.split('/')) == 5:
                data = db.get_work_tag_options(path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    self.send_json(200, data, etag=db.etag_for_representation("work-tag-options", data))
            elif path.startswith('/api/folders/') and path.endswith('/tag-options') and len(path.split('/')) == 5:
                data = db.get_folder_tag_options(path.split('/')[3])
                if data is None:
                    self.send_json(404, {"error": "Folder not found"})
                else:
                    self.send_json(200, data, etag=db.etag_for_representation("folder-tag-options", data))
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                data = db.get_work(w_id)
                if data:
                    try:
                        data['research_refs'] = research_index.work_research_refs(w_id, db)
                    except Exception as e:
                        LOGGER.warning(
                            "research_index_refs_failed work_id=%s error_type=%s",
                            safe_log_id(w_id),
                            safe_error_type(e),
                        )
                    self.send_json(200, data)
                else:
                    self.send_error(404, "Work not found")
            elif path.startswith('/api/works/') and path.endswith('/annotations-state') and len(path.split('/')) == 5:
                w_id = path.split('/')[3]
                data = db.get_work_annotations_state(w_id)
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-annotations-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/annotations-snapshot') and len(path.split('/')) == 5:
                # One DB transaction: items + revisions + materialization gens.
                w_id = path.split('/')[3]
                data = db.get_work_annotations_snapshot(w_id)
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-annotations-snapshot", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/pdf-materialization') and len(path.split('/')) == 5:
                w_id = path.split('/')[3]
                data = db.get_work_pdf_materialization(w_id)
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    self.send_json(200, data)
            elif path.startswith('/api/works/') and path.endswith('/annotations'):
                w_id = path.split('/')[3]
                data = {"work_id": w_id, "annotations_json": db.get_work_annotations(w_id)}
                self.send_json(200, data)
            elif path.startswith('/api/works/') and path.endswith('/save-confirm'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'save-confirm':
                    w_id = parts[3]
                    token = (query.get('token', [''])[0] or '').strip()
                    if not token:
                        self.send_json(400, {'error': 'token is required'})
                        return
                    with _SAVE_TOKEN_LOCK:
                        pdf_saved = _PRKS_LAST_PDF_SAVE_TOKEN_BY_WORK.get(w_id) == token
                        ann_saved = _PRKS_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK.get(w_id) == token
                    self.send_json(
                        200,
                        {
                            'work_id': w_id,
                            'token': token,
                            'pdf_saved': pdf_saved,
                            'annotations_saved': ann_saved,
                            'saved': bool(pdf_saved and ann_saved),
                        },
                    )
                else:
                    self.send_error(404, "API endpoint not found")
            elif path == '/api/folders':
                # The catalog IS the revision source (see etag_folders_catalog),
                # so build it once and derive the ETag from it.
                data = db.get_all_folders()
                etag = db.etag_folders_catalog(data)
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/folders/') and len(path.split('/')) == 4:
                f_id = path.split('/')[-1]
                data = db.get_folder(f_id)
                if data: self.send_json(200, data)
                else: self.send_error(404, "Folder not found")
            elif path.startswith('/api/works/') and path.endswith('/related_folders'):
                w_id = path.split('/')[3]
                data = db.get_related_folders_for_work(w_id)
                self.send_json(200, data)
            elif path == '/api/persons':
                etag = db.etag_persons_catalog()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_all_persons()
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/person-groups':
                etag = db.etag_person_groups_catalog()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_all_person_groups()
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/person-groups/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY. The catalogue already carries every group's
                # name, parent and description and the detail carries its
                # members, so echoing values here would make a second copy.
                data = db.get_person_group_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Group not found"})
                else:
                    etag = db.etag_for_representation("person-group-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/person-groups/') and len(path.split('/')) == 4:
                g_id = path.split('/')[-1]
                data = db.get_person_group(g_id)
                if data:
                    self.send_json(200, data)
                else:
                    self.send_error(404, "Group not found")
            elif path == '/api/saved-views':
                self.send_json(200, db.get_saved_views())
            elif path.startswith('/api/saved-views/') and len(path.split('/')) == 4:
                vid = unquote(path.split('/')[-1])
                data = db.get_saved_view(vid)
                if data:
                    self.send_json(200, data)
                else:
                    self.send_json(404, {'error': 'Saved View not found.'})
            elif path == '/api/research-graph':
                raw_people = (query.get('people') or [''])[0].strip().lower()
                include_people = raw_people in ('1', 'true', 'yes', 'on')
                try:
                    snapshot = ResearchGraphBuilder().build(
                        db,
                        research_index,
                        include_people=include_people,
                    )
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
            elif path == '/api/concepts':
                items = research_network.list_concepts(db)
                counts = research_index.concept_mention_counts()
                for item in items:
                    item['mention_count'] = int(counts.get(item['id'], 0))
                self.send_json(200, items)
            elif path.startswith('/api/concepts/') and len(path.split('/')) == 4:
                cid = unquote(path.split('/')[-1])
                item = research_network.get_concept(db, cid)
                if not item:
                    self.send_json(404, {'error': 'Concept not found.'})
                    return
                item['mention_count'] = research_index.mention_count_for_concept(cid)
                item['mentions'] = research_index.concept_backlinks(cid, db)
                self.send_json(200, item)
            elif path == '/api/positions':
                self.send_json(200, research_network.list_positions(db))
            elif path.startswith('/api/positions/') and len(path.split('/')) == 4:
                pid = unquote(path.split('/')[-1])
                item = research_network.get_position(db, pid)
                if item:
                    self.send_json(200, item)
                else:
                    self.send_json(404, {'error': 'Position not found.'})
            elif path == '/api/argument-verdicts':
                self.send_json(200, research_network.list_verdicts(db))
            elif path == '/api/arguments':
                kind = (query.get('kind') or [''])[0].strip() or None
                try:
                    items = research_network.list_arguments(db, kind=kind)
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                counts = research_index.argument_mention_counts()
                for item in items:
                    item['mention_count'] = int(counts.get(item['id'], 0))
                self.send_json(200, items)
            elif path.startswith('/api/arguments/') and len(path.split('/')) == 4:
                aid = unquote(path.split('/')[-1])
                item = research_network.get_argument(db, aid)
                if not item:
                    self.send_json(404, {'error': 'Argument not found.'})
                    return
                item['mention_count'] = research_index.mention_count_for_argument(aid)
                item['mentions'] = research_index.argument_backlinks(aid, db)
                item['verdicts'] = research_network.list_verdicts(db)
                self.send_json(200, item)
            elif path.startswith('/api/persons/') and path.endswith('/metadata-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY. The Person detail already carries every
                # profile value and none of them has a length bound, so
                # echoing them here would make a second copy of the whole
                # biography in what this sends and what IndexedDB stores.
                data = db.get_person_metadata_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Person not found"})
                else:
                    etag = db.etag_for_representation("person-metadata-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/folders/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY. The folder catalogue already carries every
                # title, parent and description.
                data = db.get_folder_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Folder not found"})
                else:
                    etag = db.etag_for_representation("folder-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/folder-state') and len(path.split('/')) == 5:
                data = db.get_work_folder_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-folder-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/positions/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY: the Position detail already carries both
                # values, and a second copy would be a second thing to keep
                # true.
                data = db.get_position_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Position not found"})
                else:
                    etag = db.etag_for_representation("position-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/arguments/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY, aggregates included: the Argument detail
                # already carries its sources and its targets, and a second
                # copy would be a second thing to keep true.
                data = db.get_argument_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Argument not found"})
                else:
                    etag = db.etag_for_representation("argument-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/concepts/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # Revisions, plus the two AGGREGATE values. A field revision is
                # a number, but an identity and a hierarchy are sets: a client
                # measuring an edit against them needs what it last
                # acknowledged, and the Concept detail carries `parents` as
                # objects rather than ids.
                data = db.get_concept_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Concept not found"})
                else:
                    etag = db.etag_for_representation("concept-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/playlists/') and path.endswith('/sync-state') and len(path.split('/')) == 5:
                # REVISIONS ONLY: the three field revisions and the ORDER
                # revision. The playlist detail already carries its items in
                # order, so echoing them here would make a second cached copy.
                data = db.get_playlist_sync_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Playlist not found"})
                else:
                    etag = db.etag_for_representation("playlist-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/works/') and path.endswith('/playlist-state') and len(path.split('/')) == 5:
                data = db.get_work_playlist_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Work not found"})
                else:
                    etag = db.etag_for_representation("work-playlist-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/persons/') and path.endswith('/group-state') and len(path.split('/')) == 5:
                # Membership revisions for one Person, including tombstones:
                # a pair this device once removed has a revision, and adding it
                # back is a mutation of that scope rather than a first write.
                data = db.get_person_groups_state(unquote(path.split('/')[3]))
                if data is None:
                    self.send_json(404, {"error": "Person not found"})
                else:
                    etag = db.etag_for_representation("person-group-state", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path.startswith('/api/persons/') and path.endswith('/profile-image'):
                parts = path.split('/')
                if len(parts) != 5:
                    self.send_error(404, "API endpoint not found")
                    return
                p_id = unquote(parts[3])
                self._send_person_profile_image(p_id)
            elif path.startswith('/api/persons/') and len(path.split('/')) == 4:
                p_id = unquote(path.split('/')[-1])
                data = db.get_person(p_id)
                if data:
                    self.send_json(200, data)
                else:
                    self.send_error(404, "Person not found")
            elif path == '/api/recent':
                # Representation-derived ETag: the old probe keyed on
                # COUNT + MAX(last_opened_at), which cannot see a re-open of a
                # Work that was already the most recent, nor a display field
                # changing on a row already in the result.
                data = db.get_recent_browse()
                etag = db.etag_for_representation('recent', data)
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/recently-added':
                data = db.get_recently_added_browse()
                etag = db.etag_for_representation('recently-added', data)
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/search':
                q = query.get('q', [''])[0]
                tag = query.get('tag', [''])[0]
                author = query.get('author', [''])[0]
                publisher = query.get('publisher', [''])[0]
                any_mode = query.get('any', [''])[0] in ('1', 'true', 'yes')
                if tag:
                    data = db.get_works_by_tag_name(tag)
                    if author and author.strip():
                        allow = set(db.work_ids_matching_author(author.strip()))
                        data = [w for w in data if w.get('id') in allow]
                    if publisher and publisher.strip():
                        allow_pub = set(db.work_ids_matching_publisher(publisher.strip()))
                        data = [w for w in data if w.get('id') in allow_pub]
                else:
                    if any_mode:
                        term = (q or author or publisher or '').strip()
                        data = db.search_works_any(term)
                    else:
                        data = db.search_works(
                            q,
                            author.strip() if author else '',
                            publisher.strip() if publisher else '',
                        )
                    if any_mode and q and str(q).strip():
                        text_ids = text_index.search_work_ids(q)
                        if text_ids:
                            existing = {str(w.get('id')) for w in data if w.get('id')}
                            author_allow = None
                            publisher_allow = None
                            if author and author.strip():
                                author_allow = set(db.work_ids_matching_author(author.strip()))
                            if publisher and publisher.strip():
                                publisher_allow = set(db.work_ids_matching_publisher(publisher.strip()))

                            def passes_filters(wid: str) -> bool:
                                if author_allow is not None and wid not in author_allow:
                                    return False
                                if publisher_allow is not None and wid not in publisher_allow:
                                    return False
                                return True

                            extra_ids = [wid for wid in text_ids if wid not in existing and passes_filters(wid)]
                            if extra_ids:
                                data.extend(db.get_work_summaries_by_ids_ordered(extra_ids))
                self.send_json(200, data)
            elif path == '/api/tags':
                used_only = query.get('used', [''])[0] in ('1', 'true', 'yes')
                if used_only:
                    data = db.get_tags_in_use()
                    self.send_json(200, data)
                else:
                    data = db.get_all_tags()
                    etag = db.etag_for_representation("tags", data)
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/publishers':
                used_only = query.get('used', [''])[0] in ('1', 'true', 'yes')
                if used_only:
                    data = db.get_publishers_in_use()
                    self.send_json(200, data)
                else:
                    self.send_json(200, [])
            elif path.startswith('/api/pdfs/'):
                filename = path.split('/')[-1]
                pdf_path = _safe_pdf_path_in_pdfs_dir(filename)
                if pdf_path and os.path.exists(pdf_path):
                    self._send_pdf_bytes(pdf_path)
                else:
                    self.send_error(404, "PDF not found")
            elif path.startswith('/api/bibtex/'):
                work_id = path.split('/')[-1]
                bibtex = db.generate_bibtex(work_id)
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(bibtex.encode())
            elif path == '/api/settings':
                self.send_json(200, db.get_app_settings_response())
            elif path == '/api/backups/download':
                token = (query.get('token') or [''])[0]
                self._handle_backup_download(token=token)
            elif path == '/api/processing-files':
                rescan_raw = (query.get('rescan') or [''])[0]
                rescan = str(rescan_raw).strip().lower() in ('1', 'true', 'yes')
                if rescan:
                    data = db.scan_processing_files()
                else:
                    data = db.get_processing_files(include_imported=False)
                self.send_json(200, data)
            elif path.startswith('/api/processing-files/') and path.endswith('/pdf'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'pdf':
                    pf_id = parts[3]
                    pdf_path = db.get_processing_file_pdf_path(pf_id)
                    if pdf_path and os.path.exists(pdf_path):
                        self._send_pdf_bytes(pdf_path)
                    else:
                        self.send_error(404, "PDF not found")
                else:
                    self.send_error(404, "API endpoint not found")
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def handle_api_post(self, parsed_path, data=None):
        path = parsed_path.path
        try:
            if path == '/api/backups/stage':
                self._handle_backup_stage(parsed_path)
                return
            if data is None:
                data = self._read_json_body()
                if data is None:
                    return
            if path == '/api/sync/operations':
                status, result = process_operation(db, data)
                if (status == 200 and result.get("code") == "ACKNOWLEDGED" and
                        data.get("operation") == work_note_sync.RESEARCH_OPERATION):
                    # Canonical note + ledger committed before this derived
                    # best-effort projection. Re-read current text so replaying
                    # an old op can never restore an old index snapshot.
                    try:
                        current = db.get_work(data.get("entity_id"))
                        if current:
                            research_index.sync_work(
                                data.get("entity_id"), current.get("text_content") or "", db)
                            # Compact preview/link map for the live Work entity.
                            # Body stays omitted; refs are derived and bounded.
                            result["research_refs"] = research_index.work_research_refs(
                                data.get("entity_id"), db)
                    except Exception as e:
                        LOGGER.warning(
                            "research_index_sync_failed work_id=%s error_type=%s",
                            safe_log_id(data.get("entity_id")), safe_error_type(e))
                if (status == 200 and result.get("code") == "ACKNOWLEDGED" and
                        data.get("operation") == "DELETE_WORK"):
                    if result.get("changed"):
                        from backend.work_deletion import cleanup_after_work_delete
                        # file_path is present only on the first ACK (ephemeral
                        # apply return). Replay ledgers omit it. When supplied,
                        # cleanup re-checks current Work references — never
                        # trusts a deletion-time managed_pdf_still_referenced.
                        cleanup_after_work_delete(
                            db, text_index, data.get("entity_id"),
                            file_path=result.get("file_path") or "",
                            existed=True,
                        )
                    # Paths are not durable client state; strip before the wire.
                    result = {
                        "code": result["code"],
                        "work_id": result["work_id"],
                        "changed": result["changed"],
                    }
                self.send_json(status, result)
                return
            if path == '/api/backups/progress':
                self._handle_backup_progress()
                return
            if path == '/api/backups/restore':
                self._handle_backup_restore(data)
                return

            if path == '/api/diagnostics/performance/reset':
                reset_performance()
                self.send_json(200, {"status": "reset"})
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
                unlinearized_only = bool(data.get("unlinearized_only", True))
                rows = db.execute_query("SELECT id, file_path FROM works WHERE file_path LIKE '/api/pdfs/%'")
                unique_paths = {}
                for row in rows:
                    fp = str(row.get("file_path") or "").strip()
                    if not fp.startswith("/api/pdfs/"):
                        continue
                    filename = fp.split("/")[-1]
                    abs_path = _safe_pdf_path_in_pdfs_dir(filename)
                    if not abs_path:
                        continue
                    unique_paths[abs_path] = True

                processed = 0
                changed = 0
                already_linearized = 0
                skipped = 0
                failed = 0
                for abs_path in sorted(unique_paths.keys()):
                    processed += 1
                    if not os.path.exists(abs_path):
                        skipped += 1
                        continue
                    if unlinearized_only and is_pdf_linearized(abs_path):
                        already_linearized += 1
                        continue
                    did_change, reason = maybe_linearize_pdf_in_place(abs_path, context="settings-bulk")
                    LOGGER.info(
                        "pdf_linearize_result context=settings-bulk changed=%s reason=%s",
                        "true" if did_change else "false",
                        safe_log_label(reason),
                    )
                    if did_change:
                        changed += 1
                    elif reason in ("disabled", "missing-qpdf", "missing-file"):
                        skipped += 1
                    elif reason == "ok":
                        changed += 1
                    else:
                        failed += 1
                self.send_json(
                    200,
                    {
                        "status": "ok",
                        "processed": processed,
                        "changed": changed,
                        "already_linearized": already_linearized,
                        "skipped": skipped,
                        "failed": failed,
                        "unlinearized_only": unlinearized_only,
                    },
                )
            elif path.startswith('/api/processing-files/') and path.endswith('/import'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'import':
                    pf_id = parts[3]
                    try:
                        out = db.import_processing_file(pf_id)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    try:
                        work_id = str(out.get("work_id") or "").strip()
                        if work_id:
                            row = db.execute_query("SELECT file_path FROM works WHERE id = ?", (work_id,))
                            fp = (row[0].get("file_path") or "").strip() if row else ""
                            text_index.sync_work(work_id, fp)
                    except Exception as e:
                        LOGGER.warning(
                            "processing_import_text_index_failed processing_file_id=%s error_type=%s",
                            safe_log_id(pf_id),
                            safe_error_type(e),
                        )
                    self.send_json(200, out)
                else:
                    self.send_error(404, "API endpoint not found")
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

                # Upload: PDF (existing behavior)
                if data.get('file_b64') and data.get('file_name'):
                    os.makedirs(pdfs_dir, exist_ok=True)
                    safe_name = "".join(c for c in data['file_name'] if c.isalnum() or c in ".-_")
                    local_filename = f"{int(time.time())}_{safe_name}"
                    try:
                        decoded_pdf = base64.b64decode(data['file_b64'], validate=True)
                    except (binascii.Error, ValueError):
                        self.send_json(400, {'error': 'Invalid file_b64 payload'})
                        return
                    with open(os.path.join(pdfs_dir, local_filename), "wb") as f:
                        f.write(decoded_pdf)
                    abs_uploaded_path = safe_pdf_path_under_dir(pdfs_dir, local_filename)
                    if abs_uploaded_path:
                        changed, reason = maybe_linearize_pdf_in_place(abs_uploaded_path, context="work-create-upload")
                        LOGGER.info(
                            "pdf_linearize_result context=work-create-upload changed=%s reason=%s",
                            "true" if changed else "false",
                            safe_log_label(reason),
                        )
                    file_path = f"/api/pdfs/{local_filename}"

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
                if effective_source_kind(source_kind, source_url, file_path) == 'video':
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
                try:
                    w_id = db.add_work(
                        title=data.get('title', 'Untitled'),
                        status=data.get('status', 'Not Started'),
                        abstract=data.get('abstract', ''),
                        text_content=data.get('text_content', ''),
                        published_date=data.get('published_date', ''),
                        file_path=file_path,
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
                # Optionally attach to playlist
                playlist_id = (data.get('playlist_id') or '').strip()
                if playlist_id:
                    try:
                        db.add_work_to_playlist(playlist_id, w_id, None)
                    except Exception as exc:
                        # best-effort: do not fail work creation if playlist link fails
                        LOGGER.warning(
                            "work_playlist_attach_failed playlist_id=%s work_id=%s request_id=%s error_type=%s",
                            safe_log_id(playlist_id),
                            safe_log_id(w_id),
                            safe_log_id(self._prks_request_id),
                            safe_error_type(exc),
                        )
                folder_id = data.get("folder_id")
                raw_folder = str(folder_id).strip() if folder_id is not None else ""
                if raw_folder:
                    try:
                        db.add_work_to_folder(raw_folder, w_id)
                    except ValueError as e:
                        delete_library_work(db, text_index, w_id)
                        self.send_json(409, {'error': str(e)})
                        return
                else:
                    try:
                        unc_id = db.ensure_default_uncategorized_folder_id()
                        db.add_work_to_folder(unc_id, w_id)
                    except ValueError as e:
                        delete_library_work(db, text_index, w_id)
                        self.send_json(409, {'error': str(e)})
                        return
                
                # Link persons/roles provided during upload
                roles = data.get('roles', [])
                if isinstance(roles, list):
                    for idx, r in enumerate(roles):
                        if not isinstance(r, dict) or not r.get('person_id') or not r.get('role_type'):
                            continue
                        p_id = r['person_id']
                        r_type = r['role_type']
                        if db.has_work_role(p_id, w_id, r_type):
                            continue
                        credit_name = r.get('credit_name', '')
                        if credit_name is not None and not isinstance(credit_name, str):
                            credit_name = ''
                        try:
                            # CONSTRUCTION: these are the relationships this
                            # Work is born with, so they carry no revision and
                            # the caller's author order is preserved. The alias
                            # side effect happens at that boundary.
                            db.insert_initial_role(
                                w_id,
                                p_id,
                                r_type,
                                order_index=idx,
                                credit_name=credit_name or '',
                            )
                        except ValueError:
                            continue
                        
                self.send_json(200, {'id': w_id})
            elif path == '/api/playlists':
                pl_id = db.add_playlist(
                    title=data.get('title', '') or '',
                    description=data.get('description', '') or '',
                    original_url=data.get('original_url', '') or '',
                )
                self.send_json(200, {'id': pl_id})
            elif path.startswith('/api/playlists/') and path.endswith('/items'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'items':
                    pl_id = parts[3]
                    w_id = (data.get('work_id') or '').strip()
                    if not w_id:
                        self.send_json(400, {'error': 'work_id is required'})
                        return
                    try:
                        pos_raw = data.get('position', None)
                        pos = int(pos_raw) if pos_raw is not None and str(pos_raw).strip() != '' else None
                    except Exception:
                        pos = None
                    try:
                        db.add_work_to_playlist(pl_id, w_id, pos)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'added'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/playlists/') and path.endswith('/reorder'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'reorder':
                    pl_id = parts[3]
                    work_ids = data.get('work_ids', [])
                    if not isinstance(work_ids, list):
                        self.send_json(400, {'error': 'work_ids must be a JSON array'})
                        return
                    try:
                        db.reorder_playlist(pl_id, [str(x) for x in work_ids])
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'reordered'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path == '/api/folders':
                try:
                    f_id = db.add_folder(
                        title=data.get('title', 'Untitled Folder'),
                        description=data.get('description', ''),
                        parent_id=data.get('parent_id'),
                    )
                except ValueError as e:
                    self.send_json(409, {'error': str(e)})
                else:
                    self.send_json(200, {'id': f_id})
            elif path == '/api/person-groups':
                pid = data.get('parent_id')
                if pid == '':
                    pid = None
                pnamed = None if pid else data.get('parent_name')
                try:
                    g_id = db.add_person_group_with_parent_options(
                        name=data.get('name', ''),
                        parent_id=pid,
                        parent_name=pnamed,
                        description=data.get('description', '') or '',
                    )
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                else:
                    self.send_json(200, {'id': g_id})
            elif path == '/api/saved-views':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    view = db.create_saved_view(data.get('name'), data.get('search'))
                except SavedViewError as e:
                    self.send_json(int(e.http_status), {'error': str(e)})
                    return
                LOGGER.info(
                    "saved_view_created view_id=%s mode=%s",
                    safe_log_id(view.get("id")),
                    safe_log_label((view.get("search") or {}).get("mode")),
                )
                self.send_json(201, view)
            elif path == '/api/concepts':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.create_concept(
                        db, data.get('name'), data.get('description', '') or ''
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(201, item)
            elif path == '/api/positions':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.create_position(
                        db, data.get('name'), data.get('description', '') or ''
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(201, item)
            elif path == '/api/arguments':
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                try:
                    item = research_network.create_argument(
                        db,
                        name=data.get('name'),
                        kind=data.get('kind'),
                        main_text=data.get('main_text', '') or '',
                        sources=data.get('sources'),
                        targets=data.get('targets'),
                    )
                except ResearchError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(201, item)
            elif path.startswith('/api/person-groups/') and path.endswith('/members'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'members':
                    g_id = parts[3]
                    try:
                        db.add_person_to_group(data.get('person_id', ''), g_id)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                    else:
                        self.send_json(200, {'status': 'added'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path == '/api/persons':
                try:
                    image_url = normalize_person_image_url(data.get('image_url', '') or '')
                except PersonImageUrlError:
                    self.send_json(400, {'error': 'Invalid image_url'})
                    return
                p_id = db.add_person(
                    first_name=data.get('first_name', ''),
                    last_name=data.get('last_name', ''),
                    aliases=data.get('aliases', ''),
                    about=data.get('about', ''),
                    image_url=image_url,
                    link_wikipedia=data.get('link_wikipedia', '') or '',
                    link_stanford_encyclopedia=data.get('link_stanford_encyclopedia', '') or '',
                    link_iep=data.get('link_iep', '') or '',
                    links_other=data.get('links_other', '') or '',
                    birth_date=data.get('birth_date', '') or '',
                    death_date=data.get('death_date', '') or '',
                )
                self.send_json(200, {'id': p_id})
            elif path == '/api/tags/merge':
                sid = (data.get('source_tag_id') or '').strip()
                tid = (data.get('target_tag_id') or '').strip()
                try:
                    out = db.merge_tags_into(sid, tid)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'merged', **out})
            elif path.startswith('/api/tags/') and path.endswith('/aliases'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'aliases':
                    tag_id = parts[3]
                    try:
                        db.add_tag_alias(tag_id, (data.get('alias') or '').strip())
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'added'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path == '/api/tags':
                try:
                    out = db.add_tag(data.get('name'), data.get('color', '#6d6cf7'))
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, out)
            elif path == '/api/publishers':
                try:
                    out = db.add_publisher(data.get('name', ''))
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, out)
            elif path.startswith('/api/publishers/') and path.endswith('/aliases'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'aliases':
                    publisher_id = parts[3]
                    try:
                        db.add_publisher_alias(
                            publisher_id, (data.get('alias') or '').strip()
                        )
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'added'})
                else:
                    self.send_error(404, "API endpoint not found")
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
            elif path.startswith('/api/works/') and path.endswith('/tags'):
                w_id = path.split('/')[3]
                db.add_tag_to_work(w_id, data.get('tag_id'))
                self.send_json(200, {'status': 'added'})
            elif path.startswith('/api/folders/') and path.endswith('/works'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'works':
                    f_id = parts[3]
                    w_id = (data.get('work_id') or '').strip()
                    if not w_id:
                        self.send_json(400, {'error': 'work_id is required'})
                        return
                    if not db.execute_query("SELECT id FROM folders WHERE id = ?", (f_id,)):
                        self.send_json(404, {'error': 'Folder not found'})
                        return
                    if not db.execute_query("SELECT id FROM works WHERE id = ?", (w_id,)):
                        self.send_json(404, {'error': 'Work not found'})
                        return
                    try:
                        db.add_work_to_folder(f_id, w_id)
                    except ValueError as e:
                        self.send_json(409, {'error': str(e)})
                        return
                    self.send_json(200, {'status': 'added'})
                else:
                    self.send_error(404, "API endpoint not found")
            elif path.startswith('/api/folders/') and path.endswith('/tags'):
                f_id = path.split('/')[3]
                db.add_tag_to_folder(f_id, data.get('tag_id'))
                self.send_json(200, {'status': 'added'})
            elif path == '/api/roles':
                p_id = (data.get('person_id') or '').strip()
                w_id = (data.get('work_id') or '').strip()
                r_type = (data.get('role_type') or '').strip()
                if not p_id or not w_id or not r_type:
                    self.send_json(400, {'error': 'person_id, work_id, and role_type are required'})
                    return
                credit_name = data.get('credit_name', '')
                if credit_name is not None and not isinstance(credit_name, str):
                    self.send_json(400, {'error': 'credit_name must be a string'})
                    return
                try:
                    oi = db.next_role_order_index(w_id)
                    db.add_role(
                        p_id,
                        w_id,
                        r_type,
                        order_index=oi,
                        credit_name=credit_name or '',
                    )
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'success'})
            elif path.startswith('/api/works/') and path.endswith('/annotations/adopt'):
                parts = path.split('/')
                if len(parts) != 6 or parts[4] != 'annotations' or parts[5] != 'adopt':
                    self.send_error(404, "API endpoint not found")
                    return
                w_id = parts[3]
                if not isinstance(data, dict):
                    self.send_json(400, {'error': 'JSON object body required'})
                    return
                viewer_annotations = data.get('viewer_annotations')
                if not isinstance(viewer_annotations, list):
                    self.send_json(
                        400,
                        {
                            'error': 'viewer_annotations must be a JSON list',
                            'code': 'malformed_annotation_payload',
                        },
                    )
                    return
                try:
                    result = db.adopt_byte_only_user_markup(w_id, viewer_annotations)
                except WorkAnnotationError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                self.send_json(200, result)
            elif path.startswith('/api/works/') and path.endswith('/pdf'):
                w_id = path.split('/')[3]
                file_b64 = data.get('file_b64', '')
                save_token = str(data.get('save_token', '') or '').strip()
                claimed_set_rev = data.get('materialized_annotation_set_revision', None)
                durable_materialize = claimed_set_rev is not None
                if file_b64:
                    try:
                        pdf_bytes = base64.b64decode(file_b64, validate=True)
                    except (binascii.Error, ValueError):
                        self.send_json(400, {'error': 'Invalid file_b64 payload'})
                        return

                    mat_lock = _pdf_materialization_lock_for(w_id)
                    with mat_lock:
                        if durable_materialize:
                            try:
                                db.accept_work_pdf_materialization_claim(
                                    w_id, claimed_set_rev
                                )
                            except LookupError:
                                self.send_json(404, {'error': 'Work not found'})
                                return
                            except ValueError as e:
                                from backend.pdf_materialization import STALE_CODE
                                if str(e) == STALE_CODE:
                                    mat = db.get_work_pdf_materialization(w_id) or {}
                                    self.send_json(
                                        409,
                                        {
                                            'error': 'PDF materialization is stale',
                                            'code': STALE_CODE,
                                            'canonical_annotation_set_revision': mat.get(
                                                'canonical_annotation_set_revision'
                                            ),
                                            'materialized_pdf_annotation_revision': mat.get(
                                                'materialized_pdf_annotation_revision'
                                            ),
                                        },
                                    )
                                    return
                                self.send_json(400, {'error': 'Invalid materialization revision'})
                                return

                        # 1. Overwrite file
                        res_path = db.execute_query("SELECT file_path FROM works WHERE id=?", (w_id,))
                        if res_path and res_path[0]['file_path']:
                            filename = res_path[0]['file_path'].split('/')[-1]
                            pdf_path = safe_pdf_path_under_dir(pdfs_dir, filename)
                            if not pdf_path:
                                self.send_json(400, {'error': 'Invalid or unsafe PDF storage path'})
                                return
                            with open(pdf_path, 'wb') as f:
                                f.write(pdf_bytes)
                            changed, reason = maybe_linearize_pdf_in_place(pdf_path, context="work-pdf-overwrite")
                            LOGGER.info(
                                "pdf_linearize_result context=work-pdf-overwrite changed=%s reason=%s",
                                "true" if changed else "false",
                                safe_log_label(reason),
                            )
                            try:
                                stored_fp = res_path[0]["file_path"]
                                text_index.sync_work(w_id, stored_fp)
                            except Exception as e:
                                LOGGER.warning(
                                    "work_pdf_replace_text_index_failed work_id=%s error_type=%s",
                                    safe_log_id(w_id),
                                    safe_error_type(e),
                                )

                        # 2. Extract [[Name]] mentions from PDF bytes (linear scan;
                        # never a backtracking regex over untrusted input).
                        from backend.pdf_byte_mentions import iter_pdf_mentioned_labels

                        for clean in iter_pdf_mentioned_labels(pdf_bytes):
                            try:
                                db_res = db.execute_query(
                                    "SELECT id FROM persons WHERE (first_name || ' ' || last_name) = ? OR last_name = ?",
                                    (clean, clean),
                                )
                                if db_res:
                                    p_id = db_res[0]['id']
                                    exist = db.execute_query(
                                        "SELECT 1 FROM roles WHERE person_id=? AND work_id=? AND role_type='Mentioned'",
                                        (p_id, w_id),
                                    )
                                    if not exist:
                                        db.add_role(p_id, w_id, 'Mentioned')
                            except Exception:
                                continue
                        if save_token:
                            with _SAVE_TOKEN_LOCK:
                                _PRKS_LAST_PDF_SAVE_TOKEN_BY_WORK[w_id] = save_token
                        # Slice F: durable path re-validates the claimed generation
                        # after the byte replace so another ACK cannot clear stale
                        # with a future/arbitrary revision. Legacy omits the claim
                        # and marks at the current tip.
                        materialized_rev = None
                        try:
                            if durable_materialize:
                                materialized_rev = db.mark_work_pdf_materialized_if_claim_current(
                                    w_id, claimed_set_rev
                                )
                            else:
                                materialized_rev = db.mark_work_pdf_materialized(w_id)
                        except LookupError:
                            self.send_json(404, {'error': 'Work not found'})
                            return
                        except ValueError as e:
                            from backend.pdf_materialization import STALE_CODE
                            if str(e) == STALE_CODE:
                                mat = db.get_work_pdf_materialization(w_id) or {}
                                self.send_json(
                                    409,
                                    {
                                        'error': 'PDF materialization is stale',
                                        'code': STALE_CODE,
                                        'canonical_annotation_set_revision': mat.get(
                                            'canonical_annotation_set_revision'
                                        ),
                                        'materialized_pdf_annotation_revision': mat.get(
                                            'materialized_pdf_annotation_revision'
                                        ),
                                    },
                                )
                                return
                            LOGGER.warning(
                                "pdf_materialization_mark_failed work_id=%s error_type=%s",
                                safe_log_id(w_id),
                                safe_error_type(e),
                            )
                        except Exception as e:
                            LOGGER.warning(
                                "pdf_materialization_mark_failed work_id=%s error_type=%s",
                                safe_log_id(w_id),
                                safe_error_type(e),
                            )
                        body = {'status': 'success'}
                        if materialized_rev is not None:
                            body['materialized_pdf_annotation_revision'] = materialized_rev
                            mat = db.get_work_pdf_materialization(w_id)
                            if mat:
                                body['canonical_annotation_set_revision'] = mat[
                                    'canonical_annotation_set_revision'
                                ]
                                body['stale'] = mat['stale']
                        self.send_json(200, body)
                else:
                    self.send_error(400, "No file_b64 provided")
            elif path.startswith('/api/works/') and path.endswith('/annotations'):
                # Compat full-list replace. Product writes use durable
                # CREATE/SET/DELETE_PDF_ANNOTATION; this remains for online-legacy
                # when the durable store is unavailable (Slice G retirement).
                w_id = path.split('/')[3]
                if not isinstance(data, dict) or 'annotations_json' not in data:
                    self.send_json(
                        400,
                        {
                            'error': 'annotations_json is required',
                            'code': 'malformed_annotation_payload',
                        },
                    )
                    return
                annotations_json = data.get('annotations_json')
                save_token = str(data.get('save_token', '') or '').strip()
                try:
                    db.save_work_annotations(w_id, annotations_json)
                except WorkAnnotationError as e:
                    self.send_json(e.http_status, {'error': str(e), 'code': e.code})
                    return
                if save_token:
                    with _SAVE_TOKEN_LOCK:
                        _PRKS_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK[w_id] = save_token
                self.send_json(200, {'status': 'saved', 'path': 'legacy-full-list'})
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def _handle_backup_progress(self) -> None:
        if _bound_storage is None:
            self._send_internal_error()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def write_line(payload: dict) -> None:
            body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
            self.wfile.write(body)
            self.wfile.flush()

        try:
            run_backup_with_progress(_bound_storage, write_line)
        except Exception as exc:
            LOGGER.error(
                "backup_progress_failed reason=internal error_type=%s request_id=%s",
                safe_error_type(exc),
                safe_log_id(self._prks_request_id),
            )
            try:
                write_line(
                    {
                        "phase": "failed",
                        "percent": 0,
                        "error": "Backup could not be created.",
                        "reason": "internal",
                    }
                )
            except Exception:
                return

    def _handle_backup_download(self, token: str = "") -> None:
        if _bound_storage is None:
            self._send_internal_error()
            return
        archive_path = None
        try:
            token = (token or "").strip()
            if not token:
                self.send_json(
                    400,
                    {
                        "error": "Backup is not available.",
                        "reason": "unknown_token",
                        "request_id": self._prks_request_id,
                    },
                )
                return
            archive_path, filename, _warnings = take_ready_backup(token)
            file_size = os.path.getsize(archive_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with open(archive_path, "rb") as handle:
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except BackupError as exc:
            self.send_json(
                exc.http_status,
                {
                    "error": exc.message,
                    "reason": exc.reason,
                    "request_id": self._prks_request_id,
                },
            )
        finally:
            if archive_path:
                try:
                    os.remove(archive_path)
                except OSError:
                    pass

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

    def _handle_backup_stage(self, parsed_path=None) -> None:
        content_length = self._validate_backup_stage_headers()
        if content_length is None:
            return
        if parsed_path is None:
            parsed_path = urlparse(self.path)
        max_bytes = backup_max_upload_bytes()
        with self._library_access(parsed_path):
            if _bound_storage is None:
                self._send_internal_error()
                return
            upload_path = None
            try:
                require_restore_upload_space(_bound_storage, content_length)
                upload_path = new_staging_upload_path(_bound_storage)
                stream_upload_to_file(
                    self.rfile,
                    upload_path,
                    expected_length=content_length,
                    max_bytes=max_bytes,
                )
                staged = stage_restore(_bound_storage, upload_path)
                self.send_json(
                    200,
                    {
                        "token": staged.token,
                        "verified": True,
                        "summary": staged.summary,
                        "warnings": staged.warnings,
                    },
                )
            except RestoreError as exc:
                self.send_json(
                    exc.http_status,
                    {
                        "error": exc.message,
                        "reason": exc.reason,
                        "request_id": self._prks_request_id,
                    },
                )
            except Exception as exc:
                self._send_internal_error(exc)
            finally:
                if upload_path:
                    discard_temp_path(upload_path)

    def _handle_backup_restore(self, data) -> None:
        if _bound_storage is None:
            self._send_internal_error()
            return
        if not isinstance(data, dict):
            self.send_json(400, {"error": "JSON object body required"})
            return
        token = data.get("token")
        confirm = data.get("confirm")
        if not isinstance(token, str) or not token.strip():
            self.send_json(400, {"error": "token is required"})
            return
        try:
            out = apply_restore(
                _bound_storage,
                token.strip(),
                confirm if isinstance(confirm, str) else "",
                rebind=bind_storage,
            )
            self.send_json(200, out)
        except RestoreError as exc:
            self.send_json(
                exc.http_status,
                {
                    "error": exc.message,
                    "reason": exc.reason,
                    "request_id": self._prks_request_id,
                },
            )

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
        self.wfile.write(body)

class PRKSThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True


def run_server(port=PORT, host=DEFAULT_HOST):
    if _bound_storage is None:
        raise RuntimeError("storage is not bound; call bind_storage() before run_server()")
    host = normalize_listen_host(host)
    trusted_hosts = parse_trusted_hosts(os.environ.get("PRKS_TRUSTED_HOSTS", ""))
    _validate_listen_port(port)
    # Setup for allowing reusing address
    socketserver.TCPServer.allow_reuse_address = True
    try:
        n = prune_orphan_pdf_thumbnails(db)
        if n:
            LOGGER.info("thumbnail_prune_complete pruned=%s", n)
    except Exception as e:
        LOGGER.warning("thumbnail_prune_skipped error_type=%s", safe_error_type(e))
    try:
        reconcile_at_startup(db, text_index)
    except Exception as e:
        LOGGER.warning("text_index_reconcile_failed error_type=%s", safe_error_type(e))
    try:
        reconcile_research_index_at_startup(db, research_index)
    except Exception as e:
        LOGGER.warning("research_index_reconcile_failed error_type=%s", safe_error_type(e))
    try:
        cleanup_stale_staging(_bound_storage)
        cleanup_expired_backup_jobs(_bound_storage)
    except Exception as e:
        LOGGER.warning("restore_staging_cleanup_skipped error_type=%s", safe_error_type(e))
    with PRKSThreadingTCPServer((host, port), PRKSHandler) as httpd:
        httpd.prks_bind_host = host
        httpd.prks_trusted_hosts = trusted_hosts
        httpd.prks_access_gate = LibraryAccessGate()
        LOGGER.info(
            "server_starting bind_scope=%s port=%s",
            safe_bind_scope(host),
            port,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            LOGGER.info("server_stopping reason=keyboard_interrupt")
