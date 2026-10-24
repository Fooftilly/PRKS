"""Wiki-link resolution: PostgreSQL renders SQLite's HTML (L-W-2 #631).

Each Library is built the same way on this leg's library and on a reference
SQLite library, in the same order and one command at a time, so "first
created" means the same row on both. A text names ids through ``{label}``
placeholders filled with each side's own ids, and the two renderings must be
equal once generated ids become their labels.
``tests/test_wiki_link_resolution.py`` pins SQLite's rules against the
per-marker oracle; ``tests/pg/test_works_wiki_links.py`` covers what only
PostgreSQL has.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from tests.backend_parity import parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase

Pair = tuple[str, str]
_LINK = '<a href="#/{kind}/{id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{name}</a>'
_UNRESOLVED = '<span class="wiki-link-unresolved" style="color:#ef4444;">[[{raw}]]</span>'


@parametrize_backends
class WikiLinkParityTests(PeopleParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids: dict[str, Pair] = {}

    def work(self, label: str, title: str, **fields: Any) -> Pair:
        pair = (self.leg.work(label, title=title, **fields), self.ref.work(label, title=title, **fields))
        self.ids[label] = pair
        return pair

    def person(self, label: str, last_name: str, first_name: str = "") -> Pair:
        pair = self.person_both(label, first_name=first_name, last_name=last_name)
        self.ids[label] = pair
        return pair

    def texts(self, template: str) -> Pair:
        """``template`` with ``{label}`` filled by each side's id."""
        return (template.format(**{label: ids[0] for label, ids in self.ids.items()}),
                template.format(**{label: ids[1] for label, ids in self.ids.items()}))

    def render(self, template: str) -> str:
        """Both sides' HTML for ``template``; asserts they match and returns the normal form."""
        mine, theirs = self.texts(template)
        out = self.leg.normal(self.leg.db.resolve_wiki_links(mine))
        self.assertEqual(out, self.ref.normal(self.ref.db.resolve_wiki_links(theirs)))
        return out

    @staticmethod
    def link(kind: str, label: str, name: str) -> str:
        prefix = "W" if kind == "works" else "P"
        return _LINK.format(kind=kind, id=f"<{prefix} {label}>", name=name)

    def seed(self) -> None:
        # Created in this order on both sides: "first" below is the earlier one.
        self.work("alpha1", "Alpha")
        self.work("alpha2", "Alpha")
        self.work("shared_w", "Shared")
        self.person("shared_p", "Shared", "Pat")
        self.person("kant1", "Kant", "Immanuel")
        self.person("kant2", "Kant", "Other")
        self.person("solo", "Solo")
        self.work("critique", "Critique of Pure Reason")

    # -- resolution

    def test_works_and_people_by_id_and_by_name(self) -> None:
        self.seed()
        out = self.render("[[Critique of Pure Reason]] [[{critique}]] [[Kant]] [[{kant2}]] [[Solo]]")
        self.assertEqual(out, " ".join((
            self.link("works", "critique", "Critique of Pure Reason"),
            self.link("works", "critique", "Critique of Pure Reason"),
            self.link("people", "kant1", "Immanuel Kant"),
            self.link("people", "kant2", "Other Kant"),
            self.link("people", "solo", " Solo"))))

    def test_a_work_wins_over_a_person_and_the_first_row_wins(self) -> None:
        self.seed()
        out = self.render("[[Shared]] [[{shared_p}]] [[Alpha]] [[{alpha2}]] [[Kant]]")
        self.assertEqual(out, " ".join((
            self.link("works", "shared_w", "Shared"),
            self.link("people", "shared_p", "Pat Shared"),
            self.link("works", "alpha1", "Alpha"),
            self.link("works", "alpha2", "Alpha"),
            self.link("people", "kant1", "Immanuel Kant"))))

    def test_an_id_that_is_another_rows_name(self) -> None:
        # The earlier Work answers to the later Work's id by its title.
        early = self.work("early", "placeholder")
        late = self.work("late", "Late title")
        for index, side in enumerate(self.sides()):
            side.db.update_work_metadata(early[index], {"title": late[index]})
        # A Person named by a Work's id: the Work still wins.
        self.ids["named_by_id"] = (self.leg.person("named_by_id", last_name=early[0]),
                                   self.ref.person("named_by_id", last_name=early[1]))
        out = self.render("[[{late}]] [[Late title]] [[{early}]]")
        # The earlier Work's title is an id here, so it reads as its label.
        self.assertEqual(out, " ".join((
            self.link("works", "early", "<W late>"),
            self.link("works", "late", "Late title"),
            self.link("works", "early", "<W late>"))))

    def test_case_whitespace_and_markers(self) -> None:
        self.seed()
        out = self.render(
            "[[  Alpha\t]] [[alpha]] [[ALPHA]] [[Critique  of Pure Reason]] [[kant]] [[Kant ]]\n"
            "[[]] [[  ]] [[a]][[b]] [[nested [[Alpha]]]] [[Alpha [single] ]] brackets]] [[Alpha")
        self.assertEqual(out, (
            f"{self.link('works', 'alpha1', 'Alpha')} {_UNRESOLVED.format(raw='alpha')} "
            f"{_UNRESOLVED.format(raw='ALPHA')} {_UNRESOLVED.format(raw='Critique  of Pure Reason')} "
            f"{_UNRESOLVED.format(raw='kant')} {self.link('people', 'kant1', 'Immanuel Kant')}\n"
            f"{_UNRESOLVED.format(raw='')} {_UNRESOLVED.format(raw='')} "
            f"{_UNRESOLVED.format(raw='a')}{_UNRESOLVED.format(raw='b')} "
            f"{_UNRESOLVED.format(raw='nested [[Alpha')}]] {_UNRESOLVED.format(raw='Alpha [single]')} "
            "brackets]] [[Alpha"))

    def test_text_without_markers_is_unchanged(self) -> None:
        for text in ("", "plain", "[single] brackets ]]", "[[open only"):
            with self.subTest(text=text):
                self.assertEqual(self.render(text), text)

    def test_names_and_ids_are_escaped(self) -> None:
        self.work("script", '<script>"x"</script>')
        self.person("quote", 'Quote"<b>', "Amp&")
        out = self.render('[[<script>"x"</script>]] [[Quote"<b>]] [[missing <img src=x onerror=y>]] [[a&b\']]')
        self.assertEqual(out, " ".join((
            self.link("works", "script", "&lt;script&gt;&quot;x&quot;&lt;/script&gt;"),
            self.link("people", "quote", "Amp&amp; Quote&quot;&lt;b&gt;"),
            _UNRESOLVED.format(raw="missing &lt;img src=x onerror=y&gt;"),
            _UNRESOLVED.format(raw="a&amp;b&#x27;"))))
        self.assertNotIn("<script>", out)
        self.assertNotIn("<img", out)

    def test_repeated_markers_and_many_distinct_targets(self) -> None:
        for i in range(120):
            self.work(f"w{i}", f"Work {i}")
            self.person(f"p{i}", f"Person {i}")
        template = " ".join(
            f"[[Work {i}]] [[Person {i}]] [[{{p{i}}}]] [[Gone {i}]] [[Work {i}]]" for i in range(120))
        expected = self.render(template)
        self.assertEqual(expected.count('href="#/works/'), 240)
        self.assertEqual(expected.count('href="#/people/'), 240)
        self.assertEqual(expected.count("wiki-link-unresolved"), 120)
        # Batches of any size render the same entry on both sides.
        for chunk in (1, 7, 400):
            with self.subTest(chunk=chunk), mock.patch.object(PRKSDatabase, "_WIKI_LINK_LOOKUP_CHUNK", chunk):
                self.assertEqual(self.render(template), expected)

    # -- Work detail

    def test_a_research_note_renders_in_the_work_detail(self) -> None:
        self.seed()
        noted = self.work("noted", "Noted")
        text = "Notes on [[Alpha]], [[Kant]], [[Shared]], [[nobody]] and [[{noted}]]. [[Alpha]] again."
        for index, (side, body) in enumerate(zip(self.sides(), self.texts(text))):
            side.db.update_work_metadata(noted[index], {"text_content": body})
        details = [side.db.get_work(noted[index]) for index, side in enumerate(self.sides())]
        self.assertEqual(self.leg.normal(details[0]["html_content"]), self.ref.normal(details[1]["html_content"]))
        self.assertEqual(self.leg.normal(details[0]["html_content"]), (
            f"Notes on {self.link('works', 'alpha1', 'Alpha')}, {self.link('people', 'kant1', 'Immanuel Kant')}, "
            f"{self.link('works', 'shared_w', 'Shared')}, {_UNRESOLVED.format(raw='nobody')} and "
            f"{self.link('works', 'noted', 'Noted')}. {self.link('works', 'alpha1', 'Alpha')} again."))
        plain = self.work("plain", "Plain")
        for index, side in enumerate(self.sides()):
            self.assertNotIn("html_content", side.db.get_work(plain[index]))
