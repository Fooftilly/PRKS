"""Playlists, their fields, membership and order on PostgreSQL (L-O-1 #633).

:class:`AnswerTests` spells out the JSON each command answers. The rest
cover refusals of what PostgreSQL text
cannot hold, the tombstones a Playlist or Work delete leaves, Library
isolation, and concurrent writers (each race holds one transaction open
until the other is waiting on it).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional
from unittest import mock

from backend.domain.identity.authorization import AccessRefused
from backend.domain.organization import playlist_commands, playlists
from backend.domain.works import commands
from backend.domain.entity_ids import generate
from tests import acting_context as facade_context
from backend.domain.organization.playlist_rules import PlaylistRuleError, scope_key
from tests.pg.test_tags import OrganizationTestCase
from backend.web.etags import etag_for_representation
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions

_NOT_FOUND = "Playlist or work not found."


class PlaylistTestCase(OrganizationTestCase):
    def add(self, title: str = "Playlist", *works: str, **fields: Any) -> str:
        playlist_id = self.library.add_playlist(title, fields.pop("description", ""),
                                                fields.pop("original_url", ""))
        for work_id in works:
            self.library.add_work_to_playlist(playlist_id, work_id)
        return playlist_id

    def works(self, *titles: str) -> list[str]:
        return [self.library.add_work(title) for title in titles]

    def refused(self, message: str, call: Any, *args: Any) -> None:
        with self.assertRaises(ValueError) as caught:
            call(*args)
        self.assertEqual(str(caught.exception), message)

    def state(self, playlist_id: str) -> tuple[dict[str, int], int]:
        found = self.library.get_playlist_sync_state(playlist_id)
        assert found is not None
        return {field: value["revision"] for field, value in found["fields"].items()}, found["order_revision"]

    def membership(self, work_id: str) -> tuple[str, int]:
        found = self.library.get_work_playlist_state(work_id)
        assert found is not None
        return found["playlist_id"], found["revision"]

    def items(self, playlist_id: str) -> list[tuple[str, int]]:
        rows = self.sql("SELECT work_id, position FROM prks.playlist_items WHERE playlist_id = %s"
                        " ORDER BY position, work_id", (playlist_id,))
        return [(row["work_id"], row["position"]) for row in rows]

    def move(self, work_id: str, playlist_id: str) -> Callable[[Any], bool]:
        """A holder that moves the Work, as the facade and ``SET_WORK_PLAYLIST`` do."""
        def run(scope: Any) -> bool:
            playlists.lock_work(scope, work_id)
            return playlists.set_work_playlist(scope, work_id, playlist_id)
        return run


_UNTOUCHED = {"description": 0, "original_url": 0, "title": 0}


class FieldTests(PlaylistTestCase):
    def test_only_a_change_advances_a_field(self) -> None:
        playlist_id = self.add("Course", description="About")
        before = self.library.get_playlist(playlist_id)
        assert before is not None
        self.assertEqual((before["description"], before["original_url"]), ("About", None))
        self.library.update_playlist(playlist_id, {"title": " Course ", "description": "About ",
                                                   "original_url": None})
        self.assertEqual(self.state(playlist_id), (_UNTOUCHED, 0))
        self.assertEqual(self.revisions("playlist-field"), {})
        self.library.update_playlist(playlist_id, {"original_url": "https://example.org", "x": 1})
        self.library.update_playlist(playlist_id, {"original_url": ""})
        self.assertEqual(self.state(playlist_id)[0]["original_url"], 2)
        self.assertIsNone(require(self.library.get_playlist(playlist_id))["original_url"])
        self.library.update_playlist(playlist_id, {"title": "  "})
        self.assertEqual(require(self.library.get_playlist(playlist_id))["title"], "Untitled playlist")
        self.refused("Playlist not found.", self.library.update_playlist, generate("PL"), {"title": "X"})

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        playlist_id = self.add("Course")
        call: Callable[..., Any]
        args: tuple[Any, ...]
        for call, args in ((self.library.add_playlist, ("a\x00b",)), (self.library.add_playlist, ("x" * 301,)),
                           (self.library.add_playlist, ("Ok", "d\x00")),
                           (self.library.update_playlist, (playlist_id, {"description": "é" * 2001})),
                           (self.library.update_playlist, (playlist_id, {"title": "ok", "original_url": "\x00"}))):
            with self.subTest(args=[str(arg)[:12] for arg in args]), self.assertRaises(ValueError):
                call(*args)
        self.assertEqual((self.count("playlists"), self.state(playlist_id)), (1, (_UNTOUCHED, 0)))
        self.assertEqual(require(self.library.get_playlist(playlist_id))["title"], "Course")  # the batch rolled back
        self.assertEqual(require(self.library.get_playlist(self.add("é" * 150)))["title"], "é" * 150)  # 300 bytes


class MembershipTests(PlaylistTestCase):
    def test_adding_moving_and_removing_advance_the_work_and_both_orders(self) -> None:
        a, b = self.add("A"), self.add("B")
        first, second = self.works("First", "Second")
        self.assertEqual(self.membership(first), ("", 0))
        self.library.add_work_to_playlist(a, first)
        self.library.add_work_to_playlist(a, first)  # already there: nothing changes
        self.library.add_work_to_playlist(a, second)
        self.assertEqual((self.membership(first), self.state(a)[1], self.items(a)),
                         ((a, 1), 2, [(first, 0), (second, 1)]))
        detail = self.library.get_work(first)
        assert detail is not None
        self.assertEqual((detail["playlist_id"], detail["playlist_title"]), (a, "A"))
        self.library.add_work_to_playlist(b, first)
        self.assertEqual((self.membership(first), self.state(a)[1], self.state(b)[1]), ((b, 2), 3, 1))
        self.assertEqual((self.items(a), self.items(b)), ([(second, 1)], [(first, 0)]))
        self.library.remove_work_from_playlist(a, first)  # not in it: nothing changes
        self.library.remove_work_from_playlist(b, first)
        self.assertEqual((self.membership(first), self.state(b)[1]), (("", 3), 2))
        self.assertEqual(require(self.library.get_work(first))["playlist_title"], None)
        self.assertEqual({row["title"]: row["item_count"] for row in self.library.get_all_playlists()},
                         {"A": 1, "B": 0})

    def test_a_failed_removal_leaves_the_membership_and_revisions(self) -> None:
        # Offline coherence treats a failed request as a no-op, so a partly
        # committed removal would make a stale cache look eligible.
        playlist_id = self.add("Rollback")
        (work_id,) = self.works("Rollback Work")
        self.library.add_work_to_playlist(playlist_id, work_id)
        before = (self.membership(work_id), self.state(playlist_id), self.items(playlist_id),
                  require(self.library.get_playlist(playlist_id))["updated_at"])
        # The timestamp write runs after the item is deleted and the order
        # advanced, so failing it is exactly the partial-commit window.
        with mock.patch.object(playlists, "_touch", side_effect=RuntimeError("forced timestamp failure")):
            with self.assertRaises(RuntimeError):
                self.library.remove_work_from_playlist(playlist_id, work_id)
        self.assertEqual((self.membership(work_id), self.state(playlist_id), self.items(playlist_id),
                          require(self.library.get_playlist(playlist_id))["updated_at"]), before)
        # The ordinary removal still works once the injected failure is gone.
        self.library.remove_work_from_playlist(playlist_id, work_id)
        self.assertEqual(self.items(playlist_id), [])

    def test_a_missing_work_or_playlist_is_refused(self) -> None:
        playlist_id, work_id = self.add("A"), self.library.add_work("W")
        for args in ((generate("PL"), work_id), (playlist_id, generate("W")), (" ", work_id), (playlist_id, "")):
            with self.subTest(args=args):
                self.refused(_NOT_FOUND, self.library.add_work_to_playlist, *args)
        self.library.remove_work_from_playlist(playlist_id, generate("W"))
        self.refused("Playlist not found.", self.library.reorder_playlist, generate("PL"), ["x"])
        self.assertEqual((self.items(playlist_id), self.revisions("work-playlist"),
                          self.revisions("playlist-order")), ([], {}, {}))

    def test_the_states_of_a_missing_work_or_playlist_are_none(self) -> None:
        work_id = self.library.add_work("W")
        self.assertEqual(self.library.get_work_playlist_state(work_id),
                         {"work_id": work_id, "playlist_id": "", "revision": 0})
        self.assertIsNone(self.library.get_work_playlist_state(generate("W")))
        self.assertIsNone(self.library.get_playlist_sync_state(generate("PL")))

    def test_an_add_at_a_position_is_two_changes(self) -> None:
        playlist_id = self.add("A")
        first, second, third = self.works("1", "2", "3")
        for work_id in (first, second):
            self.library.add_work_to_playlist(playlist_id, work_id)
        self.library.add_work_to_playlist(playlist_id, third, 1)
        self.assertEqual(self.items(playlist_id), [(first, 0), (third, 1), (second, 2)])
        self.assertEqual(self.state(playlist_id)[1], 4)
        self.assertEqual([item["id"] for item in require(self.library.get_playlist(playlist_id))["items"]],
                         [first, third, second])


class OrderTests(PlaylistTestCase):
    def test_a_reorder_keeps_membership_and_rewrites_positions(self) -> None:
        first, second, third = self.works("1", "2", "3")
        playlist_id = self.add("A", first, second, third)
        self.library.remove_work_from_playlist(playlist_id, second)
        self.assertEqual(self.items(playlist_id), [(first, 0), (third, 2)])  # a gap, as on SQLite
        self.library.reorder_playlist(playlist_id, [third, "W-" + "0" * 32, third])
        self.assertEqual(self.items(playlist_id), [(third, 0), (first, 1)])
        revision = self.state(playlist_id)[1]
        self.library.reorder_playlist(playlist_id, [third, first])  # unchanged
        self.library.reorder_playlist(playlist_id, [])
        self.assertEqual(self.state(playlist_id)[1], revision)

    def test_an_echo_is_bounded(self) -> None:
        work_ids = self.works(*(str(n) for n in range(playlists.rules.MAX_ECHOED_ITEMS + 1)))
        playlist_id = self.add("Long", *work_ids)
        status, result = self.run_command(
            lambda scope: playlists.apply_order_operation(scope, playlist_id, list(reversed(work_ids)),
                                                          self.state(playlist_id)[1]))
        self.assertEqual((status, result["changed"], "work_ids" in result), (200, True, False))
        self.library.remove_work_from_playlist(playlist_id, work_ids[0])
        status, result = self.run_command(
            lambda scope: playlists.apply_order_operation(scope, playlist_id, work_ids,
                                                          self.state(playlist_id)[1]))
        self.assertEqual((status, result["work_ids"]), (200, work_ids[1:]))


class DeleteTests(PlaylistTestCase):
    def test_a_delete_releases_its_works_and_tombstones_every_scope(self) -> None:
        member, other = self.works("Member", "Other")
        playlist_id = self.add("Doomed", member, other)
        self.library.update_playlist(playlist_id, {"title": "Renamed"})
        self.library.delete_playlist(playlist_id)
        self.library.delete_playlist(playlist_id)  # already gone
        self.assertEqual((self.count("playlists"), self.count("playlist_items"), self.count("works")), (0, 0, 2))
        # Every field scope, edited or not; the order; each member's membership.
        self.assertEqual(self.revisions("playlist-field"), {
            scope_key(playlist_id, "title"): 2, scope_key(playlist_id, "description"): 1,
            scope_key(playlist_id, "original_url"): 1})
        self.assertEqual(self.revisions("playlist-order"), {playlist_id: 3})
        self.assertEqual(self.revisions("work-playlist"), {member: 2, other: 2})
        self.assertIsNone(self.library.get_playlist(playlist_id))
        self.assertEqual(require(self.library.get_work(member))["playlist_id"], None)
        # A Playlist created again under the id starts from the tombstones.
        self.run_command(lambda scope: playlists.insert(scope, playlist_id, "Again"))
        self.assertEqual(self.state(playlist_id), ({"description": 1, "original_url": 1, "title": 2}, 3))

    def test_a_work_delete_leaves_its_playlist_and_advances_its_order(self) -> None:
        member, kept, never = self.works("Member", "Kept", "Never")
        playlist_id = self.add("A", member, kept)
        self.assertIsNotNone(self.library.delete_work_record(member))
        self.assertIsNotNone(self.library.delete_work_record(never))
        self.assertEqual((self.items(playlist_id), self.state(playlist_id)[1]), ([(kept, 1)], 3))
        # Both tombstoned, the never-listed Work's from revision 0.
        self.assertEqual(self.revisions("work-playlist"), {member: 2, kept: 1, never: 1})
        self.assertEqual([item["id"] for item in require(self.library.get_playlist(playlist_id))["items"]], [kept])
        self.library.delete_playlist(playlist_id)

    def test_a_missing_playlist_reads_as_missing_without_a_data_root(self) -> None:
        from tests.library_adapter import TestLibrary

        member, = self.works("Member")
        playlist_id = self.add("Listed", member)
        bare = TestLibrary(self.db)
        self.assertIsNone(bare.get_playlist("PL-missing"))
        # Finishing a Work summary needs the PDF directory.
        with self.assertRaisesRegex(RuntimeError, "no data root"):
            bare.get_playlist(playlist_id)


class IsolationTests(PlaylistTestCase):
    def test_another_librarys_playlists_are_invisible(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            their_work = self.library.add_work("Their work")
            theirs = self.add("Theirs", their_work)
        mine = self.library.add_work("Mine")
        self.assertIsNone(self.library.get_playlist(theirs))
        self.assertIsNone(self.library.get_playlist_sync_state(theirs))
        self.assertIsNone(self.library.get_work_playlist_state(their_work))
        self.assertEqual(self.library.get_all_playlists(), [])
        self.refused(_NOT_FOUND, self.library.add_work_to_playlist, theirs, mine)
        self.refused(_NOT_FOUND, self.library.add_work_to_playlist, self.add("Mine"), their_work)
        self.refused("Playlist not found.", self.library.update_playlist, theirs, {"title": "Mine"})
        self.refused("Playlist not found.", self.library.reorder_playlist, theirs, [their_work])
        self.library.remove_work_from_playlist(theirs, their_work)
        self.library.delete_playlist(theirs)
        with facade_context.acting_as(other.context):
            found = self.library.get_playlist(theirs)
            assert found is not None
            self.assertEqual((found["title"], [item["id"] for item in found["items"]]), ("Theirs", [their_work]))

    def test_a_reader_reads_playlists_but_cannot_change_them(self) -> None:
        work_id = self.library.add_work("W")
        playlist_id = self.add("A", work_id)
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(require(self.library.get_playlist(playlist_id))["title"], "A")
            for call in (lambda: self.library.add_playlist("B"),
                         lambda: self.library.update_playlist(playlist_id, {"title": "B"}),
                         lambda: self.library.remove_work_from_playlist(playlist_id, work_id),
                         lambda: self.library.reorder_playlist(playlist_id, [work_id]),
                         lambda: self.library.delete_playlist(playlist_id)):
                with self.assertRaises(AccessRefused):
                    call()
        self.assertEqual((self.count("playlists"), self.items(playlist_id)), (1, [(work_id, 0)]))


class RaceTests(PlaylistTestCase):
    def test_two_works_added_at_once_get_two_positions(self) -> None:
        playlist_id = self.add("A")
        first, second = self.works("1", "2")
        holder, contender = self.race(self.move(first, playlist_id),
                                      lambda: self.library.add_work_to_playlist(playlist_id, second),
                                      serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.items(playlist_id), self.state(playlist_id)[1]), ([(first, 0), (second, 1)], 2))

    def test_two_moves_of_one_work_serialize(self) -> None:
        a, b, work_id = self.add("A"), self.add("B"), self.library.add_work("W")
        holder, contender = self.race(self.move(work_id, a),
                                      lambda: self.library.add_work_to_playlist(b, work_id), serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.membership(work_id), self.items(a), self.items(b)),
                         ((b, 2), [], [(work_id, 0)]))
        self.assertEqual((self.state(a)[1], self.state(b)[1]), (2, 1))

    def test_two_crossed_moves_never_deadlock(self) -> None:
        first, second = self.works("1", "2")
        a, b = self.add("A", first), self.add("B", second)
        holder, contender = self.race(self.move(first, b),
                                      lambda: self.library.add_work_to_playlist(a, second), serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.items(a), self.items(b)), ([(second, 0)], [(first, 1)]))

    def test_a_reorder_waits_for_an_add_and_keeps_it(self) -> None:
        first, second, third = self.works("1", "2", "3")
        playlist_id = self.add("A", first, second)
        holder, contender = self.race(self.move(third, playlist_id),
                                      lambda: self.library.reorder_playlist(playlist_id, [second, first]),
                                      serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual(self.items(playlist_id), [(second, 0), (first, 1), (third, 2)])

    def test_an_add_waits_for_a_reorder_and_appends_after_it(self) -> None:
        first, second, third = self.works("1", "2", "3")
        playlist_id = self.add("A", first, second)
        holder, contender = self.race(lambda scope: playlists.set_order(scope, playlist_id, [second, first]),
                                      lambda: self.library.add_work_to_playlist(playlist_id, third))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual(self.items(playlist_id), [(second, 0), (first, 1), (third, 2)])
        self.assertEqual(self.state(playlist_id)[1], 4)

    def test_an_add_to_a_playlist_being_deleted_is_refused(self) -> None:
        doomed, work_id = self.add("Doomed"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: playlists.delete(scope, doomed),
                                      lambda: self.library.add_work_to_playlist(doomed, work_id))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual((str(contender), self.count("playlist_items")), (_NOT_FOUND, 0))
        self.assertEqual(self.membership(work_id), ("", 0))

    def test_a_delete_waits_for_an_add_and_releases_the_work(self) -> None:
        playlist_id, work_id = self.add("Busy"), self.library.add_work("W")
        holder, contender = self.race(self.move(work_id, playlist_id),
                                      lambda: self.library.delete_playlist(playlist_id), serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.count("playlist_items"), self.membership(work_id)), (0, ("", 2)))

    def test_a_move_out_of_a_playlist_being_deleted_finds_it_released(self) -> None:
        a, b, work_id = self.add("A"), self.add("B"), self.library.add_work("W")
        self.library.add_work_to_playlist(a, work_id)
        holder, contender = self.race(lambda scope: playlists.delete(scope, a),
                                      lambda: self.library.add_work_to_playlist(b, work_id))
        self.assertEqual((holder, contender), (True, None))
        # Released by the delete (2), then added to B (3).
        self.assertEqual((self.membership(work_id), self.items(b)), ((b, 3), [(work_id, 0)]))

    def test_a_removal_waiting_on_a_playlist_delete_finds_the_work_released(self) -> None:
        # READ COMMITTED, as the facade runs it: the Work was in the Playlist
        # when the removal looked, and is in none once the delete commits.
        playlist_id, work_id = self.add("A"), self.library.add_work("W")
        self.library.add_work_to_playlist(playlist_id, work_id)
        holder, contender = self.race(lambda scope: playlists.delete(scope, playlist_id),
                                      lambda: self.library.remove_work_from_playlist(playlist_id, work_id))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.membership(work_id), self.revisions("playlist-order")), (("", 2), {playlist_id: 2}))

    def test_a_work_delete_and_a_playlist_delete_never_deadlock(self) -> None:
        for work_first in (True, False):
            with self.subTest(work_first=work_first):
                self.delete_work_and_playlist(work_first)
        self.assertEqual((self.count("playlists"), self.count("playlist_items"), self.count("works")), (0, 0, 0))

    def delete_work_and_playlist(self, work_first: bool) -> None:
        work_id = self.library.add_work("W")
        playlist_id = self.add("A", work_id)
        if work_first:
            holder, contender = self.race(lambda scope: commands.delete_work(scope, work_id) is not None,
                                          lambda: self.library.delete_playlist(playlist_id))
            self.assertEqual((holder, contender), (True, None))
            expected = 2  # added, then tombstoned with the Work
        else:
            holder, contender = self.race(lambda scope: playlists.delete(scope, playlist_id),
                                          lambda: self.library.delete_work_record(work_id) is not None)
            self.assertEqual((holder, contender), (True, True))
            expected = 3  # added, released, then tombstoned
        self.assertEqual(self.revisions("work-playlist")[work_id], expected)
        # Added (1), then the Work delete's release or the Playlist's tombstone.
        self.assertEqual(self.revisions("playlist-order")[playlist_id], 3 if work_first else 2)

    def test_a_move_of_a_work_being_deleted_is_refused(self) -> None:
        playlist_id, work_id = self.add("A"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work_id) is not None,
                                      lambda: self.library.add_work_to_playlist(playlist_id, work_id))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual((str(contender), self.items(playlist_id)), (_NOT_FOUND, []))

    def test_a_work_delete_waits_for_an_add_and_then_releases_it(self) -> None:
        playlist_id, work_id = self.add("A"), self.library.add_work("W")
        holder, contender = self.race(self.move(work_id, playlist_id),
                                      lambda: self.library.delete_work_record(work_id) is not None,
                                      serializable=True)
        self.assertEqual((holder, contender), (True, True))
        self.assertEqual((self.items(playlist_id), self.revisions("work-playlist"), self.state(playlist_id)[1]),
                         ([], {work_id: 2}, 2))

    def test_two_creates_of_one_id_make_one_playlist(self) -> None:
        playlist_id = generate("PL")
        payload = {"title": "B", "description": "", "original_url": ""}
        holder, contender = self.race(lambda scope: playlists.insert(scope, playlist_id, "A"),
                                      lambda: self.run_command(
                                          lambda scope: playlists.apply_create(scope, playlist_id, payload)))
        self.assertIs(holder, True)
        self.assertEqual((contender[1]["changed"], contender[1]["playlist"]["title"]), (False, "A"))
        self.assertEqual(self.count("playlists"), 1)

    def test_a_create_whose_same_id_winner_is_deleted_first_creates_it_again(self) -> None:
        # The second create waits on the first's insert, then pauses before
        # holding the winner; a delete commits, and the id is free again.
        playlist_id = generate("PL")
        payload = {"title": "Second", "description": "", "original_url": ""}
        inserted, release, paused, resume = (threading.Event() for _ in range(4))
        outcomes: dict[str, Any] = {}
        original = playlists.share
        calls = {"n": 0}

        def pausing(*args: Any) -> Any:
            if threading.current_thread().name == "second":
                calls["n"] += 1
                if calls["n"] == 2:  # the first looked for the id before inserting
                    paused.set()
                    resume.wait(10)
            return original(*args)

        def first(scope: Any) -> bool:
            created = playlists.insert(scope, playlist_id, "First")
            inserted.set()
            release.wait(10)
            return created

        def start(name: str, fn: Callable[[Any], Any]) -> threading.Thread:
            def run() -> None:
                try:
                    outcomes[name] = self.run_command(fn)
                except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                    outcomes[name] = exc
            thread = threading.Thread(target=run, name=name)
            thread.start()
            return thread

        with mock.patch.object(playlists, "share", pausing):
            threads = [start("first", first)]
            inserted.wait(10)
            threads.append(start("second", lambda scope: playlists.apply_create(scope, playlist_id, payload)))
            self.wait_for_lock_waiter()
            release.set()
            threads[0].join(10)
            self.assertTrue(paused.wait(10))
            threads.append(start("third", lambda scope: playlists.delete(scope, playlist_id)))
            threads[2].join(10)
            resume.set()
            threads[1].join(10)
        self.assertEqual((outcomes["first"], outcomes["third"]), (True, True))
        status, result = outcomes["second"]
        self.assertEqual((status, result["changed"], result["playlist"]["title"]), (200, True, "Second"))
        self.assertEqual(self.count("playlists"), 1)

    def test_a_create_of_another_librarys_id_is_refused(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_playlist("Theirs")
        with self.assertRaises(playlists.ForeignPlaylistId):
            self.run_command(lambda scope: playlists.insert(scope, theirs, "Mine"))
        self.assertEqual(self.library.get_all_playlists(), [])

    def test_a_field_edit_of_a_missing_playlist_is_refused(self) -> None:
        with self.assertRaises(PlaylistRuleError):
            self.run_command(lambda scope: playlists.set_field(scope, generate("PL"), "title", "X"))
        self.assertEqual(self.revisions("playlist-field"), {})


_MISSING_PLAYLIST = "PL-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32


class AnswerTests(AnswerAssertions, PlaylistTestCase):
    """The JSON each Playlist command and read answers, spelled out."""

    def named(self, title: str, description: str = "", original_url: str = "", label: str = "") -> str:
        playlist_id = playlist_commands.create(self.db, self.actor.context, title, description, original_url)
        return self.labels.name(playlist_id, f"<PL {label or title}>")

    def named_work(self, title: str) -> str:
        return self.labels.name(self.library.add_work(title), f"<W {title}>")

    def detail(self, playlist_id: str) -> Optional[dict[str, Any]]:
        return playlist_commands.detail(self.db, self.actor.context, playlist_id, self.storage.pdfs_dir)

    def order(self, playlist_id: str) -> list[tuple[str, int]]:
        return self.labels.show([(item["id"], item["position"]) for item in require(self.detail(playlist_id))["items"]])

    def fields(self, playlist_id: str) -> tuple[dict[str, int], int]:
        state = require(playlist_commands.sync_state(self.db, self.actor.context, playlist_id))
        return {field: value["revision"] for field, value in state["fields"].items()}, state["order_revision"]

    def test_create_read_update_and_list(self) -> None:
        db, ctx = self.db, self.actor.context
        course = self.named("Course", "About", "https://example.org/course")
        bare = self.named("  ", label="Bare")
        self.answer(self.detail(course), {
            "created_at": "<s>", "description": "About", "id": "<PL Course>", "items": [],
            "original_url": "https://example.org/course", "title": "Course", "updated_at": "<s>"})
        # The default title, and no original URL.
        self.answer(self.detail(bare), {"description": "", "id": "<PL Bare>", "items": [], "original_url": None,
                                        "title": "Untitled playlist"})
        self.assertIsNone(self.detail(_MISSING_PLAYLIST))
        for fields in ({"title": "Renamed", "description": " About ", "original_url": "", "ignored": "x"},
                       {"title": " Renamed "}, {}):
            self.assertIsNone(playlist_commands.update(db, ctx, course, fields))
        playlist_commands.update(db, ctx, bare, {"description": None, "original_url": False})
        self.assert_refused(lambda: playlist_commands.update(db, ctx, _MISSING_PLAYLIST, {"title": "X"}), "Playlist not found.")
        self.assert_refused(lambda: playlist_commands.update(db, ctx, course, {"title": "x" * 301}), "title exceeds 300 bytes.")
        self.assert_refused(lambda: playlist_commands.create(db, ctx, "x" * 301), "title exceeds 300 bytes.")
        self.assertEqual(self.fields(course), ({"description": 0, "original_url": 1, "title": 1}, 0))
        self.assertEqual(self.fields(bare), (_UNTOUCHED, 0))
        self.assertIsNone(playlist_commands.sync_state(db, ctx, _MISSING_PLAYLIST))
        self.answer(self.detail(course), {"description": "About", "original_url": None, "title": "Renamed"})
        catalog = playlist_commands.catalog(db, ctx)
        row = {"created_at": "<s>", "item_count": 0, "original_url": None, "updated_at": "<s>"}
        self.answer(sorted(self.labels.show(catalog), key=lambda found: found["id"]), [
            {**row, "description": "", "id": "<PL Bare>", "title": "Untitled playlist"},
            {**row, "description": "About", "id": "<PL Course>", "title": "Renamed"}])
        self.assertRegex(etag_for_representation("playlists", catalog), r'^W/"prks-playlists-2-[0-9a-f]+"$')

    def test_membership_order_and_the_work_projection(self) -> None:
        db, ctx = self.db, self.actor.context
        a, b = self.named("A"), self.named("B")
        first, second, third = self.named_work("First"), self.named_work("Second"), self.named_work("Third")
        self.answer(playlist_commands.work_state(db, ctx, first),
                    {"playlist_id": "", "revision": 0, "work_id": "<W First>"})
        for playlist_id, work_id in ((_MISSING_PLAYLIST, first), (a, _MISSING_WORK), (" ", first), (a, " ")):
            with self.subTest(playlist=playlist_id, work=work_id):
                self.assert_refused(lambda: playlist_commands.add_item(db, ctx, playlist_id, work_id), _NOT_FOUND)
        for work_id in (first, first, second):  # the second add changes nothing
            self.assertIsNone(playlist_commands.add_item(db, ctx, a, work_id))
        playlist_commands.add_item(db, ctx, a, third, 0)  # then moved to the front
        self.assertEqual(self.order(a), [("<W Third>", 0), ("<W First>", 1), ("<W Second>", 2)])
        self.assertEqual((self.fields(a)[1], self.fields(b)[1]), (4, 0))
        for work_id, label in ((first, "<W First>"), (second, "<W Second>"), (third, "<W Third>")):
            self.answer(playlist_commands.work_state(db, ctx, work_id),
                        {"playlist_id": "<PL A>", "revision": 1, "work_id": label})
        self.answer(require(self.library.get_work(first)), {"playlist_id": "<PL A>", "playlist_title": "A"})
        # Unknown and repeated ids are dropped; the Works not named keep their order after the named ones.
        playlist_commands.reorder(db, ctx, a, [second, "x", first, second])
        playlist_commands.reorder(db, ctx, a, [])
        self.assert_refused(lambda: playlist_commands.reorder(db, ctx, _MISSING_PLAYLIST, ["x"]), "Playlist not found.")
        self.assertEqual(self.order(a), [("<W Second>", 0), ("<W First>", 1), ("<W Third>", 2)])
        # A move takes the Work out of one order and appends it to the other.
        playlist_commands.add_item(db, ctx, b, second, 5)
        playlist_commands.remove_item(db, ctx, a, second)  # not in it: nothing changes
        playlist_commands.remove_item(db, ctx, a, _MISSING_WORK)
        self.assertEqual(self.order(a), [("<W First>", 1), ("<W Third>", 2)])
        self.assertEqual(self.order(b), [("<W Second>", 0)])
        self.assertEqual((self.fields(a)[1], self.fields(b)[1]), (6, 1))
        playlist_commands.remove_item(db, ctx, b, second)
        self.answer(playlist_commands.work_state(db, ctx, second),
                    {"playlist_id": "", "revision": 3, "work_id": "<W Second>"})
        self.answer(require(self.library.get_work(second)), {"playlist_id": None, "playlist_title": None})
        self.answer(sorted((row["title"], row["item_count"]) for row in playlist_commands.catalog(db, ctx)),
                    [("A", 2), ("B", 0)])

    def test_delete(self) -> None:
        db, ctx = self.db, self.actor.context
        doomed, kept = self.named("Doomed"), self.named("Kept")
        member, other = self.named_work("Member"), self.named_work("Other")
        playlist_commands.add_item(db, ctx, doomed, member)
        playlist_commands.add_item(db, ctx, kept, other)
        self.assertIsNone(playlist_commands.delete(db, ctx, doomed))
        self.assertIsNone(playlist_commands.delete(db, ctx, doomed))  # already gone
        self.assertIsNone(self.detail(doomed))
        self.assertIsNone(playlist_commands.sync_state(db, ctx, doomed))
        self.answer(playlist_commands.work_state(db, ctx, member),
                    {"playlist_id": "", "revision": 2, "work_id": "<W Member>"})
        self.answer(require(self.library.get_work(member)), {"playlist_id": None, "playlist_title": None})
        self.answer([(row["id"], row["item_count"]) for row in playlist_commands.catalog(db, ctx)], [("<PL Kept>", 1)])
        # A Work delete takes the Work out of its Playlist.
        self.assertTrue(self.library.delete_work_record(other))
        self.assertEqual(self.order(kept), [])
        self.answer([(row["id"], row["item_count"]) for row in playlist_commands.catalog(db, ctx)], [("<PL Kept>", 0)])
