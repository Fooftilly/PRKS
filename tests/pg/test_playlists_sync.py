"""The Playlist sync handlers and ``CREATE_WORK``'s Playlist filing on PostgreSQL (L-O-1 #633).

:class:`AnswerTests` spells out what each Playlist operation answers. The
rest cover races decided by row
locks and SERIALIZABLE retries, each held open until the other side waits
(§7.1); one ``op_id`` replayed or reused; a stale operation after a
Playlist or a Work was deleted and created again under its id; Library
isolation and a reader; and NUL, which PostgreSQL cannot store.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend.sync import envelope
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.entity_ids import generate
from tests import acting_context as facade_context
from backend.domain.organization.playlist_rules import scope_key
from backend.sync.families import Environment, FamilyHandler
from backend.sync.handlers import playlist as playlist_handlers
from backend.sync.handlers import work_lifecycle
from tests.pg.test_playlists import PlaylistTestCase
from tests.pg.test_works_sync import RecreatedWorkTests, SyncTestCase, create_payload
from tests.narrowing import require
from backend.domain.organization import playlist_commands
from backend.domain.works import work_commands
from tests import works_fixtures

_PLAYLIST_OPERATIONS = frozenset({"CREATE_PLAYLIST", "SET_PLAYLIST_FIELD", "REORDER_PLAYLIST_ITEMS",
                                  "DELETE_PLAYLIST"})


def playlist_payload(title: str = "Playlist", **changes: str) -> dict[str, str]:
    return {"title": title, "description": "", "original_url": "", **changes}


class PlaylistSyncTestCase(SyncTestCase, PlaylistTestCase):  # pyright: ignore[reportIncompatibleMethodOverride]  # MRO picks the first base
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

    def playlist_field(self, playlist_id: str, field: str, value: str, base: int) -> dict[str, Any]:
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
        op = envelope.normalize_envelope(data)
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
        playlist_commands.add_item(self.db, self.ctx, playlist_id, works_fixtures.add_work(self.db, self.ctx, self.storage, "W"))
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
        self.assertEqual((len(self.ledger()), playlist_commands.catalog(self.db, self.ctx)), (before, []))

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


class EnvelopeTests(PlaylistSyncTestCase):
    """What the envelope and payload decide before any write (retired ``tests/test_playlist_sync.py``)."""

    def test_ids_fields_payloads_and_base_revisions_are_refused_before_the_ledger(self) -> None:
        playlist_id = self.create_playlist("Lectures")
        before = self.ledger()
        refused: list[tuple[str, dict[str, Any], str]] = []
        for bad in ("PL-ABCD1234", "lectures", "F-" + "A" * 32):
            refused.append(("id " + bad, self.create("X", bad), "INVALID_ENVELOPE"))
        for name in ("item_count", "created_at", "updated_at", "id"):
            refused.append(("field " + name, self.playlist_field(playlist_id, name, "x", 0), "INVALID_ENVELOPE"))
        refused.append(("oversized", self.playlist_field(playlist_id, "description", "x" * 5000, 0), "INVALID_ENVELOPE"))
        for index, payload in enumerate(({"work_ids": "abc"}, {"work_ids": [""]}, {"work_ids": [1]},
                                         {"work_ids": ["a"], "extra": 1}, {})):
            refused.append(("order %d" % index, self.envelope("REORDER_PLAYLIST_ITEMS", playlist_id, payload, 0),
                            "INVALID_ENVELOPE"))
        refused.append(("delete payload", self.envelope("DELETE_PLAYLIST", playlist_id, {"cascade": True}),
                        "INVALID_ENVELOPE"))
        refused.append(("delete base", self.envelope("DELETE_PLAYLIST", playlist_id, {}, 0), "INVALID_BASE_REVISION"))
        refused.append(("field base", self.envelope("SET_PLAYLIST_FIELD", playlist_id,
                                                    {"field": "title", "value": "X"}), "INVALID_BASE_REVISION"))
        for label, data, code in refused:
            with self.subTest(label):
                self.assertEqual(self.send(data), (400, {"code": code}))
        self.assertEqual((self.ledger(), require(playlist_commands.detail(self.db, self.ctx, playlist_id, self.pdfs_dir))["description"]), (before, ""))

    def test_a_title_may_repeat(self) -> None:
        self.create_playlist("Lectures")
        self.create_playlist("Lectures")
        self.assertEqual([row["title"] for row in playlist_commands.catalog(self.db, self.ctx)], ["Lectures", "Lectures"])


class WorkPlaylistTests(PlaylistSyncTestCase):
    def test_two_moves_of_one_work_advance_it_in_turn(self) -> None:
        a, b = self.create_playlist("A"), self.create_playlist("B")
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
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
        doomed, work_id = self.create_playlist("Doomed"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        holder, contender = self.race(self.applying(playlist_handlers.DELETE_HANDLER, self.delete(doomed)),
                                      lambda: self.send(self.put(work_id, doomed, 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": work_id, "current_revision": 0}))
        self.assertEqual(self.count("playlist_items"), 0)

    def test_a_delete_waits_for_an_add_and_releases_the_work(self) -> None:
        playlist_id, work_id = self.create_playlist("Busy"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
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
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        self.send(self.put(work_id, playlist_id, 0))
        self.assertEqual(self.send(self.delete(playlist_id))[1]["changed"], True)
        self.assertEqual(self.send(self.delete(playlist_id))[1]["changed"], False)
        self.assertEqual(self.send(self.playlist_field(playlist_id, "title", "X", 0))[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.send(self.create("New", playlist_id))[1]["changed"], True)
        # Every field was at revision 0 and is now at 1, the order at 2: base-0 edits are stale.
        for field in ("title", "description", "original_url"):
            with self.subTest(field=field):
                status, result = self.send(self.playlist_field(playlist_id, field, "Stale", 0))
                self.assertEqual((status, result["code"], result["current_revision"]),
                                 (409, "REVISION_CONFLICT", 1))
        self.assertEqual(self.send(self.put(work_id, playlist_id, 1))[1]["code"], "REVISION_CONFLICT")
        self.send(self.put(work_id, playlist_id, 2))
        # A base observed after the recreation is current and applies.
        self.assertEqual(self.send(self.playlist_field(playlist_id, "title", "Fresh", 1))[1]["server_revision"], 2)
        self.assertEqual(self.state(playlist_id), ({"description": 1, "original_url": 1, "title": 2}, 3))


class CreateWorkFilingTests(PlaylistSyncTestCase):
    def test_a_work_created_into_a_playlist_is_appended_without_a_revision(self) -> None:
        playlist_id = self.create_playlist("Talks")
        listed = works_fixtures.add_work(self.db, self.ctx, self.storage, "Listed")
        self.send(self.put(listed, playlist_id, 0))
        data = self.create_work(playlist_id=playlist_id)
        status, result = self.send(data)
        self.assertEqual((status, result["changed"], result["playlist_id"]), (200, True, playlist_id))
        self.assertEqual(self.send(data), (status, result))  # a replay
        self.assertEqual(self.items(playlist_id), [(listed, 0), (data["entity_id"], 1)])
        self.assertEqual((self.membership(data["entity_id"]), self.state(playlist_id)[1]), ((playlist_id, 0), 1))
        detail = work_commands.detail(self.db, self.ctx, self.pdfs_dir, data["entity_id"])
        assert detail is not None
        self.assertEqual((detail["playlist_id"], detail["playlist_title"]), (playlist_id, "Talks"))

    def test_a_missing_or_foreign_playlist_refuses_the_create(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = playlist_commands.create(self.db, self.ctx, "Theirs")
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
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        playlist_id = self.create_playlist("Mine")
        self.send(self.put(work_id, playlist_id, 0))
        other = self.other_actor()
        device = {"device_id": str(other.context.device_id)}
        with facade_context.acting_as(other.context):
            their_work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs")
        for data, answer in (
                (self.playlist_field(playlist_id, "title", "Theirs", 0),
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
        self.assertEqual((require(playlist_commands.detail(self.db, self.ctx, playlist_id, self.pdfs_dir))["title"], self.items(playlist_id)),
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


class AnswerTests(PlaylistSyncTestCase):
    """What each Playlist operation answers, spelled out."""

    def test_create_answers(self) -> None:
        data = self.create("  Course ", description="About", original_url="https://example.org")
        playlist_id = data["entity_id"]
        self.assertEqual(self.send(data), (200, {
            "code": "ACKNOWLEDGED", "changed": True, "playlist_id": playlist_id,
            "playlist": {"id": playlist_id, "title": "Course", "description": "About",
                         "original_url": "https://example.org", "item_count": 0}}))
        empty = self.create("  ")
        self.assertEqual(self.send(empty)[1]["playlist"], {
            "id": empty["entity_id"], "title": "Untitled playlist", "description": "", "original_url": None,
            "item_count": 0})
        for bad in (self.create("Base") | {"base_revision": 0}, self.create("Id", "PL-1"),
                    self.create("Prefix", generate("F")), self.envelope("CREATE_PLAYLIST", generate("PL"), {"title": "x"}),
                    self.create("x" * 301), self.envelope("CREATE_PLAYLIST", generate("PL"), playlist_payload() | {"description": 5})):
            with self.subTest(entity_id=bad["entity_id"], payload=str(bad["payload"])[:40]):
                expected = "INVALID_BASE_REVISION" if bad["base_revision"] == 0 else "INVALID_ENVELOPE"
                self.assertEqual(self.send(bad), (400, {"code": expected}))

    def test_field_decision_table(self) -> None:
        playlist_id = self.create_playlist("Course")
        sent = lambda field, value, base: self.send(self.playlist_field(playlist_id, field, value, base))
        self.assertEqual(sent("title", "Renamed", 0), (200, {
            "code": "ACKNOWLEDGED", "changed": True, "playlist_id": playlist_id, "field": "title",
            "server_revision": 1, "value_omitted": True}))
        self.assertEqual(sent("title", " Renamed ", 0)[1]["changed"], False)
        self.assertEqual(sent("title", "Stale", 0), (409, {
            "code": "REVISION_CONFLICT", "playlist_id": playlist_id, "field": "title", "current_revision": 1,
            "current_value": "Renamed", "requested_value": "Stale"}))
        self.assertEqual(sent("title", "Future", 7), (400, {
            "code": "FUTURE_REVISION", "playlist_id": playlist_id, "field": "title", "current_revision": 1,
            "current_value": "Renamed", "requested_value": "Future"}))
        self.assertEqual(sent("title", "  ", 1)[1]["server_revision"], 2)
        self.assertEqual(sent("original_url", "https://example.org", 0)[1]["server_revision"], 1)
        self.assertEqual(sent("original_url", "", 1)[1]["server_revision"], 2)
        for field, value, base in (("description", "x" * 4001, 1), ("colour", "x", 0), ("title", 5, 0)):
            with self.subTest(field=field):
                self.assertEqual(sent(field, value, base), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.envelope("SET_PLAYLIST_FIELD", playlist_id, {"field": "title", "value": "X"})),
                         (400, {"code": "INVALID_BASE_REVISION"}))
        missing = generate("PL")
        self.assertEqual(self.send(self.playlist_field(missing, "title", "X", 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": missing, "field": "title"}))
        detail = require(playlist_commands.detail(self.db, self.ctx, playlist_id, self.pdfs_dir))
        self.assertEqual((detail["title"], detail["original_url"]), ("Untitled playlist", None))

    def test_work_playlist_and_order_decision_tables(self) -> None:
        a, b = self.create_playlist("A"), self.create_playlist("B")
        first, second, third = self.works("First", "Second", "Third")
        for work_id in (first, second, third):
            self.assertEqual(self.send(self.put(work_id, a, 0)), (200, {
                "code": "ACKNOWLEDGED", "changed": True, "work_id": work_id, "playlist_id": a,
                "playlist_title": "A", "server_revision": 1}))
        self.assertEqual(self.send(self.put(first, a, 0))[1]["changed"], False)  # converged on a stale base
        self.assertEqual(self.send(self.put(first, b, 0)), (409, {
            "code": "REVISION_CONFLICT", "work_id": first, "current_revision": 1, "current_value": a,
            "requested_value": b}))
        self.assertEqual(self.send(self.put(first, b, 4)), (400, {
            "code": "FUTURE_REVISION", "work_id": first, "current_revision": 1, "current_value": a,
            "requested_value": b}))
        self.assertEqual(self.send(self.put(first, generate("PL"), 1)),
                         (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": first, "current_revision": 1}))
        missing = generate("W")
        self.assertEqual(self.send(self.put(missing, a, 0)), (404, {"code": "ENTITY_NOT_FOUND", "work_id": missing}))
        for bad in (self.put(first, " a", 1), self.envelope("SET_WORK_PLAYLIST", first, {"playlist_id": a, "x": 1}, 1)):
            self.assertEqual(self.send(bad), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.put(first, a, None)), (400, {"code": "INVALID_BASE_REVISION"}))  # type: ignore[arg-type]
        # Unknown and repeated ids are dropped; the rest of the order follows.
        order = [third, "x", first, third]
        answer = (200, {"code": "ACKNOWLEDGED", "changed": True, "playlist_id": a, "server_revision": 4,
                        "work_ids": [third, first, second]})
        self.assertEqual(self.send(self.reorder(a, order, 3)), answer)
        self.assertEqual(self.send(self.reorder(a, order, 3)), (200, answer[1] | {"changed": False}))
        self.assertEqual(self.send(self.reorder(a, [second, first], 3)), (409, {
            "code": "REVISION_CONFLICT", "playlist_id": a, "current_revision": 4, "current_count": 3,
            "requested_count": 3}))
        self.assertEqual(self.send(self.reorder(a, [second, first], 9))[1]["code"], "FUTURE_REVISION")
        # A move between Playlists advances both orders: a reorder from before it is stale.
        self.assertEqual(self.send(self.put(second, b, 1))[1]["server_revision"], 2)
        self.assertEqual(self.send(self.reorder(a, [second, first], 4)), (409, {
            "code": "REVISION_CONFLICT", "playlist_id": a, "current_revision": 5, "current_count": 2,
            "requested_count": 2}))
        unknown = generate("PL")
        self.assertEqual(self.send(self.reorder(unknown, [], 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": unknown}))
        for work_ids in ([" "], "x", ["x"] * 1001):
            self.assertEqual(self.send(self.reorder(a, work_ids, 0)), (400, {"code": "INVALID_ENVELOPE"}))  # type: ignore[arg-type]
        self.assertEqual(self.send(self.put(third, "", 1)), (200, {
            "code": "ACKNOWLEDGED", "changed": True, "work_id": third, "playlist_id": "", "playlist_title": "",
            "server_revision": 2}))
        self.assertEqual((self.state(a)[1], self.state(b)[1]), (6, 1))

    def test_delete_answers(self) -> None:
        doomed, kept = self.create_playlist("Doomed"), self.create_playlist("Kept")
        [member] = self.works("Member")
        self.send(self.put(member, doomed, 0))
        self.assertEqual(self.send(self.envelope("DELETE_PLAYLIST", doomed, {"x": 1})), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.envelope("DELETE_PLAYLIST", doomed, {}, 0)), (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(self.send(self.delete(doomed)), (200, {"code": "ACKNOWLEDGED", "changed": True,
                                                                "playlist_id": doomed}))
        self.assertEqual(self.send(self.delete(doomed))[1]["changed"], False)
        self.assertEqual(self.send(self.put(member, doomed, 2)),
                         (404, {"code": "PLAYLIST_NOT_FOUND", "work_id": member, "current_revision": 2}))
        # The release advanced the Work: a base from before it is stale.
        self.assertEqual(self.send(self.put(member, kept, 1))[1], {
            "code": "REVISION_CONFLICT", "work_id": member, "current_revision": 2, "current_value": "",
            "requested_value": kept})
        self.assertEqual(self.send(self.put(member, kept, 2))[1]["server_revision"], 3)
        self.assertEqual(self.send(self.playlist_field(doomed, "title", "X", 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": doomed, "field": "title"}))
        self.assertEqual(self.send(self.reorder(doomed, [], 0)), (404, {"code": "ENTITY_NOT_FOUND", "playlist_id": doomed}))
