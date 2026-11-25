"""Work fixtures and reads a test arranges or checks a Library with (#1426).

Each takes the database, the acting context and the Library's storage, as
a route has them. The Work commands want the PDF directory and the object
store the storage holds; these find both, and build what a command takes
from plain arguments.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from backend.db.database import Database
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.storage.config import StorageConfig
from backend.storage.objects import LocalFilesystemStorage

NO_DATA_ROOT = "this PostgreSQL library has no data root"


def pdfs_dir(storage: Optional[StorageConfig]) -> str:
    """The storage's PDF directory; a Library without a data root has none."""
    if storage is None:
        raise RuntimeError(NO_DATA_ROOT)
    return storage.pdfs_dir


def objects(storage: Optional[StorageConfig]) -> LocalFilesystemStorage:
    """The storage's object store; a Library without a data root has none."""
    if storage is None:
        raise RuntimeError(NO_DATA_ROOT)
    return LocalFilesystemStorage.for_config(storage)


def add_work(database: Database, ctx: RequestContext, storage: Optional[StorageConfig], title: str,
             **fields: Any) -> str:
    """A new Work's id: an existing managed PDF (``file_path``), a stream, or no file."""
    from backend.domain.works import work_commands

    return work_commands.create(database, ctx, pdfs_dir(storage), objects(storage),
                                work_commands.new_work_fields(title=title, **fields))


def add_work_with_pdf(database: Database, ctx: RequestContext, storage: Optional[StorageConfig],
                      file_name: str, write: Callable[[Any], None], **fields: Any) -> str:
    """A new Work's id, from an uploaded PDF as ``POST /api/works`` with ``file_b64`` creates it."""
    from backend.domain.works import work_commands

    if "file_path" in fields:
        raise TypeError("the uploaded PDF is the Work's file")
    return work_commands.create(database, ctx, pdfs_dir(storage), objects(storage),
                                work_commands.new_work_fields(**fields), (file_name, write))


def summaries(database: Database, ctx: RequestContext, storage: Optional[StorageConfig],
              read: Callable[[Any], list[dict]]) -> list[dict]:
    """Search rows ``read`` finds, finished as Work summaries."""
    from backend.domain.search import search_commands

    return search_commands.finish(library_query(database, ctx, read), pdfs_dir(storage))


def summaries_by_ids(database: Database, ctx: RequestContext, storage: Optional[StorageConfig],
                     work_ids: list[str]) -> list[dict]:
    """Work summaries in ``work_ids`` order (blank and unknown ids skipped)."""
    from backend.domain.search import search_commands

    ordered = [str(wid).strip() for wid in (work_ids or []) if str(wid).strip()]
    if not ordered:
        return []
    return summaries(database, ctx, storage, lambda scope: search_commands.summaries(scope, ordered))


def recent_works(database: Database, ctx: RequestContext, storage: Optional[StorageConfig],
                 limit: int = 30) -> list[dict]:
    """The most recently opened Works as summaries, without their Folder."""
    from backend.domain.works import projection, queries
    from backend.domain.works.rules import finish_work_summary_rows

    cores = library_query(database, ctx, lambda scope: queries.works_by_opened(scope, limit))
    rows = [projection.work_summary(core, with_folder=False) for core in cores]
    finish_work_summary_rows(rows, pdfs_dir(storage))
    return rows


def recently_added_works(database: Database, ctx: RequestContext, storage: Optional[StorageConfig],
                         limit: int = 50) -> list[dict]:
    """The most recently created Works as summaries."""
    from backend.domain.works import projection, queries
    from backend.domain.works.rules import finish_work_summary_rows

    cores = library_query(database, ctx, lambda scope: queries.works_by_created(scope, limit))
    rows = [projection.work_summary(core) for core in cores]
    finish_work_summary_rows(rows, pdfs_dir(storage))
    return rows


def materialize_refused(result: Any) -> Any:
    """A ``materialize`` refusal code raised: ``LookupError`` for a missing Work, else ``ValueError``."""
    from backend.domain.annotations import materialize

    if isinstance(result, str):
        raise (LookupError if result == materialize.NOT_FOUND else ValueError)(result)
    return result


def accept_materialization_claim(database: Database, ctx: RequestContext, work_id: str,
                                 claimed_revision: Any) -> Any:
    """The claim check a PDF materialization starts with."""
    from backend.domain.annotations import materialize

    return materialize_refused(library_query(
        database, ctx, lambda scope: materialize.check_claim(scope, work_id, claimed_revision)))


def mark_materialized_if_claim_current(database: Database, ctx: RequestContext,
                                       storage: Optional[StorageConfig], work_id: str,
                                       claimed_revision: Any) -> Any:
    """The confirm a PDF materialization ends with, while its claim is current."""
    from backend.domain.annotations import materialize

    store = objects(storage)
    return materialize_refused(library_query(
        database, ctx, lambda scope: materialize.confirm(scope, store, work_id, claimed_revision)))


def mark_materialized(database: Database, ctx: RequestContext, work_id: str, *,
                      at_revision: Any = None) -> Any:
    """Lower a Work's materialization mark; 0 for a missing Work."""
    from backend.domain.annotations import materialize

    marked = library_command(database, ctx, Capability.EDIT_LIBRARY,
                             lambda scope: materialize.lower(scope, work_id, at_revision))
    if marked == materialize.NOT_FOUND:
        return 0
    return materialize_refused(marked)


def etag_recent(database: Database, ctx: RequestContext) -> str:
    """A validator over the opened Works: how many, and the newest open."""
    from backend.domain.works import queries

    count, newest = library_query(database, ctx, queries.recent_etag_parts)
    return f'W/"prks-recent-{count}-{newest}"'


def etag_recently_added(database: Database, ctx: RequestContext) -> str:
    """A validator over the created Works: how many, and the newest creation."""
    from backend.domain.works import queries

    count, newest = library_query(database, ctx, queries.recently_added_etag_parts)
    return f'W/"prks-recently-added-{count}-{newest}"'
