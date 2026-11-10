"""Managed PDF file names and safe paths under the PDF directory (PG-C2 #641).

Which file names PRKS owns, how new ones are minted and bounded, and
which Work rows reference them. No database access. Moved from
``backend/db_manager.py``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

from backend.storage import paths


def _processing_safe_dest_name(filename: str) -> str:
    safe = "".join(c for c in str(filename or "") if c.isalnum() or c in ".-_")
    safe = safe.strip("._")
    if not safe:
        safe = "file"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return safe


def safe_pdf_path_under_dir(
    pdfs_dir: str,
    url_last_segment: str,
    *,
    windows_semantics: Optional[bool] = None,
) -> Optional[str]:
    """Resolve exactly one decoded PDF filename beneath pdfs_dir.

    Path-shaped input is rejected rather than normalized to a different
    filename. ``resolved_child_path()`` remains the final filesystem boundary
    and also refuses a drive-qualified name, which on Windows would otherwise
    re-anchor onto a different managed PDF.

    ``windows_semantics`` defaults to this platform and exists so the Windows
    branch is exercised by the suite on any host; callers must not set it.
    """
    if url_last_segment is None:
        return None
    raw_name = str(url_last_segment)
    if not raw_name or raw_name != raw_name.strip():
        return None
    try:
        name = unquote(raw_name)
    except (TypeError, ValueError):
        return None
    if (
        not name
        or name != name.strip()
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        return None
    return paths.resolved_child_path(
        pdfs_dir, name, windows_semantics=windows_semantics
    )


def _managed_pdf_basename_from_path(
    file_path: str, *, allow_url_delimiters: bool
) -> Optional[str]:
    """Exact ``/api/pdfs/<basename>`` extract, optionally allowing ``?;#``.

    Route-addressable ownership (`managed_pdf_filename`) refuses URL
    delimiters because ``urlparse`` strips them before the HTTP PDF route
    sees the path. Physical cleanup ownership (`owned_managed_pdf_basename`)
    still accepts them so a library that already stored
    ``/api/pdfs/legacy.pdf?x`` (and the matching on-disk name) can claim and
    unlink those bytes after upgrade.
    """
    prefix = "/api/pdfs/"
    if not file_path.startswith(prefix):
        return None
    remainder = file_path[len(prefix):]
    if not remainder or remainder != remainder.strip():
        return None
    if "/" in remainder or "\\" in remainder:
        return None
    if not allow_url_delimiters and any(ch in remainder for ch in "?;#"):
        return None
    if remainder in (".", ".."):
        return None
    if "\x00" in remainder:
        return None
    if unquote(remainder) != remainder:
        return None
    if prefix + remainder != file_path:
        return None
    return remainder


def managed_pdf_filename(file_path: str) -> Optional[str]:
    """Return the filename for a route-addressable ``/api/pdfs/<filename>`` path.

    The basename must round-trip through the HTTP PDF route. ``urlparse`` strips
    ``?`` (query), ``;`` (params), and ``#`` (fragment) before
    ``_safe_pdf_path_for_route`` sees the path, so adopting
    ``/api/pdfs/foo.pdf?x`` would lock/persist ``foo.pdf?x`` while GET resolved
    ``foo.pdf``. Mint sanitizes these characters away; new adoption must
    refuse them. Legacy rows that already used delimiter-bearing names are
    cleaned up via ``owned_managed_pdf_basename``, not this helper.
    """
    return _managed_pdf_basename_from_path(file_path, allow_url_delimiters=False)


def owned_managed_pdf_basename(file_path: str) -> Optional[str]:
    """Physical cleanup ownership basename, including legacy ``?;#`` names.

    Used when deleting a Work or retrying ``pending_pdf_cleanup``: the claim
    must name the on-disk basename even when that spelling is not
    route-addressable. Never used for adoption.
    """
    return _managed_pdf_basename_from_path(file_path, allow_url_delimiters=True)


def referenced_managed_pdf_filename(file_path: str) -> Optional[str]:
    """Fail-closed over-approximation of a stored path's serving identity.

    Deliberately looser than ``managed_pdf_filename()``. That helper answers
    "may this path be *adopted* as route-addressable ownership?" and must be
    exact. This one answers "could this row *might* still be pointing at
    managed PDF X?" and is only ever used to refuse deleting bytes, so it
    must over-approximate: a malformed or legacy spelling that fails to
    resolve here would let cleanup delete a PDF another row still
    references.

    Outer whitespace is tolerated. ``urlparse`` strips query/params/fragment
    before the last segment is taken, matching ``GET /api/pdfs/...`` so a
    surviving ``/api/pdfs/foo.pdf?q`` row protects ``foo.pdf``. Physical
    delimiter-bearing ownership (``legacy.pdf?x`` on disk) is checked
    separately via ``owned_managed_pdf_basename`` /
    ``row_references_managed_pdf``.

    Never use this output as positive deletion authority: traversal, nested,
    and encoded-slash aliases map here but the HTTP route cannot serve them,
    so they must not mint ``pending_pdf_cleanup`` claims.
    """
    fp = str(file_path or "").strip()
    if not fp.startswith("/api/pdfs/"):
        return None
    # Match HTTP serving: urlparse drops query/params/fragment from .path.
    path = urlparse(fp).path
    if not path.startswith("/api/pdfs/"):
        return None
    segment = path.split("/")[-1]
    name = os.path.basename(unquote(segment))
    if not name or name in (".", ".."):
        return None
    return name


def row_references_managed_pdf(file_path: str, filename: str) -> bool:
    """True when a stored path fail-closed-protects managed basename ``filename``.

    Checks the loose serving over-approximation
    (``referenced_managed_pdf_filename``) and physical ownership
    (``owned_managed_pdf_basename``) so cleanup refuses to unlink while any
    such row exists. This is a *blocker*, not ownership proof — see
    ``row_strongly_references_managed_pdf`` / ``managed_basenames_protected_by``
    for identities that may retire or mint cleanup claims.
    """
    name = str(filename or "")
    if not name:
        return False
    if referenced_managed_pdf_filename(file_path) == name:
        return True
    if owned_managed_pdf_basename(file_path) == name:
        return True
    return False


def row_strongly_references_managed_pdf(file_path: str, filename: str) -> bool:
    """True when a stored path strongly owns or serves ``filename``.

    Strong identities are ones the HTTP PDF route / physical cleanup can
    actually resolve: exact ``owned_managed_pdf_basename`` (including legacy
    ``?;#`` on-disk names) and the urlparse-stripped path when that path is
    itself an exact route-addressable ``/api/pdfs/<name>`` spelling (so
    ``/api/pdfs/foo.pdf?q`` strongly serves ``foo.pdf``). Traversal, nested,
    and encoded-slash aliases are intentionally excluded — they only
    fail-closed-block unlink via ``row_references_managed_pdf``.
    """
    name = str(filename or "")
    if not name:
        return False
    if owned_managed_pdf_basename(file_path) == name:
        return True
    fp = str(file_path or "").strip()
    if not fp.startswith("/api/pdfs/"):
        return False
    serving = managed_pdf_filename(urlparse(fp).path)
    return serving == name


def managed_basenames_protected_by(file_path: str) -> Tuple[str, ...]:
    """Distinct *strong* managed basenames a stored path may claim on delete.

    Only strong ownership / serving identities mint ``pending_pdf_cleanup``
    claims. A delimiter legacy path such as ``/api/pdfs/foo.pdf?x`` strongly
    protects both physical ``foo.pdf?x`` and serving stem ``foo.pdf``, so
    deleting that survivor reclaims a stem claim it previously superseded.

    Weak fail-closed aliases (``/api/pdfs/../x.pdf``, nested segments,
    ``%2F``) are deliberately excluded: ``referenced_managed_pdf_filename``
    may map them to a containable stem, but the route cannot resolve those
    spellings, so using them as positive deletion authority would unlink
    bytes the row never owned. Those aliases keep an *existing* stem claim
    pending (settle refuses to retire it; unlink is blocked) until they
    disappear — they never create a new claim.
    """
    names: List[str] = []
    seen = set()

    def _add(name: Optional[str]) -> None:
        if not name or name in seen:
            return
        # Containable managed basenames only.
        if owned_managed_pdf_basename(f"/api/pdfs/{name}") != name:
            return
        seen.add(name)
        names.append(name)

    _add(owned_managed_pdf_basename(file_path))
    fp = str(file_path or "").strip()
    if fp.startswith("/api/pdfs/"):
        # Strong serving stem: urlparse-stripped path must be exact ownership.
        _add(managed_pdf_filename(urlparse(fp).path))
    return tuple(names)


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
    trusted for placement: callers still resolve the result through
    ``safe_pdf_path_under_dir()`` before touching the filesystem.
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
