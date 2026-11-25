"""The tests' Library object (PG-C3 #642): the handle plus the facade's old method names.

PG-C3 moved every route family off the ``PostgresLibrary`` facade to direct
domain calls and retired the facade package: :class:`backend.library.PostgresLibrary`
is now only the database-plus-storage handle the routes hold. Many tests
still call the old names (#1426 replaces them with direct domain calls).
Each method runs the domain query or command the route runs, for the actor
bound with :mod:`tests.acting_context`, and shapes the answer as the facade
did.

Every test builds its Library with :class:`TestLibrary`, which extends the
handle, so a test can bind it as the served ``db``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, TypeVar

from backend.library import PostgresLibrary
from tests.acting_context import current_context

T = TypeVar("T")


class TestLibrary(PostgresLibrary):
    """:class:`PostgresLibrary` plus the methods only tests still call."""

    __test__ = False  # not a unittest case, whatever its name says

    # -- transactions for the bound actor -------------------------------------

    def _query(self, fn: Callable[[Any], T]) -> T:
        from backend.domain.identity.library_scope import library_query

        return library_query(self.database, current_context(), fn)

    def _edit(self, fn: Callable[[Any], T], *, serializable: bool = False) -> T:
        from backend.db.database import Isolation
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_command

        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
        return library_command(self.database, current_context(), Capability.EDIT_LIBRARY, fn,
                               isolation=isolation)

    @staticmethod
    def _materialize_refused(result: Any) -> Any:
        """A ``materialize`` refusal code raised as the facade raised it."""
        from backend.domain.annotations import materialize

        if isinstance(result, str):
            raise (LookupError if result == materialize.NOT_FOUND else ValueError)(result)
        return result

    # -- annotations ------------------------------------------------------------

    # PG-C3 PR 14: ``backend/api/annotations.py`` calls the commands.

    @staticmethod
    def _annotation_commands() -> Any:
        from backend.domain.annotations import annotation_commands

        return annotation_commands

    def _annotation_objects(self) -> Any:
        from backend.storage.objects import LocalFilesystemStorage

        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return LocalFilesystemStorage.for_config(self.storage)

    def get_work_annotations(self, work_id: str) -> str:
        return self._annotation_commands().annotations_json(self.database, current_context(), work_id)

    def get_work_annotations_state(self, work_id: str) -> Any:
        return self._annotation_commands().state(self.database, current_context(), work_id)

    def get_work_annotations_snapshot(self, work_id: str) -> Any:
        return self._annotation_commands().snapshot(self.database, current_context(), work_id)

    def get_work_pdf_materialization(self, work_id: str) -> Any:
        return self._annotation_commands().materialization(self.database, current_context(), work_id)

    def adopt_byte_only_user_markup(self, work_id: str, viewer_items: Any) -> Any:
        return self._annotation_commands().adopt(self.database, current_context(), work_id, viewer_items)

    def save_work_annotations(self, work_id: str, annotations_json: str, *, base_set_revision: Any = None) -> int:
        return self._annotation_commands().save(self.database, current_context(), work_id, annotations_json,
                                                base_set_revision=base_set_revision)

    def sync_work_annotations(self, work_id: str, items: List[dict], *, base_set_revision: Any = None) -> int:
        """The legacy full-list replace (``operations.replace_all``), which ``save_work_annotations`` runs."""
        return self._annotation_commands().replace_all(self.database, current_context(), work_id, items,
                                                       base_set_revision=base_set_revision)

    def _materialize_work_pdf(self, work_id: str, write: Any, claimed_revision: Any) -> dict[str, Any]:
        return self._annotation_commands().materialize_pdf(self.database, current_context(),
                                                           self._annotation_objects(), work_id, write,
                                                           claimed_revision)

    def _replace_work_pdf(self, work_id: str, write: Any) -> dict[str, Any]:
        return self._annotation_commands().replace_pdf(self.database, current_context(),
                                                       self._annotation_objects(), work_id, write)

    def accept_work_pdf_materialization_claim(self, work_id: str, claimed_revision: Any) -> Any:
        from backend.domain.annotations import materialize

        return self._materialize_refused(
            self._query(lambda scope: materialize.check_claim(scope, work_id, claimed_revision)))

    def mark_work_pdf_materialized_if_claim_current(self, work_id: str, claimed_revision: Any) -> Any:
        from backend.domain.annotations import materialize
        from backend.storage.objects import LocalFilesystemStorage

        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        objects = LocalFilesystemStorage.for_config(self.storage)
        return self._materialize_refused(
            self._query(lambda scope: materialize.confirm(scope, objects, work_id, claimed_revision)))

    def mark_work_pdf_materialized(self, work_id: str, *, at_revision: Any = None) -> Any:
        from backend.domain.annotations import materialize

        marked = self._edit(lambda scope: materialize.lower(scope, work_id, at_revision))
        if marked == materialize.NOT_FOUND:
            return 0  # SQLite marked nothing and answered 0 for a missing Work
        return self._materialize_refused(marked)

    # -- works --------------------------------------------------------------------

    def generate_id(self, prefix: str) -> str:
        return self._generate_id(prefix)

    def etag_recent_works(self) -> str:
        from backend.domain.works import queries

        count, newest = self._query(queries.recent_etag_parts)
        return f'W/"prks-recent-{count}-{newest}"'

    def etag_recently_added_works(self) -> str:
        from backend.domain.works import queries

        count, newest = self._query(queries.recently_added_etag_parts)
        return f'W/"prks-recently-added-{count}-{newest}"'

    def get_recent_works(self, limit: int = 30) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._query(lambda scope: queries.works_by_opened(scope, limit))
        return self._finish([projection.work_summary(core, with_folder=False) for core in cores])

    def get_recently_added_works(self, limit: int = 50) -> List[dict]:
        from backend.domain.works import projection, queries

        cores = self._query(lambda scope: queries.works_by_created(scope, limit))
        return self._finish([projection.work_summary(core) for core in cores])

    def get_work_summaries_by_ids_ordered(self, work_ids: List[str]) -> List[dict]:
        """Work summaries in ``work_ids`` order (blank and unknown ids skipped)."""
        from backend.domain.search import search_commands

        ordered_ids = [str(wid).strip() for wid in (work_ids or []) if str(wid).strip()]
        if not ordered_ids:
            return []
        return self._search_rows(lambda scope: search_commands.summaries(scope, ordered_ids))

    def resolve_wiki_links(self, text: str) -> str:
        """Targets resolve against this Library only (``backend.domain.works.wiki_links``)."""
        from backend.domain.works import wiki_links

        return self._query(lambda scope: wiki_links.render(scope, text))

    # -- Work reads (PG-C3 PR 15: ``backend/api/works.py`` calls the commands) --

    @staticmethod
    def _work_commands() -> Any:
        from backend.domain.works import work_commands

        return work_commands

    def _finish(self, rows: List[dict]) -> List[dict]:
        from backend.domain.works.rules import finish_work_summary_rows

        finish_work_summary_rows(rows, self._pdfs_dir())
        return rows

    @staticmethod
    def etag_for_representation(label: str, rows: Any) -> str:
        from backend.web.etags import etag_for_representation

        return etag_for_representation(label, rows)

    def etag_works_catalog(self, rows: List[dict]) -> str:
        return self.etag_for_representation("works", rows)

    def get_all_works(self) -> List[dict]:
        return self._work_commands().catalog(self.database, current_context(), self._pdfs_dir())

    def get_works_browse_catalog(self) -> List[dict]:
        return self._work_commands().browse_catalog(self.database, current_context(), self._pdfs_dir())

    def get_recent_browse(self, limit: int = 30) -> List[dict]:
        return self._work_commands().recent_browse(self.database, current_context(), self._pdfs_dir(), limit)

    def get_recently_added_browse(self, limit: int = 50) -> List[dict]:
        return self._work_commands().recently_added_browse(self.database, current_context(), self._pdfs_dir(),
                                                           limit)

    def get_work(self, work_id: str) -> Optional[dict]:
        return self._work_commands().detail(self.database, current_context(), self._pdfs_dir(), work_id)

    def get_primary_thumbnail_fields(self, work_id: str) -> Optional[dict]:
        return self._work_commands().thumbnail_fields(self.database, current_context(), work_id)

    def get_work_metadata_state(self, work_id: str) -> Optional[dict]:
        return self._work_commands().metadata_state(self.database, current_context(), work_id)

    def get_work_source_state(self, work_id: str) -> Any:
        return self._work_commands().source_state(self.database, current_context(), work_id)

    def get_work_notes_state(self, work_id: str) -> Optional[dict]:
        return self._work_commands().notes_state(self.database, current_context(), work_id)

    def generate_bibtex(self, work_id: str) -> str:
        return self._work_commands().bibtex(self.database, current_context(), work_id)

    def _holds_managed_pdf(self, name: str) -> bool:
        return self._work_commands().holds_managed_pdf(self.database, current_context(), name)

    # -- Work writes (PG-C3 PR 15b: ``backend/api/works.py`` calls the commands) --

    def _pdfs_dir(self) -> str:
        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return self.storage.pdfs_dir

    def _objects(self) -> Any:
        from backend.storage.objects import LocalFilesystemStorage

        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return LocalFilesystemStorage.for_config(self.storage)

    @staticmethod
    def _generate_id(prefix: str) -> str:
        from backend.domain.entity_ids import generate

        return generate(prefix)

    def add_work(self, title: str, status: str = 'Not Started', abstract: str = "",
                 text_content: str = "", published_date: str = "", file_path: str = "",
                 author_text: str = "", year: str = "", publisher: str = "",
                 location: str = "", edition: str = "",
                 journal: str = "", volume: str = "", issue: str = "",
                 pages: str = "", isbn: str = "", doi: str = "",
                 doc_type: str = "article", source_kind: str = "", source_url: str = "",
                 source_mime: str = "", thumb_url: str = "", provider: str = "",
                 provider_id: str = "", urldate: str = "", thumb_page: Any = None,
                 private_notes: str = "") -> str:
        """SQLite's ``add_work`` over ``work_commands.create`` (an existing managed PDF, a stream or no file)."""
        fields = {name: value for name, value in locals().items() if name != "self"}
        commands = self._work_commands()
        return commands.create(self.database, current_context(), self._pdfs_dir(), self._objects(),
                               commands.new_work_fields(**fields))

    def _add_work_with_pdf(self, file_name: str, write: Callable[[Any], None], **fields: Any) -> str:
        """``add_work`` from an uploaded PDF, as ``POST /api/works`` with ``file_b64`` creates it."""
        if "file_path" in fields:
            raise TypeError("the uploaded PDF is the Work's file")
        commands = self._work_commands()
        return commands.create(self.database, current_context(), self._pdfs_dir(), self._objects(),
                               commands.new_work_fields(**fields), (file_name, write))

    def mark_work_opened(self, work_id: str) -> bool:
        return self._work_commands().mark_opened(self.database, current_context(), work_id)

    def update_work_metadata(self, work_id: str, fields: dict) -> tuple:
        """SQLite's ``update_work_metadata``: ``()``, since no file is released here."""
        self._work_commands().update_metadata(self.database, current_context(), work_id, fields)
        return ()

    def delete_work_record(self, work_id: str, *, claim_pdf: bool = True) -> Any:
        return self._work_commands().delete(self.database, current_context(), work_id, claim_pdf=claim_pdf)

    def bulk_update_works(self, data: dict) -> Dict[str, Any]:
        return self._work_commands().bulk_update(self.database, current_context(), data)

    # -- Search (PG-C3 PR 13: ``backend/api/search.py`` calls the commands) --

    def _search_rows(self, fn: Callable[[Any], List[dict]]) -> List[dict]:
        from backend.domain.search import search_commands

        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return search_commands.finish(self._query(fn), self.storage.pdfs_dir)

    def work_ids_matching_author(self, author: str) -> List[str]:
        from backend.domain.search import queries

        return self._query(lambda scope: queries.author_work_ids(scope, author))

    def work_ids_matching_publisher(self, pub: str) -> List[str]:
        from backend.domain.search import queries

        return self._query(lambda scope: queries.publisher_work_ids(scope, pub))

    def search_works(self, search_term: str, author_filter: str = "", publisher_filter: str = "") -> List[dict]:
        from backend.domain.search import search_commands

        return self._search_rows(
            lambda scope: search_commands.keyword_rows(scope, search_term, author_filter, publisher_filter))

    def search_works_any(self, term: str) -> List[dict]:
        from backend.domain.search import search_commands

        return self._search_rows(lambda scope: search_commands.any_rows(scope, term))

    def get_works_by_tag_name(self, tag_name: str) -> List[dict]:
        from backend.domain.search import search_commands

        return self._search_rows(lambda scope: search_commands.tagged_rows(scope, tag_name))


class _Bytes:
    """A request body already in memory."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.consumed = not payload

    def read(self, size: int = -1) -> bytes:
        self.consumed = True
        out, self._payload = self._payload, b""
        return out

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def settimeout(self, value: Optional[float]) -> None:
        pass

    def gettimeout(self) -> Optional[float]:
        return None


def call_route(library: Any, method: str, target: str, body: Any = None) -> tuple[int, Any]:
    """``(status, JSON body)`` of ``method target`` through the served route table.

    The route runs for the bound actor (:func:`tests.acting_context.current_context`)
    on ``library``; a JSON body is read by the dispatcher's own body rules.
    """
    from backend.web.app import App
    from backend.web.dispatch import BodyRefused, read_json_object
    from backend.web.http import Headers, Request
    from backend.web.routing import Body, Match

    path, _, query = target.partition("?")
    app = App(library=library)
    found = app.routes.resolve(method, path)
    assert isinstance(found, Match), (method, target)
    headers: list[tuple[str, str]] = []
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))]
    request = Request(method, path, query, Headers(headers), app, params=found.params,
                      context=current_context(), body=_Bytes(payload))
    if found.route.body_kind is Body.JSON:
        if body is None:
            request.headers = Headers([("Content-Type", "application/json")])
        try:
            read_json_object(request)
        except BodyRefused as refused:
            return refused.response.status, json.loads(refused.response.body)
    response = found.route.handler(request)
    return response.status, json.loads(response.body) if response.body else None
