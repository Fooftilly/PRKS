"""Playlists, their fields, membership and order: PostgreSQL answers as SQLite does (L-O-1 #633).

Each call, and each Playlist semantic operation, runs on this leg's library
and on a reference SQLite library holding the same Works and Playlists, and
the results must be equal after generated ids and timestamps become labels.
Inputs avoid the documented differences (``backend/pg_facade/organization.py``):
no Playlist is created again under a deleted id, a Playlist's revisions are
not compared after a Work in it is deleted, and catalogs of Playlists the
facade made in one second (which tie, and then order by their generated
ids) are compared as sets of rows.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.test_tags_parity import SyncParityTestCase, TagParityTestCase, both

_MISSING_PLAYLIST = "PL-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32
_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_PLAYLIST_OPERATIONS = frozenset({"CREATE_PLAYLIST", "SET_PLAYLIST_FIELD", "REORDER_PLAYLIST_ITEMS",
                                  "DELETE_PLAYLIST"})


class PlaylistParityTestCase(TagParityTestCase):
    def playlist_both(self, label: str, description: str = "", original_url: str = "") -> tuple[str, str]:
        ids = [side.label(side.db.add_playlist(label, description, original_url), f"<PL {label}>")
               for side in self.sides()]
        return ids[0], ids[1]

    def same_set(self, call: str) -> None:
        """``call()`` on both sides, as sets of rows (see the module docstring)."""
        mine, theirs = (sorted(map(repr, side.normal(getattr(side.db, call)()))) for side in self.sides())
        self.assertEqual(mine, theirs, call)

    def each(self, call: str, *pairs: tuple[Any, Any]) -> None:
        """``call`` on each side, results not compared (a setup step)."""
        for index, side in enumerate(self.sides()):
            getattr(side.db, call)(*(pair[index] for pair in pairs))


@parametrize_backends
class PlaylistFacadeParityTests(PlaylistParityTestCase):
    def test_create_read_update_and_list(self) -> None:
        course = self.playlist_both("Course", "About", "https://example.org/course")
        bare = self.playlist_both("  ")
        self.same_pair("get_playlist", course)
        self.same_pair("get_playlist", bare)  # the default title, NULL original_url
        self.same_pair("get_playlist", both(_MISSING_PLAYLIST))
        self.same_pair("update_playlist", course, both({"title": "Renamed", "description": " About ",
                                                        "original_url": "", "ignored": "x"}))
        self.same_pair("update_playlist", course, both({"title": " Renamed "}))
        self.same_pair("update_playlist", course, both({}))
        self.same_pair("update_playlist", bare, both({"description": None, "original_url": False}))
        self.refused_pair("update_playlist", both(_MISSING_PLAYLIST), both({"title": "X"}))
        self.refused_pair("update_playlist", course, both({"title": "x" * 301}))
        self.refused_pair("add_playlist", both("x" * 301))
        for playlist in (course, bare, both(_MISSING_PLAYLIST)):
            self.same_pair("get_playlist_sync_state", playlist)
        self.same_pair("get_playlist", course)
        self.same_set("get_all_playlists")
        # The catalog ETag hashes the rows, ids and times included: its shape matches.
        rows = self.both("get_all_playlists")
        self.assertEqual([side.db.etag_playlists_catalog(row).split("-")[2]
                          for side, row in zip(self.sides(), rows)], ["2"] * 2)

    def test_membership_order_and_the_work_projection(self) -> None:
        a, b = self.playlist_both("A"), self.playlist_both("B")
        first, second, third = self.work_both("First"), self.work_both("Second"), self.work_both("Third")
        self.same_pair("get_work_playlist_state", first)
        for args in ((both(_MISSING_PLAYLIST), first), (a, both(_MISSING_WORK)), (both(" "), first),
                     (a, both(" "))):
            with self.subTest(args=args[0][0]):
                self.refused_pair("add_work_to_playlist", *args)
        self.same_pair("add_work_to_playlist", a, first)
        self.same_pair("add_work_to_playlist", a, first)  # already there: nothing changes
        self.same_pair("add_work_to_playlist", a, second)
        self.same_pair("add_work_to_playlist", a, third, both(0))  # then moved to the front
        self.same_pair("get_playlist", a)
        for playlist in (a, b):
            self.same_pair("get_playlist_sync_state", playlist)
        for work in (first, second, third):
            self.same_pair("get_work_playlist_state", work)
        detail = self.same_pair("get_work", first)
        self.assertEqual(detail["playlist_title"], "A")
        self.same_pair("reorder_playlist", a, ([second[0], "x", first[0], second[0]],
                                               [second[1], "x", first[1], second[1]]))
        self.same_pair("reorder_playlist", a, both([]))
        self.refused_pair("reorder_playlist", both(_MISSING_PLAYLIST), both(["x"]))
        self.same_pair("get_playlist", a)
        # A move takes the Work out of one order and appends it to the other.
        self.same_pair("add_work_to_playlist", b, second, both(5))
        self.same_pair("remove_work_from_playlist", a, second)  # not in it: nothing changes
        self.same_pair("remove_work_from_playlist", a, both(_MISSING_WORK))
        for playlist in (a, b):
            self.same_pair("get_playlist", playlist)
            self.same_pair("get_playlist_sync_state", playlist)
        self.same_pair("remove_work_from_playlist", b, second)
        self.same_pair("get_work_playlist_state", second)
        self.same_pair("get_work", second)
        self.same_set("get_all_playlists")

    def test_delete(self) -> None:
        doomed, kept = self.playlist_both("Doomed"), self.playlist_both("Kept")
        member, other = self.work_both("Member"), self.work_both("Other")
        self.each("add_work_to_playlist", doomed, member)
        self.each("add_work_to_playlist", kept, other)
        self.same_pair("delete_playlist", doomed)
        self.same_pair("delete_playlist", doomed)  # already gone
        self.same_pair("get_playlist", doomed)
        self.same_pair("get_playlist_sync_state", doomed)
        self.same_pair("get_work_playlist_state", member)
        self.same_pair("get_work", member)
        self.same_set("get_all_playlists")
        # A Work delete takes the Work out of its Playlist.
        self.assertTrue(all(self.both_pair("delete_work_record", other)))
        self.same_pair("get_playlist", kept)
        self.same_set("get_all_playlists")

    def both_pair(self, call: str, *pairs: tuple[Any, Any]) -> list[Any]:
        return [getattr(side.db, call)(*(pair[index] for pair in pairs))
                for index, side in enumerate(self.sides())]


@parametrize_backends
class PlaylistSyncParityTests(SyncParityTestCase):
    """The five Playlist operations and ``CREATE_WORK``'s filing into a Playlist."""

    def entity_type(self, operation: str) -> str:
        return "playlist" if operation in _PLAYLIST_OPERATIONS else super().entity_type(operation)

    def new_id(self, prefix: str, label: str) -> str:
        entity_id = prefix + "-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(entity_id, f"<{prefix} {label}>")
        return entity_id

    def create_playlist(self, label: str, **fields: str) -> str:
        playlist_id = self.new_id("PL", label)
        payload = {"title": label, "description": "", "original_url": "", **fields}
        self.assertEqual(self.send_both("CREATE_PLAYLIST", playlist_id, payload)[0], 200)
        return playlist_id

    def same_catalog(self) -> None:
        # Playlists created within the same second may tie on catalog order.
        # Their generated IDs can then sort differently across databases.
        # Compare the complete normalized rows without requiring an order.
        mine, theirs = (
            sorted(side.normal(side.db.get_all_playlists()), key=repr)
            for side in self.sides()
        )
        self.assertEqual(mine, theirs, "get_all_playlists")

    def test_create_playlist(self) -> None:
        playlist_id = self.new_id("PL", "Course")
        op_id = str(uuid.uuid4())
        payload = {"title": "  Course ", "description": "About", "original_url": "https://example.org"}
        self.send_both("CREATE_PLAYLIST", playlist_id, payload, op_id=op_id)
        self.send_both("CREATE_PLAYLIST", playlist_id, payload, op_id=op_id)  # a replay
        self.send_both("CREATE_PLAYLIST", playlist_id, dict(payload, title="Other"))  # converged
        self.send_both("CREATE_PLAYLIST", self.new_id("PL", "Empty"), dict(payload, title="  ", original_url=""))
        self.send_both("CREATE_PLAYLIST", self.new_id("PL", "Base"), payload, base=0)
        self.send_both("CREATE_PLAYLIST", "PL-1", payload)
        self.send_both("CREATE_PLAYLIST", self.new_id("F", "Prefix"), payload)
        self.send_both("CREATE_PLAYLIST", self.new_id("PL", "Short"), {"title": "x"})
        self.send_both("CREATE_PLAYLIST", self.new_id("PL", "Long"), dict(payload, title="x" * 301))
        self.send_both("CREATE_PLAYLIST", self.new_id("PL", "Typed"), dict(payload, description=5))
        self.same_pair("get_playlist", both(playlist_id))
        self.same_catalog()

    def test_playlist_fields(self) -> None:
        playlist_id = self.create_playlist("Course")
        for field, value, base in (("title", "Renamed", 0), ("title", " Renamed ", 0), ("title", "Stale", 0),
                                   ("title", "Future", 7), ("title", "  ", 1), ("description", "About", 0),
                                   ("original_url", "https://example.org", 0), ("original_url", "", 1),
                                   ("description", "x" * 4001, 1), ("colour", "x", 0), ("title", 5, 0)):
            with self.subTest(field=field, value=str(value)[:10], base=base):
                self.send_both("SET_PLAYLIST_FIELD", playlist_id, {"field": field, "value": value}, base)
        self.send_both("SET_PLAYLIST_FIELD", playlist_id, {"field": "title", "value": "X"}, None)
        self.send_both("SET_PLAYLIST_FIELD", playlist_id, {"field": "title"}, 0)
        self.send_both("SET_PLAYLIST_FIELD", _MISSING_PLAYLIST, {"field": "title", "value": "X"}, 0)
        self.same_pair("get_playlist_sync_state", both(playlist_id))
        self.same_pair("get_playlist", both(playlist_id))

    def test_work_playlist_and_order(self) -> None:
        a, b = self.create_playlist("A"), self.create_playlist("B")
        works = [self.work_both(label) for label in ("First", "Second", "Third")]
        first, second, third = works
        for work in works:
            self.send_both("SET_WORK_PLAYLIST", work, {"playlist_id": a}, 0)
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": a}, 0)  # converged on a stale base
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": b}, 0)  # stale: a conflict
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": b}, 4)  # future
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": _MISSING_PLAYLIST}, 1)
        self.send_both("SET_WORK_PLAYLIST", both(_MISSING_WORK), {"playlist_id": a}, 0)
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": " a"}, 1)
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": a}, None)
        self.send_both("SET_WORK_PLAYLIST", first, {"playlist_id": a, "x": 1}, 1)
        order = tuple({"work_ids": [w if isinstance(w, str) else w[i] for w in (third, "x", first, third)]}
                      for i in (0, 1))
        self.send_both("REORDER_PLAYLIST_ITEMS", a, order, 3)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, order, 3)  # stale, but already that order
        reverse = tuple([w[i] for w in (second, first)] for i in (0, 1))
        self.send_both("REORDER_PLAYLIST_ITEMS", a, ({"work_ids": reverse[0]}, {"work_ids": reverse[1]}), 3)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, ({"work_ids": reverse[0]}, {"work_ids": reverse[1]}), 9)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, order, 3)
        # A move between Playlists advances both orders: a reorder from before it is stale.
        self.send_both("SET_WORK_PLAYLIST", second, {"playlist_id": b}, 1)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, ({"work_ids": reverse[0]}, {"work_ids": reverse[1]}), 4)
        self.send_both("REORDER_PLAYLIST_ITEMS", _MISSING_PLAYLIST, {"work_ids": []}, 0)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, {"work_ids": [" "]}, 0)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, {"work_ids": "x"}, 0)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, {"work_ids": ["x"] * 1001}, 0)
        self.send_both("REORDER_PLAYLIST_ITEMS", a, {"work_ids": []}, None)
        self.send_both("SET_WORK_PLAYLIST", third, {"playlist_id": ""}, 1)
        for playlist in (a, b):
            self.same_pair("get_playlist_sync_state", both(playlist))
            self.same_pair("get_playlist", both(playlist))
        for work in works:
            self.same_pair("get_work_playlist_state", work)
        self.same_catalog()

    def test_delete_playlist(self) -> None:
        doomed, kept = self.create_playlist("Doomed"), self.create_playlist("Kept")
        member = self.work_both("Member")
        self.send_both("SET_WORK_PLAYLIST", member, {"playlist_id": doomed}, 0)
        self.send_both("DELETE_PLAYLIST", doomed, {"x": 1})
        self.send_both("DELETE_PLAYLIST", doomed, {}, base=0)
        self.send_both("DELETE_PLAYLIST", doomed, {})
        self.send_both("DELETE_PLAYLIST", doomed, {})  # already gone
        self.send_both("SET_WORK_PLAYLIST", member, {"playlist_id": doomed}, 2)
        self.send_both("SET_WORK_PLAYLIST", member, {"playlist_id": kept}, 1)  # stale after the release
        self.send_both("SET_WORK_PLAYLIST", member, {"playlist_id": kept}, 2)
        self.send_both("SET_PLAYLIST_FIELD", doomed, {"field": "title", "value": "X"}, 0)
        self.send_both("REORDER_PLAYLIST_ITEMS", doomed, {"work_ids": []}, 0)
        self.same_pair("get_work_playlist_state", member)
        self.same_pair("get_playlist", both(kept))
        self.same_catalog()

    def test_create_work_files_the_work_into_a_playlist(self) -> None:
        playlist_id = self.create_playlist("Talks")
        payload = {"title": "A talk", "status": "", "doc_type": "", "abstract": "", "author_text": "",
                   "year": "", "published_date": "", "urldate": "", "private_notes": "", "thumb_url": "",
                   "source": {"kind": "video", "url": _VIDEO}, "folder_id": "", "playlist_id": playlist_id,
                   "roles": []}
        first = self.new_id("W", "First")
        self.send_both("CREATE_WORK", first, payload)
        self.send_both("CREATE_WORK", first, payload)  # converged
        second = self.new_id("W", "Second")
        self.send_both("CREATE_WORK", second, dict(payload, source={"kind": "video", "url": _VIDEO + "1"}))
        self.send_both("CREATE_WORK", self.new_id("W", "Lost"), dict(payload, playlist_id=_MISSING_PLAYLIST,
                                                                     source={"kind": "video", "url": _VIDEO + "2"}))
        for work_id in (first, second):
            self.same_pair("get_work_playlist_state", both(work_id))
        self.same_pair("get_playlist_sync_state", both(playlist_id))
        # SQLite's sync insert leaves file_path NULL where PostgreSQL projects "".
        mine, theirs = (side.normal(side.db.get_playlist(playlist_id)) for side in self.sides())
        for found in (mine, theirs):
            for item in found["items"]:
                item.pop("file_path")
        self.assertEqual(mine, theirs, "get_playlist")
        self.same_catalog()
