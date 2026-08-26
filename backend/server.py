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
from dataclasses import replace
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Add the parent directory to sys.path to ensure 'backend' module is resolvable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db_manager import (
    PRKSDatabase,
    safe_pdf_path_under_dir,
    prks_thumb_cache_safe_wid,
    prks_thumb_cache_stem,
    prune_orphan_pdf_thumbnails,
    prks_person_image_cache_path,
    prks_person_image_legacy_bin_path,
    prks_delete_person_image_cache,
)
from backend.log_config import setup_logging
from backend.text_index import (
    PRKSTextIndex,
    get_text_index,
    replace_text_index,
    reset_text_index,
)
from backend.pdf_linearize import maybe_linearize_pdf_in_place, is_pdf_linearized
from backend.storage import paths
from backend.storage.config import StorageConfig

LOGGER = logging.getLogger("prks.server")

PORT = 8080
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
        if processing_local != paths.PROCESSING_PROD_PREFERRED:
            raise
        processing_local = paths.processing_prod_fallback()
        os.makedirs(processing_local, exist_ok=True)
    if processing_local != config.processing_dir:
        config = replace(config, processing_dir=processing_local)

    os.makedirs(config.pdfs_dir, exist_ok=True)
    os.makedirs(config.thumbs_dir, exist_ok=True)

    db_local = PRKSDatabase(storage=config, schema_path="backend/db_schema.sql")
    candidate = PRKSTextIndex(storage=config)

    global _bound_storage, pdfs_dir, thumbs_dir, processing_dir, db, text_index
    previous_published = (
        _bound_storage,
        pdfs_dir,
        thumbs_dir,
        processing_dir,
        db,
        text_index,
    )
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    try:
        replace_text_index(candidate)
        _bound_storage = config
        pdfs_dir = config.pdfs_dir
        thumbs_dir = config.thumbs_dir
        processing_dir = config.processing_dir
        db = db_local
        text_index = candidate
    except Exception:
        if previous_index is not None:
            replace_text_index(previous_index)
        else:
            reset_text_index()
        (
            _bound_storage,
            pdfs_dir,
            thumbs_dir,
            processing_dir,
            db,
            text_index,
        ) = previous_published
        raise
    return config


def _prks_detect_image_mime(header: bytes) -> str:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


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


def _prks_portrait_cache_bytes(
    raw: bytes, max_edge: int = 512
) -> tuple[bytes, str] | None:
    """
    Resize/transcode remote portrait bytes for on-disk cache + API serve.
    Returns (bytes, mime_subtype) e.g. (..., 'webp'), or None if not decodable.
    """
    from io import BytesIO

    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        w, h = img.size
        m = max(w, h)
        if m > max_edge:
            scale = max_edge / float(m)
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((nw, nh), resample)
        webp = _prks_pil_to_card_webp_bytes(img)
        if webp is not None:
            return webp, "webp"
        jpeg = _prks_pil_to_jpeg_bytes(img)
        if jpeg is not None:
            return jpeg, "jpeg"
        return None
    except Exception:
        return None


def _prks_write_person_image_cache(cache_path: str, body: bytes) -> None:
    parent = os.path.dirname(cache_path)
    os.makedirs(parent, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(body)
    os.replace(tmp, cache_path)


def _prks_image_content_type(subtype: str, body: bytes) -> str:
    if subtype == "webp":
        return "image/webp"
    if subtype == "jpeg":
        return "image/jpeg"
    if subtype == "png":
        return "image/png"
    return _prks_detect_image_mime(body[:64])


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

def _youtube_video_id(url: str) -> str | None:
    try:
        u = urlparse(url)
    except Exception:
        return None
    host = (u.netloc or "").lower()
    if host.endswith("youtu.be"):
        vid = (u.path or "").strip("/").split("/")[0].strip()
        return vid or None
    if "youtube.com" in host:
        qs = parse_qs(u.query or "")
        vid = (qs.get("v") or [""])[0].strip()
        if vid:
            return vid
        # /embed/<id>
        parts = (u.path or "").strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "embed" and parts[1].strip():
            return parts[1].strip()
    return None


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
        parsed = urlparse(getattr(self, "path", ""))
        return {
            "method": getattr(self, "command", ""),
            "path": parsed.path,
            "query": parsed.query,
            "client": getattr(self, "client_address", ("", 0))[0],
            "request_id": self._prks_request_id,
        }

    def _send_internal_error(self, exc: Exception | None = None):
        ctx = self._request_context()
        if exc is not None:
            LOGGER.exception(
                "unhandled_api_error method=%s path=%s query=%s client=%s request_id=%s",
                ctx["method"],
                ctx["path"],
                ctx["query"],
                ctx["client"],
                ctx["request_id"],
            )
        else:
            LOGGER.error(
                "internal_error method=%s path=%s query=%s client=%s request_id=%s",
                ctx["method"],
                ctx["path"],
                ctx["query"],
                ctx["client"],
                ctx["request_id"],
            )
        self.send_json(500, {"error": "internal_error", "request_id": self._prks_request_id})

    def _read_json_body(self):
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

    @staticmethod
    def _sanitize_client_error_text(value, limit=500):
        if value is None:
            return ""
        text = str(value).strip()
        if len(text) > limit:
            return text[:limit]
        return text

    def _parse_client_error_payload(self, data):
        if not isinstance(data, dict):
            raise ValueError("JSON object body required")
        message = self._sanitize_client_error_text(data.get("message"), limit=2000)
        if not message:
            raise ValueError("message is required")
        payload = {
            "kind": self._sanitize_client_error_text(data.get("kind"), limit=64) or "client_error",
            "message": message,
            "stack": self._sanitize_client_error_text(data.get("stack"), limit=8000),
            "route": self._sanitize_client_error_text(data.get("route"), limit=512),
            "source": self._sanitize_client_error_text(data.get("source"), limit=512),
            "request_id": self._sanitize_client_error_text(data.get("request_id"), limit=64),
            "client_time": self._sanitize_client_error_text(data.get("client_time"), limit=64),
            "user_agent": self._sanitize_client_error_text(self.headers.get("User-Agent"), limit=512),
        }
        return payload

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
        if self._prks_request_id:
            self.send_header("X-Request-ID", self._prks_request_id)
        super().end_headers()

    def log_message(self, format, *args):
        LOGGER.info(
            "request_access client=%s request_id=%s message=%s",
            self.address_string(),
            self._prks_request_id,
            format % args,
        )

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
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
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
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
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            self.handle_api_post(parsed_path)
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PATCH(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
            self.handle_api_patch(parsed_path)
        else:
            self.send_error(405, "Method Not Allowed")

    def handle_api_patch(self, parsed_path):
        path = parsed_path.path
        try:
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
                if db.update_role_credit_name(
                    w_id, person_id, role_type, order_index, credit_name or ''
                ):
                    cn = (credit_name or '').strip()
                    if cn:
                        db.append_person_alias_if_new(person_id, cn)
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
                    db.update_work_metadata(w_id, body)
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/playlists/') and len(path.split('/')) == 4:
                pl_id = path.split('/')[-1]
                db.update_playlist(pl_id, data)
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/persons/') and len(path.split('/')) == 4:
                p_id = path.split('/')[-1]
                group_ids = data.pop('group_ids', None)
                if 'image_url' in data:
                    prks_delete_person_image_cache(p_id, _bound_storage.people_dir)
                db.update_person_metadata(p_id, data)
                if group_ids is not None:
                    if not isinstance(group_ids, list):
                        self.send_json(400, {'error': 'group_ids must be a JSON array'})
                        return
                    try:
                        db.set_person_group_memberships(p_id, group_ids)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/person-groups/') and len(path.split('/')) == 4:
                g_id = path.split('/')[-1]
                if 'parent_name' in data:
                    data = dict(data)
                    raw = data.pop('parent_name')
                    data.pop('parent_id', None)
                    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
                        data['parent_id'] = None
                    else:
                        try:
                            data['parent_id'] = db.resolve_or_create_parent_group_by_name(
                                str(raw).strip(), g_id
                            )
                        except ValueError as e:
                            self.send_json(400, {'error': str(e)})
                            return
                try:
                    db.update_person_group(g_id, data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'updated'})
            elif path.startswith('/api/folders/') and len(path.split('/')) == 4:
                f_id = path.split('/')[-1]
                try:
                    db.update_folder_metadata(f_id, data)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'updated'})
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
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def do_DELETE(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith('/api/'):
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
                db.delete_work(w_id)
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
            LOGGER.info(
                "pdf_stream mode=partial method=%s head_only=%s request_id=%s path=%s start=%s end=%s file_size=%s",
                self.command,
                head_only,
                self._prks_request_id,
                self.path,
                start,
                end,
                file_size,
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

        LOGGER.info(
            "pdf_stream mode=full method=%s head_only=%s request_id=%s path=%s file_size=%s",
            self.command,
            head_only,
            self._prks_request_id,
            self.path,
            file_size,
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
        mime = _prks_image_content_type(subtype, body)
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

        body = read_cache_file(cache_path)
        if body:
            self._send_person_profile_image_bytes(body, "webp")
            return

        legacy_body = read_cache_file(legacy_bin)
        if legacy_body:
            encoded = _prks_portrait_cache_bytes(legacy_body)
            if encoded is not None:
                out, subtype = encoded
                try:
                    _prks_write_person_image_cache(cache_path, out)
                    try:
                        os.remove(legacy_bin)
                    except OSError:
                        pass
                except OSError:
                    pass
                self._send_person_profile_image_bytes(out, subtype, max_age=3600)
                return
            self._send_person_profile_image_bytes(
                legacy_body, _prks_detect_image_mime(legacy_body[:64]).split("/")[-1]
            )
            return

        fetched: bytes | None = None
        if url.startswith("http://") or url.startswith("https://"):
            try:
                req = Request(url, headers={"User-Agent": "PRKS/1.0 (person portrait cache)"})
                with urlopen(req, timeout=25) as resp:
                    code = int(getattr(resp, "status", 0) or getattr(resp, "code", 0) or 0)
                    if code == 200:
                        blob = resp.read()
                        if blob:
                            fetched = blob
            except (HTTPError, URLError, OSError, ValueError, TypeError, TimeoutError):
                fetched = None
            except Exception:
                fetched = None

        if fetched:
            encoded = _prks_portrait_cache_bytes(fetched)
            if encoded is not None:
                out, subtype = encoded
                try:
                    _prks_write_person_image_cache(cache_path, out)
                except OSError:
                    pass
                self._send_person_profile_image_bytes(out, subtype, max_age=3600)
                return
            try:
                parent = os.path.dirname(legacy_bin)
                os.makedirs(parent, exist_ok=True)
                tmp = legacy_bin + ".tmp"
                with open(tmp, "wb") as fp:
                    fp.write(fetched)
                os.replace(tmp, legacy_bin)
            except OSError:
                pass
            raw_sub = _prks_detect_image_mime(fetched[:64]).split("/")[-1]
            self._send_person_profile_image_bytes(fetched, raw_sub, max_age=3600)
            return

        self.send_error(404, "Profile image not available")

    def handle_api_get(self, parsed_path):
        query = parse_qs(parsed_path.query)
        path = parsed_path.path
        
        try:
            if path == '/api/works':
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
                            "thumbnail_fitz_import_failed error=%s request_id=%s",
                            e,
                            self._prks_request_id,
                        )
                        self.send_error(404, "Thumbnail unavailable")
                        return

                    try:
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
                            generated_bytes, thumb_sub = _prks_thumbnail_bytes_from_pixmap(pix)
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
                            "thumbnail_render_failed work_id=%s page=%s request_id=%s",
                            w_id,
                            page,
                            self._prks_request_id,
                            exc_info=exc,
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
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                data = db.get_work(w_id)
                if data: self.send_json(200, data)
                else: self.send_error(404, "Work not found")
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
                etag = db.etag_folders_catalog()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_all_folders()
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
            elif path.startswith('/api/person-groups/') and len(path.split('/')) == 4:
                g_id = path.split('/')[-1]
                data = db.get_person_group(g_id)
                if data:
                    self.send_json(200, data)
                else:
                    self.send_error(404, "Group not found")
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
                etag = db.etag_recent_works()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_recent_works()
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/recently-added':
                etag = db.etag_recently_added_works()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_recently_added_works()
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
                    etag = db.etag_tags_all()
                    if self._prks_if_none_match(etag):
                        self._send_json_not_modified(etag)
                        return
                    data = db.get_all_tags()
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
            elif path == '/api/processing-files':
                db.scan_processing_files()
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

    def handle_api_post(self, parsed_path):
        path = parsed_path.path
        try:
            data = self._read_json_body()
            if data is None:
                return

            if path == '/api/client-errors':
                try:
                    payload = self._parse_client_error_payload(data)
                except ValueError as e:
                    self.send_json(400, {"error": str(e)})
                    return
                LOGGER.error(
                    "client_error kind=%s message=%s route=%s source=%s client_request_id=%s request_id=%s user_agent=%s stack=%s",
                    payload["kind"],
                    payload["message"],
                    payload["route"],
                    payload["source"],
                    payload["request_id"],
                    self._prks_request_id,
                    payload["user_agent"],
                    payload["stack"],
                )
                self.send_json(200, {"status": "logged", "request_id": self._prks_request_id})
            elif path == '/api/works/reindex-pdf-text':
                summary = text_index.reindex_all(db)
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
                        "pdf_linearize_result context=settings-bulk changed=%s reason=%s path=%s",
                        did_change,
                        reason,
                        abs_path,
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
                            if fp.startswith("/api/pdfs/"):
                                filename = fp.split("/")[-1]
                                abs_path = _safe_pdf_path_in_pdfs_dir(filename)
                                if abs_path and os.path.exists(abs_path):
                                    text_index.upsert_from_pdf(work_id, abs_path)
                    except Exception as e:
                        LOGGER.warning("processing_import_text_index_failed processing_file_id=%s error=%s", pf_id, e)
                    self.send_json(200, out)
                else:
                    self.send_error(404, "API endpoint not found")
            elif path == '/api/works':
                file_path = data.get('file_path', '')
                source_kind = (data.get('source_kind') or '').strip().lower()
                source_url = (data.get('source_url') or '').strip()

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
                            "pdf_linearize_result context=work-create-upload changed=%s reason=%s path=%s",
                            changed,
                            reason,
                            abs_uploaded_path,
                        )
                    file_path = f"/api/pdfs/{local_filename}"

                provider = (data.get('provider') or '').strip().lower()
                provider_id = (data.get('provider_id') or '').strip()
                thumb_url = (data.get('thumb_url') or '').strip()
                source_mime = (data.get('source_mime') or '').strip()
                urldate = (data.get('urldate') or '').strip()

                # Video ingest: URL + oEmbed metadata
                if source_kind == 'video' and source_url:
                    if not provider:
                        try:
                            host = (urlparse(source_url).netloc or '').lower()
                        except Exception:
                            host = ''
                        if 'youtube.com' in host or 'youtu.be' in host:
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
                if file_path.startswith("/api/pdfs/"):
                    try:
                        filename = file_path.split("/")[-1]
                        abs_path = _safe_pdf_path_in_pdfs_dir(filename)
                        if abs_path and os.path.exists(abs_path):
                            text_index.upsert_from_pdf(w_id, abs_path)
                    except Exception as e:
                        LOGGER.warning("work_create_text_index_failed work_id=%s error=%s", w_id, e)
                # Optionally attach to playlist
                playlist_id = (data.get('playlist_id') or '').strip()
                if playlist_id:
                    try:
                        db.add_work_to_playlist(playlist_id, w_id, None)
                    except Exception as exc:
                        # best-effort: do not fail work creation if playlist link fails
                        LOGGER.warning(
                            "work_playlist_attach_failed playlist_id=%s work_id=%s request_id=%s",
                            playlist_id,
                            w_id,
                            self._prks_request_id,
                            exc_info=exc,
                        )
                folder_id = data.get("folder_id")
                raw_folder = str(folder_id).strip() if folder_id is not None else ""
                if raw_folder:
                    try:
                        db.add_work_to_folder(raw_folder, w_id)
                    except ValueError as e:
                        db.delete_work(w_id)
                        self.send_json(409, {'error': str(e)})
                        return
                else:
                    try:
                        unc_id = db.ensure_default_uncategorized_folder_id()
                        db.add_work_to_folder(unc_id, w_id)
                    except ValueError as e:
                        db.delete_work(w_id)
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
                            db.add_role(
                                p_id,
                                w_id,
                                r_type,
                                order_index=idx,
                                credit_name=credit_name or '',
                            )
                            cn = (credit_name or '').strip()
                            if cn:
                                db.append_person_alias_if_new(p_id, cn)
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
                p_id = db.add_person(
                    first_name=data.get('first_name', ''),
                    last_name=data.get('last_name', ''),
                    aliases=data.get('aliases', ''),
                    about=data.get('about', ''),
                    image_url=data.get('image_url', '') or '',
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
                    cn = (credit_name or '').strip()
                    if cn:
                        db.append_person_alias_if_new(p_id, cn)
                except ValueError as e:
                    self.send_json(400, {'error': str(e)})
                    return
                self.send_json(200, {'status': 'success'})
            elif path == '/api/arguments':
                a_id = db.add_argument(data.get('work_id'), data.get('premise'), data.get('conclusion'))
                self.send_json(200, {'id': a_id})
            elif path == '/api/concepts':
                c_id = db.add_concept(data.get('name'), data.get('description'))
                w_id = data.get('work_id')
                file_b64 = data.get('file_b64', '')
                text = data.get('annotations_text', '')

                matches = []
                if file_b64:
                    try:
                        pdf_bytes = base64.b64decode(file_b64, validate=True)
                    except (binascii.Error, ValueError):
                        self.send_json(400, {'error': 'Invalid file_b64 payload'})
                        return
                    byte_matches = re.findall(rb'\[\[(.*?)\]\]', pdf_bytes)
                    for b in byte_matches:
                        try:
                            decoded = b.decode('utf-8', errors='ignore').strip()
                            clean = ''.join(c for c in decoded if c.isalnum() or c.isspace() or c in "-_")
                            if clean:
                                matches.append(clean)
                        except Exception:
                            pass
                elif text:
                    matches = [m.strip() for m in re.findall(r'\[\[(.*?)\]\]', text)]

                mention_status = 'skipped'
                if w_id and matches:
                    mention_status = 'processed'
                    for raw in matches:
                        db_res = db.execute_query(
                            "SELECT id FROM persons WHERE (first_name || ' ' || last_name) = ? OR last_name = ?",
                            (raw, raw),
                        )
                        if db_res:
                            p_id = db_res[0]['id']
                            exist = db.execute_query(
                                "SELECT 1 FROM roles WHERE person_id=? AND work_id=? AND role_type='Mentioned'",
                                (p_id, w_id),
                            )
                            if not exist:
                                db.add_role(p_id, w_id, 'Mentioned')
                self.send_json(200, {'id': c_id, 'status': mention_status})
                
            elif path.startswith('/api/works/') and path.endswith('/pdf'):
                w_id = path.split('/')[3]
                file_b64 = data.get('file_b64', '')
                save_token = str(data.get('save_token', '') or '').strip()
                if file_b64:
                    try:
                        pdf_bytes = base64.b64decode(file_b64, validate=True)
                    except (binascii.Error, ValueError):
                        self.send_json(400, {'error': 'Invalid file_b64 payload'})
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
                            "pdf_linearize_result context=work-pdf-overwrite changed=%s reason=%s path=%s",
                            changed,
                            reason,
                            pdf_path,
                        )
                        try:
                            text_index.upsert_from_pdf(w_id, pdf_path)
                        except Exception as e:
                            LOGGER.warning("work_pdf_replace_text_index_failed work_id=%s error=%s", w_id, e)
                            
                    # 2. Extract annotations
                    byte_matches = re.findall(rb'\[\[(.*?)\]\]', pdf_bytes)
                    for b in byte_matches:
                        try:
                            decoded = b.decode('utf-8', errors='ignore').strip()
                            clean = ''.join(c for c in decoded if c.isalnum() or c.isspace() or c in "-_")
                            if clean:
                                db_res = db.execute_query("SELECT id FROM persons WHERE (first_name || ' ' || last_name) = ? OR last_name = ?", (clean, clean))
                                if db_res:
                                    p_id = db_res[0]['id']
                                    exist = db.execute_query("SELECT 1 FROM roles WHERE person_id=? AND work_id=? AND role_type='Mentioned'", (p_id, w_id))
                                    if not exist:
                                        db.add_role(p_id, w_id, 'Mentioned')
                        except Exception:
                            continue
                    if save_token:
                        _PRKS_LAST_PDF_SAVE_TOKEN_BY_WORK[w_id] = save_token
                    self.send_json(200, {'status': 'success'})
                else:
                    self.send_error(400, "No file_b64 provided")
            elif path.startswith('/api/works/') and path.endswith('/annotations'):
                w_id = path.split('/')[3]
                annotations_json = data.get('annotations_json', '[]')
                save_token = str(data.get('save_token', '') or '').strip()
                db.save_work_annotations(w_id, annotations_json)
                if save_token:
                    _PRKS_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK[w_id] = save_token
                self.send_json(200, {'status': 'saved'})
            else:
                self.send_error(404, "API endpoint not found")
        except Exception as exc:
            self._send_internal_error(exc)

    def send_json(self, status, context, etag=None, precondition_checked=False):
        if etag and status == 200 and not precondition_checked and self._prks_if_none_match(etag):
            self._send_json_not_modified(etag)
            return
        body = json.dumps(context).encode("utf-8")
        ae = (self.headers.get("Accept-Encoding") or "").lower()
        use_gzip = "gzip" in ae and len(body) >= _PRKS_JSON_GZIP_MIN_BYTES
        if use_gzip:
            body = gzip.compress(body, compresslevel=6)
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

def run_server(port=PORT):
    if _bound_storage is None:
        raise RuntimeError("storage is not bound; call bind_storage() before run_server()")
    _validate_listen_port(port)
    # Setup for allowing reusing address
    socketserver.TCPServer.allow_reuse_address = True
    try:
        n = prune_orphan_pdf_thumbnails(db)
        if n:
            LOGGER.info("thumbnail_prune_complete pruned=%s", n)
    except Exception as e:
        LOGGER.warning("thumbnail_prune_skipped error=%s", e)
    with socketserver.TCPServer(("", port), PRKSHandler) as httpd:
        LOGGER.info("server_starting url=http://localhost:%s", port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            LOGGER.info("server_stopping reason=keyboard_interrupt")

if __name__ == "__main__":
    if "--testing" in sys.argv:
        os.environ["PRKS_TESTING"] = "1"
    config = StorageConfig.from_env()
    config = bind_storage(config)
    setup_logging(config)
    run_server()
