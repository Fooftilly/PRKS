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
_UPDATE = re.compile(r"""\bUPDATE\s+(?:ONLY\s+)?(?:"?(?P<schema>\w+)"?\s*\.\s*)?"""
                     r"""(?:"?(?P<table>\w+)"?|(?P<composed>\{\w*\}|%s|%\(\w+\)s))"""
                     r"""(?:\s*\*)?(?:\s+(?:AS\s+)?(?!SET\b)\w+)?\s+SET\b\s*""", re.IGNORECASE)
# Every UPDATE that is not part of FOR UPDATE, ON CONFLICT DO UPDATE, a
# trigger event or a foreign key action must be a statement _UPDATE reads.
# Any case: PostgreSQL keywords are case-insensitive. An upper-case UPDATE
# the parser cannot read fails the audit; in another case it may be prose
# ("Update the fields"), so it counts only when it reads as a statement.
_UPDATE_WORD = re.compile(r"\bUPDATE\b", re.IGNORECASE)
_NOT_A_STATEMENT_BEFORE = re.compile(r"\b(?:FOR|DO|OR|ON|BEFORE|AFTER|KEY|INSTEAD\s+OF)\s*$", re.IGNORECASE)
_NOT_A_STATEMENT_AFTER = re.compile(r"\s+(?:OF|NOTHING|CASCADE|RESTRICT|NO\s+ACTION|SET\s+(?:NULL|DEFAULT))\b"
                                    r"""|\s*[^\s\w"{%]""", re.IGNORECASE)  # or prose: "UPDATE … SET"
# SQL comments, which PostgreSQL treats as whitespace anywhere in a statement.
_SQL_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_CLAUSE_END = re.compile(r"(?:FROM|WHERE|RETURNING)\b", re.IGNORECASE)
_SINGLE_TARGET = re.compile(r'\s*"?(\w+)"?\s*=', re.DOTALL)
_COMPOSED_COLUMN = re.compile(r"\s*(?:\{\w*\}|%s|%\(\w+\)s)\s*(?:=|$)")  # a column or a whole list
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
    sql = _SQL_COMMENT.sub(" ", sql)
    for word in _UPDATE_WORD.finditer(sql):
        if _NOT_A_STATEMENT_BEFORE.search(sql, 0, word.start()):
            continue
        if not sql[word.end():].strip():  # a fragment: the statement is assembled elsewhere
            if word.group(0) == "UPDATE":
                raise ValueError("unresolved UPDATE fragment: %r" % sql[max(0, word.start() - 40):])
            continue
        if _NOT_A_STATEMENT_AFTER.match(sql, word.end()):
            continue
        update = _UPDATE.match(sql, word.start())
        if update is None and word.group(0) != "UPDATE":
            continue
        if update is None:
            raise ValueError("unresolved UPDATE: %r" % sql[word.start(): word.start() + 80])
        if update.group("composed") or update.group("table") is None:
            found.append(("{composed: %s}" % update.group(0).strip(), set()))  # its columns may be too
            continue
        table = "%s.%s" % ((update.group("schema") or "prks").lower(), update.group("table").lower())
        columns: set[str] = set()
        items = _set_items(sql, update.end())
        if items == [""] and not sql[update.end():].strip():
            found.append((table, {DYNAMIC}))
            continue
        for item in items:
            single, many = _SINGLE_TARGET.match(item), _TUPLE_TARGET.match(item)
            if _COMPOSED_COLUMN.match(item):
                columns.add(DYNAMIC)  # the column is chosen at run time
            elif single:
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

    def test_scope_keys_round_trip_and_never_collide(self) -> None:
        values = ["", "a", "Z9", " ", "/", ":", "|", '"', "\\", "'", "é", "λ", "中", "🔥", 'a","b', "W-a:b", "b:doi"]
        keys = {}
        for work in values:
            for field in values:
                key = meta.scope_key(work, field)
                self.assertEqual(json.loads(key), [work, field])
                self.assertNotIn(key, keys, (work, field))
                keys[key] = (work, field)


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


_PSYCOPG_SQL = {"SQL", "Identifier", "Literal", "Placeholder", "Composed"}


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _render(node: ast.expr) -> str:
    """A SQL expression's text, with each value Python fills in as ``{}``.

    Strings and ``psycopg.sql`` objects compose the same way: an f-string, a
    ``+`` chain, a ``%`` template, ``.format``, ``sql.SQL``, ``sql.Identifier``
    (rendered quoted, as PostgreSQL would see it) and ``sql.Composed``.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_render(part) if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left) + _render(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _render(node.left)  # its %s placeholders stay visible
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == "format" and isinstance(node.func, ast.Attribute) and _is_text(node.func.value):
            return _render(node.func.value)  # its {} placeholders stay visible
        if name == "SQL" and len(node.args) == 1:
            return _render(node.args[0])
        if name == "Identifier":
            parts = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            return ".".join('"%s"' % part for part in parts) if parts and len(parts) == len(node.args) else "{}"
        if name in ("Literal", "Placeholder"):
            return "%s"
        if name == "Composed" and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
            return "".join(_render(part) for part in node.args[0].elts)
    return "{}"


def _is_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call):
        name = _call_name(node)
        return name in _PSYCOPG_SQL or (name in ("format", "join") and isinstance(node.func, ast.Attribute)
                                        and _is_text(node.func.value))
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)) \
        and (_is_text(node.left) or _is_text(node.right))


def _consumed(node: ast.AST) -> list[ast.AST]:
    """The sub-expressions ``_render`` folds into ``node``'s text.

    Anything else inside it (a ``.format`` or ``.join`` argument) is read
    as an expression of its own, so a statement passed there is still seen.
    """
    if not isinstance(node, ast.Call):
        return list(ast.iter_child_nodes(node))
    name = _call_name(node)
    if name in ("format", "join"):
        return [node.func.value] if isinstance(node.func, ast.Attribute) else []
    if name == "Composed" and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
        return list(node.args[0].elts)
    return list(node.args)


def _sql_literals(source: str) -> list[str]:
    """Every string expression of a module, assembled as far as the source shows.

    An f-string, a ``+`` chain, a ``%`` template and ``psycopg.sql``
    composition are rendered whole, with each value Python supplies as
    ``{}``, so a target composed in Python reads as composed instead of as
    unrelated fragments. Docstrings are prose.
    """
    tree = ast.parse(source)
    inner: set[int] = set()
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))
        if _is_text(node):
            inner.update(id(part) for part in _consumed(node))
    return [_render(node) for node in ast.walk(tree)
            if _is_text(node) and id(node) not in inner and id(node) not in docstrings]


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

    def module_offences(self, name: str, source: str) -> tuple[int, list[str]]:
        """``(UPDATE statements read, offences)`` for one module's source."""
        offenders: list[str] = []
        statements = 0
        for literal in _sql_literals(source):
            if _SQLITE_BIND.search(literal):
                continue  # psycopg cannot bind ``?``: SQLite runtime SQL
            try:
                statements += len(_updates(literal))
                offenders.extend(self.offences(name, literal))
            except ValueError as error:
                offenders.append("%s: %s" % (name, error))
        return statements, offenders

    def test_only_the_field_writer_and_the_source_aggregate_update_a_synchronized_column(self) -> None:
        offenders = []
        statements = 0
        for path in sorted((_ROOT / "backend").rglob("*.py")):
            name = path.relative_to(_ROOT).as_posix()
            if name in SQLITE_RUNTIME:
                continue
            read, found = self.module_offences(name, path.read_text(encoding="utf-8"))
            statements += read
            offenders.extend(found)
        self.assertGreater(statements, 10, "the scan reads the SQL it audits")
        self.assertEqual(offenders, [])

    def test_sql_composed_in_python_is_read_whole_or_refused(self) -> None:
        rogue = "backend/domain/rogue.py"
        for source in (
            'sql = f"UPDATE prks.{table} SET status = %(value)s WHERE id = %(id)s"',
            'sql = "UPDATE prks." + table + " SET status = %s"',
            'sql = "UPDATE " + table + " SET author_text = %s"',
            'sql = "UPDATE prks.%s SET status = %%s" % table',
            'sql = "UPDATE {} SET status = %s".format(table)',
            'sql = f"UPDATE prks.works SET {column} = %(value)s"',
            'sql = "UPDATE prks.works SET " + column + " = %s"',
            'sql = "UPDATE prks.works SET %s = %%s" % column',
            'sql = "UPDATE prks.works AS w SET " + ", ".join(parts)',
            'sql = (\n    "UPDATE prks.works w "\n    f"SET author_text = {value!r}"\n)',
            'run(f"UPDATE prks.manifestations SET doi = %s WHERE id = {work_id}")',
            'sql = "update works set status = %s"',
            'sql = "Update manifestations set doi = %s"',
            'sql = "uPdAtE ONLY works AS w sEt author_text = %s"',
            'sql = f"update {table} set status = %s"',
            'sql = "update works set " + column + " = %s"',
            'sql = "update /* change status */ works set status = %s"',
            'sql = "update works /* comment */ set status = %s"',
            'sql = "UPDATE /* comment */ prks.works SET status = %s"',
            'sql = "UPDATE works -- the status\\n SET status = %s"',
            'sql = "UPDATE works SET /* x */ author_text /* y */ = %s"',
            'stmt = sql.SQL("UPDATE ") + sql.Identifier("works") + sql.SQL(" SET status = %s")',
            'stmt = sql.SQL("UPDATE {} SET status = %s").format(sql.Identifier("works"))',
            'stmt = SQL("UPDATE prks.works SET {} = %s").format(Identifier(column))',
            'stmt = sql.Composed([sql.SQL("UPDATE "), sql.Identifier("prks", "manifestations"), sql.SQL(" SET doi = %s")])',
            'stmt = sql.SQL("UPDATE ") + table + sql.SQL(" SET status = %s")',
            'parts = ["UPDATE "]',
            'stmt = sql.SQL(", ").join([sql.SQL("UPDATE prks.works SET status = %s")])',
        ):
            with self.subTest(source=source):
                _read, offenders = self.module_offences(rogue, source)
                self.assertTrue(offenders, source)
        for source in ('"""Merges re-point rows (UPDATE … SET work_id)."""',
                       'NOTE = "re-pointed by the merge (UPDATE … SET work_id)"',
                       'LABEL = "Update Position fields"',
                       'HELP = "update the abstract, then save"',
                       'sql = f"UPDATE prks.jobs SET status = %s WHERE id = {job}"',
                       'sql = "SELECT 1 FROM prks.works FOR UPDATE"'):
            with self.subTest(source=source):
                self.assertEqual(self.module_offences(rogue, source)[1], [])
        _read, offenders = self.module_offences(
            "backend/domain/works/metadata.py", 'sql = f"UPDATE prks.works SET {column} = %(value)s"')
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
            _updates("UPDATE prks.works SET status + 1")

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
