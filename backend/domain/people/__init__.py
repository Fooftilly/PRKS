"""People lane (L-P-1, #632; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1).

Persons and their aliases, Person Groups and their members, publishers, and
Work-Person roles on PostgreSQL. The schema is
``backend/db/schema/110_people.sql``. The sync handlers are
``backend/sync/handlers/person.py``, ``person_group.py`` and ``work_role.py``.

**Lock order.** Every People command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Library's Person Group hierarchy lock (only commands that create a
   group under a parent, change a parent, or delete a group);
2. Person Group rows, by id;
3. the Work row (role commands, which lock no group: ``roles``);
4. Person rows, by id;
5. revision scopes, sorted (``backend.domain.works.revisions.lock_many``).

No People command locks a Work after a Person, and no Works command locks
a Person, so the Works lane's order (the Work row first) composes with this
one.

A row is locked in its own statement before it is read for a decision,
because under READ COMMITTED a statement that waited for a row returns that
row's newest version only.

**Differences from SQLite**, each deliberate (the recorded parity tests
avoid them). The commands that routes call are ``person_commands``,
``group_commands``, ``role_commands`` and ``publisher_commands`` (PG-C3
#642; the facade's ``PeopleMixin`` is gone); a SQLite method name below is
the one ``tests/library_adapter.py`` keeps:

- Names (groups, the parent-name lookup) compare by
  ``prks.identity_key`` (§8.1), not ASCII-only ``LOWER``.
- ``aliases`` is stored as rows and reads back joined by ``", "``, one
  spelling per identity key; profile fields and descriptions are ``""``,
  never NULL; a portrait URL is normalized on create too.
- A deleted group's or Person's scopes advance (tombstones): every field
  scope, never-edited ones included, and every membership pair it ever had.
- A role names an existing Work and Person: ``add_role`` and
  ``insert_initial_role`` refuse a missing one with ``ValueError`` (SQLite's
  foreign key raises ``IntegrityError``). ``add_role`` reports a duplicate
  role inside the transaction that would insert it, and
  ``insert_initial_role`` refuses one with ``ValueError`` (SQLite's unique
  key raises ``IntegrityError``).
- A credit is promoted into the Person's aliases unless it already is the
  Person's name or an alias by identity key (SQLite compares ``lower()``).
- The methods write Work-scoped roles, as SQLite does; the domain commands
  (``backend.domain.people.roles``) also take a Manifestation, and the
  credits read are ``credits(primary M)`` (``docs/work-identity-model.md``
  §7.5).
- A Work delete tombstones every role scope of the Work and its
  Manifestations, and a Person delete every role scope naming the Person.
- ``delete_person_if_unlinked``, and a change of ``image_url``, never delete
  the cached portrait's bytes: they drop its reference and record a cleanup
  claim in the same transaction (``backend.domain.objects.portraits``), and
  settling the claim is maintenance, never inline (§5.2).
"""
