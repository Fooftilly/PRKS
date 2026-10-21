"""People family of the PostgreSQL transition facade (L-P-1).

Owned by the People lane (``docs/postgresql-architecture.md`` §15.2):
persons, roles, person groups and publishers. Each ported method keeps the
``PRKSDatabase`` signature and return shape and is registered by one line,
``@ported`` from :mod:`backend.pg_facade.registry`, directly above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one ``library_command`` transaction. The domain
modules (and so psycopg) are imported inside the methods.

Ported here (L-P-1 PR 2): Persons and their profile fields, Person Groups
with their hierarchy and members, and publishers. Roles (``add_role`` and
the rest of the ``work-person-role`` family, Person ``works`` and the Work
projection's people fields) are the next slice: until then ``get_person``
refuses a Person who has roles with ``NotPortedError`` rather than answer
without their Works. Differences from SQLite, each deliberate:

- Names (groups, publishers, the parent-name lookup) compare by
  ``prks.identity_key`` (§8.1), not ASCII-only ``LOWER``.
- ``aliases`` is stored as rows and reads back joined by ``", "``, one
  spelling per identity key; profile fields and descriptions are ``""``,
  never NULL; a portrait URL is normalized on create too.
- A new publisher may not be named like another publisher's alias.
- A deleted group's or Person's scopes advance (tombstones): every field
  scope, never-edited ones included, and every membership pair it ever had.
- ``delete_person_if_unlinked`` leaves the cached portrait file in place:
  stored bytes belong to the objects lane (L-A-1), and a post-commit side
  effect is a job, never inline (§5.2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")

_PROFILE_FIELDS = frozenset({
    "first_name", "last_name", "aliases", "about", "image_url", "link_wikipedia",
    "link_stanford_encyclopedia", "link_iep", "links_other", "birth_date", "death_date",
})


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    """One People command; a name lost to a concurrent rename reads as SQLite's refusal."""
    from backend.db.database import Isolation
    from backend.db.errors import ConstraintViolationError
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command
    from backend.person_group_sync import GroupRuleError

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    try:
        return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                               isolation=isolation)
    except ConstraintViolationError as error:
        if error.constraint == "person_groups_library_id_name_key_key":
            raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.") from error
        raise


def _new_id(prefix: str) -> str:
    from backend.entity_ids import generate

    return generate(prefix)


def _etag(label: str, rows: List[dict]) -> str:
    from backend.db_manager import PRKSDatabase

    return PRKSDatabase.etag_for_representation(label, rows)


class PeopleMixin(FacadeMixin):
    """Persons, Person Groups and publishers (L-P-1 PR 2)."""

    # -- Persons ----------------------------------------------------------------

    @ported
    def add_person(
        self,
        first_name: str,
        last_name: str,
        aliases: str = "",
        about: str = "",
        image_url: str = "",
        link_wikipedia: str = "",
        link_stanford_encyclopedia: str = "",
        link_iep: str = "",
        links_other: str = "",
        birth_date: str = "",
        death_date: str = "",
        person_id: str | None = None,
    ) -> str:
        from backend.domain.people import persons
        from backend.entity_ids import is_distributed

        if person_id is None:
            person_id = _new_id("P")
        elif not is_distributed(person_id, "P"):
            raise ValueError("invalid person id")
        fields = persons.canonical_fields({
            "first_name": first_name, "last_name": last_name, "aliases": aliases, "about": about,
            "image_url": image_url, "link_wikipedia": link_wikipedia,
            "link_stanford_encyclopedia": link_stanford_encyclopedia, "link_iep": link_iep,
            "links_other": links_other, "birth_date": birth_date, "death_date": death_date,
        })
        new_id = person_id
        _command(self, lambda scope: persons.create_person(scope, new_id, fields))
        return new_id

    @ported
    def get_all_persons(self) -> List[dict]:
        from backend.domain.people import queries

        return _read(self, queries.person_catalog)

    @ported
    def get_person(self, person_id: str) -> Optional[dict]:
        from backend.domain.people import queries
        from backend.pg_facade.registry import NotPortedError

        found = _read(self, lambda scope: queries.person_detail(scope, person_id))
        if found is None:
            return None
        person, role_count = found
        if role_count:
            raise NotPortedError("get_person(works)")
        person["works"] = []
        return person

    @ported
    def etag_persons_catalog(self, rows: List[dict]) -> str:
        return _etag("persons", rows)

    @ported
    def get_person_metadata_state(self, person_id: str) -> Optional[dict]:
        from backend.domain.people import persons

        return _read(self, lambda scope: persons.field_state(scope, person_id))

    @ported
    def update_person_profile(self, person_id: str, fields: dict, group_ids=None) -> None:
        """Profile fields and, when ``group_ids`` is a list, the exact group set, atomically."""
        from backend.domain.people import persons

        updates = {name: persons.canonical_field(name, value)
                   for name, value in (fields or {}).items() if name in _PROFILE_FIELDS}
        if group_ids is None:
            if updates:
                _command(self, lambda scope: persons.update_profile(scope, person_id, updates, None))
            return
        if not isinstance(group_ids, list):
            raise ValueError("group_ids must be a JSON array")
        clean: List[str] = []
        for group_id in group_ids:
            if not group_id or str(group_id) in clean:
                continue
            clean.append(str(group_id))

        def run(scope: LibraryScope) -> None:
            if not persons.update_profile(scope, person_id, updates, clean):
                raise ValueError("Person not found.")

        _command(self, run, serializable=True)

    @ported
    def delete_person_if_unlinked(self, person_id: str) -> None:
        from backend.domain.people import persons

        pid = (person_id or "").strip()
        if not pid:
            raise ValueError("Person not found.")
        deleted, linked = _command(self, lambda scope: persons.delete_person(scope, pid),
                                   serializable=True)
        if linked:
            raise ValueError("Cannot delete person with linked works.")
        if not deleted:
            raise ValueError("Person not found.")

    # -- Person Groups ----------------------------------------------------------

    @ported
    def add_person_group_with_parent_options(
        self,
        name: str,
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Resolve or create the parent, then create the group, in one transaction."""
        from backend.domain.people import groups

        group_id, parent_new_id = _new_id("PG"), _new_id("PG")
        n = (name or "").strip()
        pn = (parent_name or "").strip()

        def run(scope: LibraryScope) -> str:
            if parent_id or pn:
                groups.lock_hierarchy(scope)
            if not parent_id and pn and groups.same_name(scope, pn, n):
                raise ValueError("Parent group cannot have the same name as the new group.")
            pid = parent_id or (groups.resolve_parent(scope, pn, parent_new_id) if pn else None)
            return groups.insert_group(scope, group_id, n, pid or "", description or "")

        return _command(self, run)

    @ported
    def update_person_group(self, group_id: str, fields: dict) -> None:
        from backend import person_group_sync as rules
        from backend.domain.people import groups

        parent_new_id = _new_id("PG")

        def run(scope: LibraryScope) -> None:
            changes = dict(fields)
            if "parent_name" in changes or "parent_id" in changes:
                groups.lock_hierarchy(scope)
            if groups.group(scope, group_id) is None:
                raise ValueError("Group not found.")
            if "parent_name" in changes:
                raw = changes.pop("parent_name")
                pn = str(raw).strip() if raw is not None else ""
                changes["parent_id"] = groups.resolve_parent(scope, pn, parent_new_id) if pn else None
            for field in rules.FIELDS:
                if field in changes:
                    value = changes[field]
                    groups.set_field(scope, group_id, field,
                                     "" if value in (None, False) else str(value))

        _command(self, run)

    @ported
    def delete_person_group(self, group_id: str) -> None:
        from backend.domain.people import groups

        if not _command(self, lambda scope: groups.delete_group(scope, group_id)):
            raise ValueError("Group not found.")

    @ported
    def get_all_person_groups(self) -> List[dict]:
        from backend.domain.people import queries

        return _read(self, queries.group_catalog)

    @ported
    def get_person_group(self, group_id: str) -> Optional[dict]:
        from backend.domain.people import queries

        return _read(self, lambda scope: queries.group_detail(scope, group_id))

    @ported
    def etag_person_groups_catalog(self, rows: List[dict]) -> str:
        return _etag("person-groups", rows)

    @ported
    def add_person_to_group(self, person_id: str, group_id: str) -> None:
        from backend.domain.people import groups

        def run(scope: LibraryScope) -> None:
            group_found, person_found = groups.lock_pair(scope, group_id, person_id)
            if not group_found or not person_found:
                raise ValueError("Person or group not found.")
            groups.set_member(scope, group_id, person_id, True)

        _command(self, run, serializable=True)

    @ported
    def remove_person_from_group(self, person_id: str, group_id: str) -> None:
        from backend.domain.people import groups

        _command(self, lambda scope: groups.set_member(scope, group_id, person_id, False))

    @ported
    def get_person_group_sync_state(self, group_id: str) -> Optional[dict]:
        from backend.domain.people import groups

        return _read(self, lambda scope: groups.group_state(scope, group_id))

    @ported
    def get_person_groups_state(self, person_id: str) -> Optional[dict]:
        from backend.domain.people import groups

        return _read(self, lambda scope: groups.person_group_state(scope, person_id))

    # -- Publishers -------------------------------------------------------------

    @ported
    def add_publisher(self, name: str) -> Dict[str, Any]:
        from backend.domain.people import publishers

        new_id = _new_id("R")
        return _command(self, lambda scope: publishers.create_publisher(scope, new_id, name))

    @ported
    def add_publisher_alias(self, publisher_id: str, alias: str) -> None:
        from backend.domain.people import publishers

        new_id = _new_id("M")
        _command(self, lambda scope: publishers.add_alias(scope, new_id, publisher_id, alias))

    @ported
    def delete_publisher_alias(self, publisher_id: str, alias: str) -> bool:
        from backend.domain.people import publishers

        return _command(self, lambda scope: publishers.delete_alias(scope, publisher_id, alias))

    @ported
    def delete_publisher(self, publisher_id: str) -> None:
        from backend.domain.people import publishers

        _command(self, lambda scope: publishers.delete_publisher(scope, publisher_id))

    @ported
    def get_publishers_in_use(self) -> List[dict]:
        from backend.domain.people import publishers

        return _read(self, publishers.publishers_in_use)
