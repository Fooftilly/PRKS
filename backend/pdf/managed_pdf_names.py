"""How managed PDF object names are minted and bounded.

A name is a storage key in the ``asset-objects`` namespace, never an
identity: a Work and its clients address the bytes by Asset id
(``backend/domain/objects/content_url.py``, #1427). No database access.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime


def _processing_safe_dest_name(filename: str) -> str:
    safe = "".join(c for c in str(filename or "") if c.isalnum() or c in ".-_")
    safe = safe.strip("._")
    if not safe:
        safe = "file"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return safe


# Most filesystems cap a single name component at 255 bytes, and a name that
# overruns it fails at open() rather than at validation.
_MANAGED_PDF_NAME_MAX_BYTES = 255


def mint_managed_pdf_filename(original_name: str) -> str:
    """Mint a fresh managed PDF basename: ``<unix-seconds>_<8 hex>_<name>.pdf``.

    The random component is load-bearing, not decoration. A timestamp alone
    collides for two uploads of the same filename within one second, and the
    second write then replaced the first Work's bytes while both rows still
    pointed at that one path.

    ``original_name`` is advisory. It is sanitized for readability and never
    trusted for placement: the name is a storage key, which the storage
    backend validates and places (``backend/storage/objects.py``).
    """
    safe = _processing_safe_dest_name(os.path.basename(str(original_name or "")))
    prefix = f"{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}_"
    return bound_managed_pdf_basename(prefix, safe)


def bound_managed_pdf_basename(prefix: str, stem: str) -> str:
    """Join ``prefix`` and ``stem`` into a ``.pdf`` basename a filesystem holds.

    Every managed name is bounded here rather than at each caller, because the
    copy-on-write path re-prefixes a name that was already minted: a stem sized
    to the limit plus a second timestamp, Work id and uuid overruns it, and the
    replace then fails with ENAMETOOLONG. Bounding the joined result keeps that
    composition safe however many times a name is re-minted, including for names
    already stored before this existed.
    """
    if stem.lower().endswith(".pdf"):
        stem = stem[: -len(".pdf")]
    budget = _MANAGED_PDF_NAME_MAX_BYTES - len(prefix.encode("utf-8")) - len(".pdf")
    # Truncating bytes can split a multi-byte character; drop the partial tail.
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") if budget > 0 else ""
    return f"{prefix}{stem.strip('._') or 'file'}.pdf"
