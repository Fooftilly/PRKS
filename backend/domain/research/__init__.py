"""Research lane (L-R-1, #634; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1, §8.4).

Concepts, their aliases and hierarchy (``concepts``) on PostgreSQL. The
schema is ``backend/db/schema/130_research.sql``; the sync handlers are
``backend/sync/handlers/concept.py``. Positions, Arguments, note markup and
derived mentions are later slices of this lane.

Nothing in the running application imports this package yet: SQLite stays
the only backend until the cutover slices (§15.3).

**Lock order.** Every Concept command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Library's Concept hierarchy lock, for a command that adds or removes
   a parent edge (a parent change, a delete);
2. the Concept row the command changes: ``FOR UPDATE`` for a delete,
   ``FOR NO KEY UPDATE`` otherwise;
3. the other Concepts it names (new parents), by id, ``FOR KEY SHARE``, so
   a delete of one waits for the command or the command finds it gone;
4. revision scopes, sorted (``backend.domain.works.revisions.lock_many``).

No Concept command locks a Work, a Tag or a Folder. A Research Note
write holds the Concepts its markup names ``FOR KEY SHARE`` after its Work
row and note scope (``concepts.share_referenced``), which composes with
this order because no Concept command waits on a Work or a note scope; a
delete's ``FOR UPDATE`` therefore never misses a note committed alongside
it (``CONCEPT_IN_USE``).
"""

from backend.db import register_constraint

# The name and every alias of every Concept are one key namespace
# (``concept_keys``, §8.1). A rename or an alias written while a concurrent
# writer holds the key fails on the primary key when that writer commits;
# the command then runs once more and refuses from what it reads
# (``concepts.KEY_INDEX``).
register_constraint("concept_keys_pkey", "CONCEPT_EXISTS")
