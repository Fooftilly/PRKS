"""The Work metadata field rules the PostgreSQL handlers use (kept by PG-C2 #641).

``backend/work_metadata_sync.py`` holds the field registry, the size and
allowlist contract, the ``thumb_page`` codec, the conflict-result bound the
browser shares, and the scope key. These are pure; ``tests/pg/
test_works_metadata_sync.py`` exercises them through the PostgreSQL boundary.

The writer audit replaces the SQLite source scans of ``UPDATE works SET``:
on PostgreSQL a synchronized field lives on ``prks.works``,
``prks.manifestations`` or ``prks.assets``, and only the field writer
(``backend/domain/works/metadata.py``) and the source aggregate
(``backend/domain/works/source.py``) may set one of those columns on an
existing Work. Any other writer would change a value without advancing its
revision, and every offline device would overwrite it on its next save.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

from backend import work_metadata_sync as meta
from backend.domain.works import metadata as pg_metadata

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TABLES = {"work": "prks.works", "manifestation": "prks.manifestations", "asset": "prks.assets"}
# An UPDATE statement up to its SET, in any form PostgreSQL accepts: ONLY,
# a trailing ``*``, quoted names, an alias with or without AS, a composed
# ``{t}`` target. The SET list is read item by item, so FROM, WHERE and
# RETURNING are all optional.
_UPDATE = re.compile(r"""\bUPDATE\s+(?:ONLY\s+)?"""
                     r"""(?P<target>(?:"?(?P<schema>\w+)"?\s*\.\s*)?"?(?P<table>\w+)"?|\{\w*\})"""
                     r"""(?:\s*\*)?(?:\s+(?:AS\s+)?(?!SET\b)\w+)?\s+SET\b\s*""", re.IGNORECASE)
_CLAUSE_END = re.compile(r"(?:FROM|WHERE|RETURNING)\b", re.IGNORECASE)
_SINGLE_TARGET = re.compile(r'\s*"?(\w+)"?\s*=', re.DOTALL)
_TUPLE_TARGET = re.compile(r'\s*\(([^()]*)\)\s*=', re.DOTALL)
_SQLITE_BIND = re.compile(r"[=(,]\s*\?(?![\w?|&])")
DYNAMIC = "*"  # a SET list the code appends at run time: it may write any column


def _set_items(sql: str, start: int) -> list[str]:
    """The SET clause from ``start``, split on its top-level commas."""
    items, depth, item_start, i, quote = [], 0, start, start, ""
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch == ";" and depth == 0:
            break
        elif depth == 0 and ch == ",":
            items.append(sql[item_start:i])
            item_start = i + 1
        elif depth == 0 and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] in "_.")) \
                and _CLAUSE_END.match(sql, i):
            break
        i += 1
    items.append(sql[item_start:i])
    return items


def _updates(sql: str) -> list[tuple[str, set[str]]]:
    """``(target, assigned columns)`` for every UPDATE statement in ``sql``.

    ``target`` is ``schema.table`` (an unqualified table resolves to
    ``prks``) or the composed placeholder (``{t}``). A SET list built at run
    time yields ``DYNAMIC``. Raises ``ValueError`` for a SET item it cannot
    read, so the audit fails closed.
    """
    found = []
    for update in _UPDATE.finditer(sql):
        if update.group("table") is None:
            found.append((update.group("target"), set()))  # composed: the columns are placeholders as well
            continue
        table = "%s.%s" % ((update.group("schema") or "prks").lower(), update.group("table").lower())
        columns: set[str] = set()
        items = _set_items(sql, update.end())
        if items == [""] and not sql[update.end():].strip():
            found.append((table, {DYNAMIC}))
            continue
        for item in items:
            single, many = _SINGLE_TARGET.match(item), _TUPLE_TARGET.match(item)
            if single:
                columns.add(single.group(1).lower())
            elif many:
                columns.update(part.strip().strip('"').lower() for part in many.group(1).split(","))
            else:
                raise ValueError("unparsed SET item in %r: %r" % (sql[update.start(): update.start() + 80], item))
        found.append((table, columns))
    return found


class RegistryTests(unittest.TestCase):
    def test_the_synchronized_fields(self) -> None:
        self.assertEqual(sorted(meta.SYNCED_FIELDS),
                         ["abstract", "author_text", "doc_type", "doi", "edition", "isbn",
                          "issue", "journal", "location", "pages", "published_date",
                          "publisher", "source_url", "status", "thumb_page", "title",
                          "volume", "year"])
        for field in meta.SYNCED_FIELDS:
            self.assertIn(field, pg_metadata.FIELD_COLUMNS, field)

    def test_the_projection_map_names_every_dependency(self) -> None:
        self.assertEqual(sorted(meta.FIELD_PROJECTIONS),
                         ["abstract", "author_text", "doc_type", "published_date",
                          "publisher", "source_url", "status", "thumb_page", "title", "year"])
        self.assertEqual(meta.FIELD_PROJECTIONS["abstract"], ("works-browse",))
        self.assertEqual(meta.FIELD_PROJECTIONS["publisher"], ("recently-added",))
        for field in ("year", "published_date", "status", "author_text", "thumb_page",
                      "doc_type", "title", "source_url"):
            self.assertEqual(meta.FIELD_PROJECTIONS[field], ("works-browse", "recent", "recently-added"), field)

    def test_embedded_summary_fields_are_named(self) -> None:
        self.assertEqual(sorted(meta.SUMMARY_FIELDS),
                         ["author_text", "doc_type", "published_date", "publisher",
                          "source_url", "status", "thumb_page", "title", "year"])
        self.assertEqual(meta.SUMMARY_ENTITY_KINDS, ("folder", "person", "playlist"))
        self.assertTrue(set(meta.SUMMARY_FIELDS) <= set(meta.SYNCED_FIELDS))

    def test_the_documented_bounds(self) -> None:
        self.assertEqual(sorted(meta.BYTE_LIMITED_FIELDS), ["abstract", "author_text", "source_url", "title"])
        self.assertEqual(sorted(meta.BYTE_LIMITS), sorted(meta.BYTE_LIMITED_FIELDS))
        for field, limit in meta.BYTE_LIMITS.items():
            self.assertEqual(meta.SYNCED_FIELDS[field], limit, field)
        self.assertEqual((meta.MAX_AUTHOR_TEXT_UTF8_BYTES, meta.MAX_TITLE_UTF8_BYTES,
                          meta.MAX_SOURCE_URL_UTF8_BYTES, meta.MAX_ABSTRACT_UTF8_BYTES),
                         (64 * 1024, 64 * 1024, 64 * 1024, 1024 * 1024))
        self.assertEqual((meta.SYNCED_FIELDS["publisher"], meta.SYNCED_FIELDS["location"]), (500, 500))
        self.assertEqual(meta.FIELD_KIND_GUARDS, {"source_url": "video"})

    def test_byte_limited_fields_count_utf8_bytes_and_small_ones_code_points(self) -> None:
        for field in ("author_text", "title"):
            with self.subTest(field=field):
                limit = meta.SYNCED_FIELDS[field]
                self.assertTrue(meta.is_valid_field_value(field, "x" * limit))
                self.assertFalse(meta.is_valid_field_value(field, "x" * (limit + 1)))
                self.assertFalse(meta.is_valid_field_value(field, "日" * 30000))
                self.assertTrue(meta.is_valid_field_value(field, "日" * 20000))
        self.assertTrue(meta.is_valid_field_value("journal", "日" * meta.SYNCED_FIELDS["journal"]))
        for good in meta.WORK_STATUSES:
            self.assertTrue(meta.is_valid_field_value("status", good), good)


class CodecTests(unittest.TestCase):
    def test_the_input_contract_is_explicit_for_page_and_identity_fields(self) -> None:
        for value, accepted in ((3, True), ("3", True), (None, True), (0, False), ("1.5", False), (True, False)):
            with self.subTest(value=value):
                self.assertIs(meta.is_valid_field_value("thumb_page", value), accepted)
        identity = meta.codec_for("title")
        self.assertTrue(identity.is_valid_input("Some Work"))
        self.assertFalse(identity.is_valid_input(None))

    def test_the_thumb_page_codec_accepts_only_page_numbers(self) -> None:
        codec = meta.codec_for("thumb_page")
        for good, expected in (("", None), ("1", 1), ("3", 3), ("003", 3), (" 3 ", 3), ("10", 10), ("  ", None)):
            with self.subTest(wire=good):
                self.assertTrue(codec.is_valid_wire(good))
                self.assertEqual(codec.to_database(good), expected)
        for bad in ("0", "-1", "1.5", "abc", "3abc", "+3", "３", "1_0", "٣", "1e3"):
            with self.subTest(wire=bad):
                self.assertFalse(codec.is_valid_wire(bad))
                self.assertIsNone(codec.to_database(bad))
        self.assertEqual((codec.to_wire(None), codec.to_wire(3), codec.to_wire(0), codec.to_wire(True)),
                         ("", "3", "", ""))

    def test_doc_type_normalizes_to_the_canonical_set(self) -> None:
        self.assertEqual(sorted(meta.DOC_TYPE_SET), sorted(meta.DOC_TYPES))
        self.assertIn("misc", meta.DOC_TYPE_SET)
        self.assertEqual((meta.normalize_doc_type("BOOK"), meta.normalize_doc_type("  Article ")), ("book", "article"))
        for unknown in ("bogus", "", None, "  ", 7):
            self.assertEqual(meta.normalize_doc_type(unknown), "misc", repr(unknown))

    def test_scope_keys_are_structural(self) -> None:
        self.assertNotEqual(meta.scope_key("W-a:b", "doi"), meta.scope_key("W-a", "b:doi"))
        self.assertEqual(json.loads(meta.scope_key("W-a", "doi")), ["W-a", "doi"])


class ResultBoundTests(unittest.TestCase):
    def test_the_server_and_the_browser_agree_on_the_result_bound(self) -> None:
        self.assertEqual(meta.MAX_DURABLE_RESULT_BYTES, 2048)
        store = (_ROOT / "frontend" / "js" / "local-store.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_RESULT_BYTES = %d;" % meta.MAX_DURABLE_RESULT_BYTES, store)

    def test_a_preview_never_splits_a_surrogate_pair(self) -> None:
        result = meta.fit_terminal_result({"code": "REVISION_CONFLICT", "current_revision": 1,
                                           **meta.disagreement("author_text", "\U0001f9ea" * 5000, "D")},
                                          "\U0001f9ea" * 5000, "D")
        preview = result["current_preview"]
        self.assertTrue(preview)
        self.assertEqual(preview, "\U0001f9ea" * len(preview))
        self.assertLessEqual(meta.serialized_result_bytes(result), meta.MAX_DURABLE_RESULT_BYTES)


def _sql_literals(path: pathlib.Path) -> list[str]:
    """Every string literal of a module, with literal concatenations joined."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.append("".join(part.value for part in node.values
                                 if isinstance(part, ast.Constant) and isinstance(part.value, str)))
    return found


# The SQLite runtime PG-C2 deletes; its statements never reach PostgreSQL.
SQLITE_RUNTIME = {"backend/db_manager.py", "backend/db_migrations.py"}


class WriterAuditTests(unittest.TestCase):
    SANCTIONED = {"backend/domain/works/metadata.py", "backend/domain/works/source.py"}

    def synced_columns(self) -> dict[str, set[str]]:
        columns: dict[str, set[str]] = {table: set() for table in _TABLES.values()}
        for field in meta.SYNCED_FIELDS:
            owner, column = pg_metadata.FIELD_COLUMNS[field]
            columns[_TABLES[owner]].add(column)
        columns["prks.assets"].add("url")  # a video's source_url is its stream's URL
        return columns

    # Modules whose UPDATE targets are composed (``UPDATE {t}``), with what
    # those targets are. A new composed target must be read and added here.
    COMPOSED = {
        # restore renumbers key columns in its private staging schema
        "backend/domain/maintenance/staging.py",
    }

    def offences(self, name: str, literal: str) -> list[str]:
        columns = self.synced_columns()
        offenders = []
        for table, written in _updates(literal):
            if table.startswith("{"):
                if name not in self.COMPOSED:
                    offenders.append("%s: composed UPDATE %s" % (name, table))
                continue
            synced = columns.get(table, set())
            hits = synced if DYNAMIC in written else written & synced
            if hits and name not in self.SANCTIONED:
                offenders.append("%s: %s %s" % (name, table, sorted(hits)))
        return offenders

    def test_only_the_field_writer_and_the_source_aggregate_update_a_synchronized_column(self) -> None:
        offenders = []
        statements = 0
        for path in sorted((_ROOT / "backend").rglob("*.py")):
            name = path.relative_to(_ROOT).as_posix()
            if name in SQLITE_RUNTIME:
                continue
            for literal in _sql_literals(path):
                if _SQLITE_BIND.search(literal):
                    continue  # psycopg cannot bind ``?``: SQLite runtime SQL
                try:
                    statements += len(_updates(literal))
                    offenders.extend(self.offences(name, literal))
                except ValueError as error:
                    offenders.append("%s: %s" % (name, error))
        self.assertGreater(statements, 10, "the scan reads the SQL it audits")
        self.assertEqual(offenders, [])

    def test_every_valid_update_form_of_a_synchronized_column_is_caught(self) -> None:
        rogue = "backend/domain/rogue.py"
        for sql in (
            "UPDATE prks.works SET status = %s WHERE id = %s",
            "UPDATE prks.works AS w SET status = %s WHERE w.id = %s",
            "UPDATE prks.works w SET status = %s",
            "UPDATE prks.works SET status = %s",
            "UPDATE prks.works SET status = %s RETURNING id",
            "update ONLY prks.works * set author_text = %s from prks.people p where true",
            'UPDATE "prks"."works" SET "author_text" = %s',
            "UPDATE prks.works SET revision = revision + 1, author_text = %s",
            "UPDATE prks.works SET seen = (SELECT max(x) FROM t WHERE a = 1), status = %s",
            "UPDATE prks.works SET (status, author_text) = (%s, %s) WHERE id = %s",
            "UPDATE prks.manifestations m SET doi = %s",
            "WITH x AS (SELECT 1) UPDATE prks.works SET status = 'read'",
            "UPDATE works SET status = %s",  # the search_path resolves it to prks
            "UPDATE prks.works SET ",  # a SET list appended at run time
        ):
            with self.subTest(sql=sql):
                self.assertTrue(self.offences(rogue, sql), sql)
                self.assertEqual(self.offences("backend/domain/works/metadata.py", sql), [])
        for sql in ("UPDATE prks.works SET revision = revision + 1 WHERE id = %s",
                    "UPDATE prks.jobs j SET status = 'done'",
                    "UPDATE derived.work_search s SET doc = %s FROM prks.works w",
                    "INSERT INTO prks.works (status) VALUES (%s) ON CONFLICT (id) DO UPDATE SET x = 1",
                    "SELECT 1 FROM prks.works FOR UPDATE OF w"):
            with self.subTest(sql=sql):
                self.assertEqual(self.offences(rogue, sql), [])
        self.assertTrue(self.offences(rogue, "UPDATE {t} SET {c} = 1"))
        with self.assertRaises(ValueError):
            _updates("UPDATE prks.works SET %s = 1")

    def test_the_field_writer_builds_its_statements_from_the_field_registry_only(self) -> None:
        owned = {column for _owner, column in pg_metadata.FIELD_COLUMNS.values()} | {"url", "media_type"}
        for statements in (pg_metadata._SET_WORK, pg_metadata._SET_MANIFESTATION, pg_metadata._SET_ASSET):
            for column in statements:
                self.assertIn(column, owned)

    def test_the_oembed_author_fill_only_reaches_a_create(self) -> None:
        server = (_ROOT / "backend" / "server.py").read_text(encoding="utf-8")
        at = server.index("If author_text not provided, fill from oEmbed author_name.")
        window = server[at: at + 4000]
        self.assertIn("_create_postgres_work(", window)
        self.assertNotIn("update_work_metadata", window)
        create = server[server.index("def _create_postgres_work("):]
        create = create[: create.index("\ndef ", 1)]
        self.assertIn("db.add_work(", create)
        self.assertIn("db._add_work_with_pdf(", create)
        self.assertNotIn("update_work_metadata", create)


if __name__ == "__main__":
    unittest.main()
