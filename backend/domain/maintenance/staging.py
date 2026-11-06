"""R1: stage an archive in ``restore_<run>`` and map its identities (L-B-1 #639; §11.4, §13.3).

Staging writes only into its own schema (and, for objects, adds keys the
data root does not have yet), so nothing it does is visible to the Library:

1. **Tables.** ``restore_<run>.<t>`` is built from the live table with
   ``CREATE TABLE … (LIKE prks.<t> INCLUDING DEFAULTS INCLUDING CONSTRAINTS
   INCLUDING INDEXES INCLUDING GENERATED)``, never by the migration runner,
   whose files name the live schemas. Each CSV member is copied (text
   ``COPY … FROM STDIN``) into an all-text load table and inserted from there
   with a cast of every column into its live type, so the interchange type,
   not the archive's server, decides how a value is read. Generated columns
   (the ``*_key`` identity keys) are recomputed by the insert under this
   instance's rules. Every ``library_id`` becomes the target Library's.
   Identity columns (the ``seq`` tiebreaks of ``roles`` and
   ``processing_item_roles``) are globally unique, so their archived values
   are replaced by fresh values from the live sequences, keeping each
   table's order; another Library's values are never hit, and R4 needs no
   ``setval``. The archive's rows must name exactly the archive's objects,
   with the hashes and sizes the archive carries for them.
2. **Identities.** ``_map_users``: an archive user that exists here maps to
   itself; any other maps to the restoring owner. ``_map_devices``: a device
   that exists here and belongs to the mapped user is reused; one that does
   not exist is marked new (R4 inserts it; staging never writes
   ``prks.devices``); one that belongs to another user refuses the restore,
   naming how many ledger operations it carries. Every user and device
   column of the staged rows is rewritten through the maps.
3. **Foreign keys.** ``LIKE`` copies no foreign key, so every foreign key of
   every content table is checked with a generated anti-join whose parent is
   where it will be after R4 (:func:`tables.parent_route`): the staged copy of
   a content table, live plus new devices, the live Library and users, or a
   live global table. A staged row whose entity ID is held by *another*
   Library here refuses the restore (entity IDs are global, so that would be
   restoring one Library over another while the original still holds them).
4. **Objects** (unless ``objects=False``, as for a backup's own
   verification). For each archive object, under the per-object lock that
   cleanup's settle also takes, a ``restore_run_objects`` row is inserted
   and committed; only then is the data root looked at: a missing key is
   written from the archive with exclusive create, a present key with the
   archive's hash is reused, and a present key with another hash aborts the
   restore (a live object is never overwritten).

:func:`drop_staging` removes the schema; it is idempotent and is what every
abort, and the startup and maintenance cleanup, call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

import psycopg
from psycopg import sql

from backend.domain.maintenance import tables
from backend.domain.maintenance.archive import Archive, ArchiveRefused, Member
from backend.domain.maintenance.format import IO_CHUNK_SIZE, object_references
from backend.domain.maintenance.connection import pin_interchange_settings
from backend.domain.objects.lifecycle import OBJECT_LOCK_SQL
from backend.storage.objects import InvalidStorageKey, ObjectExists, StorageBackend, StorageKey

STAGING_PREFIX = "restore_"
_LOAD_PREFIX = "_load_"


def staging_schema(run_id: uuid.UUID) -> str:
    """``restore_<32 hex>``: one schema per run, so a stale one is found by its name."""
    return f"{STAGING_PREFIX}{run_id.hex}"


@dataclass(frozen=True)
class Staged:
    run_id: uuid.UUID
    schema: str
    target_library_id: str
    new_devices: int
    objects_written: int
    objects_reused: int


class StagingRefused(ArchiveRefused):
    """A restore R1 refuses (an identity collision, a broken key, a conflicting object)."""


# -- helpers ----------------------------------------------------------------------------------


_LIVE_COLUMNS_SQL: LiteralString = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod)
  FROM pg_attribute a
 WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
 ORDER BY a.attnum
"""

_PRIMARY_KEY_SQL: LiteralString = """
SELECT a.attname
  FROM pg_index i
  JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(n, pos) ON true
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.n
 WHERE i.indrelid = to_regclass(%s) AND i.indisprimary
 ORDER BY u.pos
"""


def _copy_member(conn: psycopg.Connection[Any], archive: Archive, member: Member,
                 target: sql.Composable, columns: list[str]) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER MATCH, ENCODING 'UTF8')").format(
        target, sql.SQL(", ").join(sql.Identifier(c) for c in columns))
    with archive.open_member(member) as handle, conn.cursor().copy(statement) as copy:
        while True:
            chunk = handle.read(IO_CHUNK_SIZE)
            if not chunk:
                break
            copy.write(chunk)


def _count(conn: psycopg.Connection[Any], query: sql.Composable) -> int:
    row = conn.execute(sql.SQL("SELECT count(*) FROM ({}) q").format(query)).fetchone()
    assert row is not None
    return int(row[0])


# -- 1. tables --------------------------------------------------------------------------------


def _create_tables(conn: psycopg.Connection[Any], schema: str) -> None:
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    for table in tables.BACKUP_CONTENT_TABLES:
        live_schema, name = tables.split_name(table)
        conn.execute(sql.SQL(
            "CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES "
            "INCLUDING GENERATED)").format(sql.Identifier(schema, name), sql.Identifier(live_schema, name)))


def _load_table(conn: psycopg.Connection[Any], archive: Archive, schema: str, table: str,
                target_library_id: str) -> None:
    _, name = tables.split_name(table)
    columns = [(row[0], row[1]) for row in conn.execute(_LIVE_COLUMNS_SQL, (table,)).fetchall()]
    load = sql.Identifier(schema, _LOAD_PREFIX + name)
    conn.execute(sql.SQL("CREATE TABLE {} ({})").format(
        load, sql.SQL(", ").join(sql.SQL("{} text").format(sql.Identifier(c)) for c, _ in columns)))
    _copy_member(conn, archive, archive.tables[table], load, [c for c, _ in columns])
    loaded = _count(conn, sql.SQL("SELECT 1 FROM {}").format(load))
    if loaded != archive.tables[table].rows:
        raise StagingRefused("row_count", f"The backup's {table} rows do not match its manifest.")
    selects = []
    for column, pg_type in columns:
        if column == "library_id":
            selects.append(sql.Literal(target_library_id))
        else:
            selects.append(sql.SQL("CAST({} AS {})").format(sql.Identifier(column), sql.SQL(pg_type)))
    conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
        sql.Identifier(schema, name), sql.SQL(", ").join(sql.Identifier(c) for c, _ in columns),
        sql.SQL(", ").join(selects), load))
    conn.execute(sql.SQL("DROP TABLE {}").format(load))


_IDENTITY_COLUMNS_SQL: LiteralString = """
SELECT a.attname
  FROM pg_attribute a
 WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped AND a.attidentity <> ''
 ORDER BY a.attnum
"""


def _renumber_identities(conn: psycopg.Connection[Any], schema: str) -> None:
    """Give staged identity values fresh live-sequence values in their archived order."""
    for table in tables.BACKUP_CONTENT_TABLES:
        live_schema, name = tables.split_name(table)
        staged = sql.Identifier(schema, name)
        for (column,) in conn.execute(_IDENTITY_COLUMNS_SQL, (table,)).fetchall():
            col = sql.Identifier(column)
            # Negate first: the staged copy keeps the unique index, and a fresh
            # value may equal another row's archived one.
            conn.execute(sql.SQL("UPDATE {t} SET {c} = -{c}").format(t=staged, c=col))
            conn.execute(sql.SQL(
                "WITH fresh AS (SELECT v, row_number() OVER (ORDER BY v) AS n FROM ("
                "  SELECT nextval(pg_get_serial_sequence({live}, {name})) AS v"
                "    FROM generate_series(1, (SELECT count(*) FROM {t}))) s), "
                "old AS (SELECT {c} AS was, row_number() OVER (ORDER BY {c} DESC) AS n FROM {t}) "
                "UPDATE {t} SET {c} = fresh.v FROM old JOIN fresh USING (n) WHERE {t}.{c} = old.was"
            ).format(t=staged, c=col, live=sql.Literal(f"{live_schema}.{name}"), name=sql.Literal(column)))


def _check_object_references(conn: psycopg.Connection[Any], archive: Archive, schema: str,
                             target_library_id: str) -> None:
    named: dict[StorageKey, list[tuple[Optional[str], Optional[int]]]] = {}
    for namespace, name, sha256, size in conn.execute(
            object_references(schema), {"library_id": target_library_id}).fetchall():
        try:
            obj_key = StorageKey(namespace, name)
        except InvalidStorageKey:
            raise StagingRefused("object_missing", "The backup's rows name an object by an invalid key.") from None
        named.setdefault(obj_key, []).append((sha256, size))
    missing = sorted(str(k) for k in named.keys() - archive.objects.keys())
    if missing:
        raise StagingRefused("object_missing", f"The backup's rows name objects it does not carry: {missing[0]}.")
    extra = sorted(str(k) for k in archive.objects.keys() - named.keys())
    if extra:
        raise StagingRefused("object_unreferenced", f"The backup carries an object no row names: {extra[0]}.")
    for obj_key, recorded in named.items():
        member = archive.objects[obj_key]
        for sha256, size in recorded:
            if (sha256 is not None and sha256 != member.sha256) or (size is not None and size != member.size):
                raise StagingRefused("object_mismatch",
                                     f"The backup's rows record other contents for {obj_key} than it carries.")


# -- 2. identities ----------------------------------------------------------------------------


def _load_identities(conn: psycopg.Connection[Any], archive: Archive, schema: str, owner_user_id: str) -> int:
    users = sql.Identifier(schema, "_archive_users")
    devices = sql.Identifier(schema, "_archive_devices")
    conn.execute(sql.SQL("CREATE TABLE {} (archive_user_id text PRIMARY KEY, display_name text)").format(users))
    conn.execute(sql.SQL("CREATE TABLE {} (device_id uuid PRIMARY KEY, archive_user_id text NOT NULL, "
                         "label text, first_seen timestamptz NOT NULL)").format(devices))
    _copy_member(conn, archive, archive.users, users, ["archive_user_id", "display_name"])
    _copy_member(conn, archive, archive.devices, devices, ["device_id", "archive_user_id", "label", "first_seen"])
    map_users = sql.Identifier(schema, "_map_users")
    map_devices = sql.Identifier(schema, "_map_devices")
    conn.execute(sql.SQL(
        "CREATE TABLE {map} AS SELECT a.archive_user_id, COALESCE(u.id, {owner}) AS user_id "
        "FROM {users} a LEFT JOIN prks.users u ON u.id = a.archive_user_id").format(
            map=map_users, owner=sql.Literal(owner_user_id), users=users))
    conn.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY (archive_user_id)").format(map_users))
    conn.execute(sql.SQL(
        "CREATE TABLE {map} AS SELECT d.device_id, m.user_id, d.label, d.first_seen, "
        "(live.id IS NULL) AS is_new, (live.id IS NOT NULL AND live.user_id <> m.user_id) AS collides "
        "FROM {devices} d JOIN {users} m ON m.archive_user_id = d.archive_user_id "
        "LEFT JOIN prks.devices live ON live.id = d.device_id").format(
            map=map_devices, devices=devices, users=map_users))
    conn.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY (device_id)").format(map_devices))
    mapped = _count(conn, sql.SQL("SELECT 1 FROM {}").format(map_devices))
    if mapped != archive.devices.rows:
        raise StagingRefused("identity", "A device in the backup has no user in the backup.")
    collisions = conn.execute(sql.SQL(
        "SELECT count(*) FROM {s}.sync_operations o JOIN {map} d ON d.device_id = o.device_id "
        "WHERE d.collides").format(s=sql.Identifier(schema), map=map_devices)).fetchone()
    assert collisions is not None
    colliding_devices = _count(conn, sql.SQL("SELECT 1 FROM {} WHERE collides").format(map_devices))
    if colliding_devices:
        raise StagingRefused(
            "device_collision",
            f"{colliding_devices} device(s) in the backup belong to another account here; "
            f"{int(collisions[0])} recorded operation(s) would be attributed to the wrong account.")
    # Rewrite every user and device column of the staged rows through the maps.
    found = tables.identity_columns(conn)
    for parent, map_table, source, target in (("prks.users", map_users, "archive_user_id", "user_id"),
                                              ("prks.devices", map_devices, "device_id", "device_id")):
        for fk in found[parent]:
            _, name = tables.split_name(fk.table)
            (column,) = fk.columns
            staged = sql.Identifier(schema, name)
            unmapped = _count(conn, sql.SQL(
                "SELECT 1 FROM {t} s WHERE s.{c} IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM {m} m WHERE m.{src} = s.{c})").format(
                    t=staged, c=sql.Identifier(column), m=map_table, src=sql.Identifier(source)))
            if unmapped:
                raise StagingRefused("identity", f"The backup's {fk.table}.{column} names an identity the "
                                                 "backup does not carry.")
            if source != target:
                conn.execute(sql.SQL("UPDATE {t} s SET {c} = m.{dst} FROM {m} m WHERE m.{src} = s.{c}").format(
                    t=staged, c=sql.Identifier(column), m=map_table, src=sql.Identifier(source),
                    dst=sql.Identifier(target)))
    new = _count(conn, sql.SQL("SELECT 1 FROM {} WHERE is_new").format(map_devices))
    return new


# -- 3. keys ----------------------------------------------------------------------------------


def _check_foreign_keys(conn: psycopg.Connection[Any], schema: str) -> None:
    for table in tables.BACKUP_CONTENT_TABLES:
        _, name = tables.split_name(table)
        for fk in tables.foreign_keys(conn, table):
            route = fk.route
            parent_schema, parent_name = fk.parent.split(".", 1)
            if route is tables.ParentRoute.STAGED:
                parent: sql.Composable = sql.Identifier(schema, parent_name)
            elif route is tables.ParentRoute.DEVICES:
                parent = sql.SQL("(SELECT id FROM prks.devices UNION ALL "
                                 "SELECT device_id FROM {} WHERE is_new)").format(
                    sql.Identifier(schema, "_map_devices"))
            else:
                parent = sql.Identifier(parent_schema, parent_name)
            not_null = sql.SQL(" AND ").join(
                sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(c)) for c in fk.columns)
            match = sql.SQL(" AND ").join(
                sql.SQL("p.{} = c.{}").format(sql.Identifier(pc), sql.Identifier(cc))
                for cc, pc in zip(fk.columns, fk.parent_columns))
            if route is tables.ParentRoute.DEVICES:
                match = sql.SQL("p.id = c.{}").format(sql.Identifier(fk.columns[0]))
            broken = _count(conn, sql.SQL(
                "SELECT 1 FROM {child} c WHERE {not_null} AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {match})"
            ).format(child=sql.Identifier(schema, name), not_null=not_null, parent=parent, match=match))
            if broken:
                raise StagingRefused("foreign_key", f"The backup's {table} rows refer to missing "
                                                    f"{fk.parent} rows ({fk.name}).")


_GLOBAL_KEYS_SQL: LiteralString = """
SELECT array_agg(a.attname ORDER BY u.pos), i.indnullsnotdistinct, pg_get_expr(i.indpred, i.indrelid)
  FROM pg_index i
  JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(n, pos) ON true
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.n
 WHERE i.indrelid = to_regclass(%s) AND i.indisunique AND NOT (0 = ANY (i.indkey))
 GROUP BY i.indexrelid, i.indnullsnotdistinct, i.indpred, i.indrelid
HAVING NOT bool_or(a.attname = 'library_id')
"""


def _check_foreign_library_ids(conn: psycopg.Connection[Any], schema: str, target_library_id: str) -> None:
    """Refuse staged rows that another Library holds under a globally unique key.

    Only a unique key without ``library_id`` is global (entity IDs, keys such
    as a role's Work and Person, and partial ones such as a Processing item's
    object locator); a Library-scoped key such as a tag's
    ``(library_id, key)`` is shared by design and never a collision.
    """
    for table in tables.BACKUP_CONTENT_TABLES:
        live_schema, name = tables.split_name(table)
        for columns, nulls_not_distinct, predicate in conn.execute(_GLOBAL_KEYS_SQL, (table,)).fetchall():
            same = sql.SQL(" IS NOT DISTINCT FROM ") if nulls_not_distinct else sql.SQL(" = ")
            match = sql.SQL(" AND ").join(
                sql.SQL("l.{c}{op}s.{c}").format(c=sql.Identifier(c), op=same) for c in columns)
            # A partial index is unique only among the rows its predicate (the
            # catalogue's own text, over unqualified columns) admits.
            where = sql.SQL(" WHERE ({})").format(sql.SQL(predicate)) if predicate else sql.SQL("")
            held = _count(conn, sql.SQL(
                "SELECT 1 FROM (SELECT * FROM {staged}{where}) s JOIN (SELECT * FROM {live}{where}) l ON {match} "
                "WHERE l.library_id <> {lib}").format(
                    staged=sql.Identifier(schema, name), live=sql.Identifier(live_schema, name), where=where,
                    match=match, lib=sql.Literal(target_library_id)))
            if held:
                raise StagingRefused(
                    "ids_in_other_library",
                    "Another library here still holds records of this backup. Restore it over its own library.")


# -- 4. objects -------------------------------------------------------------------------------

_STAGE_OBJECT_SQL: LiteralString = (
    "INSERT INTO prks.restore_run_objects (run_id, namespace, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
)


def _stage_objects(conn: psycopg.Connection[Any], archive: Archive, run_id: uuid.UUID,
                   storage: StorageBackend, renew: Callable[[], None]) -> tuple[int, int]:
    written = reused = 0
    for obj_key, member in sorted(archive.objects.items(), key=lambda item: str(item[0])):
        with conn.transaction():
            conn.execute(OBJECT_LOCK_SQL, (str(obj_key),))
            conn.execute(_STAGE_OBJECT_SQL, (run_id, obj_key.namespace, obj_key.name))
        if _place_object(archive, storage, obj_key, member):
            written += 1
        else:
            reused += 1
        renew()
    return written, reused


def _place_object(archive: Archive, storage: StorageBackend, obj_key: StorageKey, member: Member) -> bool:
    """Write a missing object from the archive (True) or reuse a matching one (False)."""
    if storage.stat(obj_key) is not None:
        _reuse(storage, obj_key, member)
        return False

    def write(stream: Any) -> None:
        with archive.open_member(member) as handle:
            while True:
                chunk = handle.read(IO_CHUNK_SIZE)
                if not chunk:
                    break
                stream.write(chunk)

    try:
        info = storage.put_new(obj_key, write, hash=True)
    except ObjectExists:
        _reuse(storage, obj_key, member)
        return False
    if info.sha256 != member.sha256 or info.size != member.size:
        raise StagingRefused("object_conflict", f"The backup's copy of {obj_key} did not write back intact.")
    return True


def _reuse(storage: StorageBackend, obj_key: StorageKey, member: Member) -> None:
    if storage.verify(obj_key, member.sha256) is not True:
        raise StagingRefused("object_conflict",
                             f"The stored object {obj_key} differs from the backup's copy; it is never overwritten.")


# -- the phase --------------------------------------------------------------------------------


def stage(conn: psycopg.Connection[Any], archive: Archive, *, run_id: uuid.UUID, target_library_id: str,
          owner_user_id: str, storage: Optional[StorageBackend] = None,
          renew: Callable[[], None] = lambda: None) -> Staged:
    """R1 on the run's maintenance connection; see the module docstring.

    The tables, maps and checks are built in one transaction, so a failure
    (``53100`` included) leaves no half-built schema behind; the caller still
    calls :func:`drop_staging` on every abort. With ``storage=None`` objects
    are not staged (a backup's own verification).
    """
    schema = staging_schema(run_id)
    with conn.transaction():
        pin_interchange_settings(conn)
        _create_tables(conn, schema)
        for table in tables.BACKUP_CONTENT_TABLES:
            _load_table(conn, archive, schema, table, target_library_id)
            renew()
        _renumber_identities(conn, schema)
        _check_object_references(conn, archive, schema, target_library_id)
        new_devices = _load_identities(conn, archive, schema, owner_user_id)
        _check_foreign_library_ids(conn, schema, target_library_id)
        _check_foreign_keys(conn, schema)
    written = reused = 0
    if storage is not None:
        written, reused = _stage_objects(conn, archive, run_id, storage, renew)
    return Staged(run_id=run_id, schema=schema, target_library_id=target_library_id, new_devices=new_devices,
                  objects_written=written, objects_reused=reused)


def drop_staging(conn: psycopg.Connection[Any], run_id: uuid.UUID) -> None:
    """Drop ``restore_<run>`` if it exists; idempotent."""
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(staging_schema(run_id))))
