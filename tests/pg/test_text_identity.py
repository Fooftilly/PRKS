"""prks.identity_key and prks.search_norm in PostgreSQL (#624, §8.1, §8.3).

The same vector file as tests/test_text_identity.py, run through SQL, so SQL
and Python agree on every vector. Also the immutability proof: every function
the stored body of prks.identity_key calls is IMMUTABLE, and a generated key
column with a unique index works in a builtin-locale database.
"""

from __future__ import annotations

import re
import unicodedata

import psycopg
from psycopg import sql

from backend.domain.text_identity import WHITESPACE, UnsupportedTextError, identity_key
from tests.pg.harness import PostgresTestCase, fetch_one
from tests.test_text_identity import load_vectors

_FUNCID_RE = re.compile(r":(?:funcid|opfuncid) (\d+)")


class IdentityKeySqlTests(PostgresTestCase):
    def setUp(self) -> None:
        self.conn = self.connect(autocommit=True)

    def sql_key(self, value: str, collation: str | None = None) -> str | None:
        if collation is None:
            row = fetch_one(self.conn.execute("SELECT prks.identity_key(%s::text)", (value,)))
        else:
            row = fetch_one(
                self.conn.execute(
                    sql.SQL("SELECT prks.identity_key(%s::text COLLATE {})").format(
                        sql.Identifier(collation)
                    ),
                    (value,),
                )
            )
        return row[0]

    def test_sql_and_python_agree_on_every_vector(self) -> None:
        for vector in load_vectors():
            with self.subTest(vector["note"]):
                self.assertEqual(self.sql_key(vector["input"]), vector["key"])
                self.assertEqual(identity_key(vector["input"]), vector["key"])

    def test_python_never_returns_a_key_sql_would_not(self) -> None:
        # Unicode 16.0 letters PostgreSQL 18 folds; Python either knows them
        # and agrees, or refuses them (case folding of an assigned character
        # never changes, so unassigned code points are the only skew).
        for codepoint in (0xA7CB, 0xA7CC, 0xA7DC, 0x1C89):
            char = chr(codepoint)
            with self.subTest(codepoint=hex(codepoint)):
                if unicodedata.category(char) == "Cn":
                    with self.assertRaises(UnsupportedTextError):
                        identity_key(char)
                else:
                    self.assertEqual(identity_key(char), self.sql_key(char))

    def test_result_does_not_depend_on_the_callers_collation(self) -> None:
        for vector in load_vectors():
            for collation in ("C", "pg_c_utf8", "ucs_basic"):
                with self.subTest(vector["note"], collation=collation):
                    self.assertEqual(self.sql_key(vector["input"], collation), vector["key"])

    def test_every_whitespace_character_collapses_and_others_do_not(self) -> None:
        for char in WHITESPACE:
            with self.subTest(codepoint=hex(ord(char))):
                value = f"{char}A{char}{char}B{char}"
                self.assertEqual(self.sql_key(value), "a b")
                self.assertEqual(identity_key(value), "a b")
        # Not whitespace for either side: zero width space, Mongolian vowel
        # separator, BOM, and a control character outside the set.
        for codepoint in (0x200B, 0x180E, 0xFEFF, 0x0007):
            with self.subTest(codepoint=hex(codepoint)):
                value = f"A{chr(codepoint)}B"
                self.assertEqual(self.sql_key(value), identity_key(value))
                self.assertIn(chr(codepoint), identity_key(value))

    def test_null_in_null_out(self) -> None:
        self.assertIsNone(fetch_one(self.conn.execute("SELECT prks.identity_key(NULL)"))[0])

    def test_immutability_proof_from_the_stored_body(self) -> None:
        row = fetch_one(
            self.conn.execute(
                "SELECT provolatile, proisstrict, prosqlbody::text FROM pg_proc "
                "WHERE oid = 'prks.identity_key(text)'::regprocedure"
            )
        )
        volatility, strict, body = row
        self.assertEqual(volatility, "i")
        self.assertTrue(strict)
        self.assertIsNotNone(body, "identity_key must have an SQL-standard (BEGIN ATOMIC) body")
        funcids = sorted({int(m) for m in _FUNCID_RE.findall(body)})
        rows = self.conn.execute(
            "SELECT proname, provolatile FROM pg_proc WHERE oid = ANY(%s)", (funcids,)
        ).fetchall()
        called = {name: vol for name, vol in rows}
        self.assertEqual(len(rows), len(funcids))
        for name in ("casefold", "normalize", "regexp_replace", "btrim"):
            self.assertIn(name, called)
        self.assertEqual({vol for vol in called.values()}, {"i"}, called)

    def test_generated_key_column_with_unique_index(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, %s, %s)",
                ("U-" + "A" * 32, "  Stra\u00dfe\u3000Owner ", "Owner"),
            )
            key = fetch_one(conn.execute("SELECT login_key FROM prks.users"))[0]
            self.assertEqual(key, "strasse owner")
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, %s, %s)",
                        ("U-" + "B" * 32, "STRASSE OWNER", "Other"),
                    )
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, %s, %s)",
                        ("U-" + "C" * 32, " \t ", "Blank"),
                    )
            with self.assertRaises(psycopg.errors.GeneratedAlways):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO prks.users (id, login_name, login_key, display_name) "
                        "VALUES (%s, %s, %s, %s)",
                        ("U-" + "D" * 32, "x", "y", "Forged"),
                    )
            conn.rollback()

    def test_database_uses_the_builtin_unicode_locale(self) -> None:
        row = fetch_one(
            self.conn.execute(
                "SELECT datlocprovider, datlocale FROM pg_database WHERE datname = current_database()"
            )
        )
        self.assertEqual(row, ("b", "PG_UNICODE_FAST"))


class SearchNormSqlTests(PostgresTestCase):
    def setUp(self) -> None:
        self.conn = self.connect(autocommit=True)

    def norm(self, value: str | None) -> str | None:
        return fetch_one(self.conn.execute("SELECT prks.search_norm(%s::text)", (value,)))[0]

    def test_folds_case_and_diacritics_after_nfc(self) -> None:
        self.assertEqual(self.norm("\u0106evapi"), "cevapi")
        self.assertEqual(self.norm("C\u0301evapi"), "cevapi")  # NFD input
        self.assertEqual(self.norm("Stra\u00dfe"), "strasse")
        self.assertIsNone(self.norm(None))

    def test_uses_the_one_prks_dictionary(self) -> None:
        row = fetch_one(
            self.conn.execute(
                "SELECT pg_get_functiondef('prks.search_norm(text)'::regprocedure)"
            )
        )
        self.assertIn("'prks.search_unaccent'::regdictionary", row[0])
        self.assertIn("COLLATE pg_unicode_fast", row[0])
