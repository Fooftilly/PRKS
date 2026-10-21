"""Organization family of the PostgreSQL transition facade (L-O-1).

Owned by the Organization lane (``docs/postgresql-architecture.md`` §15.2):
tags, folders, playlists, Saved Views and settings. Each ported method
keeps the ``PRKSDatabase`` signature and return shape and is registered by
one line, ``@ported`` from :mod:`backend.pg_facade.registry`, directly
above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one ``library_command`` transaction, SERIALIZABLE
where §7.1 lists the command (a Tag merge, and every add to its peer
tables: a Tag, an alias, a Work or Folder tag). The domain modules (and so
psycopg) are imported inside the methods.

Ported here: Tags, their aliases, delete and merge, Work and Folder tags,
and their tag-option revision states (L-O-1 PR 2). Folders themselves,
Playlists, Saved Views and settings are later slices. Differences from
SQLite, each deliberate:

- Names and aliases compare by ``prks.identity_key`` (§8.1), not ASCII-only
  ``LOWER``: "Straße" and "STRASSE", or "Λόγος" and "ΛΌΓΟΣ", are one name.
- A name or alias over 200 UTF-8 bytes, or holding NUL, is refused with
  ``ValueError`` (SQLite stores it); a blank colour stores the default.
- A link to a missing Work, Folder or Tag is refused with ``ValueError``
  (SQLite's foreign key raises ``IntegrityError``).
- ``get_work_tags``, ``get_folder_tags`` and the affected-id lists of a
  delete or merge are ordered by id (SQLite gives storage order).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    from backend.db.database import Isolation
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                           isolation=isolation)


def _new_id(prefix: str) -> str:
    from backend.entity_ids import generate

    return generate(prefix)


class OrganizationMixin(FacadeMixin):
    """Tags and Work and Folder tags (L-O-1 PR 2)."""

    # -- Tags -------------------------------------------------------------------

    @ported
    def add_tag(self, name: str, color: str = "#6d6cf7") -> Dict[str, Any]:
        from backend.domain.organization import tags

        new_id = _new_id("T")
        return _command(self, lambda scope: tags.add_tag(scope, new_id, name, color), serializable=True)

    @ported
    def add_tag_alias(self, tag_id: str, alias: str) -> None:
        from backend.domain.organization import tags

        alias_id = _new_id("L")
        _command(self, lambda scope: tags.add_alias(scope, alias_id, tag_id, alias), serializable=True)

    @ported
    def delete_tag_alias(self, tag_id: str, alias: str) -> bool:
        from backend.domain.organization import tags

        return _command(self, lambda scope: tags.delete_alias(scope, tag_id, alias))

    @ported
    def merge_tags_into(self, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        """Move all links from source tag to target, drop source row, add source name as alias of target."""
        from backend.domain.organization import tags

        from backend.db.errors import ConstraintViolationError

        alias_id = _new_id("L")

        def run(scope: LibraryScope) -> Dict[str, Any]:
            return tags.merge(scope, source_tag_id, target_tag_id, alias_id)

        try:
            return _command(self, run, serializable=True)
        except ConstraintViolationError as error:
            # A link committed during the wait, by a writer that is not
            # SERIALIZABLE: decide again with it visible, once.
            if error.constraint not in tags.LINK_KEYS:
                raise
            return _command(self, run, serializable=True)

    @ported
    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        from backend.domain.organization import tags

        tid = (tag_id or "").strip()
        if not tid:
            raise ValueError("tag_id is required")
        deleted, affected = _command(self, lambda scope: tags.delete_tag(scope, tid))
        if not deleted:
            raise ValueError("tag not found")
        return {"status": "deleted", **affected}

    @ported
    def resolve_tag_id_by_label(self, label: str) -> Optional[str]:
        from backend.domain.organization import tags

        return _read(self, lambda scope: tags.resolve_label(scope, label))

    @ported
    def get_all_tags(self) -> List[dict]:
        from backend.domain.organization import tags

        return _read(self, tags.all_tags)

    @ported
    def get_tags_in_use(self) -> List[dict]:
        from backend.domain.organization import tags

        return _read(self, tags.tags_in_use)

    @ported
    def etag_tags_all(self) -> str:
        from backend.db_manager import PRKSDatabase

        return PRKSDatabase.etag_for_representation("tags", self.get_all_tags())

    # -- Work and Folder tags ---------------------------------------------------

    @ported
    def add_tag_to_work(self, work_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.WORK, work_id, tag_id, True), serializable=True)

    @ported
    def remove_tag_from_work(self, work_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.WORK, work_id, tag_id, False))

    @ported
    def get_work_tag_options(self, work_id: str):
        from backend.domain.organization import links

        return _read(self, lambda scope: links.options(scope, links.WORK, work_id))

    @ported
    def get_work_tags(self, work_id: str) -> List[dict]:
        from backend.domain.organization import links

        return _read(self, lambda scope: links.tags_of(scope, links.WORK, work_id))

    @ported
    def add_tag_to_folder(self, folder_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.FOLDER, folder_id, tag_id, True),
                 serializable=True)

    @ported
    def remove_tag_from_folder(self, folder_id: str, tag_id: str):
        from backend.domain.organization import links

        _command(self, lambda scope: links.edit(scope, links.FOLDER, folder_id, tag_id, False))

    @ported
    def get_folder_tag_options(self, folder_id: str):
        from backend.domain.organization import links

        return _read(self, lambda scope: links.options(scope, links.FOLDER, folder_id))

    @ported
    def get_folder_tags(self, folder_id: str) -> List[dict]:
        from backend.domain.organization import links

        return _read(self, lambda scope: links.tags_of(scope, links.FOLDER, folder_id))
