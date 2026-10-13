"""Normalized catalog snapshot of the PRKS schema (``docs/postgresql-architecture.md`` §4.4, §13.2).

:func:`catalog_snapshot` reads a migrated database's catalog (schemas,
extensions, tables with columns, constraints and indexes, views, functions,
text search objects, triggers and types) into plain JSON-able data with a
stable order. ``backend/db/schema/snapshot.json`` is that data for a database
built from the schema files; ``scripts/check_pg_schema.py`` and
``tests/pg/test_schema_snapshot.py`` compare a fresh build against it.

:func:`search_norm_dependents` is the §8.3 guard: nothing stored may be
computed from ``prks.search_norm``, whose rules can change with the database
image, so no index expression, column default or generated column may depend
on it, directly or through another function. String-bodied functions hide
their calls from the catalog, so the guard refuses stored values computed
through them too.

Extension members (``ext``) are not listed one by one: the extension's name
and schema stand for them, and their versions belong to the server image.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from backend.db.migrate import SCHEMA_DIR

if TYPE_CHECKING:
    import psycopg

SNAPSHOT_PATH = os.path.join(SCHEMA_DIR, "snapshot.json")
SNAPSHOT_FORMAT = 1
SCHEMAS = ("prks", "derived", "ext")
# Schemas whose objects the snapshot lists one by one.
OWNED_SCHEMAS = ("prks", "derived")


def _rows(conn: psycopg.Connection[Any], query: Any, params: Any = None) -> list[tuple[Any, ...]]:
    return list(conn.execute(query, params).fetchall())


def _columns(conn: psycopg.Connection[Any], relid: int) -> list[dict[str, Any]]:
    columns = []
    for name, type_, not_null, generated, identity, default, collation in _rows(
        conn,
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               a.attgenerated, a.attidentity, pg_get_expr(d.adbin, d.adrelid),
               CASE WHEN a.attcollation <> t.typcollation THEN c.collname END
          FROM pg_attribute a
          JOIN pg_type t ON t.oid = a.atttypid
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
          LEFT JOIN pg_collation c ON c.oid = a.attcollation
         WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
        """,
        (relid,),
    ):
        column: dict[str, Any] = {"name": name, "type": type_, "not_null": bool(not_null)}
        if generated:
            column["generated"] = default
        elif default is not None:
            column["default"] = default
        if identity:
            column["identity"] = identity
        if collation is not None:
            column["collation"] = collation
        columns.append(column)
    return columns


def _relations(conn: psycopg.Connection[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tables: dict[str, Any] = {}
    views: dict[str, Any] = {}
    for relid, schema, name, kind in _rows(
        conn,
        """
        SELECT c.oid, n.nspname, c.relname, c.relkind
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
         ORDER BY n.nspname, c.relname
        """,
        (list(OWNED_SCHEMAS),),
    ):
        qualified = f"{schema}.{name}"
        if kind in ("v", "m"):
            row = conn.execute("SELECT pg_get_viewdef(%s::oid, true)", (relid,)).fetchone()
            views[qualified] = {"kind": kind, "definition": None if row is None else row[0]}
            continue
        if kind == "S":
            type_, start, increment, minimum, maximum, cache, cycle = _rows(
                conn,
                """
                SELECT format_type(seqtypid, NULL), seqstart, seqincrement, seqmin, seqmax,
                       seqcache, seqcycle
                  FROM pg_sequence WHERE seqrelid = %s
                """,
                (relid,),
            )[0]
            tables[qualified] = {
                "kind": "sequence",
                "type": type_,
                "start": start,
                "increment": increment,
                "min": minimum,
                "max": maximum,
                "cache": cache,
                "cycle": cycle,
            }
            continue
        constraints = {
            conname: pg_def
            for conname, pg_def in _rows(
                conn,
                """
                SELECT conname, pg_get_constraintdef(oid, true)
                  FROM pg_constraint WHERE conrelid = %s ORDER BY conname
                """,
                (relid,),
            )
        }
        indexes = {
            idxname: pg_def
            for idxname, pg_def in _rows(
                conn,
                """
                SELECT ic.relname, pg_get_indexdef(i.indexrelid)
                  FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
                 WHERE i.indrelid = %s ORDER BY ic.relname
                """,
                (relid,),
            )
        }
        triggers = {
            tgname: pg_def
            for tgname, pg_def in _rows(
                conn,
                """
                SELECT tgname, pg_get_triggerdef(oid, true)
                  FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal ORDER BY tgname
                """,
                (relid,),
            )
        }
        table: dict[str, Any] = {
            "columns": _columns(conn, relid),
            "constraints": constraints,
            "indexes": indexes,
        }
        if kind == "p":
            table["partitioned"] = True
        if triggers:
            table["triggers"] = triggers
        tables[qualified] = table
    return tables, views


def _functions(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    functions: dict[str, Any] = {}
    for signature, volatility, strict, parallel, definition in _rows(
        conn,
        """
        SELECT p.oid::regprocedure::text, p.provolatile, p.proisstrict, p.proparallel,
               pg_get_functiondef(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = ANY(%s)
         ORDER BY 1
        """,
        (list(OWNED_SCHEMAS),),
    ):
        functions[signature] = {
            "volatility": volatility,
            "strict": bool(strict),
            "parallel": parallel,
            "definition": definition,
        }
    return functions


def _text_search(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    dictionaries = {
        f"{schema}.{name}": {"template": template, "options": options}
        for schema, name, template, options in _rows(
            conn,
            """
            SELECT n.nspname, d.dictname, tn.nspname || '.' || t.tmplname, d.dictinitoption
              FROM pg_ts_dict d
              JOIN pg_namespace n ON n.oid = d.dictnamespace
              JOIN pg_ts_template t ON t.oid = d.dicttemplate
              JOIN pg_namespace tn ON tn.oid = t.tmplnamespace
             WHERE n.nspname = ANY(%s)
             ORDER BY 1, 2
            """,
            (list(OWNED_SCHEMAS),),
        )
    }
    configurations: dict[str, Any] = {}
    for cfgid, schema, name in _rows(
        conn,
        """
        SELECT c.oid, n.nspname, c.cfgname
          FROM pg_ts_config c JOIN pg_namespace n ON n.oid = c.cfgnamespace
         WHERE n.nspname = ANY(%s)
         ORDER BY 2, 3
        """,
        (list(OWNED_SCHEMAS),),
    ):
        mappings = [
            [token, dictionary]
            for token, dictionary in _rows(
                conn,
                """
                SELECT t.alias, m.mapdict::regdictionary::text
                  FROM pg_ts_config_map m
                  JOIN ts_token_type(
                         (SELECT cfgparser FROM pg_ts_config WHERE oid = %s)) t
                    ON t.tokid = m.maptokentype
                 WHERE m.mapcfg = %s
                 ORDER BY t.alias, m.mapseqno
                """,
                (cfgid, cfgid),
            )
        ]
        configurations[f"{schema}.{name}"] = {"mappings": mappings}
    return {"dictionaries": dictionaries, "configurations": configurations}


def _type_definition(conn: psycopg.Connection[Any], typid: int, kind: str) -> dict[str, Any]:
    if kind == "d":
        base, not_null, default, collation = _rows(
            conn,
            """
            SELECT format_type(t.typbasetype, t.typtypmod), t.typnotnull, t.typdefault,
                   CASE WHEN t.typcollation <> b.typcollation THEN c.collname END
              FROM pg_type t
              JOIN pg_type b ON b.oid = t.typbasetype
              LEFT JOIN pg_collation c ON c.oid = t.typcollation
             WHERE t.oid = %s
            """,
            (typid,),
        )[0]
        checks = [
            [name, definition]
            for name, definition in _rows(
                conn,
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE contypid = %s ORDER BY conname",
                (typid,),
            )
        ]
        return {
            "base": base,
            "not_null": not_null,
            "default": default,
            "collation": collation,
            "constraints": checks,
        }
    if kind == "e":
        labels = _rows(
            conn, "SELECT enumlabel FROM pg_enum WHERE enumtypid = %s ORDER BY enumsortorder", (typid,)
        )
        return {"labels": [label for (label,) in labels]}
    if kind == "c":
        attributes = _rows(
            conn,
            """
            SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                   CASE WHEN a.attcollation <> t.typcollation THEN c.collname END
              FROM pg_attribute a
              JOIN pg_type t ON t.oid = a.atttypid
              LEFT JOIN pg_collation c ON c.oid = a.attcollation
             WHERE a.attrelid = (SELECT typrelid FROM pg_type WHERE oid = %s)
               AND a.attnum > 0 AND NOT a.attisdropped
             ORDER BY a.attnum
            """,
            (typid,),
        )
        return {"attributes": [list(row) for row in attributes]}
    if kind == "r":
        subtype, opclass, collation, canonical, subdiff = _rows(
            conn,
            """
            SELECT format_type(r.rngsubtype, NULL), o.opcname, c.collname,
                   NULLIF(r.rngcanonical::regproc::text, '-'),
                   NULLIF(r.rngsubdiff::regproc::text, '-')
              FROM pg_range r
              JOIN pg_opclass o ON o.oid = r.rngsubopc
              LEFT JOIN pg_collation c ON c.oid = r.rngcollation
             WHERE r.rngtypid = %s
            """,
            (typid,),
        )[0]
        return {
            "subtype": subtype,
            "opclass": opclass,
            "collation": collation,
            "canonical": canonical,
            "subdiff": subdiff,
        }
    return {}  # a multirange follows from its range


def _types(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    # Free-standing types only (domains, enums, composites, ranges); a table's
    # row type and array types follow from the table.
    return {
        f"{schema}.{name}": {"kind": kind, **_type_definition(conn, typid, kind)}
        for typid, schema, name, kind in _rows(
            conn,
            """
            SELECT t.oid, n.nspname, t.typname, t.typtype
              FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname = ANY(%s)
               AND t.typtype IN ('d', 'e', 'c', 'r', 'm')
               AND (t.typrelid = 0
                    OR (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c')
             ORDER BY 2, 3
            """,
            (list(OWNED_SCHEMAS),),
        )
    }


def catalog_snapshot(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """The normalized catalog of a migrated PRKS database."""
    schemas = [
        name
        for (name,) in _rows(
            conn,
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY nspname",
            (list(SCHEMAS),),
        )
    ]
    extensions = {
        name: schema
        for name, schema in _rows(
            conn,
            """
            SELECT e.extname, n.nspname
              FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
             WHERE e.extname <> 'plpgsql'
             ORDER BY e.extname
            """,
        )
    }
    files = [
        f"{version:03d}_{name}.sql"
        for version, name in _rows(
            conn, "SELECT version, name FROM prks.schema_migrations ORDER BY version"
        )
    ]
    tables, views = _relations(conn)
    return {
        "format": SNAPSHOT_FORMAT,
        "schema_files": files,
        "schemas": schemas,
        "extensions": extensions,
        "tables": tables,
        "views": views,
        "functions": _functions(conn),
        "text_search": _text_search(conn),
        "types": _types(conn),
    }


def search_norm_dependents(conn: psycopg.Connection[Any]) -> list[str]:
    """Stored objects that may be computed from ``prks.search_norm``; must be empty (§8.3).

    PostgreSQL records what an SQL-standard (``BEGIN ATOMIC``) body calls, but
    not what a string body (``LANGUAGE sql AS $$…$$``, PL/pgSQL) calls. The
    guard therefore fails closed: it starts from ``prks.search_norm`` *and*
    every string-bodied SQL or PL/pgSQL function outside the system schemas and
    extensions, follows the recorded function-to-function edges, and reports
    every index, column default or generated column, and constraint that
    depends on any of them, as ``"<object> -> <function>"``. A stored value
    computed through a function the check cannot see into is refused like a
    direct use; such a function must be rewritten with a ``BEGIN ATOMIC`` body.
    """
    rows = _rows(
        conn,
        """
        WITH RECURSIVE fns(oid) AS (
          SELECT 'prks.search_norm(text)'::regprocedure::oid
          UNION
          SELECT p.oid
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_language l ON l.oid = p.prolang
           WHERE p.prosqlbody IS NULL
             AND l.lanname IN ('sql', 'plpgsql')
             AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND NOT EXISTS (SELECT 1 FROM pg_depend e
                              WHERE e.classid = 'pg_proc'::regclass AND e.objid = p.oid
                                AND e.deptype = 'e')
          UNION
          SELECT d.objid
            FROM pg_depend d JOIN fns ON d.refobjid = fns.oid
           WHERE d.refclassid = 'pg_proc'::regclass AND d.classid = 'pg_proc'::regclass
        )
        SELECT DISTINCT pg_describe_object(d.classid, d.objid, d.objsubid)
                        || ' -> ' || fns.oid::regprocedure::text
          FROM pg_depend d JOIN fns ON d.refobjid = fns.oid
         WHERE d.refclassid = 'pg_proc'::regclass
           AND d.classid IN ('pg_class'::regclass, 'pg_attrdef'::regclass,
                             'pg_constraint'::regclass, 'pg_index'::regclass)
         ORDER BY 1
        """,
    )
    return [str(row[0]) for row in rows]


def dumps(snapshot: dict[str, Any]) -> str:
    """The checked-in text form: sorted keys, two-space indent, final newline."""
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
