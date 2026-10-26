"""Research lane (L-R-1, #634; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1, §8.4).

Concepts, their aliases and hierarchy (``concepts``), Positions
(``positions``), and Arguments and Stances with their targets and cited
sources (``arguments``) on PostgreSQL. The schema is
``backend/db/schema/130_research.sql``; the sync handlers are
``backend/sync/handlers/concept.py``, ``position.py`` and ``argument.py``.
A Research Note's derived mentions are rebuilt by the
``research.reindex_work`` job (``reindex``, §8.4).

The running application imports only ``reindex`` (the jobs worker, to
register its handler): SQLite stays the only backend until the cutover
slices (§15.3).

**Lock order.** Every Research command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Works a citation list names, by id, ``FOR KEY SHARE`` (an Argument
   create or a source change), the order a Work delete takes them in: the
   Work ``FOR UPDATE``, then the Arguments citing it;
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
its Work row and note scope, creates the Concepts its markup names that do
not exist yet (under the Concept key lock, in key order, since it may write
several keys) and holds them and the Arguments it names ``FOR KEY SHARE``
(``concepts.ensure_referenced``, ``arguments.share_referenced``), which
composes with this order because no Research command waits on a note scope,
and the only Work it waits on is a cited one, before any Research row; a
delete's ``FOR UPDATE`` therefore never misses a note committed alongside
it (``CONCEPT_IN_USE``, ``ARGUMENT_IN_USE``).

``research.reindex_work`` takes the Work's reindex lock, the Work row ``FOR
KEY SHARE``, then its note ``FOR SHARE``, and writes only ``derived`` rows,
which no command locks: a note write or a Work delete waits for it at most
once and never holds anything it waits on.
"""

from backend.db import register_constraint

# The name and every alias of every Concept are one key namespace
# (``concept_keys``, §8.1). A rename or an alias written while a concurrent
# writer holds the key fails on the primary key when that writer commits;
# the command then runs once more and refuses from what it reads
# (``concepts.KEY_INDEX``).
register_constraint("concept_keys_pkey", "CONCEPT_EXISTS")
