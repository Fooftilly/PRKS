"""Work card thumbnails: ``GET /api/works/:id/thumbnail?page=N`` (#1424).

A page of the Work's managed PDF (``page``, else the Work's ``thumb_page``,
else 1), rendered card-sized and encoded as lossy WebP (JPEG, then PNG when
WebP encoding is unavailable; ``PRKS_THUMB_LOSSLESS=1`` keeps lossless
WebP/PNG for debugging). The PDF is read through the Library's
``StorageBackend`` (#1427). Renders are cached under the root's thumbs
directory, keyed by Work, page, primary Asset and PDF name; a copy older
than the stored object is a miss. A cache write is best effort: a read-only volume
still serves the rendered image.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from backend.media.derived_cache_publish import publish_derived_cache_bytes
from backend.media.derived_caches import (
    prks_delete_pdf_thumbnails_for_work_id,
    prks_prune_thumbnail_page,
    prks_thumb_cache_stem,
)
from backend.runtime.log_safety import safe_error_type, safe_log_id
from backend.pdf.managed_pdf_names import managed_pdf_filename, managed_pdf_key
from backend.runtime.performance import clock_ns, record_counter, record_span
from backend.storage.objects import StorageObjectError, readable_path
from backend.web.http import Request, Response, bytes_response, error_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")

#: Card width the page is scaled to, and the scale bounds.
TARGET_WIDTH = 560.0
MAX_SCALE = 2.25
MIN_SCALE = 0.6


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


def _page(req: Request, thumb: dict[str, Any]) -> int:
    """The 1-based page: ``?page=``, else the Work's ``thumb_page``, else 1."""
    raw = req.query_value("page").strip()
    page: Optional[int] = None
    if raw:
        try:
            page = int(raw)
        except ValueError:
            page = None
    if page is None:
        try:
            page = int(thumb.get("thumb_page") or 1)
        except (TypeError, ValueError):
            page = 1
    return max(page, 1)


def _cached(path_webp: str, pdf_mtime: float) -> Optional[bytes]:
    """A cached copy no older than the PDF. Read now: a prune may remove the
    file before it is sent, and a vanished file is then simply a miss."""
    try:
        if os.path.getmtime(path_webp) >= pdf_mtime:
            with open(path_webp, "rb") as cached:
                return cached.read() or None
    except OSError:
        return None
    return None


def _render(req: Request, pdf_path: str, page: int) -> tuple[bytes, str]:
    """The page rendered and encoded: ``(bytes, image subtype)``."""
    import pymupdf as fitz  # PyMuPDF

    t_render = clock_ns()
    doc = fitz.open(pdf_path)
    try:
        page_index = page - 1
        if page_index < 0 or page_index >= doc.page_count:
            page_index = 0
        pg = doc.load_page(page_index)
        rect = pg.rect
        width = float(rect.width) if rect and rect.width else 612.0
        scale = TARGET_WIDTH / width if width > 0 else 1.0
        scale = min(max(scale, MIN_SCALE), MAX_SCALE)
        pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        _record_span("thumbnail_render", t_render)
        t_encode = clock_ns()
        encoded = _prks_thumbnail_bytes_from_pixmap(pix)
        _record_span("thumbnail_encode", t_encode)
        return encoded
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _record_span(name: str, started: int) -> None:
    try:
        record_span(name, clock_ns() - started)
    except Exception:
        pass


def _count(name: str) -> None:
    try:
        record_counter(name)
    except Exception:
        pass


def thumbnail(req: Request) -> Response:
    from backend.api import works as works_api
    from backend.api.common import objects

    w_id = req.params["work_id"]
    thumb = works_api.thumbnail_fields(req, w_id)
    if not thumb:
        return error_response(404, "Work not found")
    # Ownership identity, not the last '/'-separated piece: a stored path
    # that is not exactly /api/pdfs/<filename> does not own a managed PDF
    # and must not resolve to some other one.
    pdf_filename = managed_pdf_filename(str(thumb.get("file_path") or ""))
    key = managed_pdf_key(pdf_filename) if pdf_filename else None
    if key is None:
        return error_response(404, "PDF not found")
    store = objects(req)
    try:
        info = store.stat(key)
    except (StorageObjectError, OSError):
        info = None
    if info is None:
        return error_response(404, "PDF not found")
    page = _page(req, thumb)
    pdf_mtime = float(info.modified_at or 0.0)
    cache_base = prks_thumb_cache_stem(w_id, page, thumb.get("primary_asset_id"), source=pdf_filename)
    thumbs_root = req.app.thumbs_dir
    path_webp = os.path.join(thumbs_root, cache_base + ".webp")

    image = _cached(path_webp, pdf_mtime)
    if image is not None:
        _count("thumbnail_cache_hits")
        return _image(image, "image/webp")
    _count("thumbnail_cache_misses")
    if os.path.exists(path_webp):
        try:
            os.remove(path_webp)
        except OSError:
            pass
    try:
        import pymupdf  # noqa: F401
    except Exception as e:
        # Common when the image/venv omits `pip install -r requirements.txt` (see Dockerfile).
        LOGGER.warning("thumbnail_fitz_import_failed error_type=%s request_id=%s",
                       safe_error_type(e), safe_log_id(req.request_id))
        return error_response(404, "Thumbnail unavailable")
    try:
        with readable_path(store, key) as pdf_path:
            image, subtype = _render(req, pdf_path, page)
    except Exception as exc:
        # A PDF that cannot be rendered never takes down the request path.
        LOGGER.warning(
            "thumbnail_render_failed work_id=%s page=%s request_id=%s error_type=%s",
            safe_log_id(w_id), page, safe_log_id(req.request_id), safe_error_type(exc),
        )
        return error_response(404, "Thumbnail unavailable")
    # v2 cache: WebP only (lossy default); fallbacks use matching ext.
    name = f"{cache_base}.{subtype}"
    try:
        publish_derived_cache_bytes(thumbs_root, name, image)
        # Only copies older than the PDF just rendered go: a late render of
        # replaced bytes never evicts a newer PDF's copy, whatever order the
        # two finish in.
        prks_prune_thumbnail_page(thumbs_root, w_id, page, name, older_than=pdf_mtime)
    except Exception:
        try:
            tmp_path = os.path.join(thumbs_root, name + ".tmp")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    if not works_api.thumbnail_fields(req, w_id):
        # The Work was deleted while this render ran. Its delete clears the
        # thumbnails once, after its commit; reading the Work again after
        # publishing means a copy published after that cleanup is removed
        # here instead.
        prks_delete_pdf_thumbnails_for_work_id(w_id, thumbs_root)
        return error_response(404, "Work not found")
    if not image:
        return error_response(404, "Thumbnail unavailable")
    return _image(image, f"image/{subtype}")


def _image(body: bytes, mime: str) -> Response:
    return bytes_response(200, body, mime, [("Cache-Control", "public, max-age=86400")])


ROUTES = (
    route("GET", "/api/works/{work_id}/thumbnail", thumbnail),
)
