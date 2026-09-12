"""The client's excerpt must equal the server's, character for character.

Progress renders `abstract_excerpt`, which the server derives with SQLite's
`SUBSTR(COALESCE(abstract, ''), 1, 100)`. A pending local Abstract has to
produce the same excerpt the server will send back once it acknowledges, or the
card would visibly change at acknowledgement for no reason the user caused.

SQLite counts CHARACTERS (code points). JavaScript's `slice`/`substring` count
UTF-16 code units, which differ for every astral character -- emoji, historic
scripts, many maths symbols. This pins that the client helper matches SQLite
rather than matching JavaScript's default.
"""
import json
import pathlib
import sqlite3
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_MODULE = ROOT / "frontend" / "js" / "work-metadata-state.js"
EXCERPT_LEN = 100

CASES = {
    "ascii": "The quick brown fox jumps over the lazy dog. " * 6,
    "accented_latin": "Café naïve résumé Ångström Łódź coöperate façade señor " * 4,
    "cjk": "量子力学の基礎的な考え方について論じる。" * 12,
    "emoji": "Result 🧪🔬🧬 analysis 😀😃😄 outcome 🚀🌍 " * 8,
    "mixed_bmp_and_astral": "ASCII 漢字 café 🧪 é " * 20,
    "combining_marks": "école à la mode ñino über " * 10,
    "astral_exactly_at_100": "y" * 99 + "🧪" + " tail",
    "astral_spanning_100": "y" * 98 + "🧪🧪" + " tail",
    "exactly_100_code_points": "x" * EXCERPT_LEN,
    "one_over_100": "x" * (EXCERPT_LEN + 1),
    "all_astral_over_limit": "🧪" * 150,
    "shorter_than_limit": "Too short to truncate.",
    "empty": "",
}

JS = """
const path = process.argv[1];
require(path);
const cases = JSON.parse(process.argv[2]);
const out = {};
for (const [name, text] of Object.entries(cases)) out[name] = globalThis.prksAbstractExcerpt(text);
process.stdout.write(JSON.stringify(out));
"""


def sqlite_excerpts(cases):
    """Exactly the expression `_prks_work_browse_select()` emits."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE works (id TEXT, abstract TEXT)")
    conn.executemany("INSERT INTO works VALUES (?, ?)", list(cases.items()))
    rows = conn.execute(
        "SELECT id, SUBSTR(COALESCE(abstract, ''), 1, %d) FROM works" % EXCERPT_LEN
    ).fetchall()
    conn.close()
    return dict(rows)


def js_excerpts(cases):
    proc = subprocess.run(
        ["node", "-e", JS, str(STATE_MODULE), json.dumps(cases)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return json.loads(proc.stdout)


class AbstractExcerptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = sqlite_excerpts(CASES)
        cls.actual = js_excerpts(CASES)

    def test_client_excerpt_equals_the_server_projection(self):
        for name in CASES:
            with self.subTest(case=name):
                self.assertEqual(self.actual[name], self.expected[name])

    def test_the_excerpt_is_bounded_in_code_points_not_utf16_units(self):
        for name, text in CASES.items():
            with self.subTest(case=name):
                self.assertLessEqual(len(self.actual[name]), EXCERPT_LEN)
                if len(text) > EXCERPT_LEN:
                    self.assertEqual(len(self.actual[name]), EXCERPT_LEN,
                                     "a long abstract must yield a full-length excerpt")

    def test_utf16_slicing_would_have_been_wrong(self):
        """The reason this contract test exists: JavaScript's obvious
        implementation disagrees with SQLite on every astral input.

        Python slices by code point, so JS must be modelled explicitly --
        encode to UTF-16 and cut units, the way `String.prototype.slice` does.
        """
        def utf16_slice(text, limit):
            units = text.encode("utf-16-le")[: limit * 2]
            return units.decode("utf-16-le", errors="replace")

        diverged = [name for name, text in CASES.items()
                    if utf16_slice(text, EXCERPT_LEN) != self.expected[name]]
        self.assertTrue(diverged, "expected at least one case where naive slicing differs")
        for name in ("emoji", "all_astral_over_limit", "astral_spanning_100"):
            self.assertIn(name, diverged, name)

    def test_no_excerpt_ends_mid_surrogate_pair(self):
        """A split pair renders as a replacement glyph. Iterating code points
        makes that unrepresentable; this pins it rather than assuming it."""
        for name, value in self.actual.items():
            with self.subTest(case=name):
                if not value:
                    continue
                self.assertFalse(0xD800 <= ord(value[-1]) <= 0xDBFF,
                                 "excerpt ends in a high surrogate")

    def test_the_server_length_constant_is_the_one_the_client_uses(self):
        from backend.db_manager import _PRKS_ABSTRACT_EXCERPT_LEN
        source = STATE_MODULE.read_text(encoding="utf-8")
        self.assertEqual(_PRKS_ABSTRACT_EXCERPT_LEN, EXCERPT_LEN)
        self.assertIn("const EXCERPT_CODE_POINTS = %d;" % EXCERPT_LEN, source)

    def test_progress_consumes_the_server_excerpt_without_re_truncating(self):
        """The bug this replaced: a second `substring(0, 100)` over an already
        bounded excerpt cut it by UTF-16 units and could split a pair."""
        progress = (ROOT / "frontend" / "js" / "components" / "progress.js").read_text()
        at = progress.index("const excerpt =")
        body = progress[at: at + 400]
        self.assertNotIn("substring(0, 100)", body)
        self.assertNotIn("slice(0, 100)", body)
        self.assertIn("prksAbstractExcerpt(", body)
