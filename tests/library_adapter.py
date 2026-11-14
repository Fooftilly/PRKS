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

    # PG-C3 PR 14: ``backend/api/annotations.py`` calls the commands.

    @staticmethod
    def _annotation_commands() -> Any:
        from backend.domain.annotations import annotation_commands

        return annotation_commands

    def _annotation_objects(self) -> Any:
        from backend.api.annotations import _objects

        return _objects(self)

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
        from backend.domain.people import role_commands

        return role_commands.missing_person_ids(self.database, current_context(), person_ids)

    # -- Work-Person roles (PG-C3 PR 9: ``backend/api/work_roles.py`` calls the commands) --

    def add_role(self, person_id: str, work_id: str, role_type: str, order_index: int = 0,
                 credit_name: str = "") -> None:
        """A role on an existing Work, appended (``order_index`` is ignored)."""
        from backend.domain.people import role_commands

        del order_index
        role_commands.add(self.database, current_context(), person_id, work_id, role_type, credit_name)

    def missing_initial_role_person_ids(self, roles: Any) -> List[str]:
        from backend.domain.people import role_commands

        return role_commands.missing_initial_person_ids(self.database, current_context(), roles)

    def insert_initial_roles(self, work_id: str, roles: Any) -> int:
        from backend.domain.people import role_commands

        return role_commands.insert_initial(self.database, current_context(), work_id, roles)

    def next_role_order_index(self, work_id: str) -> int:
        from backend.domain.people import role_commands

        return role_commands.next_order_index(self.database, current_context(), work_id)

    def get_work_roles_state(self, work_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import role_commands

        return role_commands.work_state(self.database, current_context(), work_id)

    def update_role_credit_name(self, work_id: str, person_id: str, role_type: str,
                                order_index: int, credit_name: str) -> bool:
        """The credit of an existing role (``order_index`` is ignored)."""
        from backend.domain.people import role_commands

        del order_index
        return role_commands.update_credit(self.database, current_context(), work_id, person_id,
                                           role_type, credit_name)

    def delete_work_role(self, work_id: str, person_id: str, role_type: str,
                         order_index: int = 0) -> bool:
        """Remove one role (``order_index`` is ignored)."""
        from backend.domain.people import role_commands

        del order_index
        return role_commands.remove(self.database, current_context(), work_id, person_id, role_type)

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

    # -- Tags (PG-C3 PR 4: ``backend/api/tags.py`` calls the commands) --

    def add_tag(self, name: str, color: str = "#6d6cf7") -> Dict[str, Any]:
        from backend.domain.organization import tag_commands

        return tag_commands.create(self.database, current_context(), name, color)

    def add_tag_alias(self, tag_id: str, alias: str) -> None:
        from backend.domain.organization import tag_commands

        tag_commands.add_alias(self.database, current_context(), tag_id, alias)

    def delete_tag_alias(self, tag_id: str, alias: str) -> bool:
        from backend.domain.organization import tag_commands

        return tag_commands.delete_alias(self.database, current_context(), tag_id, alias)

    def merge_tags_into(self, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        from backend.domain.organization import tag_commands

        return tag_commands.merge(self.database, current_context(), source_tag_id, target_tag_id)

    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        from backend.domain.organization import tag_commands

        return tag_commands.delete(self.database, current_context(), tag_id)

    def get_all_tags(self) -> List[Dict[str, Any]]:
        from backend.domain.organization import tag_commands

        return tag_commands.all_tags(self.database, current_context())

    def get_tags_in_use(self) -> List[Dict[str, Any]]:
        from backend.domain.organization import tag_commands

        return tag_commands.in_use(self.database, current_context())

    def add_tag_to_work(self, work_id: str, tag_id: str) -> None:
        from backend.domain.organization import tag_commands

        tag_commands.tag_work(self.database, current_context(), work_id, tag_id)

    def remove_tag_from_work(self, work_id: str, tag_id: str) -> None:
        from backend.domain.organization import tag_commands

        tag_commands.untag_work(self.database, current_context(), work_id, tag_id)

    def get_work_tag_options(self, work_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import tag_commands

        return tag_commands.work_options(self.database, current_context(), work_id)

    def add_tag_to_folder(self, folder_id: str, tag_id: str) -> None:
        from backend.domain.organization import tag_commands

        tag_commands.tag_folder(self.database, current_context(), folder_id, tag_id)

    def remove_tag_from_folder(self, folder_id: str, tag_id: str) -> None:
        from backend.domain.organization import tag_commands

        tag_commands.untag_folder(self.database, current_context(), folder_id, tag_id)

    def get_folder_tag_options(self, folder_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import tag_commands

        return tag_commands.folder_options(self.database, current_context(), folder_id)

    # -- Folders (PG-C3 PR 6: ``backend/api/folders.py`` calls the commands) --

    def ensure_default_uncategorized_folder_id(self) -> str:
        from backend.domain.organization import folder_commands

        return folder_commands.ensure_uncategorized(self.database, current_context())

    def add_folder(self, title: str, description: str = "", parent_id: Optional[str] = None) -> str:
        from backend.domain.organization import folder_commands

        return folder_commands.create(self.database, current_context(), title, description, parent_id)

    def get_all_folders(self) -> List[Dict[str, Any]]:
        from backend.domain.organization import folder_commands

        return folder_commands.catalog(self.database, current_context())

    def etag_folders_catalog(self, rows: Optional[List[Dict[str, Any]]] = None) -> str:
        from backend.etags import etag_for_representation

        return etag_for_representation("folders", self.get_all_folders() if rows is None else rows)

    def get_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import folder_commands

        return folder_commands.detail(self.database, current_context(), folder_id,
                                       None if self.storage is None else self.storage.pdfs_dir)

    def get_folder_work_ids(self, folder_id: str) -> List[str]:
        from backend.domain.organization import folder_commands

        return folder_commands.member_work_ids(self.database, current_context(), folder_id)

    def folder_exists(self, folder_id: str) -> bool:
        from backend.domain.organization import folder_commands

        return folder_commands.exists(self.database, current_context(), folder_id)

    def update_folder_metadata(self, folder_id: str, fields: Dict[str, Any]) -> None:
        from backend.domain.organization import folder_commands

        folder_commands.update(self.database, current_context(), folder_id, fields)

    def delete_empty_folder(self, folder_id: str) -> None:
        from backend.domain.organization import folder_commands

        folder_commands.delete(self.database, current_context(), folder_id)

    def get_folder_sync_state(self, folder_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import folder_commands

        return folder_commands.sync_state(self.database, current_context(), folder_id)

    def get_related_folders_for_work(self, work_id: str) -> List[Dict[str, Any]]:
        from backend.domain.organization import folder_commands

        return folder_commands.related(self.database, current_context(), work_id)

    def add_work_to_folder(self, folder_id: str, work_id: str) -> None:
        from backend.domain.organization import folder_commands

        folder_commands.file_work(self.database, current_context(), folder_id, work_id)

    def move_work_to_folder(self, work_id: str, folder_id: Optional[str]) -> None:
        from backend.domain.organization import folder_commands

        folder_commands.move_work(self.database, current_context(), work_id, folder_id)

    def get_work_folder_state(self, work_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import folder_commands

        return folder_commands.work_state(self.database, current_context(), work_id)

    # -- Playlists (PG-C3 PR 5: ``backend/api/playlists.py`` calls the commands) --

    def add_playlist(self, title: str, description: str = "", original_url: str = "") -> str:
        from backend.domain.organization import playlist_commands

        return playlist_commands.create(self.database, current_context(), title, description, original_url)

    def update_playlist(self, playlist_id: str, fields: Dict[str, Any]) -> None:
        from backend.domain.organization import playlist_commands

        playlist_commands.update(self.database, current_context(), playlist_id, fields)

    def delete_playlist(self, playlist_id: str) -> None:
        from backend.domain.organization import playlist_commands

        playlist_commands.delete(self.database, current_context(), playlist_id)

    def get_all_playlists(self) -> List[Dict[str, Any]]:
        from backend.domain.organization import playlist_commands

        return playlist_commands.catalog(self.database, current_context())

    def etag_playlists_catalog(self, rows: List[Dict[str, Any]]) -> str:
        from backend.etags import etag_for_representation

        return etag_for_representation("playlists", rows)

    def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import playlist_commands

        return playlist_commands.detail(self.database, current_context(), playlist_id,
                                       None if self.storage is None else self.storage.pdfs_dir)

    def add_work_to_playlist(self, playlist_id: str, work_id: str, position: Optional[int] = None) -> None:
        from backend.domain.organization import playlist_commands

        playlist_commands.add_item(self.database, current_context(), playlist_id, work_id, position)

    def remove_work_from_playlist(self, playlist_id: str, work_id: str) -> None:
        from backend.domain.organization import playlist_commands

        playlist_commands.remove_item(self.database, current_context(), playlist_id, work_id)

    def reorder_playlist(self, playlist_id: str, work_ids: List[str]) -> None:
        from backend.domain.organization import playlist_commands

        playlist_commands.reorder(self.database, current_context(), playlist_id, work_ids)

    def get_playlist_sync_state(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import playlist_commands

        return playlist_commands.sync_state(self.database, current_context(), playlist_id)

    def get_work_playlist_state(self, work_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.organization import playlist_commands

        return playlist_commands.work_state(self.database, current_context(), work_id)

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

    # -- Persons (PG-C3 PR 8: ``backend/api/persons.py`` calls the commands) --

    def add_person(self, first_name: str, last_name: str, aliases: str = "", about: str = "",
                   image_url: str = "", link_wikipedia: str = "", link_stanford_encyclopedia: str = "",
                   link_iep: str = "", links_other: str = "", birth_date: str = "",
                   death_date: str = "", person_id: Optional[str] = None) -> str:
        from backend.domain.people import person_commands

        fields = {
            "first_name": first_name, "last_name": last_name, "aliases": aliases, "about": about,
            "image_url": image_url, "link_wikipedia": link_wikipedia,
            "link_stanford_encyclopedia": link_stanford_encyclopedia, "link_iep": link_iep,
            "links_other": links_other, "birth_date": birth_date, "death_date": death_date,
        }
        return person_commands.create(self.database, current_context(), fields, person_id)

    def get_all_persons(self) -> List[Dict[str, Any]]:
        from backend.domain.people import person_commands

        return person_commands.catalog(self.database, current_context())

    def get_person(self, person_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import person_commands

        return person_commands.detail(self.database, current_context(), person_id,
                                      None if self.storage is None else self.storage.pdfs_dir)

    def etag_persons_catalog(self, rows: List[Dict[str, Any]]) -> str:
        from backend.etags import etag_for_representation

        return etag_for_representation("persons", rows)

    def get_person_metadata_state(self, person_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import person_commands

        return person_commands.metadata_state(self.database, current_context(), person_id)

    def update_person_profile(self, person_id: str, fields: Dict[str, Any], group_ids: Any = None) -> None:
        from backend.domain.people import person_commands

        person_commands.update_profile(self.database, current_context(), person_id, fields, group_ids)

    def delete_person_if_unlinked(self, person_id: str) -> None:
        from backend.domain.people import person_commands

        person_commands.delete_if_unlinked(self.database, current_context(), person_id)

    def _portrait_objects(self) -> Any:
        from backend.storage.objects import LocalFilesystemStorage

        if self.storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return LocalFilesystemStorage.for_config(self.storage)

    def _person_portrait(self, person_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import person_commands

        return person_commands.portrait(self.database, current_context(), self._portrait_objects(),
                                        person_id)

    def _cache_person_portrait(self, person_id: str, prepare: Any = None) -> Optional[Dict[str, Any]]:
        from backend.domain.people import person_commands
        from backend.person_image import fetch_and_prepare

        return person_commands.cache_portrait(self.database, current_context(), self._portrait_objects(),
                                              person_id, prepare or fetch_and_prepare)

    def _clear_person_portrait(self, person_id: str) -> bool:
        from backend.domain.people import person_commands

        return person_commands.clear_portrait(self.database, current_context(), person_id)

    # -- Concepts (PG-C3 PR 10: ``backend/api/concepts.py`` calls the commands) --

    def list_concepts(self) -> List[Dict[str, Any]]:
        from backend.domain.research import concept_commands

        return concept_commands.catalog(self.database, current_context())

    def get_concept(self, concept_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import concept_commands

        return concept_commands.detail(self.database, current_context(), concept_id)

    def create_concept(self, name: Any, description: Any = "") -> Dict[str, Any]:
        from backend.domain.research import concept_commands

        return concept_commands.create(self.database, current_context(), name, description)

    def update_concept(self, concept_id: str, *, name: Any = None, description: Any = None) -> Dict[str, Any]:
        from backend.domain.research import concept_commands

        return concept_commands.update(self.database, current_context(), concept_id, name=name,
                                       description=description)

    def replace_concept_aliases(self, concept_id: str, aliases: Any) -> Dict[str, Any]:
        from backend.domain.research import concept_commands

        return concept_commands.replace_aliases(self.database, current_context(), concept_id, aliases)

    def replace_concept_parents(self, concept_id: str, parent_ids: Any) -> Dict[str, Any]:
        from backend.domain.research import concept_commands

        return concept_commands.replace_parents(self.database, current_context(), concept_id, parent_ids)

    def delete_concept(self, concept_id: str) -> None:
        from backend.domain.research import concept_commands

        concept_commands.delete(self.database, current_context(), concept_id)

    def get_concept_sync_state(self, concept_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import concept_commands

        return concept_commands.sync_state(self.database, current_context(), concept_id)

    # -- Positions (PG-C3 PR 11: ``backend/api/positions.py`` calls the commands) --

    def list_positions(self) -> List[Dict[str, Any]]:
        from backend.domain.research import position_commands

        return position_commands.catalog(self.database, current_context())

    def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import position_commands

        return position_commands.detail(self.database, current_context(), position_id)

    def create_position(self, name: Any, description: Any = "") -> Dict[str, Any]:
        from backend.domain.research import position_commands

        return position_commands.create(self.database, current_context(), name, description)

    def update_position(self, position_id: str, *, name: Any = None, description: Any = None) -> Dict[str, Any]:
        from backend.domain.research import position_commands

        return position_commands.update(self.database, current_context(), position_id, name=name,
                                        description=description)

    def delete_position(self, position_id: str) -> None:
        from backend.domain.research import position_commands

        position_commands.delete(self.database, current_context(), position_id)

    def get_position_sync_state(self, position_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import position_commands

        return position_commands.sync_state(self.database, current_context(), position_id)

    # -- Arguments and the Research Note (PG-C3 PR 12: ``backend/api/arguments.py``
    # and ``backend/api/research_notes.py`` call the commands) --

    def list_verdicts(self) -> List[Dict[str, Any]]:
        from backend.domain.research import argument_commands

        return argument_commands.verdicts(self.database, current_context())

    def list_arguments(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        from backend.domain.research import argument_commands

        return argument_commands.catalog(self.database, current_context(), kind)

    def get_argument(self, argument_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import argument_commands

        return argument_commands.detail(self.database, current_context(), argument_id)

    def create_argument(self, *, name: Any, kind: Any, main_text: Any = "", sources: Any = None,
                        targets: Any = None) -> Dict[str, Any]:
        from backend.domain.research import argument_commands

        return argument_commands.create(self.database, current_context(), name=name, kind=kind,
                                        main_text=main_text, sources=sources, targets=targets)

    def update_argument(self, argument_id: str, *, name: Any = None, kind: Any = None,
                        main_text: Any = None) -> Dict[str, Any]:
        from backend.domain.research import argument_commands

        return argument_commands.update(self.database, current_context(), argument_id, name=name, kind=kind,
                                        main_text=main_text)

    def replace_argument_sources(self, argument_id: str, sources: Any) -> Dict[str, Any]:
        from backend.domain.research import argument_commands

        return argument_commands.replace_sources(self.database, current_context(), argument_id, sources)

    def replace_argument_targets(self, argument_id: str, targets: Any) -> Dict[str, Any]:
        from backend.domain.research import argument_commands

        return argument_commands.replace_targets(self.database, current_context(), argument_id, targets)

    def delete_argument(self, argument_id: str) -> None:
        from backend.domain.research import argument_commands

        argument_commands.delete(self.database, current_context(), argument_id)

    def get_argument_sync_state(self, argument_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.research import argument_commands

        return argument_commands.sync_state(self.database, current_context(), argument_id)

    def save_work_notes(self, work_id: str, text_content: Any) -> None:
        from backend.domain.research import note_commands

        note_commands.save(self.database, current_context(), work_id, text_content)

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

    # -- Person Groups (PG-C3 PR 7: ``backend/api/person_groups.py`` calls the commands) --

    def add_person_group_with_parent_options(self, name: str, parent_id: Optional[str] = None,
                                             parent_name: Optional[str] = None,
                                             description: str = "") -> str:
        from backend.domain.people import group_commands

        return group_commands.create(self.database, current_context(), name, parent_id,
                                     parent_name, description)

    def update_person_group(self, group_id: str, fields: Dict[str, Any]) -> None:
        from backend.domain.people import group_commands

        group_commands.update(self.database, current_context(), group_id, fields)

    def delete_person_group(self, group_id: str) -> None:
        from backend.domain.people import group_commands

        group_commands.delete(self.database, current_context(), group_id)

    def get_all_person_groups(self) -> List[Dict[str, Any]]:
        from backend.domain.people import group_commands

        return group_commands.catalog(self.database, current_context())

    def get_person_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import group_commands

        return group_commands.detail(self.database, current_context(), group_id)

    def etag_person_groups_catalog(self, rows: List[Dict[str, Any]]) -> str:
        from backend.etags import etag_for_representation

        return etag_for_representation("person-groups", rows)

    def add_person_to_group(self, person_id: str, group_id: str) -> None:
        from backend.domain.people import group_commands

        group_commands.add_member(self.database, current_context(), group_id, person_id)

    def remove_person_from_group(self, person_id: str, group_id: str) -> None:
        from backend.domain.people import group_commands

        group_commands.remove_member(self.database, current_context(), group_id, person_id)

    def get_person_group_sync_state(self, group_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import group_commands

        return group_commands.sync_state(self.database, current_context(), group_id)

    def get_person_groups_state(self, person_id: str) -> Optional[Dict[str, Any]]:
        from backend.domain.people import group_commands

        return group_commands.person_state(self.database, current_context(), person_id)


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
