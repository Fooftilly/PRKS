"""Wiki-link resolution on PostgreSQL (L-W-2 #631).

:class:`AnswerTests` spells out the HTML each marker renders. The other
tests cover the bound actor and its
read authorization, Library isolation of every lookup, the ordering rule for
rows created in one transaction, the Work detail's one snapshot, and bounded
statement counts.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any
from unittest import mock

from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import queries, wiki_links
from tests import acting_context as facade_context
from tests.pg.answers import AnswerAssertions
from tests.pg.test_works_facade import WorksFacadeTestCase
from tests.narrowing import require

_LINK = '<a href="#/{kind}/{id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{name}</a>'
_UNRESOLVED = '<span class="wiki-link-unresolved" style="color:#ef4444;">[[{raw}]]</span>'


def link(kind: str, target_id: str, name: str) -> str:
    return _LINK.format(kind=kind, id=target_id, name=name)


class WikiLinkTestCase(WorksFacadeTestCase):
    def lookups(self, call: Any) -> tuple[Any, list[str]]:
        """Run ``call``; returns its result and the lookup statements it issued."""
        seen: list[str] = []
        real = LibraryScope.execute

        def traced(scope: LibraryScope, query: Any, *args: Any, **kwargs: Any) -> Any:
            if "= ANY(%(targets)s" in str(query):
                seen.append("works" if "prks.works" in str(query) else "persons")
            return real(scope, query, *args, **kwargs)

        with mock.patch.object(LibraryScope, "execute", traced):
            return call(), seen


class AuthorizationTests(WikiLinkTestCase):
    def test_a_reader_resolves(self) -> None:
        work_id = self.library.add_work("Notes")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.resolve_wiki_links("[[Notes]]"), link("works", work_id, "Notes"))

    def test_an_unbound_call_is_refused(self) -> None:
        self.library.add_work("Notes")
        for text in ("[[Notes]]", "plain"):
            with self.subTest(text=text), self.assertRaises(facade_context.NoRequestContext):
                contextvars.Context().run(self.library.resolve_wiki_links, text)

    def test_a_removed_member_is_refused(self) -> None:
        self.library.add_work("Notes")
        reader = self.reader_context()
        self.sql("DELETE FROM prks.library_memberships WHERE library_id = %s AND role = 'reader'",
                 (self.actor.library_id,))
        with facade_context.acting_as(reader):
            with self.assertRaises(AccessRefused) as caught:
                self.library.resolve_wiki_links("[[Notes]]")
        self.assertIs(caught.exception.refusal, Refusal.NOT_AUTHENTICATED)


class IsolationTests(WikiLinkTestCase):
    def test_another_librarys_works_and_people_are_not_found(self) -> None:
        mine = self.library.add_work("Mine")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs")
            their_person = self.library.add_person("Their", "Person")
            self.library.add_work("Mine")  # the same title, in their Library
        text = f"[[Theirs]] [[{theirs}]] [[Person]] [[{their_person}]] [[Mine]]"
        self.assertEqual(self.library.resolve_wiki_links(text), " ".join((
            _UNRESOLVED.format(raw="Theirs"), _UNRESOLVED.format(raw=theirs),
            _UNRESOLVED.format(raw="Person"), _UNRESOLVED.format(raw=their_person),
            link("works", mine, "Mine"))))
        with facade_context.acting_as(other.context):
            out = self.library.resolve_wiki_links(f"[[{mine}]] [[Theirs]]")
        self.assertEqual(out, f"{_UNRESOLVED.format(raw=mine)} {link('works', theirs, 'Theirs')}")

    def test_lookups_read_this_library_only(self) -> None:
        mine = self.library.add_work("Shared title")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.library.add_work("Shared title")
        from backend.domain.identity.library_scope import library_query

        found = library_query(self.db, other.context, lambda scope: wiki_links.resolve_targets(
            scope, [mine, "Shared title"]))
        self.assertEqual(list(found), ["Shared title"])
        self.assertNotEqual(found["Shared title"][1], mine)


class OrderingTests(WikiLinkTestCase):
    def test_the_earliest_created_row_wins_then_the_id(self) -> None:
        first = self.library.add_work("Twin")
        second = self.library.add_work("Twin")
        self.assertEqual(self.library.resolve_wiki_links("[[Twin]]"), link("works", first, "Twin"))
        # Rows created in one transaction share created_at; the id decides.
        low, high = sorted((second, first))
        self.sql("UPDATE prks.works SET created_at = '2001-01-01' WHERE id = ANY(%s)", ([first, second],))
        self.assertEqual(self.library.resolve_wiki_links("[[Twin]]"), link("works", low, "Twin"))
        self.sql("UPDATE prks.works SET created_at = '2000-01-01' WHERE id = %s", (high,))
        self.assertEqual(self.library.resolve_wiki_links("[[Twin]]"), link("works", high, "Twin"))

    def test_people_follow_the_same_rule(self) -> None:
        first = self.library.add_person("A", "Kant")
        second = self.library.add_person("B", "Kant")
        self.assertEqual(self.library.resolve_wiki_links("[[Kant]]"), link("people", first, "A Kant"))
        self.sql("UPDATE prks.persons SET created_at = '2000-01-01' WHERE id = %s", (second,))
        self.assertEqual(self.library.resolve_wiki_links("[[Kant]]"), link("people", second, "B Kant"))

    def test_a_target_postgresql_text_cannot_hold_matches_nothing(self) -> None:
        self.library.add_work("Real")
        out, seen = self.lookups(lambda: self.library.resolve_wiki_links("[[Re\x00al]] [[Real]]"))
        self.assertTrue(out.startswith(_UNRESOLVED.format(raw="Re\x00al") + " <a "))
        self.assertEqual(seen, ["works"])


    def test_the_first_row_wins_whatever_batch_its_targets_fall_in(self) -> None:
        """A row matching targets in different batches claims each independently."""
        one = self.library.add_work("SharedTitle")
        two = self.library.add_work(one)  # its title is the first Work's id
        alpha = self.library.add_work("Alpha")
        self.library.add_work("Alpha")
        kant = self.library.add_person("Immanuel", "Kant")
        self.library.add_person("Other", "Kant")
        self.library.add_person("Pat", "SharedTitle")
        self.library.add_person("", "Solo")
        text = f"[[{one}]] [[SharedTitle]] [[Alpha]] [[{two}]] [[Kant]] [[Solo]] [[nobody]] [[{alpha}]]"
        expected = self.library.resolve_wiki_links(text)
        self.assertIn(link("works", one, "SharedTitle"), expected)
        self.assertEqual(expected.count(link("works", alpha, "Alpha")), 2)
        self.assertIn(link("people", kant, "Immanuel Kant"), expected)
        reversed_text = " ".join(reversed(text.split(" ")))
        reversed_expected = self.library.resolve_wiki_links(reversed_text)
        for chunk in (1, 2, 3, 5):
            with self.subTest(chunk=chunk), mock.patch.object(wiki_links, "LOOKUP_CHUNK", chunk):
                self.assertEqual(self.library.resolve_wiki_links(text), expected)
                self.assertEqual(self.library.resolve_wiki_links(reversed_text), reversed_expected)


class SnapshotTests(WikiLinkTestCase):
    def test_the_work_detail_and_its_links_are_one_snapshot(self) -> None:
        target = self.library.add_work("Target")
        noted = self.library.add_work("Noted", text_content="See [[Target]] and [[Later]].")
        original = queries.work
        changed: list[bool] = []

        def work_then_a_concurrent_edit(scope: LibraryScope, *args: Any) -> Any:
            found = original(scope, *args)
            if not changed:
                # Committed by another transaction after the Work was read,
                # before its links are looked up.
                self.library.update_work_metadata(target, {"title": "Renamed"})
                self.library.add_work("Later")
                changed.append(True)
            return found

        with mock.patch.object(queries, "work", side_effect=work_then_a_concurrent_edit):
            detail = self.library.get_work(noted)
        self.assertEqual(changed, [True])
        assert detail is not None
        self.assertEqual(detail["html_content"],
                         f"See {link('works', target, 'Target')} and {_UNRESOLVED.format(raw='Later')}.")
        later = self.sql("SELECT id FROM prks.works WHERE title = 'Later'")[0]["id"]
        self.assertEqual(require(self.library.get_work(noted))["html_content"],
                         f"See {_UNRESOLVED.format(raw='Target')} and {link('works', later, 'Later')}.")


class BatchingTests(WikiLinkTestCase):
    def test_text_without_markers_looks_nothing_up(self) -> None:
        for text in ("", "plain", "[single] ]]"):
            with self.subTest(text=text):
                out, seen = self.lookups(lambda t=text: self.library.resolve_wiki_links(t))
                self.assertEqual((out, seen), (text, []))

    def test_repeated_markers_cost_one_lookup_per_table(self) -> None:
        self.library.add_work("Alpha")
        self.library.add_person("Immanuel", "Kant")
        text = " ".join(["[[Alpha]]"] * 100 + ["[[Kant]]"] * 100 + ["[[nobody]]"] * 100)
        _, seen = self.lookups(lambda: self.library.resolve_wiki_links(text))
        self.assertEqual(seen, ["works", "persons"])
        _, seen = self.lookups(lambda: self.library.resolve_wiki_links("[[Alpha]] " * 100))
        self.assertEqual(seen, ["works"])

    def test_more_occurrences_of_the_same_markers_add_no_lookups(self) -> None:
        self.library.add_work("Alpha")
        self.library.add_person("Immanuel", "Kant")
        for repeat in (1, 10, 500):
            with self.subTest(repeat=repeat):
                out, seen = self.lookups(
                    lambda r=repeat: self.library.resolve_wiki_links("[[Alpha]] [[Kant]] [[nobody]] " * r))
                self.assertEqual(seen, ["works", "persons"])
                self.assertEqual(out.count("wiki-link-unresolved"), repeat)

    def test_hundreds_of_distinct_targets_use_bounded_batches(self) -> None:
        chunk = wiki_links.LOOKUP_CHUNK
        rows = ", ".join(
            f"('{self.actor.library_id}', 'P-{uuid.uuid4().hex.upper()}', '', 'Person {i}')" for i in range(300))
        self.sql(f"INSERT INTO prks.persons (library_id, id, first_name, last_name) VALUES {rows}")
        works = [self.library.add_work(f"Work {i}") for i in range(0, 300, 3)]
        names = [f"Work {i}" if i % 3 == 0 else f"Person {i}" if i % 3 == 1 else f"Gone {i}"
                 for i in range(chunk * 2 + 7)]
        out, seen = self.lookups(lambda: self.library.resolve_wiki_links(" ".join(f"[[{n}]]" for n in names)))
        rest = len(names) - len(works)
        self.assertEqual(seen, ["works"] * 3 + ["persons"] * -(-rest // chunk))
        self.assertEqual(out.count('href="#/works/'), len(works))
        self.assertEqual(out.count('href="#/people/'), 100)

    def test_lookups_probe_indexes_instead_of_scanning_the_library(self) -> None:
        for i in range(400):
            self.library.add_work(f"Work {i}")
        with facade_context.acting_as(self.other_actor().context):
            for _ in range(600):  # one title, common in another Library
                self.library.add_work("Work 7")
        rows = ", ".join(
            f"('{self.actor.library_id}', 'P-{uuid.uuid4().hex.upper()}', '', 'Person {i}')" for i in range(4000))
        self.sql(f"INSERT INTO prks.persons (library_id, id, first_name, last_name) VALUES {rows}")
        self.sql("ANALYZE prks.works")
        self.sql("ANALYZE prks.persons")

        def plan(sql: Any) -> str:
            params = {"library_id": self.actor.library_id, "targets": ["Work 7", "W-" + "0" * 32]}
            rows = self.db.command(lambda tx: tx.execute("EXPLAIN " + sql, params).fetchall())
            return "\n".join(str(next(iter(row.values()))) for row in rows)

        works_plan, persons_plan = plan(wiki_links._WORKS_SQL), plan(wiki_links._PERSONS_SQL)
        self.assertNotIn("Seq Scan", works_plan)
        # Keyed by Library first: the other Library's "Work 7" rows are never probed.
        self.assertIn("works_library_title_md5", works_plan)
        self.assertNotIn("Seq Scan", persons_plan)
        self.assertIn("persons_library_last_name", persons_plan)


class AnswerTests(AnswerAssertions, WikiLinkTestCase):
    """The HTML each marker renders, spelled out."""

    def setUp(self) -> None:
        super().setUp()
        self.ids: dict[str, str] = {}

    def work(self, label: str, title: str) -> str:
        self.ids[label] = self.labels.name(self.library.add_work(title), f"<W {label}>")
        return self.ids[label]

    def person(self, label: str, last_name: str, first_name: str = "") -> str:
        self.ids[label] = self.labels.name(self.library.add_person(first_name, last_name), f"<P {label}>")
        return self.ids[label]

    def render(self, template: str) -> str:
        """The labelled HTML for ``template``, its ``{label}`` filled by that row's id."""
        return str(self.labels.show(self.library.resolve_wiki_links(template.format(**self.ids))))

    @staticmethod
    def link(kind: str, label: str, name: str) -> str:
        return link(kind, f"<{'W' if kind == 'works' else 'P'} {label}>", name)

    def seed(self) -> None:
        # Created in this order: "first" below is the earlier one.
        self.work("alpha1", "Alpha")
        self.work("alpha2", "Alpha")
        self.work("shared_w", "Shared")
        self.person("shared_p", "Shared", "Pat")
        self.person("kant1", "Kant", "Immanuel")
        self.person("kant2", "Kant", "Other")
        self.person("solo", "Solo")
        self.work("critique", "Critique of Pure Reason")

    def test_works_and_people_by_id_and_by_name(self) -> None:
        self.seed()
        self.assertEqual(self.render("[[Critique of Pure Reason]] [[{critique}]] [[Kant]] [[{kant2}]] [[Solo]]"),
                         " ".join((self.link("works", "critique", "Critique of Pure Reason"),
                                   self.link("works", "critique", "Critique of Pure Reason"),
                                   self.link("people", "kant1", "Immanuel Kant"),
                                   self.link("people", "kant2", "Other Kant"),
                                   self.link("people", "solo", " Solo"))))

    def test_a_work_wins_over_a_person_and_the_first_row_wins(self) -> None:
        self.seed()
        self.assertEqual(self.render("[[Shared]] [[{shared_p}]] [[Alpha]] [[{alpha2}]] [[Kant]]"),
                         " ".join((self.link("works", "shared_w", "Shared"),
                                   self.link("people", "shared_p", "Pat Shared"),
                                   self.link("works", "alpha1", "Alpha"),
                                   self.link("works", "alpha2", "Alpha"),
                                   self.link("people", "kant1", "Immanuel Kant"))))

    def test_an_id_that_is_another_rows_name(self) -> None:
        # The earlier Work answers to the later Work's id by its title.
        early = self.work("early", "placeholder")
        late = self.work("late", "Late title")
        self.library.update_work_metadata(early, {"title": late})
        # A Person named by a Work's id: the Work still wins.
        self.person("named_by_id", early)
        # The earlier Work's title is an id here, so it reads as its label.
        self.assertEqual(self.render("[[{late}]] [[Late title]] [[{early}]]"),
                         " ".join((self.link("works", "early", "<W late>"),
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
                self.assertEqual(self.library.resolve_wiki_links(text), text)

    def test_names_and_ids_are_escaped(self) -> None:
        self.work("script", '<script>"x"</script>')
        self.person("quote", 'Quote"<b>', "Amp&")
        out = self.render('[[<script>"x"</script>]] [[Quote"<b>]] [[missing <img src=x onerror=y>]] [[a&b\']]')
        self.assertEqual(out, " ".join((
            self.link("works", "script", "&lt;script&gt;&quot;x&quot;&lt;/script&gt;"),
            self.link("people", "quote", "Amp&amp; Quote&quot;&lt;b&gt;"),
            _UNRESOLVED.format(raw="missing &lt;img src=x onerror=y&gt;"),
            _UNRESOLVED.format(raw="a&amp;b&#x27;"))))

    def test_repeated_markers_and_many_distinct_targets(self) -> None:
        for i in range(120):
            self.work(f"w{i}", f"Work {i}")
            self.person(f"p{i}", f"Person {i}")
        template = " ".join(f"[[Work {i}]] [[Person {i}]] [[{{p{i}}}]] [[Gone {i}]] [[Work {i}]]" for i in range(120))
        expected = " ".join(
            " ".join((self.link("works", f"w{i}", f"Work {i}"), self.link("people", f"p{i}", f" Person {i}"),
                      self.link("people", f"p{i}", f" Person {i}"), _UNRESOLVED.format(raw=f"Gone {i}"),
                      self.link("works", f"w{i}", f"Work {i}")))
            for i in range(120))
        self.assertEqual(self.render(template), expected)
        # Batches of any size render the same.
        for chunk in (1, 7, 400):
            with self.subTest(chunk=chunk), mock.patch.object(wiki_links, "LOOKUP_CHUNK", chunk):
                self.assertEqual(self.render(template), expected)

    def test_a_research_note_renders_in_the_work_detail(self) -> None:
        self.seed()
        noted = self.work("noted", "Noted")
        text = "Notes on [[Alpha]], [[Kant]], [[Shared]], [[nobody]] and [[{noted}]]. [[Alpha]] again."
        self.library.update_work_metadata(noted, {"text_content": text.format(**self.ids)})
        self.assertEqual(self.labels.show(require(self.library.get_work(noted))["html_content"]), (
            f"Notes on {self.link('works', 'alpha1', 'Alpha')}, {self.link('people', 'kant1', 'Immanuel Kant')}, "
            f"{self.link('works', 'shared_w', 'Shared')}, {_UNRESOLVED.format(raw='nobody')} and "
            f"{self.link('works', 'noted', 'Noted')}. {self.link('works', 'alpha1', 'Alpha')} again."))
        self.assertNotIn("html_content", require(self.library.get_work(self.work("plain", "Plain"))))
