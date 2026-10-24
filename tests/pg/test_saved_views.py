"""Saved Views on PostgreSQL: what parity cannot show (L-O-1 #633).

``tests/test_saved_views_parity.py`` proves the facade returns SQLite's JSON
and refusals. These tests cover what only PostgreSQL has: names compared by
identity key, the unique index as the backstop of a name race, Library
isolation and reader refusals, timestamps, the rollback of a failed write,
and concurrent writers (each race holds one transaction open until the
other is waiting on it).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from typing import Any, Callable
from unittest import mock

from backend.db_manager import PRKS_SAVED_VIEW_MAX, SavedViewError
from backend.domain.identity.authorization import AccessRefused
from backend.domain.saved_views import views
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from tests.pg.test_tags import OrganizationTestCase

_ALL = {"mode": "all", "q": "culture", "tag": "", "author": "", "publisher": ""}
_ADVANCED = {"mode": "advanced", "q": "", "tag": "", "author": "Adorno", "publisher": ""}
_TAG = {"mode": "tag", "q": "", "tag": "Ethics", "author": "", "publisher": "Verso"}
_MOMENT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
_MISSING = "SV-" + "0" * 32


class SavedViewTestCase(OrganizationTestCase):
    def create(self, name: str, search: dict[str, str] | None = None) -> dict[str, Any]:
        return self.library.create_saved_view(name, dict(search or _ALL))

    def refused(self, message: str, status: int, call: Callable[..., Any], *args: Any,
                **kwargs: Any) -> None:
        with self.assertRaises(SavedViewError) as caught:
            call(*args, **kwargs)
        self.assertEqual((str(caught.exception), caught.exception.http_status), (message, status))

    def seed(self, n: int, library_id: str | None = None) -> None:
        """``n`` Saved Views as rows, without the facade (a fast fill to near the limit)."""
        for index in range(n):
            self.sql("INSERT INTO prks.saved_views (library_id, id, name, mode, search_q)"
                     " VALUES (%s, %s, %s, 'all', 'x')",
                     (library_id or self.actor.library_id, generate("SV"), f"Seed {index:03d}"))

    def names(self) -> list[str]:
        return [row["name"] for row in self.library.get_saved_views()]


class CrudTests(SavedViewTestCase):
    def test_create_read_update_and_delete(self) -> None:
        created = self.create("  Industry ", {**_ALL, "q": "  culture  "})
        self.assertRegex(created["id"], r"^SV-[0-9A-F]{32}$")
        self.assertEqual((created["name"], created["search"]), ("Industry", _ALL))
        self.assertEqual(set(created), {"id", "name", "search", "created_at", "updated_at"})
        view_id = created["id"]
        self.assertEqual(self.library.get_saved_view(f"  {view_id} "), created)
        self.assertEqual(self.library.get_saved_views(), [created])

        renamed = self.library.update_saved_view(view_id, name="Renamed")
        self.assertEqual((renamed["name"], renamed["search"]), ("Renamed", _ALL))  # the definition kept
        redefined = self.library.update_saved_view(view_id, search=_ADVANCED)
        self.assertEqual((redefined["name"], redefined["search"]), ("Renamed", _ADVANCED))  # the name kept
        both = self.library.update_saved_view(f" {view_id} ", name="Both", search=_TAG)
        self.assertEqual((both["name"], both["search"]), ("Both", _TAG))
        self.assertEqual(self.library.get_saved_view(view_id), both)

        self.assertIsNone(self.library.delete_saved_view(f" {view_id}"))
        self.assertIsNone(self.library.get_saved_view(view_id))
        self.assertEqual((self.library.get_saved_views(), self.count("saved_views")), ([], 0))
        self.refused(views.NOT_FOUND, 404, self.library.delete_saved_view, view_id)
        self.refused(views.NOT_FOUND, 404, self.library.update_saved_view, view_id, name="Again")

    def test_every_definition_shape_is_stored_as_given(self) -> None:
        shapes = [
            _ALL,
            {**_ADVANCED, "author": "", "q": "dialectic"},
            {**_ADVANCED, "author": "", "publisher": "Verso"},
            {**_ADVANCED, "q": "enlightenment", "publisher": "Verso"},
            {**_TAG, "publisher": ""},
            {**_TAG, "author": "Adorno"},
            {"mode": " tag ", "q": " ", "tag": " Λόγος ", "author": "", "publisher": ""},
        ]
        for index, search in enumerate(shapes):
            with self.subTest(search=search):
                created = self.create(f"Shape {index}", search)
                self.assertEqual(created["search"], {key: value.strip() for key, value in search.items()})
                self.assertEqual(self.library.get_saved_view(created["id"]), created)

    def test_invalid_input_is_refused_before_any_write(self) -> None:
        refusals: list[tuple[Any, Any, str]] = [
            (None, _ALL, "Name must be a string."),
            ("   ", _ALL, "Name is required."),
            ("a\x00b", _ALL, "Name contains invalid characters."),
            ("a\tb", _ALL, "Name contains invalid characters."),
            ("x" * 81, _ALL, "Name is too long."),
            ("Ok", None, "Search definition is required."),
            ("Ok", {"mode": "all", "q": "x"}, "Search definition is incomplete."),
            ("Ok", {**_ALL, "mode": 1}, "Invalid search mode."),
            ("Ok", {**_ALL, "mode": "fuzzy"}, "Invalid search mode."),
            ("Ok", {**_ALL, "q": 5}, "Search fields must be strings."),
            ("Ok", {**_ALL, "q": "a\x00b"}, "Search definition contains invalid characters."),
            ("Ok", {**_ALL, "q": "x" * 501}, "Search field is too long."),
            ("Ok", {**_TAG, "tag": "x" * 201}, "Search field is too long."),
            ("Ok", {**_ALL, "q": ""}, "All-fields views require a query and no other filters."),
            ("Ok", {**_ALL, "author": "A"}, "All-fields views require a query and no other filters."),
            ("Ok", {**_ADVANCED, "tag": "T"}, "Advanced views cannot include a tag."),
            ("Ok", {**_ADVANCED, "author": ""}, "Advanced views require keywords, author, or publisher."),
            ("Ok", {**_TAG, "tag": ""}, "Tag views require a tag and no keyword query."),
            ("Ok", {**_TAG, "q": "x"}, "Tag views require a tag and no keyword query."),
        ]
        for name, search, message in refusals:
            with self.subTest(message=message, name=str(name)[:10]):
                self.refused(message, 400, self.library.create_saved_view, name, search)
        self.assertEqual(self.count("saved_views"), 0)
        kept = self.create("Kept")
        for name, search, message in refusals:
            if name is None or search is None:
                continue  # None is "not given" to an update
            with self.subTest(update=message):
                self.refused(message, 400, self.library.update_saved_view, kept["id"],
                             name=name if name != "Ok" else None, search=search if name == "Ok" else None)
        self.refused("Nothing to update.", 400, self.library.update_saved_view, kept["id"])
        self.assertEqual(self.library.get_saved_views(), [kept])

    def test_an_id_no_saved_view_can_have_is_not_found(self) -> None:
        kept = self.create("Kept")
        for view_id in ("", "   ", "SV-missing", _MISSING, "a\x00b", kept["id"].lower()):
            with self.subTest(view_id=view_id):
                self.assertIsNone(self.library.get_saved_view(view_id))
                self.refused(views.NOT_FOUND, 404, self.library.update_saved_view, view_id, name="X")
                self.refused(views.NOT_FOUND, 404, self.library.delete_saved_view, view_id)
        # SQLite's order: an empty id is not found before "Nothing to update"; any other id after it.
        self.refused(views.NOT_FOUND, 404, self.library.update_saved_view, "")
        self.refused("Nothing to update.", 400, self.library.update_saved_view, _MISSING)
        self.refused("Name is required.", 400, self.library.update_saved_view, _MISSING, name=" ")
        self.assertEqual(self.library.get_saved_views(), [kept])


class NameTests(SavedViewTestCase):
    def test_names_compare_by_identity_key(self) -> None:
        pairs = [("Straße", "STRASSE"), ("Λόγος", "ΛΌΓΟΣ"),
                 (unicodedata.normalize("NFC", "Café"), unicodedata.normalize("NFD", "CAFÉ")),
                 ("Two  words", "two words"), ("Ethics", "ethics")]
        for first, second in pairs:
            with self.subTest(name=first):
                self.create(first)
                self.refused(views.NAME_TAKEN, 409, self.create, second)
        # Diacritics are not folded (§8.1).
        self.create("Resume")
        self.create("Résumé")
        self.assertEqual(self.count("saved_views"), len(pairs) + 2)

    def test_a_rename_may_change_only_the_case_of_its_own_name(self) -> None:
        mine, other = self.create("Straße"), self.create("Logic")
        self.assertEqual(self.library.update_saved_view(mine["id"], name="STRASSE")["name"], "STRASSE")
        self.refused(views.NAME_TAKEN, 409, self.library.update_saved_view, other["id"], name="strasse")
        self.refused(views.NAME_TAKEN, 409, self.library.update_saved_view, other["id"], name="straße",
                     search=_ADVANCED)
        self.assertEqual(self.library.get_saved_view(other["id"]), other)

    def test_the_backstop_is_registered_on_the_saved_view_import_path(self) -> None:
        code = ("import backend.domain.saved_views.views as views; from backend.db.errors import DEFAULT_REGISTRY;"
                " import sys; m = DEFAULT_REGISTRY.lookup(views.NAME_KEY);"
                " sys.exit(0 if m is not None and m.code == 'NAME_TAKEN' and m.http_status == 409 else 1)")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(subprocess.run([sys.executable, "-c", code], cwd=root, check=False).returncode, 0)

    def test_the_unique_index_is_the_backstop_and_answers_409(self) -> None:
        self.create("Ethics")
        with mock.patch.object(views, "_assert_name_free"):
            self.refused(views.NAME_TAKEN, 409, self.create, "ETHICS")
            renamed = self.create("Logic")
            self.refused(views.NAME_TAKEN, 409, self.library.update_saved_view, renamed["id"], name="ethics")
        self.assertEqual(self.names(), ["Ethics", "Logic"])


class LimitTests(SavedViewTestCase):
    def test_a_library_holds_at_most_the_limit(self) -> None:
        self.seed(PRKS_SAVED_VIEW_MAX - 1)
        last = self.create("Last")
        self.refused(views.LIMIT_REACHED, 409, self.create, "One more")
        # The limit is checked before the name, as SQLite does.
        self.refused(views.LIMIT_REACHED, 409, self.create, "LAST")
        self.assertEqual(self.count("saved_views"), PRKS_SAVED_VIEW_MAX)
        # A full Library still renames and redefines.
        self.assertEqual(self.library.update_saved_view(last["id"], name="Final", search=_TAG)["name"], "Final")
        self.library.delete_saved_view(last["id"])
        self.create("One more")

    def test_another_librarys_views_do_not_count(self) -> None:
        other = self.other_actor()
        self.seed(PRKS_SAVED_VIEW_MAX, other.library_id)
        self.create("Mine")
        with facade_context.acting_as(other.context):
            self.refused(views.LIMIT_REACHED, 409, self.create, "Theirs")


class TimestampAndOrderTests(SavedViewTestCase):
    def test_an_update_keeps_created_at_and_moves_updated_at(self) -> None:
        created = self.create("Dated")
        self.assertRegex(created["created_at"], _MOMENT)
        self.assertEqual(created["created_at"], created["updated_at"])
        self.sql("UPDATE prks.saved_views SET created_at = '2020-01-02 03:04:05+00',"
                 " updated_at = '2020-01-02 03:04:05+00'")
        other = self.create("Other")
        for refused in ((views.NAME_TAKEN, 409, {"name": "other"}),
                        ("Nothing to update.", 400, {})):
            self.refused(refused[0], refused[1], self.library.update_saved_view, created["id"], **refused[2])
        self.assertEqual(self.library.get_saved_view(created["id"])["updated_at"], "2020-01-02 03:04:05")
        for change in ({"name": "Dated"}, {"search": _ALL}):  # a write moves it even when nothing differs
            with self.subTest(change=change):
                self.sql("UPDATE prks.saved_views SET updated_at = '2020-01-02 03:04:05+00' WHERE id = %s",
                         (created["id"],))
                updated = self.library.update_saved_view(created["id"], **change)
                self.assertEqual(updated["created_at"], "2020-01-02 03:04:05")
                self.assertRegex(updated["updated_at"], _MOMENT)
                self.assertGreater(updated["updated_at"], "2020-01-02 03:04:05")
        self.assertEqual(self.library.get_saved_view(other["id"]), other)

    def test_the_list_orders_by_ascii_folded_name_then_id(self) -> None:
        names = ["beta", "Alpha", "alpha two", "Zeta", "_under", "Ärger", "ångström", "Ωmega", "10", "9"]
        for name in names:
            self.create(name)
        ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(self.names(), sorted(names, key=lambda name: name.translate(ascii_lower)))


class AtomicityTests(SavedViewTestCase):
    def test_a_refused_rename_leaves_the_definition_too(self) -> None:
        mine, other = self.create("Mine"), self.create("Other")
        self.refused(views.NAME_TAKEN, 409, self.library.update_saved_view, mine["id"], name="OTHER",
                     search=_ADVANCED)
        self.assertEqual(self.library.get_saved_view(mine["id"]), mine)
        self.assertEqual(self.library.get_saved_view(other["id"]), other)

    def test_a_failure_after_the_write_rolls_it_back(self) -> None:
        kept = self.create("Kept")
        with mock.patch.object(views, "view", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.create("Lost")
            with self.assertRaises(RuntimeError):
                self.library.update_saved_view(kept["id"], name="Changed", search=_TAG)
        self.assertEqual(self.library.get_saved_views(), [kept])


class IsolationTests(SavedViewTestCase):
    def test_another_librarys_views_are_invisible(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.create("Shared name")
        self.assertIsNone(self.library.get_saved_view(theirs["id"]))
        self.assertEqual(self.library.get_saved_views(), [])
        self.refused(views.NOT_FOUND, 404, self.library.update_saved_view, theirs["id"], name="Mine")
        self.refused(views.NOT_FOUND, 404, self.library.delete_saved_view, theirs["id"])
        mine = self.create("SHARED NAME")  # names are unique per Library
        self.assertEqual(self.library.get_saved_views(), [mine])
        with facade_context.acting_as(other.context):
            self.assertEqual(self.library.get_saved_views(), [theirs])

    def test_a_reader_reads_views_but_cannot_change_them(self) -> None:
        view = self.create("Read me")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_saved_views(), [view])
            self.assertEqual(self.library.get_saved_view(view["id"]), view)
            for call in (lambda: self.create("New"),
                         lambda: self.library.update_saved_view(view["id"], name="Changed"),
                         lambda: self.library.update_saved_view(view["id"], search=_TAG),
                         lambda: self.library.delete_saved_view(view["id"])):
                with self.assertRaises(AccessRefused):
                    call()
        self.assertEqual(self.library.get_saved_views(), [view])


class RaceTests(SavedViewTestCase):
    def test_two_creates_of_one_name_keep_one(self) -> None:
        holder, contender = self.race(lambda scope: views.create(scope, generate("SV"), "Same", _ALL),
                                      lambda: self.create("SAME"))
        self.assertEqual(holder["name"], "Same")
        self.assertIsInstance(contender, SavedViewError)
        self.assertEqual((str(contender), contender.http_status), (views.NAME_TAKEN, 409))
        self.assertEqual(self.names(), ["Same"])

    def test_a_create_waiting_on_a_rename_to_its_name_is_refused(self) -> None:
        moving = self.create("Before")
        holder, contender = self.race(lambda scope: views.update(scope, moving["id"], name="Target"),
                                      lambda: self.create("target"))
        self.assertEqual(holder["name"], "Target")
        self.assertIsInstance(contender, SavedViewError)
        self.assertEqual(str(contender), views.NAME_TAKEN)
        self.assertEqual(self.names(), ["Target"])

    def test_a_rename_waiting_on_a_create_of_its_name_is_refused(self) -> None:
        moving = self.create("Before")
        holder, contender = self.race(lambda scope: views.create(scope, generate("SV"), "Target", _ALL),
                                      lambda: self.library.update_saved_view(moving["id"], name="TARGET",
                                                                             search=_TAG))
        self.assertEqual(holder["name"], "Target")
        self.assertIsInstance(contender, SavedViewError)
        self.assertEqual(str(contender), views.NAME_TAKEN)
        self.assertEqual(self.library.get_saved_view(moving["id"]), moving)

    def test_a_rename_freeing_a_name_lets_the_waiting_create_take_it(self) -> None:
        moving = self.create("Taken")
        holder, contender = self.race(lambda scope: views.update(scope, moving["id"], name="Moved"),
                                      lambda: self.create("taken"))
        self.assertEqual((holder["name"], contender["name"]), ("Moved", "taken"))
        self.assertEqual(self.names(), ["Moved", "taken"])

    def test_the_last_free_slot_goes_to_one_of_two_creates(self) -> None:
        self.seed(PRKS_SAVED_VIEW_MAX - 1)
        holder, contender = self.race(lambda scope: views.create(scope, generate("SV"), "Hundredth", _ALL),
                                      lambda: self.create("Hundred and first"))
        self.assertEqual(holder["name"], "Hundredth")
        self.assertIsInstance(contender, SavedViewError)
        self.assertEqual((str(contender), contender.http_status), (views.LIMIT_REACHED, 409))
        self.assertEqual(self.count("saved_views"), PRKS_SAVED_VIEW_MAX)

    def test_an_update_waiting_on_a_delete_is_not_found(self) -> None:
        doomed = self.create("Doomed")
        holder, contender = self.race(lambda scope: views.delete(scope, doomed["id"]),
                                      lambda: self.library.update_saved_view(doomed["id"], search=_TAG))
        self.assertIsNone(holder)
        self.assertIsInstance(contender, SavedViewError)
        self.assertEqual((str(contender), contender.http_status), (views.NOT_FOUND, 404))
        self.assertEqual(self.count("saved_views"), 0)

    def test_a_delete_waits_for_an_update(self) -> None:
        busy = self.create("Busy")
        holder, contender = self.race(lambda scope: views.update(scope, busy["id"], search=_TAG),
                                      lambda: self.library.delete_saved_view(busy["id"]))
        self.assertEqual((holder["search"], contender), (_TAG, None))
        self.assertEqual(self.count("saved_views"), 0)

    def test_concurrent_updates_of_the_two_parts_keep_both(self) -> None:
        view = self.create("Old name")
        holder, contender = self.race(lambda scope: views.update(scope, view["id"], search=_TAG),
                                      lambda: self.library.update_saved_view(view["id"], name="New name"))
        self.assertEqual(holder["search"], _TAG)
        self.assertEqual((contender["name"], contender["search"]), ("New name", _TAG))
        found = self.library.get_saved_view(view["id"])
        self.assertEqual((found["name"], found["search"], found["created_at"]),
                         ("New name", _TAG, view["created_at"]))
