"""Organization lane (L-O-1, #633; ``docs/postgresql-architecture.md`` §6.5, §7.1, §8.1).

Tags, their aliases and lifecycle, Work and Folder tags (``tags``,
``lifecycle``, ``links``), Folders, their hierarchy and fields and the
Folder a Work is in (``folders``), and Playlists, their fields, order and
the Playlist a Work is in (``playlists``) on PostgreSQL; Saved Views are
``backend.domain.saved_views`` and the application settings
``backend.domain.settings``. The schema is
``backend/db/schema/120_organization.sql``. The sync handlers are
``backend/sync/handlers/tag.py``, ``tag_link.py``, ``folder.py`` and
``playlist.py``.

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

**Differences from SQLite**, each deliberate (the recorded parity tests
avoid them). The commands that routes call are ``tag_commands``,
``folder_commands`` and ``playlist_commands`` (PG-C3 #642; the facade's
``OrganizationMixin`` is gone):

- Names, aliases and Folder titles compare by
  ``prks.identity_key`` (§8.1), not ASCII-only ``LOWER``: "Straße" and
  "STRASSE", or "Λόγος" and "ΛΌΓΟΣ", are one name.
- A name or alias over 200 UTF-8 bytes, or a name, alias or Folder field
  holding NUL, is refused with ``ValueError`` (SQLite stores it); a blank
  colour stores the default.
- A link to a missing Work, Folder or Tag is refused with ``ValueError``
  (SQLite's foreign key raises ``IntegrityError``), and so is filing a
  missing Work into a Folder.
- Filing a Work (``folder_commands.file_work``, SQLite's ``add_work_to_folder``) advances the Work's ``work-folder`` revision when it
  files the Work: the Work exists unfiled before it, so filing it is a
  change another device must see (SQLite writes no revision). Filing at
  ``CREATE_WORK`` is construction and advances nothing on either backend.
- ``folder_commands.ensure_uncategorized`` (SQLite's
  ``ensure_default_uncategorized_folder_id``) returns the root Folder whose
  title has the key of "Uncategorized" (SQLite fails to create one when a
  root Folder holds the title in another case).
- A Folder delete removes its Tag links and leaves every field and
  Folder-Tag scope one revision on (SQLite cascades the links and leaves
  the scopes), so a base observed before the delete is stale on a Folder
  recreated under the id.
- A Playlist delete leaves every field scope one revision on, and a Work
  delete advances its Playlist's order revision (SQLite advances neither),
  so a base observed before the delete is stale. A Playlist's empty
  ``original_url`` is stored as ``''`` and read back as ``None``, as SQLite
  stores it.
- ``get_work_tags``, ``get_folder_tags``, ``get_related_folders_for_work``
  and the affected-id lists of a delete or merge are ordered by id (SQLite
  gives storage order); equal Folder titles, and Works of one Folder created
  in the same second, are ordered by id.
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
# A Processing draft's tag (``160_processing.sql``); an edit adding one is SERIALIZABLE too.
register_constraint("processing_item_tags_tag_fkey", "TAG_HAS_LINKS")
# A Folder title is unique per parent; the index decides a race between two
# writers of one title, and the loser decides again (``folders.TITLE_KEY``).
register_constraint("folders_library_id_parent_id_title_key_key", "TITLE_TAKEN")
# The Work delete takes the Work out of its Playlist first
# (``playlists.release_work``), and every other writer of the item waits for
# the Work's lock; the item's key is the backstop.
register_constraint("playlist_items_work_fkey", "WORK_IN_PLAYLIST")
