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
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen, Request

# Add the parent directory to sys.path to ensure 'backend' module is resolvable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db_manager import (
    PRKSDatabase,
    default_prks_db_path,
    default_local_pdfs_dir,
    safe_pdf_path_under_dir,
    resolve_processing_dir,
)

PORT = 8080

# Minimum uncompressed JSON size before gzip (Accept-Encoding: gzip).
_PRKS_JSON_GZIP_MIN_BYTES = 1024
_PRKS_MAX_JSON_BODY_BYTES = 50 * 1024 * 1024

# Get the path to the frontend directory
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dir = os.path.join(base_dir, "frontend")

# Ensure frontend dir exists so http.server doesn't crash on startup
os.makedirs(frontend_dir, exist_ok=True)


def _get_storage_root() -> str | None:
    raw = os.environ.get("PRKS_STORAGE")
    if raw is None:
        return None
    root = str(raw).strip()
    if not root:
        return None
    if _is_testing_env() and (root == "/data" or root.startswith("/data/")):
        raise RuntimeError("PRKS_TESTING is set: refusing to use PRKS_STORAGE under /data")
    return root


def _resolve_db_path() -> str:
    storage_root = _get_storage_root()
    if storage_root:
        return os.path.join(storage_root, "prks_data.db")
    return default_prks_db_path()


def _resolve_pdfs_dir() -> str:
    storage_root = _get_storage_root()
    if storage_root:
        return os.path.join(storage_root, "pdfs")
    return default_local_pdfs_dir()

def _is_testing_env() -> bool:
    v = os.environ.get("PRKS_TESTING", "")
    return str(v).strip().lower() in ("1", "true", "yes")

def _resolve_thumbs_dir() -> str:
    storage_root = _get_storage_root()
    if storage_root:
        return os.path.join(storage_root, "thumbs")
    # Mirror backend/db_manager.py default dirs
    if _is_testing_env():
        return os.path.join(base_dir, "data_testing", "thumbs")
    return os.path.join(base_dir, "data", "thumbs")


pdfs_dir = _resolve_pdfs_dir()
os.makedirs(pdfs_dir, exist_ok=True)
thumbs_dir = _resolve_thumbs_dir()
os.makedirs(thumbs_dir, exist_ok=True)
processing_dir = resolve_processing_dir()
os.makedirs(processing_dir, exist_ok=True)


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
    Encode extracted raster for caching/serving: lossless WebP first, then lossless optimized PNG,
    then raw PyMuPDF PNG as last resort.
    Returns (bytes, mime_subtype) — 'webp' or 'png' for Content-Type image/<subtype>.
    """
    webp = _prks_pixmap_to_lossless_webp_bytes(pix)
    if webp is not None:
        return webp, "webp"
    png = _prks_pixmap_to_lossless_png_bytes(pix)
    if png is not None:
        return png, "png"
    return pix.tobytes("png"), "png"


db = PRKSDatabase(db_path=_resolve_db_path(), schema_path="backend/db_schema.sql")

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

    _STATIC_LONG_CACHE_EXTS = frozenset(
        {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".css", ".js", ".woff2", ".map"}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=frontend_dir, **kwargs)

    def _send_internal_error(self):
        self.send_json(500, {"error": "internal_error"})

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

    def end_headers(self):
        # Avoid hammering the server: browsers and embedded viewers may revalidate small assets often
        # if Cache-Control is missing (default was heuristic / no-store in some cases).
        try:
            p = urlparse(self.path).path
            if not p.startswith("/api/") and p != "/index.html":
                leaf = p.rstrip("/").split("/")[-1].lower()
                if leaf == "sw.js":
                    self.send_header("Cache-Control", "no-cache")
                elif leaf == "manifest.webmanifest":
                    # Browsers re-check the manifest often; no-cache caused a 304 storm in logs.
                    self.send_header("Cache-Control", "public, max-age=3600")
                else:
                    ext = os.path.splitext(p)[1].lower()
                    if ext in self._STATIC_LONG_CACHE_EXTS:
                        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        except Exception:
            pass
        super().end_headers()

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
        except Exception:
            self._send_internal_error()

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
                db.delete_tag(t_id)
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
        except Exception:
            self._send_internal_error()

    def _send_pdf_bytes(self, pdf_path: str) -> None:
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
            self.send_response(206)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
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

        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()
        with open(pdf_path, "rb") as f:
            self.wfile.write(f.read())

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

                safe_wid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(w_id))
                cache_base = f"{safe_wid}_p{page}"
                path_webp = os.path.join(thumbs_dir, cache_base + ".webp")
                path_png = os.path.join(thumbs_dir, cache_base + ".png")

                cache_path: str | None = None
                serve_mime = "image/webp"
                for cand, mime in ((path_webp, "image/webp"), (path_png, "image/png")):
                    if os.path.exists(cand):
                        try:
                            if os.path.getmtime(cand) >= pdf_mtime:
                                cache_path = cand
                                serve_mime = mime
                                break
                        except OSError:
                            pass

                cache_hit = cache_path is not None
                generated_bytes: bytes | None = None

                if not cache_hit:
                    for stale in (path_webp, path_png):
                        if os.path.exists(stale):
                            try:
                                os.remove(stale)
                            except OSError:
                                pass
                    try:
                        import fitz  # PyMuPDF
                    except Exception as e:
                        # Common when the image/venv omits `pip install -r requirements.txt` (see Dockerfile).
                        print(
                            "[PRKS] PDF thumbnails need PyMuPDF (pip install -r requirements.txt). "
                            "import fitz failed:",
                            e,
                            file=sys.stderr,
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
                            cache_path = os.path.join(thumbs_dir, f"{cache_base}.{thumb_sub}")
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
                    except Exception:
                        # If a particular PDF can't be rendered, don't take down the whole request path.
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
                except Exception:
                    self._send_internal_error()
            elif path.startswith('/api/works/') and len(path.split('/')) == 4:
                w_id = path.split('/')[-1]
                data = db.get_work(w_id)
                if data: self.send_json(200, data)
                else: self.send_error(404, "Work not found")
            elif path.startswith('/api/works/') and path.endswith('/annotations'):
                w_id = path.split('/')[3]
                data = {"work_id": w_id, "annotations_json": db.get_work_annotations(w_id)}
                self.send_json(200, data)
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
            elif path.startswith('/api/persons/') and len(path.split('/')) == 4:
                p_id = path.split('/')[-1]
                data = db.get_person(p_id)
                if data: self.send_json(200, data)
                else: self.send_error(404, "Person not found")
            elif path == '/api/recent':
                etag = db.etag_recent_works()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.get_recent_works()
                self.send_json(200, data, etag=etag, precondition_checked=True)
            elif path == '/api/search':
                q = query.get('q', [''])[0]
                tag = query.get('tag', [''])[0]
                author = query.get('author', [''])[0]
                publisher = query.get('publisher', [''])[0]
                if tag:
                    data = db.get_works_by_tag_name(tag)
                    if author and author.strip():
                        allow = set(db.work_ids_matching_author(author.strip()))
                        data = [w for w in data if w.get('id') in allow]
                    if publisher and publisher.strip():
                        allow_pub = set(db.work_ids_matching_publisher(publisher.strip()))
                        data = [w for w in data if w.get('id') in allow_pub]
                else:
                    data = db.search_works(
                        q,
                        author.strip() if author else '',
                        publisher.strip() if publisher else '',
                    )
                self.send_json(200, data)
            elif path == '/api/tags':
                used_only = query.get('used', [''])[0] in ('1', 'true', 'yes')
                recent_only = query.get('recent', [''])[0] in ('1', 'true', 'yes')
                try:
                    lim = int(query.get('limit', ['8'])[0])
                except ValueError:
                    lim = 8
                if recent_only:
                    data = db.get_recent_tags_in_use(lim)
                    self.send_json(200, data)
                elif used_only:
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
            elif path == '/api/graph':
                etag = db.etag_graph()
                if self._prks_if_none_match(etag):
                    self._send_json_not_modified(etag)
                    return
                data = db.build_graph_data()
                self.send_json(200, data, etag=etag, precondition_checked=True)
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
                rescan = query.get('rescan', [''])[0] in ('1', 'true', 'yes')
                if rescan:
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
        except Exception:
            self._send_internal_error()

    def handle_api_post(self, parsed_path):
        path = parsed_path.path
        try:
            data = self._read_json_body()
            if data is None:
                return

            if path.startswith('/api/processing-files/') and path.endswith('/import'):
                parts = path.split('/')
                if len(parts) == 5 and parts[4] == 'import':
                    pf_id = parts[3]
                    try:
                        out = db.import_processing_file(pf_id)
                    except ValueError as e:
                        self.send_json(400, {'error': str(e)})
                        return
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
                # Optionally attach to playlist
                playlist_id = (data.get('playlist_id') or '').strip()
                if playlist_id:
                    try:
                        db.add_work_to_playlist(playlist_id, w_id, None)
                    except Exception:
                        # best-effort: do not fail work creation if playlist link fails
                        pass
                folder_id = data.get('folder_id')
                if folder_id:
                    try:
                        db.add_work_to_folder(folder_id, w_id)
                    except ValueError as e:
                        db.delete_work(w_id)
                        self.send_json(409, {'error': str(e)})
                        return
                
                # Link persons/roles provided during upload
                roles = data.get('roles', [])
                if isinstance(roles, list):
                    for idx, r in enumerate(roles):
                        if isinstance(r, dict) and r.get('person_id') and r.get('role_type'):
                            db.add_role(r['person_id'], w_id, r['role_type'], order_index=idx)
                        
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
                oi = db.next_role_order_index(w_id)
                db.add_role(p_id, w_id, r_type, order_index=oi)
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
                    self.send_json(200, {'status': 'success'})
                else:
                    self.send_error(400, "No file_b64 provided")
            elif path.startswith('/api/works/') and path.endswith('/annotations'):
                w_id = path.split('/')[3]
                annotations_json = data.get('annotations_json', '[]')
                db.save_work_annotations(w_id, annotations_json)
                self.send_json(200, {'status': 'saved'})
            else:
                self.send_error(404, "API endpoint not found")
        except Exception:
            self._send_internal_error()

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
    # Setup for allowing reusing address
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), PRKSHandler) as httpd:
        print(f"Serving PRKS internal API and frontend at http://localhost:{port}")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
