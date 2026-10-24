"""Playlists, their fields, membership and order on PostgreSQL: what parity cannot show (L-O-1 #633).

``tests/test_playlists_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: refusals of what PostgreSQL text
cannot hold, the tombstones a Playlist or Work delete leaves, Library
isolation, and concurrent writers (each race holds one transaction open
until the other is waiting on it).
"""

from __future__ import annotations

import threading
from typing import Any, Callable
from unittest import mock

from backend.domain.identity.authorization import AccessRefused
from backend.domain.organization import playlists
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.playlist_sync import PlaylistRuleError, scope_key
from tests.pg.test_tags import OrganizationTestCase

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
        self.assertEqual((before["description"], before["original_url"]), ("About", None))
        self.library.update_playlist(playlist_id, {"title": " Course ", "description": "About ",
                                                   "original_url": None})
        self.assertEqual(self.state(playlist_id), (_UNTOUCHED, 0))
        self.assertEqual(self.revisions("playlist-field"), {})
        self.library.update_playlist(playlist_id, {"original_url": "https://example.org", "x": 1})
        self.library.update_playlist(playlist_id, {"original_url": ""})
        self.assertEqual(self.state(playlist_id)[0]["original_url"], 2)
        self.assertIsNone(self.library.get_playlist(playlist_id)["original_url"])
        self.library.update_playlist(playlist_id, {"title": "  "})
        self.assertEqual(self.library.get_playlist(playlist_id)["title"], "Untitled playlist")
        self.refused("Playlist not found.", self.library.update_playlist, generate("PL"), {"title": "X"})

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        playlist_id = self.add("Course")
        for call, args in ((self.library.add_playlist, ("a\x00b",)), (self.library.add_playlist, ("x" * 301,)),
                           (self.library.add_playlist, ("Ok", "d\x00")),
                           (self.library.update_playlist, (playlist_id, {"description": "é" * 2001})),
                           (self.library.update_playlist, (playlist_id, {"title": "ok", "original_url": "\x00"}))):
            with self.subTest(args=[str(arg)[:12] for arg in args]), self.assertRaises(ValueError):
                call(*args)
        self.assertEqual((self.count("playlists"), self.state(playlist_id)), (1, (_UNTOUCHED, 0)))
        self.assertEqual(self.library.get_playlist(playlist_id)["title"], "Course")  # the batch rolled back
        self.assertEqual(self.library.get_playlist(self.add("é" * 150))["title"], "é" * 150)  # 300 bytes


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
        self.assertEqual((detail["playlist_id"], detail["playlist_title"]), (a, "A"))
        self.library.add_work_to_playlist(b, first)
        self.assertEqual((self.membership(first), self.state(a)[1], self.state(b)[1]), ((b, 2), 3, 1))
        self.assertEqual((self.items(a), self.items(b)), ([(second, 1)], [(first, 0)]))
        self.library.remove_work_from_playlist(a, first)  # not in it: nothing changes
        self.library.remove_work_from_playlist(b, first)
        self.assertEqual((self.membership(first), self.state(b)[1]), (("", 3), 2))
        self.assertEqual(self.library.get_work(first)["playlist_title"], None)
        self.assertEqual({row["title"]: row["item_count"] for row in self.library.get_all_playlists()},
                         {"A": 1, "B": 0})

    def test_a_missing_work_or_playlist_is_refused(self) -> None:
        playlist_id, work_id = self.add("A"), self.library.add_work("W")
        for args in ((generate("PL"), work_id), (playlist_id, generate("W")), (" ", work_id), (playlist_id, "")):
            with self.subTest(args=args):
                self.refused(_NOT_FOUND, self.library.add_work_to_playlist, *args)
        self.library.remove_work_from_playlist(playlist_id, generate("W"))
        self.refused("Playlist not found.", self.library.reorder_playlist, generate("PL"), ["x"])
        self.assertEqual((self.items(playlist_id), self.revisions("work-playlist"),
                          self.revisions("playlist-order")), ([], {}, {}))

    def test_an_add_at_a_position_is_two_changes(self) -> None:
        playlist_id = self.add("A")
        first, second, third = self.works("1", "2", "3")
        for work_id in (first, second):
            self.library.add_work_to_playlist(playlist_id, work_id)
        self.library.add_work_to_playlist(playlist_id, third, 1)
        self.assertEqual(self.items(playlist_id), [(first, 0), (third, 1), (second, 2)])
        self.assertEqual(self.state(playlist_id)[1], 4)
        self.assertEqual([item["id"] for item in self.library.get_playlist(playlist_id)["items"]],
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
        self.assertEqual(self.library.get_work(member)["playlist_id"], None)
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
        self.assertEqual([item["id"] for item in self.library.get_playlist(playlist_id)["items"]], [kept])
        self.library.delete_playlist(playlist_id)


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
            self.assertEqual((found["title"], [item["id"] for item in found["items"]]), ("Theirs", [their_work]))

    def test_a_reader_reads_playlists_but_cannot_change_them(self) -> None:
        work_id = self.library.add_work("W")
        playlist_id = self.add("A", work_id)
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_playlist(playlist_id)["title"], "A")
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
