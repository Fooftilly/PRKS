"""Managed-PDF ingestion through the ``StorageBackend`` (§11.2 "Ingest"; L-A-1 #635).

:func:`store_pdf` is the filesystem half of an upload and touches no
database. It streams the bytes to a freshly minted key with exclusive
create; the backend hashes the raw stream while writing (``ingest_sha256``)
and makes it durable before publishing. Then, unless the PDF is already
linearized or linearization is off or fails, it linearizes into a **second**
fresh key whose hash becomes ``content_sha256``. The source key is never
modified.

The database half is the caller's one Library command: ``create_work`` with
:meth:`IngestedPdf.managed_file` adopts both keys (``lifecycle.adopt``,
under the per-object locks) and inserts the Asset with ``storage_locator``
on the working key and ``source_locator`` on the pristine source when the two
differ. Nothing here deletes: bytes no transaction adopts (a refused or
rolled-back create, a crash before COMMIT) are orphans the sweep claims
after its grace period, so a key that a concurrent adopter might still name
is never unlinked on a failure path.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import BinaryIO

from backend.domain.objects.lifecycle import StoredObject
from backend.log_safety import safe_error_type, safe_log_label
from backend.pdf_linearize import is_pdf_linearized, linearize_pdf_to
from backend.storage.objects import (
    IO_CHUNK_SIZE,
    NAMESPACE_ASSET_OBJECTS,
    LocalPathCapable,
    StorageBackend,
    StorageKey,
    StorageObjectError,
    Writer,
    mint_name,
)

LOGGER = logging.getLogger("prks.objects")


@dataclass(frozen=True)
class IngestedPdf:
    """The keys one ingest wrote: the raw ``source`` and the ``working`` file readers get.

    ``working`` is ``source`` itself when no linearized copy was made;
    ``linearize`` says why (``pdf_linearize`` reasons, or ``already``).
    """

    source: StoredObject
    working: StoredObject
    linearize: str

    def managed_file(self, origin: str = "upload"):
        """The ``commands.ManagedFile`` that adopts these keys in one Work create."""
        from backend.domain.works.commands import ManagedFile

        kept = self.source.key != self.working.key
        return ManagedFile(
            storage_locator=self.working.key.name, sha256=self.working.sha256,
            byte_size=self.working.size, origin=origin, version=self.working.version,
            source=self.source if kept else None, ingest_sha256=self.source.sha256)


def store_pdf(storage: StorageBackend, file_name: str, write: Writer, *,
              context: str = "work-create-upload") -> IngestedPdf:
    """Store an incoming PDF under new keys; returns what was written.

    ``file_name`` is only a hint for the minted names. A failed source write
    publishes nothing and raises; a linearization that fails in any way
    leaves the source as the working file, as on SQLite (an optimization
    never fails an upload). ``context`` labels the linearize logs.
    """
    source = _put(storage, mint_name(NAMESPACE_ASSET_OBJECTS, file_name), write)
    working, reason = _linearized(storage, source, file_name, context)
    LOGGER.info("pdf_linearize_result context=%s changed=%s reason=%s", safe_log_label(context),
                "true" if working is not None else "false", safe_log_label(reason))
    return IngestedPdf(source=source, working=working or source, linearize=reason)


def _put(storage: StorageBackend, name: str, write: Writer) -> StoredObject:
    info = storage.put_new(StorageKey(NAMESPACE_ASSET_OBJECTS, name), write)
    assert info.sha256 is not None
    return StoredObject(info.key, info.size, info.sha256, info.version)


def _linearized(storage: StorageBackend, source: StoredObject, file_name: str,
                context: str) -> tuple[StoredObject | None, str]:
    """A linearized copy of ``source`` under a new key, or ``(None, reason)``."""
    if not isinstance(storage, LocalPathCapable):
        return None, "no-local-path"  # qpdf needs a file; a remote backend copies first
    try:
        # qpdf's output is scratch outside the data root: only the backend
        # writes beneath it, and it syncs the copy it publishes.
        with tempfile.TemporaryDirectory(prefix="prks-linearize-") as scratch:
            out = os.path.join(scratch, "linearized.pdf")
            with storage.local_path(source.key) as path:
                if is_pdf_linearized(path):
                    return None, "already"
                written, reason = linearize_pdf_to(path, out, context=context)
            if not written:
                return None, reason

            def copy(sink: BinaryIO) -> None:
                with open(out, "rb") as handle:
                    shutil.copyfileobj(handle, sink, IO_CHUNK_SIZE)

            return _put(storage, mint_name(NAMESPACE_ASSET_OBJECTS, file_name), copy), "ok"
    except (OSError, StorageObjectError) as exc:
        LOGGER.warning("pdf_linearize_error context=%s error_type=%s", safe_log_label(context),
                       safe_error_type(exc))
        return None, "error"
