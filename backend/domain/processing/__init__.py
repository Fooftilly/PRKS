"""Processing queue (L-PR-1, #636; ``docs/postgresql-architecture.md`` §11.5, decision 12).

PDFs the browser uploads into managed objects, staged as drafts of the Works
they will become, and imported one transaction per item: ``items`` (reads,
the draft edit, delete), ``uploads`` (the body into a fresh key, then the
item), ``imports`` (the item into a Work) and ``commands`` (each operation
for one actor, as the HTTP routes call it). The schema is
``backend/db/schema/160_processing.sql``. The SQLite inbox directory, its
scanner and its claims are not ported.

Nothing in the running application reaches this package yet: SQLite stays
the only backend until the cutover (§15.3), and the queue's frontend lands
with PG-C1.

**Lock order.**

1. a target Folder ``FOR KEY SHARE`` (an edit naming one);
2. the item row: ``FOR NO KEY UPDATE`` for an edit, ``FOR UPDATE`` for an
   import or a delete, which change ``status``, a column of the draft's key;
3. an edit's Persons and Tags ``FOR KEY SHARE``, by id; an import's target
   Folder ``FOR KEY SHARE`` (or "Uncategorized"), then the Work create's,
   the roles', the Tag links' and the filing's own orders.

A Folder delete holds the Folder and then clears drafts' targets, which
never waits for an item lock. A Tag merge holds its Tags ``FOR UPDATE`` and
then moves draft tag links, whose key takes the draft row ``FOR KEY SHARE``;
edits update the draft row ``FOR NO KEY UPDATE``, which does not conflict,
and import and delete remove the draft with one statement, so neither order
waits on the other twice. Adding a tag link, and an import, write peer
tables of the Tag and Work merges, so both run SERIALIZABLE (§7.1).
"""
