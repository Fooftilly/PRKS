"""PDF text for search: ``text.extract`` and ``derived.asset_text`` (L-S-1 #637; §8.3, §12.1).

The text of a managed PDF Asset is extracted by a job, never in the
request that stores the bytes, into ``derived.asset_text``, keyed by the
Asset and the ``content_sha256`` of the bytes it was read from. A row is
never served for other bytes, and the canonical tables never hold the
text (``backend/AGENTS.md``, PDF text index).

**Who enqueues.** Every command that gives a managed-file Asset new bytes
calls :func:`content_changed` in its own transaction, after it switched
``content_sha256``: a Work create with a managed PDF
(``commands._insert_asset``), a replacement (``objects.replace.commit``)
and a materialization (``objects.lifecycle.replace_object``). It deletes
the Asset's rows for any other hash and enqueues one ``text.extract`` job
for the Asset. Jobs are never coalesced, for the reason
``research.reindex_work`` gives: a queued twin could run before the
enqueuing transaction commits and read the old bytes. A Work delete calls
:func:`drop_assets` after its Assets are deleted.

**The job** (:func:`extract_asset`) reads the Asset in one snapshot and
returns early when the Asset is gone, is not a managed file, or already has
this extractor's row for its current hash (a retried or duplicate job). It
then extracts the text outside any transaction, which can take long, and
writes in a second, READ COMMITTED transaction (:func:`write_text`):

1. the Asset row ``FOR SHARE``: a delete or content switch in flight is
   waited for, and one that starts later waits for this transaction;
2. when the Asset is gone or its ``content_sha256`` is no longer the one
   extracted, nothing is written;
3. otherwise the row for that hash is upserted.

So an extraction that locks first commits its row before the delete or
switch removes it (both delete after taking their own lock on the Asset),
and one that locks second writes nothing: no stale row survives.

**Outcomes.** A PDF PyMuPDF cannot read is an outcome, not an empty
document: its row has status ``failed`` and no text, and it is not retried
(the bytes will not change under the same hash). A document with no text
is ``empty``. An extractor that is not installed fails the job, which the
queue retries, so a missing dependency never records an outcome.

**The document.** ``doc`` is ``strip(to_tsvector(...))`` of the folded
text: prefix matching needs the lexemes only, and dropping positions keeps
a long PDF under the ``tsvector`` size limit. A text whose lexemes still
exceed the limit is halved until they fit and is marked ``truncated``.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, LiteralString, Optional

from backend.db.errors import DatabaseError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, LocalPathCapable, StorageBackend, StorageKey

LOGGER = logging.getLogger("prks.search.text")

KIND = "text.extract"
# Bump when the extracted text of the same bytes would change; every row of
# an older version is then stale and re-extracted by the rebuild (§8.6).
EXTRACTOR_VERSION = 1
MAX_CHARS = 2_000_000
# A tsvector holds at most 1 MiB; a text whose lexemes exceed it is halved
# at most this many times before the job gives up.
MAX_HALVINGS = 8
_TOO_LONG = "54000"  # program_limit_exceeded

Extractor = Callable[[str], tuple[str, bool]]


# -- the commands' side ----------------------------------------------------------------

_DELETE_OTHER_HASHES: LiteralString = """
DELETE FROM derived.asset_text
WHERE library_id = %(library_id)s AND asset_id = %(asset_id)s
  AND content_sha256 IS DISTINCT FROM %(sha256)s
"""


def content_changed(scope: LibraryScope, asset_id: str, sha256: Optional[str]) -> None:
    """The Asset now holds the bytes ``sha256``: drop rows of other bytes, queue the extraction.

    Called by the command that switched ``content_sha256``, after it took
    its lock on the Asset row.
    """
    scope.execute(_DELETE_OTHER_HASHES, {"asset_id": asset_id, "sha256": sha256})
    queue.enqueue_library_job(scope, KIND, {"asset_id": asset_id})


_DELETE_ASSETS: LiteralString = """
DELETE FROM derived.asset_text
WHERE library_id = %(library_id)s AND asset_id = ANY(%(asset_ids)s::text[])
"""


def drop_assets(scope: LibraryScope, asset_ids: list[str]) -> None:
    """Delete the rows of Assets the caller has just deleted (after the delete, see the module docs)."""
    if asset_ids:
        scope.execute(_DELETE_ASSETS, {"asset_ids": asset_ids})


# -- the job ---------------------------------------------------------------------------

_ASSET: LiteralString = """
SELECT a.storage_locator, a.content_sha256,
       EXISTS (SELECT 1 FROM derived.asset_text t
               WHERE t.library_id = %(library_id)s AND t.asset_id = a.id
                 AND t.content_sha256 = a.content_sha256
                 AND t.extractor_version = %(version)s) AS done
FROM prks.assets a
WHERE a.library_id = %(library_id)s AND a.id = %(asset_id)s AND a.kind = 'managed_file'
"""
_LOCK_ASSET: LiteralString = """
SELECT content_sha256 FROM prks.assets
WHERE library_id = %(library_id)s AND id = %(asset_id)s
FOR SHARE
"""
_UPSERT: LiteralString = """
INSERT INTO derived.asset_text AS t
  (library_id, asset_id, content_sha256, extractor_version, status, truncated, text, doc)
VALUES (%(library_id)s, %(asset_id)s, %(sha256)s, %(version)s, %(status)s, %(truncated)s, %(text)s,
        strip(to_tsvector('prks.prks_search'::regconfig, prks.search_fold(%(text)s))))
ON CONFLICT (asset_id, content_sha256) DO UPDATE
SET extractor_version = EXCLUDED.extractor_version, status = EXCLUDED.status,
    truncated = EXCLUDED.truncated, text = EXCLUDED.text, doc = EXCLUDED.doc, updated_at = now()
WHERE t.library_id = %(library_id)s
"""


def write_text(scope: LibraryScope, asset_id: str, sha256: str, status: str, text: str,
               truncated: bool) -> bool:
    """Store one extraction outcome for ``sha256``; False (nothing written) when the bytes moved on."""
    row = scope.execute(_LOCK_ASSET, {"asset_id": asset_id}).fetchone()
    if row is None or row["content_sha256"] != sha256:
        return False
    scope.execute(_UPSERT, {"asset_id": asset_id, "sha256": sha256, "version": EXTRACTOR_VERSION,
                            "status": status, "truncated": truncated, "text": text})
    return True


def extract_pdf(path: str) -> tuple[str, bool]:
    """``(text, truncated)`` of the PDF at ``path`` (``backend.pdf_text.extract_pdf``)."""
    from backend.pdf_text import extract_pdf as extract

    found = extract(path, MAX_CHARS)
    return ("" if found.empty else found.text), found.truncated


def extract_asset(run: JobRun, storage: StorageBackend, asset_id: str,
                  extractor: Extractor = extract_pdf) -> Optional[str]:
    """The ``text.extract`` job for one Asset; the status written, or None when nothing was.

    Raises the extractor's ``PDFTextExtractorUnavailable`` (the job is
    retried) and lets a storage error fail the job the same way.
    """
    from backend.pdf_text import PDFTextExtractionError

    asset = run.library_command(Capability.EDIT_LIBRARY, lambda scope: _read_asset(scope, asset_id))
    if asset is None or asset["done"] or not asset["storage_locator"] or not asset["content_sha256"]:
        return None
    sha256 = str(asset["content_sha256"])
    key = StorageKey(NAMESPACE_ASSET_OBJECTS, str(asset["storage_locator"]))
    try:
        with _readable_path(storage, key) as path:
            text, truncated = extractor(path)
        status = "indexed" if text.strip() else "empty"
    except PDFTextExtractionError as exc:
        LOGGER.info("text_extract_failed reason=%s", exc.reason)
        text, truncated, status = "", False, "failed"
    if status != "indexed":
        text, truncated = "", False
    for _ in range(MAX_HALVINGS + 1):
        run.check()
        try:
            written = run.library_command(
                Capability.EDIT_LIBRARY,
                lambda scope, text=text, truncated=truncated: write_text(
                    scope, asset_id, sha256, status, text, truncated))
        except DatabaseError as exc:
            if exc.sqlstate != _TOO_LONG or not text:
                raise
            text, truncated = text[: len(text) // 2], True
            continue
        return status if written else None
    raise RuntimeError("the extracted text does not fit a tsvector")


@contextmanager
def _readable_path(storage: StorageBackend, key: StorageKey) -> Iterator[str]:
    """A local file holding the object: the stored file itself when the backend has one, else a copy."""
    if isinstance(storage, LocalPathCapable):
        with storage.local_path(key) as path:
            yield path
        return
    with tempfile.NamedTemporaryFile(suffix=".pdf") as copy, storage.open_read(key) as source:
        shutil.copyfileobj(source, copy)
        copy.flush()
        yield copy.name


def _read_asset(scope: LibraryScope, asset_id: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_ASSET, {"asset_id": asset_id, "version": EXTRACTOR_VERSION}).fetchone()
    return None if row is None else dict(row)


class TextExtraction:
    """The ``text.extract`` handler of one worker, bound to its data root's storage."""

    def __init__(self, storage: StorageBackend, extractor: Extractor = extract_pdf) -> None:
        self.storage = storage
        self.extractor = extractor

    def handle(self, run: JobRun) -> None:
        asset_id = run.payload.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("text.extract needs an asset_id")
        extract_asset(run, self.storage, asset_id, self.extractor)


def attach(registry: HandlerRegistry, storage: StorageBackend) -> TextExtraction:
    """Register ``text.extract`` on a worker's own registry, reading from ``storage``."""
    extraction = TextExtraction(storage)
    registry.register(KIND, extraction.handle)
    return extraction


# -- reads -----------------------------------------------------------------------------

# The Works whose primary managed PDF's current bytes, read by this
# extractor version, hold every token as a prefix (``/api/search``'s any
# mode, through ``search_commands``).
# ``doc`` keeps no positions, so a rank could not tell matches apart: they
# come most recently updated first.
_PDF_TEXT_SQL: LiteralString = """
WITH q AS (
  SELECT to_tsquery('prks.prks_search'::regconfig,
                    string_agg(prks.tsquery_prefix(prks.search_fold(t)), ' & ' ORDER BY i)) AS query
  FROM unnest(%(tokens)s::text[]) WITH ORDINALITY AS u(t, i)
)
SELECT w.id AS work_id
FROM q, prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
JOIN derived.asset_text t
  ON t.library_id = a.library_id AND t.asset_id = a.id AND t.content_sha256 = a.content_sha256
WHERE w.library_id = %(library_id)s AND a.kind = 'managed_file' AND t.status = 'indexed'
  AND t.extractor_version = %(version)s AND t.doc @@ q.query
ORDER BY w.updated_at DESC, w.id COLLATE "C"
LIMIT %(limit)s
"""


def pdf_text_work_ids(scope: LibraryScope, text: str, limit: int = 2000) -> list[str]:
    """Works whose PDF text matches the free-text query (every token as a prefix)."""
    from backend.domain.search.queries import split_query

    tokens = split_query(text)
    if not tokens:
        return []
    rows = scope.execute(_PDF_TEXT_SQL, {"tokens": tokens, "version": EXTRACTOR_VERSION,
                                         "limit": max(1, int(limit))})
    return [str(row["work_id"]) for row in rows]
