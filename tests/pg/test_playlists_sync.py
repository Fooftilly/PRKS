"""The Playlist sync handlers and ``CREATE_WORK``'s Playlist filing on PostgreSQL (L-O-1 #633).

``tests/test_playlists_parity.py`` proves each Playlist operation answers as
on SQLite. These tests cover what only PostgreSQL has: races decided by row
locks and SERIALIZABLE retries, each held open until the other side waits
(§7.1); one ``op_id`` replayed or reused; a stale operation after a
Playlist or a Work was deleted and created again under its id; Library
isolation and a reader; and NUL, which PostgreSQL cannot store.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend import sync_protocol
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.playlist_sync import scope_key
from backend.sync.families import Environment, FamilyHandler
from backend.sync.handlers import playlist as playlist_handlers
from backend.sync.handlers import work_lifecycle
from tests.pg.test_playlists import PlaylistTestCase
from tests.pg.test_works_sync import RecreatedWorkTests, SyncTestCase, create_payload

_PLAYLIST_OPERATIONS = frozenset({"CREATE_PLAYLIST", "SET_PLAYLIST_FIELD", "REORDER_PLAYLIST_ITEMS",
                                  "DELETE_PLAYLIST"})


def playlist_payload(title: str = "Playlist", **changes: str) -> dict[str, str]:
    return {"title": title, "description": "", "original_url": "", **changes}


class PlaylistSyncTestCase(SyncTestCase, PlaylistTestCase):
    def setUp(self) -> None:
        super().setUp()
        # The real filing: the Organization lane's Folder and Playlist, then People's roles.
        self.env = Environment(pdfs_dir=self.storage.pdfs_dir)

    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        changes.setdefault("entity_type", "playlist" if operation in _PLAYLIST_OPERATIONS else "work")
        return super().envelope(operation, entity_id, payload, base, **changes)

    def create(self, title: str = "Playlist", playlist_id: Optional[str] = None, **fields: str) -> dict[str, Any]:
        return self.envelope("CREATE_PLAYLIST", playlist_id or generate("PL"), playlist_payload(title, **fields))

    def create_playlist(self, title: str = "Playlist") -> str:
        data = self.create(title)
        status, result = self.send(data)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return data["entity_id"]

    def field(self, playlist_id: str, field: str, value: str, base: int) -> dict[str, Any]:
        return self.envelope("SET_PLAYLIST_FIELD", playlist_id, {"field": field, "value": value}, base)

    def put(self, work_id: str, playlist_id: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope("SET_WORK_PLAYLIST", work_id, {"playlist_id": playlist_id}, base, **changes)

    def reorder(self, playlist_id: str, work_ids: list[str], base: int) -> dict[str, Any]:
        return self.envelope("REORDER_PLAYLIST_ITEMS", playlist_id, {"work_ids": work_ids}, base)

    def delete(self, playlist_id: str) -> dict[str, Any]:
        return self.envelope("DELETE_PLAYLIST", playlist_id, {})

    def create_work(self, work_id: Optional[str] = None, **payload: Any) -> dict[str, Any]:
        payload.setdefault("source", {"kind": "video", "url": "https://www.youtube.com/watch?v="
                                      + uuid.uuid4().hex[:11]})
        return self.envelope("CREATE_WORK", work_id or generate("W"), create_payload(**payload))

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)


class CreateTests(PlaylistSyncTestCase):
    def test_creation_acknowledges_replays_and_converges(self) -> None:
        data = self.create("  Course ", description="About", original_url="")
        playlist_id = data["entity_id"]
        first = self.send(data)
        self.assertEqual(first, (200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id, "changed": True,
                                       "playlist": {"id": playlist_id, "title": "Course", "description": "About",
                                                    "original_url": None, "item_count": 0}}))
        self.assertEqual(self.send(data), first)  # a replay
        self.library.add_work_to_playlist(playlist_id, self.library.add_work("W"))
        again = self.send(self.create("Other", playlist_id))
        self.assertEqual((again[1]["changed"], again[1]["playlist"]["title"], again[1]["playlist"]["item_count"]),
                         (False, "Course", 1))
        self.assertEqual(self.send(dict(data, payload=playlist_payload("Reused"))), (409, {"code": "OP_ID_REUSE"}))
        # Construction moved no revision; only the add advanced the order.
        self.assertEqual((self.revisions("playlist-field"), self.revisions("playlist-order")),
                         ({}, {playlist_id: 1}))

    def test_another_librarys_id_is_refused_unledgered(self) -> None:
        other = self.other_actor()
        theirs = generate("PL")
        self.assertEqual(self.send(self.create("Theirs", theirs) | {"device_id": str(other.context.device_id)},
                                   other.context)[0], 200)
        before = len(self.ledger())
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.create("Mine", theirs))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((len(self.ledger()), self.library.get_all_playlists()), (before, []))

    def test_nul_is_an_invalid_envelope(self) -> None:
        self.assertEqual(self.send(self.create("a\x00b")), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])

    def test_two_creates_of_one_id_make_one_playlist(self) -> None:
        playlist_id = generate("PL")
        holder, contender = self.race(
            self.applying(playlist_handlers.CREATE_HANDLER, self.create("A", playlist_id)),
            lambda: self.send(self.create("B", playlist_id)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[1]["changed"], contender[1]["playlist"]["title"]), (False, "A"))
        self.assertEqual(self.count("playlists"), 1)


class WorkPlaylistTests(PlaylistSyncTestCase):
    def test_two_moves_of_one_work_advance_it_in_turn(self) -> None:
        a, b = self.create_playlist("A"), self.create_playlist("B")
        work_id = self.library.add_work("W")
        holder, contender = self.race(self.applying(playlist_handlers.WORK_PLAYLIST_HANDLER, self.put(work_id, a, 0)),
                                      lambda: self.send(self.put(work_id, b, 0)), serializable=True)
        self.assertEqual((holder[1]["server_revision"], holder[1]["playlist_title"]), (1, "A"))
        # It waited, retried and saw the first move: its base is stale.
        self.assertEqual(contender, (409, {"code": "REVISION_CONFLICT", "work_id": work_id, "current_revision": 1,
                                           "current_value": a, "requested_value": b}))
        self.assertEqual((self.membership(work_id), self.items(b)), ((a, 1), []))

    def test_concurrent_adds_to_one_playlist_get_two_positions(self) -> None:
        playlist_id = self.create_playlist("A")
        first, second = self.works("1", "2")
        holder, contender = self.race(
            self.applying(playlist_handlers.WORK_PLAYLIST_HANDLER, self.put(first, playlist_id, 0)),
            lambda: self.send(self.put(second, playlist_id, 0)), serializable=True)
        self.assertEqual((holder[1]["changed"], contender[1]["changed"]), (True, True))
        self.assertEqual((self.items(playlist_id), self.state(playlist_id)[1]), ([(first, 0), (second, 1)], 2))

    def test_an_add_to_a_playlist_being_deleted_is_refused(self) -> None:
        doomed, work_id = self.create_playlist("Doomed"), self.library.add_work("W")
        holder, contender = self.race(self.applying(playlist_handlers.DELETE_HANDLER, self.delete(doomed)),
                                      lambda: self.send(self.put(work_id, doomed, 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": work_id, "current_revision": 0}))
        self.assertEqual(self.count("playlist_items"), 0)

    def test_a_delete_waits_for_an_add_and_releases_the_work(self) -> None:
        playlist_id, work_id = self.create_playlist("Busy"), self.library.add_work("W")
        holder, contender = self.race(
            self.applying(playlist_handlers.WORK_PLAYLIST_HANDLER, self.put(work_id, playlist_id, 0)),
            lambda: self.send(self.delete(playlist_id)), serializable=True)
        self.assertEqual((holder[1]["changed"], contender[1]["changed"]), (True, True))
        # The add's base is now stale: the release advanced the Work.
        self.assertEqual(self.membership(work_id), ("", 2))
        self.assertEqual(self.send(self.put(work_id, "", 1))[1]["code"], "ACKNOWLEDGED")  # converged


class OrderTests(PlaylistSyncTestCase):
    def test_a_stale_reorder_never_overwrites_a_membership_change(self) -> None:
        first, second, third = self.works("1", "2", "3")
        a, b = self.create_playlist("A"), self.create_playlist("B")
        for work_id in (first, second, third):
            self.send(self.put(work_id, a, 0))
        self.assertEqual(self.state(a)[1], 3)
        # A move to B advances both orders: a reorder of either from before it is stale.
        self.send(self.put(second, b, 1))
        self.assertEqual((self.state(a)[1], self.state(b)[1]), (4, 1))
        status, result = self.send(self.reorder(a, [third, second, first], 3))
        self.assertEqual((status, result), (409, {"code": "REVISION_CONFLICT", "playlist_id": a,
                                                  "current_revision": 4, "current_count": 2,
                                                  "requested_count": 2}))
        # Unless the order it asks for is the one there: convergence.
        self.assertEqual(self.send(self.reorder(a, [first, third], 3))[1],
                         {"code": "ACKNOWLEDGED", "playlist_id": a, "changed": False, "server_revision": 4,
                          "work_ids": [first, third]})
        self.assertEqual(self.items(a), [(first, 0), (third, 2)])

    def test_a_reorder_waiting_on_an_add_decides_on_it(self) -> None:
        first, second, third = self.works("1", "2", "3")
        playlist_id = self.create_playlist("A")
        for work_id in (first, second):
            self.send(self.put(work_id, playlist_id, 0))
        holder, contender = self.race(
            self.applying(playlist_handlers.WORK_PLAYLIST_HANDLER, self.put(third, playlist_id, 0)),
            lambda: self.send(self.reorder(playlist_id, [second, first], 2)), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"code": "REVISION_CONFLICT", "playlist_id": playlist_id,
                                           "current_revision": 3, "current_count": 3, "requested_count": 3}))
        self.assertEqual(self.items(playlist_id), [(first, 0), (second, 1), (third, 2)])

    def test_an_add_waiting_on_a_reorder_appends_after_it(self) -> None:
        first, second, third = self.works("1", "2", "3")
        playlist_id = self.create_playlist("A")
        for work_id in (first, second):
            self.send(self.put(work_id, playlist_id, 0))
        holder, contender = self.race(
            self.applying(playlist_handlers.ORDER_HANDLER, self.reorder(playlist_id, [second, first], 2)),
            lambda: self.send(self.put(third, playlist_id, 0)))
        self.assertEqual((holder[1]["server_revision"], holder[1]["work_ids"]), (3, [second, first]))
        self.assertEqual(contender[1]["changed"], True)
        self.assertEqual(self.items(playlist_id), [(second, 0), (first, 1), (third, 2)])
        self.assertEqual(self.state(playlist_id)[1], 4)

    def test_a_reorder_of_a_playlist_being_deleted_is_not_found(self) -> None:
        playlist_id = self.create_playlist("Doomed")
        holder, contender = self.race(self.applying(playlist_handlers.DELETE_HANDLER, self.delete(playlist_id)),
                                      lambda: self.send(self.reorder(playlist_id, [], 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": playlist_id}))


class DeleteTests(PlaylistSyncTestCase):
    def test_a_base_from_before_the_delete_never_lands_on_the_playlist_created_again(self) -> None:
        playlist_id = generate("PL")
        self.send(self.create("Old", playlist_id))
        work_id = self.library.add_work("W")
        self.send(self.put(work_id, playlist_id, 0))
        self.assertEqual(self.send(self.delete(playlist_id))[1]["changed"], True)
        self.assertEqual(self.send(self.delete(playlist_id))[1]["changed"], False)
        self.assertEqual(self.send(self.field(playlist_id, "title", "X", 0))[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.send(self.create("New", playlist_id))[1]["changed"], True)
        # Every field was at revision 0 and is now at 1, the order at 2: base-0 edits are stale.
        for field in ("title", "description", "original_url"):
            with self.subTest(field=field):
                status, result = self.send(self.field(playlist_id, field, "Stale", 0))
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, "REVISION_CONFLICT", 1))
        self.assertEqual(self.send(self.put(work_id, playlist_id, 1))[1]["code"], "REVISION_CONFLICT")
        self.send(self.put(work_id, playlist_id, 2))
        # A base observed after the recreation is current and applies.
        self.assertEqual(self.send(self.field(playlist_id, "title", "Fresh", 1))[1]["server_revision"], 2)
        self.assertEqual(self.state(playlist_id), ({"description": 1, "original_url": 1, "title": 2}, 3))


class CreateWorkFilingTests(PlaylistSyncTestCase):
    def test_a_work_created_into_a_playlist_is_appended_without_a_revision(self) -> None:
        playlist_id = self.create_playlist("Talks")
        listed = self.library.add_work("Listed")
        self.send(self.put(listed, playlist_id, 0))
        data = self.create_work(playlist_id=playlist_id)
        status, result = self.send(data)
        self.assertEqual((status, result["changed"], result["playlist_id"]), (200, True, playlist_id))
        self.assertEqual(self.send(data), (status, result))  # a replay
        self.assertEqual(self.items(playlist_id), [(listed, 0), (data["entity_id"], 1)])
        self.assertEqual((self.membership(data["entity_id"]), self.state(playlist_id)[1]), ((playlist_id, 0), 1))
        detail = self.library.get_work(data["entity_id"])
        self.assertEqual((detail["playlist_id"], detail["playlist_title"]), (playlist_id, "Talks"))

    def test_a_missing_or_foreign_playlist_refuses_the_create(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_playlist("Theirs")
        for playlist_id in (generate("PL"), theirs):
            with self.subTest(playlist_id=playlist_id):
                data = self.create_work(playlist_id=playlist_id)
                self.assertEqual(self.send(data), (404, {"code": "PLAYLIST_NOT_FOUND",
                                                         "work_id": data["entity_id"]}))
        self.assertEqual((self.count("works"), self.count("playlist_items")), (0, 0))

    def test_a_create_into_a_playlist_being_deleted_is_refused(self) -> None:
        doomed = self.create_playlist("Doomed")
        data = self.create_work(playlist_id=doomed)
        holder, contender = self.race(self.applying(playlist_handlers.DELETE_HANDLER, self.delete(doomed)),
                                      lambda: self.send(data))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": data["entity_id"]}))
        self.assertEqual(self.count("works"), 0)

    def test_a_delete_waits_for_a_create_into_the_playlist_and_releases_it(self) -> None:
        playlist_id = self.create_playlist("Busy")
        data = self.create_work(playlist_id=playlist_id)
        holder, contender = self.race(self.applying(work_lifecycle.CREATE_HANDLER, data),
                                      lambda: self.send(self.delete(playlist_id)), serializable=True)
        self.assertEqual((holder[1]["changed"], contender[1]["changed"]), (True, True))
        self.assertEqual((self.count("works"), self.membership(data["entity_id"])), (1, ("", 1)))

    def test_two_creates_into_one_playlist_get_two_positions(self) -> None:
        playlist_id = self.create_playlist("Talks")
        first, second = self.create_work(playlist_id=playlist_id), self.create_work(playlist_id=playlist_id)
        holder, contender = self.race(self.applying(work_lifecycle.CREATE_HANDLER, first),
                                      lambda: self.send(second), serializable=True)
        self.assertEqual((holder[1]["changed"], contender[1]["changed"]), (True, True))
        self.assertEqual(self.items(playlist_id), [(first["entity_id"], 0), (second["entity_id"], 1)])


class RecreatedListedWorkTests(PlaylistSyncTestCase):
    recreate = RecreatedWorkTests.recreate

    def test_a_listed_work_delete_tombstones_its_membership(self) -> None:
        playlist_id, work_id = self.create_playlist("A"), generate("W")
        self.recreate(work_id)
        self.assertEqual(self.send(self.put(work_id, playlist_id, 0))[1]["server_revision"], 1)
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], True)
        self.assertEqual((self.items(playlist_id), self.state(playlist_id)[1]), ([], 2))
        self.recreate(work_id)
        self.assertEqual(self.membership(work_id), ("", 2))
        self.assertEqual(self.send(self.put(work_id, playlist_id, 1)), (409, {
            "code": "REVISION_CONFLICT", "work_id": work_id, "current_revision": 2, "current_value": "",
            "requested_value": playlist_id}))
        self.assertEqual(self.send(self.put(work_id, playlist_id, 2))[1]["server_revision"], 3)
        self.assertEqual(self.revisions("playlist-field"), {})
        self.assertNotIn(scope_key(playlist_id, "title"), self.revisions("playlist-field"))


class LedgerAndIsolationTests(PlaylistSyncTestCase):
    def test_another_library_cannot_see_or_change_these_playlists(self) -> None:
        work_id = self.library.add_work("W")
        playlist_id = self.create_playlist("Mine")
        self.send(self.put(work_id, playlist_id, 0))
        other = self.other_actor()
        device = {"device_id": str(other.context.device_id)}
        with facade_context.acting_as(other.context):
            their_work = self.library.add_work("Theirs")
        for data, answer in (
                (self.field(playlist_id, "title", "Theirs", 0),
                 (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": playlist_id, "field": "title"})),
                (self.put(work_id, "", 1), (404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id})),
                (self.put(their_work, playlist_id, 0),
                 (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": their_work, "current_revision": 0})),
                (self.reorder(playlist_id, [work_id], 1),
                 (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": playlist_id})),
                (self.delete(playlist_id),
                 (200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id, "changed": False}))):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data | device, other.context), answer)
        self.assertEqual((self.library.get_playlist(playlist_id)["title"], self.items(playlist_id)),
                         ("Mine", [(work_id, 0)]))

    def test_an_op_id_of_another_library_is_reuse_and_discloses_nothing(self) -> None:
        data = self.create("Mine")
        self.send(data)
        other = self.other_actor()
        replayed = self.send(data | {"device_id": str(other.context.device_id)}, other.context)
        self.assertEqual(replayed, (409, {"code": "OP_ID_REUSE"}))

    def test_a_reader_cannot_send_playlist_operations(self) -> None:
        reader = self.reader_context()
        with self.assertRaises(AccessRefused):
            self.send(self.create("Talks") | {"device_id": str(reader.device_id)}, reader)
        self.assertEqual(self.count("playlists"), 0)
