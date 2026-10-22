"""Organization lane (L-O-1, #633; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1).

Tags, their aliases and lifecycle, Work and Folder tags (``tags``,
``lifecycle``, ``links``), Folders, their hierarchy and fields and the
Folder a Work is in (``folders``), and Playlists, their fields, order and
the Playlist a Work is in (``playlists``) on PostgreSQL; Saved Views are
``backend.domain.saved_views`` and settings come in a later slice. The schema is
``backend/db/schema/120_organization.sql``. The sync handlers are
``backend/sync/handlers/tag.py``, ``tag_link.py``, ``folder.py`` and
``playlist.py``.

Nothing in the running application imports this package yet: SQLite stays
the only backend until the cutover slices (§15.3).

**Lock order.** Every Tag command takes its locks in one order, so two
commands that share rows queue instead of deadlocking:

1. the Work or Folder row a relationship command changes, ``FOR NO KEY
   UPDATE`` (it serializes the writers of that owner's tags);
2. Tag rows, by id;
3. the Work and Folder rows a Tag delete or merge moves links of, by id,
   ``FOR KEY SHARE`` (only a Work delete's ``FOR UPDATE`` waits on it, and a
   Work delete takes no Tag lock);
4. revision scopes, sorted (``backend.domain.works.revisions.lock_many``).

Step 3 comes after step 2 because the links to move are known only once the
Tag is held; ``FOR KEY SHARE`` conflicts with no lock of step 1, so the two
orders never wait on each other. It still matters: a Work delete removes the
Work's links before it tombstones their scopes, so a Tag delete that locked
a link's scope without holding the Work could wait on that link while the
Work delete waits on the scope.

The Works lane's order (the Work row first, then its scopes) composes with
this one, and no People command locks a Tag. The Folder commands' own order
(the Work row, the folder hierarchy lock, Folder rows, scopes) is in
``folders``, and the Playlist commands' (the Work row, Playlist rows by id,
scopes) in ``playlists``; neither locks a Tag, and no Playlist command locks
a Folder.

A row is locked in its own statement before it is read for a decision,
because under READ COMMITTED a statement that waited for a row returns that
row's newest version only. The commands §7.1 lists as SERIALIZABLE (a merge,
and every add to its peer tables: a Tag, an alias, a Work or Folder tag)
keep the same locks: a lock that waited on a row the other transaction
changed fails with ``40001`` there, and the retry reads the committed state.
"""

from backend.db import register_constraint

# A Tag merge is SERIALIZABLE (§7.1), so its snapshot is taken before it
# waits for the Tags. A link added by a writer that is not SERIALIZABLE and
# committed during that wait is invisible to it, and the key from the link to
# the Tag refuses the source's delete; the merge then runs once more
# (``retry_constraints``) and moves the link. Every PRKS command that adds a
# link is SERIALIZABLE, which turns the same race into a ``40001`` retry.
register_constraint("work_tags_tag_fkey", "TAG_HAS_LINKS")
register_constraint("folder_tags_tag_fkey", "TAG_HAS_LINKS")
# A Folder title is unique per parent; the index decides a race between two
# writers of one title, and the loser decides again (``folders.TITLE_KEY``).
register_constraint("folders_library_id_parent_id_title_key_key", "TITLE_TAKEN")
# The Work delete takes the Work out of its Playlist first
# (``playlists.release_work``), and every other writer of the item waits for
# the Work's lock; the item's key is the backstop.
register_constraint("playlist_items_work_fkey", "WORK_IN_PLAYLIST")
# A Saved View name is unique by identity key per Library; every writer of a
# name holds the Library's Saved View lock, so the index is the backstop
# (``backend.domain.saved_views.views.NAME_KEY``).
register_constraint("saved_views_library_id_name_key_key", "NAME_TAKEN")
