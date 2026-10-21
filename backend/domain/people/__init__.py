"""People lane (L-P-1, #632; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1).

Persons and their aliases, Person Groups and their members, and publishers
on PostgreSQL. The schema is ``backend/db/schema/110_people.sql``. Roles
(``work-person-role``) and the sync handlers are later slices of the lane.
Nothing in the running application imports this package yet: SQLite stays
the only backend until the cutover slices (§15.3).

**Lock order.** Every People command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Library's Person Group hierarchy lock (only commands that create a
   group under a parent, change a parent, or delete a group);
2. Person Group rows, by id;
3. Person rows, by id;
4. revision scopes, sorted (``backend.domain.works.revisions.lock_many``).

A row is locked in its own statement before it is read for a decision,
because under READ COMMITTED a statement that waited for a row returns that
row's newest version only.
"""

from backend.db import register_constraint

# A rename that races another rename or create of the same name: both pass
# the read check, and the unique index refuses the second (§7.1). Creates
# use ON CONFLICT and never reach these.
register_constraint("person_groups_library_id_name_key_key", "NAME_TAKEN")
register_constraint("publisher_keys_pkey", "NAME_TAKEN")
