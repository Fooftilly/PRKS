"""Research lane (L-R-1, #634; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1, §8.4).

Concepts, their aliases and hierarchy (``concepts``), Positions
(``positions``), and Arguments and Stances with their targets and cited
sources (``arguments``) on PostgreSQL. The schema is
``backend/db/schema/130_research.sql``; the sync handlers are
``backend/sync/handlers/concept.py``, ``position.py`` and ``argument.py``.
A Research Note's derived mentions are rebuilt by the
``research.reindex_work`` job (``reindex``, §8.4), which also answers the
mention reads; ``graph`` projects the Research Graph from the canonical
tables and those mentions (read only).

**Lock order.** Every Research command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Works a citation list names, by id, then the Versions it names, by
   id, ``FOR KEY SHARE`` (an Argument create or a source change), the order
   a Work delete takes them in: the Work ``FOR UPDATE``, then the Arguments
   citing it;
2. the Library's Concept hierarchy lock, for a command that adds or removes
   a parent edge (a parent change, a Concept delete), or its Argument
   target lock, for a target change;
3. the Concept, Position or Argument row the command changes: ``FOR
   UPDATE`` for a delete, ``FOR NO KEY UPDATE`` otherwise (a Work delete:
   every Argument citing the Work, by id);
4. the Library's Concept key lock, for an identity write (a rename, an
   alias set), so two writers never take each other's keys in opposite
   orders;
5. the other rows it names (new parents, the Positions and Arguments a
   target list names), by id, ``FOR KEY SHARE``, so a delete of one waits
   for the command or the command finds it gone;
6. revision scopes, sorted (``backend.domain.works.revisions.lock_many``).

A Concept create writes one key and holds no other, so it takes no key
lock, and an Argument create needs no target lock: nothing can answer an
Argument before it exists.

No Research command locks a Tag or a Folder. A Research Note write, after
its Work row and note scope, holds the Concepts its markup names ``FOR KEY
SHARE`` (a name counts as existing only once its Concept is held), creates
the rest under the Concept key lock, in key order, and holds them too, then
holds the Arguments it names (``concepts.ensure_referenced``,
``arguments.share_referenced``). This composes with this order because no
Research command waits on a note scope, the only Work it waits on is a
cited one, before any Research row, and a command holding the key lock
(an identity write) holds its Concept ``FOR NO KEY UPDATE``, which a ``FOR
KEY SHARE`` never waits for; a delete's ``FOR UPDATE`` therefore never
misses a note committed alongside it (``CONCEPT_IN_USE``,
``ARGUMENT_IN_USE``), and a Concept deleted before the note holds it is
created again.

``research.reindex_work`` takes the Work's reindex lock, the Work row ``FOR
KEY SHARE``, then its note ``FOR SHARE``, and writes only ``derived`` rows,
which no command locks: a note write or a Work delete waits for it at most
once and never holds anything it waits on.

**Differences from SQLite**, each deliberate (the recorded parity tests
avoid them). The commands that routes call are ``concept_commands``,
``position_commands``, ``argument_commands`` and ``note_commands`` (PG-C3
#642; the facade's ``ResearchMixin`` is gone); a SQLite method name below
is the one ``tests/library_adapter.py`` keeps. Refusals are SQLite's
``ResearchError`` codes, messages and statuses.

- Names and aliases compare by ``prks.identity_key`` (§8.1), which is
  ``normalize_concept_key``; resolution reads the ``concept_keys`` index
  instead of scanning every Concept, and since no two Concepts can share a
  key, ``ambiguous_concept`` is never answered.
- A delete leaves every scope the Concept owned one revision on (its
  definition, identity and parents, never-edited ones too) and advances
  ``concept-parents`` of each Concept on the other end of an edge (SQLite
  advances the identity and those neighbours), so a base observed before
  the delete is stale on a Concept created again under the id.
- Aliases with equal ``LOWER()`` are ordered by spelling, and the parents
  and children of a command's answer by name and then id (SQLite gives
  storage order).
- An id holding NUL is no Concept, Position or Argument (``not_found``;
  PostgreSQL text cannot hold it).
- A citation pins a Version (``docs/work-identity-model.md`` §8.5): a
  replaced entry keeps the Version of the one stored row it matches by Work
  and pages, a new one with pages is pinned to the Work's only Version, and
  an entry the legacy shape cannot pin (it matches several stored rows, or
  its Work has several Versions) refuses the list with
  ``argument_source_version_required`` (409). SQLite pins the primary.
- ``create_argument`` and ``replace_argument_sources`` also take a
  Version-aware list, ``[{work_id, manifestation_id, pages}]``
  (``manifestation_id`` null for the Work in general), which may cite one
  Work several times and refuses ``argument_source_version_not_found``
  (404) and ``argument_source_version_mismatch`` (409); every citation row
  read back carries ``manifestation_id`` (:mod:`backend.domain.research.arguments`).
- A Work delete removes its citations and advances each citing Argument's
  ``argument-sources`` (SQLite's cascade advances nothing).
- A delete of a Position or an Argument leaves every scope it owned one
  revision on, never-edited ones too (SQLite leaves them).
- Ties SQLite leaves to storage (equal ``order_index`` in a list, Authors
  of equal order and last name) are broken by id.
"""
