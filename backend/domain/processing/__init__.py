"""Processing lane (L-PR-1, #636; ``docs/postgresql-architecture.md`` §6.5, §11.3, §11.5).

Files for Processing on PostgreSQL: the inbox's PDFs staged for import, with
the fields, roles and tags the Work will get (``files``), the scan and the
import under the Library's inbox lock (``inbox``), and the
``processing.scan`` job (``jobs``). The schema is
``backend/db/schema/160_processing.sql``; the facade methods are
``backend/pg_facade/processing.py``.

Nothing in the running application imports this package yet: SQLite stays
the only backend until the cutover slices (§15.3).

**Lock order.**

1. the Library's inbox lock (session-level, its own connection), held by the
   scan and the import across their transactions and the file changes, and
   never taken inside a transaction;
2. a target Folder ``FOR KEY SHARE`` (an update naming one, the import's
   Folder; the import's "Uncategorized" create is ``folders``');
3. the file row ``FOR NO KEY UPDATE``;
4. the Persons and Tags an update names ``FOR KEY SHARE``, by id; the
   import's Work create, roles, tags and filing take the Works,
   People and Organization lanes' own order after it.

A file row is locked ``FOR NO KEY UPDATE``, never ``FOR UPDATE``: a Tag
merge holds its Tags ``FOR UPDATE`` (Organization step 2) and then inserts
the target's links, whose key takes the file row ``FOR KEY SHARE``; that
conflicts only with ``FOR UPDATE``, so the two orders never wait on each
other. A Folder delete holds the Folder and then sets the files' target to
NULL, which waits for step 3; step 2 comes first, so an update never holds
the row while it waits for the Folder. Adding a tag to a file adds to a peer
table of the Tag merge, so an update that sets tags is SERIALIZABLE (§7.1),
as is the import.
"""
