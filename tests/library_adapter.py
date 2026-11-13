"""The tests' Library object during PG-C3 (#642): the facade plus the methods it shed.

PG-C3 moves each route family off :class:`backend.pg_facade.PostgresLibrary`
to direct domain calls, and removes the facade methods no route calls any
more. The tests still need those names: the parity tests replay SQLite's
recorded answers by method name and arguments (``tests/parity_reference.py``),
and nothing can record them again, so a parity test keeps its calls and this
class keeps answering them. Each method here runs the same domain query or
command the facade ran, for the bound actor, and shapes the answer as the
facade did, so the recorded SQLite answers still hold for the domain code.

Every test builds its Library with :class:`TestLibrary` rather than the
facade. As a family moves, the methods its tests call move here, built over
the domain and the route module's own response shaping; when the facade is
gone (the last PG-C3 PR), this class stops extending it.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

from backend.pg_facade import PostgresLibrary
from backend.pg_facade.context import current_context

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

    def sync_work_annotations(self, work_id: str, items: List[dict], *, base_set_revision: Any = None) -> int:
        """The legacy full-list replace (``operations.replace_all``), which ``save_work_annotations`` runs."""
        return self._sync_work_annotations(work_id, items, base_set_revision=base_set_revision)

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

    # -- organization -----------------------------------------------------------

    def resolve_tag_id_by_label(self, label: str) -> Optional[str]:
        from backend.domain.organization import tags

        return self._query(lambda scope: tags.resolve_label(scope, label))

    def etag_tags_all(self) -> str:
        from backend.etags import etag_for_representation

        return etag_for_representation("tags", self.get_all_tags())

    def get_work_tags(self, work_id: str) -> List[dict]:
        from backend.domain.organization import links

        return self._query(lambda scope: links.tags_of(scope, links.WORK, work_id))

    def get_folder_tags(self, folder_id: str) -> List[dict]:
        from backend.domain.organization import links

        return self._query(lambda scope: links.tags_of(scope, links.FOLDER, folder_id))

    def get_app_settings_map(self) -> Dict[str, str]:
        from backend.domain.settings import app_settings

        return self._query(app_settings.settings_map)

    # -- people -------------------------------------------------------------------

    def has_work_role(self, person_id: str, work_id: str, role_type: str) -> bool:
        from backend.domain.people import roles

        return self._query(lambda scope: roles.has_role(scope, work_id, person_id, role_type))

    def insert_initial_role(self, work_id: str, person_id: str, role_type: str,
                            order_index: int = 0, credit_name: str = "") -> None:
        """A relationship a new Work is born with. Creates no revision."""
        from backend.domain.people import roles

        wanted = [roles.InitialRole(person_id, role_type, int(order_index), credit_name or "")]
        self._edit(lambda scope: roles.insert_initial_roles(scope, work_id, wanted, skip_invalid=False),
                   serializable=True)

    def missing_person_ids(self, person_ids: Any) -> List[str]:
        return self._missing_person_ids(person_ids)

    def get_work_roles(self, work_id: str) -> List[dict]:
        from backend.domain.people import roles

        return self._query(lambda scope: roles.work_roles(scope, work_id))

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

    def resolve_wiki_links(self, text: str) -> str:
        """Targets resolve against this Library only (``backend.domain.works.wiki_links``)."""
        from backend.domain.works import wiki_links

        return self._query(lambda scope: wiki_links.render(scope, text))

    # -- Saved Views (PG-C3 PR 1: ``backend/api/saved_views.py`` calls the commands) --

    def get_saved_views(self) -> List[dict]:
        from backend.domain.saved_views import commands

        return commands.all_views(self.database, current_context())

    def get_saved_view(self, view_id: str) -> Optional[dict]:
        from backend.domain.saved_views import commands

        return commands.view(self.database, current_context(), view_id)

    def create_saved_view(self, name: Any, search: Any) -> dict:
        from backend.domain.saved_views import commands

        return commands.create(self.database, current_context(), name, search)

    def update_saved_view(self, view_id: str, *, name: Any = None, search: Any = None) -> dict:
        from backend.domain.saved_views import commands

        return commands.update(self.database, current_context(), view_id, name=name, search=search)

    def delete_saved_view(self, view_id: str) -> None:
        from backend.domain.saved_views import commands

        commands.delete(self.database, current_context(), view_id)

    # -- application settings (PG-C3 PR 2: ``backend/api/settings.py`` calls the commands) --

    def get_app_settings_response(self) -> Dict[str, Any]:
        from backend.domain.settings import commands

        return commands.response(self.database, current_context())

    def patch_app_settings(self, data: Any) -> None:
        from backend.domain.settings import commands

        commands.patch(self.database, current_context(), data)

    # -- publishers (PG-C3 PR 3: ``backend/api/publishers.py`` calls the commands) --

    def add_publisher(self, name: str) -> Dict[str, Any]:
        from backend.domain.people import publisher_commands

        return publisher_commands.create(self.database, current_context(), name)

    def add_publisher_alias(self, publisher_id: str, alias: str) -> None:
        from backend.domain.people import publisher_commands

        publisher_commands.add_alias(self.database, current_context(), publisher_id, alias)

    def delete_publisher_alias(self, publisher_id: str, alias: str) -> bool:
        from backend.domain.people import publisher_commands

        return publisher_commands.delete_alias(self.database, current_context(), publisher_id, alias)

    def delete_publisher(self, publisher_id: str) -> None:
        from backend.domain.people import publisher_commands

        publisher_commands.delete(self.database, current_context(), publisher_id)

    def get_publishers_in_use(self) -> List[Dict[str, Any]]:
        from backend.domain.people import publisher_commands

        return publisher_commands.in_use(self.database, current_context())


class SavedViewCommandsOver:
    """``backend.domain.saved_views.commands`` answered by a Library's old method names.

    A parity test drives ``backend/api/saved_views.py`` on both sides. The
    reference side replays SQLite's recorded calls by method name, so there
    the route's commands are patched to this (:func:`route_commands_over`):
    each command becomes the ``PRKSDatabase`` call it replaced, and the
    database and context the route passes are not used.
    """

    def __init__(self, library: Any) -> None:
        self._library = library

    def all_views(self, _db: Any, _ctx: Any) -> Any:
        return self._library.get_saved_views()

    def view(self, _db: Any, _ctx: Any, view_id: Any) -> Any:
        return self._library.get_saved_view(view_id)

    def create(self, _db: Any, _ctx: Any, name: Any, search: Any) -> Any:
        return self._library.create_saved_view(name, search)

    def update(self, _db: Any, _ctx: Any, view_id: Any, *, name: Any = None, search: Any = None) -> Any:
        return self._library.update_saved_view(view_id, name=name, search=search)

    def delete(self, _db: Any, _ctx: Any, view_id: Any) -> None:
        self._library.delete_saved_view(view_id)


class SettingsCommandsOver:
    """``backend.domain.settings.commands`` answered by a Library's old method names (see :class:`SavedViewCommandsOver`)."""

    def __init__(self, library: Any) -> None:
        self._library = library

    def response(self, _db: Any, _ctx: Any) -> Any:
        return self._library.get_app_settings_response()

    def patch(self, _db: Any, _ctx: Any, data: Any) -> None:
        self._library.patch_app_settings(data)


@contextlib.contextmanager
def route_commands_over(route_module: Any, commands: Any) -> Iterator[None]:
    """Run ``route_module`` with its commands (``_commands()``) replaced by ``commands`` (a ``*CommandsOver``)."""
    kept = route_module._commands
    route_module._commands = lambda: commands
    try:
        yield
    finally:
        route_module._commands = kept
