"""Test helpers for the #60 Slice A (schema 17) work identity layer.

`revert_to_v16_schema` turns a database created at the current schema back
into the exact schema-16 shape, keeping its rows, so upgrade tests can start
from a fresh `PRKSDatabase` + ordinary API calls and still exercise the real
v16 -> v17 migration. Changing `schema_version` alone would leave v17 objects
behind for the migration to trip over.
"""
from backend.db_migrations import _INDEX_SQL, _v17_objects

_LEGACY_TABLE_SQL = {
    "annotations": """
        CREATE TABLE annotations_v16 (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL,
            type TEXT,
            content TEXT,
            page_index INTEGER,
            color TEXT,
            geometry_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
        )""",
    "roles": """
        CREATE TABLE roles_v16 (
            person_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            role_type TEXT NOT NULL,
            order_index INTEGER DEFAULT 0,
            credit_name TEXT,
            PRIMARY KEY (person_id, work_id, role_type, order_index),
            FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
            FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
        )""",
    "argument_sources": """
        CREATE TABLE argument_sources_v16 (
            argument_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            pages TEXT NOT NULL DEFAULT '',
            order_index INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (argument_id, work_id),
            FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
            FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
        )""",
}
_LEGACY_COLUMNS = {
    "annotations": "id, work_id, type, content, page_index, color, geometry_json, created_at, updated_at",
    "roles": "person_id, work_id, role_type, order_index, credit_name",
    "argument_sources": "argument_id, work_id, pages, order_index, created_at",
}
_LEGACY_INDEXES = {
    "annotations": ("idx_annotations_work_id",),
    "roles": ("idx_roles_work_id", "idx_roles_person_id", "idx_roles_person_work_role_unique"),
    "argument_sources": ("idx_argument_sources_work_id",),
}
_UNSCOPED_WORKS_AU = """
CREATE TRIGGER works_au AFTER UPDATE ON works BEGIN
  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
  INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
  VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
END
"""


def revert_to_v16_schema(conn, *, set_version=True):
    """Rewrite a current-schema DB into the schema-16 shape, rows kept.

    `conn` is a plain `sqlite3` connection in its default isolation mode. Its
    pending work is committed first because `PRAGMA foreign_keys` cannot change
    inside a transaction.
    """
    conn.commit()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        objects = _v17_objects()
        for kind, name, _sql in objects:
            if kind == "trigger":
                conn.execute("DROP TRIGGER IF EXISTS %s" % name)
        for kind, name, _sql in objects:
            if kind == "view":
                conn.execute("DROP VIEW IF EXISTS %s" % name)
        for kind, name, _sql in objects:
            if kind == "index":
                conn.execute("DROP INDEX IF EXISTS %s" % name)
        conn.execute(_UNSCOPED_WORKS_AU)
        for table, ddl in _LEGACY_TABLE_SQL.items():
            cols = _LEGACY_COLUMNS[table]
            conn.execute(ddl)
            if table == "argument_sources":
                conn.execute(
                    "INSERT INTO argument_sources_v16 (%s) SELECT %s FROM argument_sources"
                    % (cols, cols))
            else:
                conn.execute(
                    "INSERT INTO %s_v16 (rowid, %s) SELECT rowid, %s FROM %s"
                    % (table, cols, cols, table))
            conn.execute("DROP TABLE %s" % table)
            conn.execute("ALTER TABLE %s_v16 RENAME TO %s" % (table, table))
            for index in _LEGACY_INDEXES[table]:
                conn.execute(_INDEX_SQL[index])
        for kind, name, _sql in objects:
            if kind == "table" and name not in _LEGACY_TABLE_SQL:
                conn.execute("DROP TABLE IF EXISTS %s" % name)
        conn.execute("ALTER TABLE works DROP COLUMN citation_manifestation_id")
        conn.execute("ALTER TABLE works DROP COLUMN primary_manifestation_id")
        if set_version:
            conn.execute("UPDATE schema_version SET version = 16")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = %s" % ("ON" if fk else "OFF"))
