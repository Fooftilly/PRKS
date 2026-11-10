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
_UPDATE = re.compile(r"UPDATE\s+(prks\.\w+)\s+SET\s+(.*?)\s+WHERE\b", re.IGNORECASE | re.DOTALL)


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


class WriterAuditTests(unittest.TestCase):
    SANCTIONED = {"backend/domain/works/metadata.py", "backend/domain/works/source.py"}

    def synced_columns(self) -> dict[str, set[str]]:
        columns: dict[str, set[str]] = {table: set() for table in _TABLES.values()}
        for field in meta.SYNCED_FIELDS:
            owner, column = pg_metadata.FIELD_COLUMNS[field]
            columns[_TABLES[owner]].add(column)
        columns["prks.assets"].add("url")  # a video's source_url is its stream's URL
        return columns

    def test_only_the_field_writer_and_the_source_aggregate_update_a_synchronized_column(self) -> None:
        columns = self.synced_columns()
        offenders = []
        statements = 0
        for path in sorted((_ROOT / "backend").rglob("*.py")):
            name = path.relative_to(_ROOT).as_posix()
            if name == "backend/db_manager.py":  # the SQLite runtime PG-C2 deletes
                continue
            for literal in _sql_literals(path):
                for table, assignments in _UPDATE.findall(literal):
                    statements += 1
                    written = set(re.findall(r"(?:^|,)\s*(\w+)\s*=", assignments))
                    hits = written & columns.get(table.lower(), set())
                    if hits and name not in self.SANCTIONED:
                        offenders.append("%s: %s %s" % (name, table, sorted(hits)))
        self.assertGreater(statements, 10, "the scan reads the SQL it audits")
        self.assertEqual(offenders, [])

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
