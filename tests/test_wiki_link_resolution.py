"""Batched wiki-link resolution (#124).

``resolve_wiki_links`` must render exactly what the per-marker implementation
rendered -- Work before Person, first matching row in table order -- while
issuing statements per distinct target set rather than per marker.
"""
import html
import re
import tempfile
import unittest
from unittest.mock import patch

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


def legacy_resolve_wiki_links(db, text):
    """The pre-#124 per-marker implementation, kept as the output oracle."""
    if not text:
        return ""

    def replacer(match):
        raw = match.group(1).strip()
        res = db.execute_query("SELECT id, title as name FROM works WHERE id=? OR title=?", (raw, raw))
        if res:
            safe_name = html.escape(str(res[0]["name"] or ""), quote=True)
            safe_id = html.escape(str(res[0]["id"] or ""), quote=True)
            return f'<a href="#/works/{safe_id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{safe_name}</a>'
        res2 = db.execute_query("SELECT id, (first_name || ' ' || last_name) as name FROM persons WHERE id=? OR last_name=?", (raw, raw))
        if res2:
            safe_name = html.escape(str(res2[0]["name"] or ""), quote=True)
            safe_id = html.escape(str(res2[0]["id"] or ""), quote=True)
            return f'<a href="#/people/{safe_id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{safe_name}</a>'
        safe_raw = html.escape(raw, quote=True)
        return f'<span class="wiki-link-unresolved" style="color:#ef4444;">[[{safe_raw}]]</span>'

    return re.sub(r"\[\[(.*?)\]\]", replacer, text)


class WikiLinkResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-wiki-links-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))

    def work(self, work_id, title):
        self.db.execute_query("INSERT INTO works (id, title) VALUES (?, ?)", (work_id, title))

    def person(self, person_id, last_name, first_name=None):
        self.db.execute_query(
            "INSERT INTO persons (id, first_name, last_name) VALUES (?, ?, ?)",
            (person_id, first_name, last_name),
        )

    def selects(self, call):
        """Run ``call`` and return the SELECT statements it issued."""
        sqls = []
        real_get = self.db.get_connection

        def traced():
            conn = real_get()
            conn.set_trace_callback(sqls.append)
            return conn

        with patch.object(self.db, "get_connection", side_effect=traced):
            result = call()
        return result, [s for s in sqls if s.lstrip().upper().startswith("SELECT")]

    def seed_ambiguous_library(self):
        # Two Works share a title: the first inserted one wins.
        self.work("W-ALPHA-1", "Alpha")
        self.work("W-ALPHA-2", "Alpha")
        # A Work whose id is another Work's title: the earlier row wins.
        self.work("W-ID-AS-TITLE", "W-LATE")
        self.work("W-LATE", "Late title")
        # A Work and a Person share a name: the Work wins.
        self.work("W-SHARED", "Shared")
        self.person("P-SHARED", "Shared", "Pat")
        # Persons resolve by id or last name; first row wins on duplicates.
        self.person("P-KANT-1", "Kant", "Immanuel")
        self.person("P-KANT-2", "Kant", "Other")
        self.person("P-NOFIRST", "Solo")
        self.person("P-ESC", 'Quote"<b>', "Amp&")
        self.work("W-ESC", '<script>"x"</script>')

    def assert_matches_legacy(self, text):
        out = self.db.resolve_wiki_links(text)
        self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
        return out

    def test_single_work_link(self):
        self.work("W-1", "Critique of Pure Reason")
        out = self.assert_matches_legacy("See [[Critique of Pure Reason]].")
        self.assertEqual(
            out,
            'See <a href="#/works/W-1" class="wiki-link" style="color:var(--accent); '
            'text-decoration:none;">Critique of Pure Reason</a>.',
        )

    def test_single_person_link(self):
        self.person("P-1", "Kant", "Immanuel")
        out = self.assert_matches_legacy("By [[Kant]].")
        self.assertEqual(
            out,
            'By <a href="#/people/P-1" class="wiki-link" style="color:var(--accent); '
            'text-decoration:none;">Immanuel Kant</a>.',
        )

    def test_unresolved_target(self):
        out = self.assert_matches_legacy("[[Nobody <here>]]")
        self.assertEqual(
            out,
            '<span class="wiki-link-unresolved" style="color:#ef4444;">[[Nobody &lt;here&gt;]]</span>',
        )

    def test_multiple_distinct_markers(self):
        self.work("W-1", "Alpha")
        self.work("W-2", "Beta")
        self.person("P-1", "Gamma", "G")
        out = self.assert_matches_legacy("[[Alpha]], [[Beta]], [[Gamma]] and [[Delta]]")
        self.assertIn('href="#/works/W-1"', out)
        self.assertIn('href="#/works/W-2"', out)
        self.assertIn('href="#/people/P-1"', out)
        self.assertIn("wiki-link-unresolved", out)

    def test_case_and_whitespace_semantics(self):
        """Lookups are exact (case-sensitive); only the ends of a target are trimmed."""
        self.work("W-1", "Alpha Beta")
        self.person("P-1", "Kant", "Immanuel")
        cases = {
            "[[  Alpha Beta\t]]": "#/works/W-1",
            "[[Kant ]]": "#/people/P-1",
            "[[alpha beta]]": None,
            "[[ALPHA BETA]]": None,
            "[[Alpha  Beta]]": None,
            "[[kant]]": None,
        }
        for text, href in cases.items():
            with self.subTest(text=text):
                out = self.assert_matches_legacy(text)
                if href:
                    self.assertIn(f'href="{href}"', out)
                else:
                    self.assertIn("wiki-link-unresolved", out)

    def test_work_detail_html_content_is_unchanged(self):
        self.seed_ambiguous_library()
        text = "Notes on [[Alpha]], [[Kant]], [[Shared]] and [[nobody]]. [[Alpha]] again."
        self.db.execute_query("UPDATE works SET text_content = ? WHERE id = ?", (text, "W-LATE"))
        work = self.db.get_work("W-LATE")
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(work["html_content"], legacy_resolve_wiki_links(self.db, text))

    def test_output_matches_per_marker_implementation(self):
        self.seed_ambiguous_library()
        text = (
            "Intro [[Alpha]] and [[ Alpha ]] again [[W-ALPHA-2]].\n"
            "[[W-LATE]] [[Late title]] [[Shared]] [[P-SHARED]]\n"
            "[[Kant]] [[P-KANT-2]] [[Solo]] [[Quote\"<b>]] [[<script>\"x\"</script>]]\n"
            "[[missing <thing>]] [[]] [[  ]] [[a]][[b]] [[nested [[Alpha]]]] [[Alpha"
        )
        self.assertEqual(
            self.db.resolve_wiki_links(text),
            legacy_resolve_wiki_links(self.db, text),
        )

    def test_precedence_and_first_row_are_explicit(self):
        self.seed_ambiguous_library()
        out = self.db.resolve_wiki_links("[[Alpha]] [[Shared]] [[Kant]] [[W-LATE]]")
        self.assertIn('href="#/works/W-ALPHA-1"', out)
        self.assertNotIn("W-ALPHA-2", out)
        self.assertIn('href="#/works/W-SHARED"', out)
        self.assertNotIn("P-SHARED", out)
        self.assertIn('href="#/people/P-KANT-1"', out)
        self.assertIn('href="#/works/W-ID-AS-TITLE"', out)

    def test_text_without_markers_is_returned_unchanged_without_queries(self):
        for text in ("", "plain text", "[single] brackets ]]"):
            with self.subTest(text=text):
                out, selects = self.selects(lambda t=text: self.db.resolve_wiki_links(t))
                self.assertEqual(out, text)
                self.assertEqual(selects, [])

    def test_repeated_markers_cost_one_lookup_per_table(self):
        self.seed_ambiguous_library()
        text = " ".join(["[[Alpha]]"] * 100 + ["[[Kant]]"] * 100 + ["[[nobody]]"] * 100)
        out, selects = self.selects(lambda: self.db.resolve_wiki_links(text))
        self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
        self.assertEqual(len(selects), 2)
        self.assertIn("FROM works", selects[0])
        self.assertIn("FROM persons", selects[1])

    def test_more_occurrences_of_the_same_markers_add_no_queries(self):
        self.seed_ambiguous_library()
        mixed = "[[Alpha]] [[Kant]] [[nobody]] "
        counts = []
        for repeat in (1, 10, 500):
            text = mixed * repeat
            out, selects = self.selects(lambda t=text: self.db.resolve_wiki_links(t))
            self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
            counts.append(len(selects))
        self.assertEqual(counts, [2, 2, 2])

    def test_more_distinct_markers_stay_within_one_chunk_per_table(self):
        for i in range(300):
            self.work(f"W-{i:04d}", f"Work {i}")
            self.person(f"P-{i:04d}", f"Person {i}")
        counts = []
        for distinct in (1, 30, 300):
            text = " ".join(
                f"[[Work {i}]] [[Person {i}]] [[Gone {i}]]" for i in range(distinct)
            )
            out, selects = self.selects(lambda t=text: self.db.resolve_wiki_links(t))
            self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
            counts.append(len(selects))
        # At 300 the 900 distinct targets were 900 Works lookups plus 600
        # Persons lookups before; now ceil(900 / 400) Works statements plus
        # ceil(600 / 400) Persons statements.
        self.assertEqual(counts, [2, 2, 3 + 2])

    def test_work_only_targets_skip_the_person_lookup(self):
        self.seed_ambiguous_library()
        text = "[[Alpha]] [[Shared]] [[W-LATE]] " * 50
        out, selects = self.selects(lambda: self.db.resolve_wiki_links(text))
        self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
        self.assertEqual(len(selects), 1)
        self.assertIn("FROM works", selects[0])

    def test_first_row_wins_even_when_its_targets_straddle_chunks(self):
        """A row matching targets in different chunks claims each independently."""
        self.seed_ambiguous_library()
        # One Work answers to two targets (its id and its title); an earlier
        # Work shares the second target as its title.
        self.work("W-ONE", "SharedTitle")
        self.work("W-TWO", "W-ONE")
        text = (
            "[[W-ONE]] [[SharedTitle]] [[Alpha]] [[W-ALPHA-2]] [[Kant]] [[P-KANT-2]] "
            "[[Shared]] [[W-LATE]] [[Late title]] [[W-ID-AS-TITLE]] [[nobody]] [[Solo]]"
        )
        expected = legacy_resolve_wiki_links(self.db, text)
        for chunk in (1, 2, 3, 5, 400):
            with self.subTest(chunk=chunk), patch.object(
                PRKSDatabase, "_WIKI_LINK_LOOKUP_CHUNK", chunk
            ):
                self.assertEqual(self.db.resolve_wiki_links(text), expected)
                # Reversed order puts the same targets into different chunks.
                reversed_text = " ".join(reversed(text.split(" ")))
                self.assertEqual(
                    self.db.resolve_wiki_links(reversed_text),
                    legacy_resolve_wiki_links(self.db, reversed_text),
                )

    def test_many_distinct_targets_use_bounded_chunks(self):
        chunk = PRKSDatabase._WIKI_LINK_LOOKUP_CHUNK
        count = chunk * 2 + 7
        for i in range(0, count, 3):
            self.work(f"W-{i:05d}", f"Work {i}")
        for i in range(1, count, 3):
            self.person(f"P-{i:05d}", f"Person {i}")
        names = []
        for i in range(count):
            names.append(f"Work {i}" if i % 3 == 0 else f"Person {i}" if i % 3 == 1 else f"Gone {i}")
        text = " ".join(f"[[{name}]]" for name in names)
        out, selects = self.selects(lambda: self.db.resolve_wiki_links(text))
        self.assertEqual(out, legacy_resolve_wiki_links(self.db, text))
        work_selects = [s for s in selects if "FROM works" in s]
        person_selects = [s for s in selects if "FROM persons" in s]
        self.assertEqual(len(work_selects), 3)
        # Only the targets no Work claimed reach the Person lookup.
        unresolved_after_works = count - len(range(0, count, 3))
        self.assertEqual(len(person_selects), -(-unresolved_after_works // chunk))


if __name__ == "__main__":
    unittest.main()
