"""Playlists: five shapes, and parity with the ordinary endpoints.

The interesting decisions here are which unit each change belongs to. A
playlist's three columns are independent FIELDS; which playlist a video is in
is a SCALAR on the VIDEO, because a video is in at most one; and the order is
an AGGREGATE under one revision.
"""
import tempfile
import unittest
import uuid

from backend import entity_ids, playlist_sync as playlists, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class PlaylistSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-playlist-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    # ---- helpers ----------------------------------------------------------

    def send(self, operation, entity_type, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type=entity_type, entity_id=entity_id, payload=payload,
            base_revision=base, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))

    def create(self, title, description="", original_url="", playlist_id=None):
        pid = playlist_id or entity_ids.generate("PL")
        status, result = self.send("CREATE_PLAYLIST", "playlist", pid, dict(
            title=title, description=description, original_url=original_url), None)
        return pid, status, result

    def field(self, playlist_id, name, value, base):
        return self.send("SET_PLAYLIST_FIELD", "playlist", playlist_id,
                         dict(field=name, value=value), base)

    def file_in(self, work_id, playlist_id, base):
        return self.send("SET_WORK_PLAYLIST", "work", work_id,
                         dict(playlist_id=playlist_id), base)

    def reorder(self, playlist_id, work_ids, base):
        return self.send("REORDER_PLAYLIST_ITEMS", "playlist", playlist_id,
                         dict(work_ids=list(work_ids)), base)

    def work(self, title):
        return self.db.add_work(title=title, doc_type="video")

    def stored(self, playlist_id):
        rows = self.db.execute_query("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
        return rows[0] if rows else None

    def revision(self, playlist_id, field):
        with self.db.connection() as conn:
            return playlists.get_revision(conn, playlist_id, field)

    def order_revision(self, playlist_id):
        with self.db.connection() as conn:
            return playlists.get_order_revision(conn, playlist_id)

    def work_revision(self, work_id):
        with self.db.connection() as conn:
            return playlists.get_work_revision(conn, work_id)

    def order(self, playlist_id):
        with self.db.connection() as conn:
            return playlists.current_order(conn, playlist_id)

    # ---- construction -----------------------------------------------------

    def test_a_playlist_is_created_under_the_id_the_client_minted(self):
        pid, status, result = self.create("Lectures", description="Term one")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["playlist"]["title"], "Lectures")
        self.assertEqual(result["playlist"]["item_count"], 0)
        self.assertEqual(self.stored(pid)["description"], "Term one")
        # Construction is not mutation: nothing has "changed" yet.
        for name in playlists.FIELDS:
            self.assertEqual(self.revision(pid, name), 0, name)
        self.assertEqual(self.order_revision(pid), 0)

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("PL-ABCD1234", "lectures", "F-" + "A" * 32):
            with self.subTest(bad=bad):
                _, status, result = self.create("X", playlist_id=bad)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_empty_title_becomes_the_placeholder_on_both_paths(self):
        pid, status, _ = self.create("   ")
        self.assertEqual(status, 200)
        self.assertEqual(self.stored(pid)["title"], "Untitled playlist")
        other = self.db.add_playlist("")
        self.assertEqual(self.stored(other)["title"], "Untitled playlist")

    def test_replaying_a_creation_changes_nothing_twice(self):
        pid = entity_ids.generate("PL")
        op_id = str(uuid.uuid4())
        first = self.send("CREATE_PLAYLIST", "playlist", pid, dict(
            title="Once", description="", original_url=""), None, op_id=op_id)
        second = self.send("CREATE_PLAYLIST", "playlist", pid, dict(
            title="Once", description="", original_url=""), None, op_id=op_id)
        self.assertEqual(first, second)
        self.assertEqual(len(self.db.get_all_playlists()), 1)

    def test_a_second_device_minting_the_same_id_is_not_an_error(self):
        """Ids are permanent and distributed, so a duplicate delivery of the
        same creation is idempotent rather than a refusal."""
        pid, _, _ = self.create("Shared")
        _, status, result = self.create("Shared Again", playlist_id=pid)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(pid)["title"], "Shared",
                         "an existing playlist is never rewritten by a replayed creation")

    def test_a_title_may_repeat(self):
        """Playlists have never been unique by title, and inventing that rule
        here would refuse something the ordinary endpoint accepts."""
        self.create("Lectures")
        _, status, result = self.create("Lectures")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))

    # ---- fields -----------------------------------------------------------

    def test_each_field_carries_its_own_revision(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.field(pid, "description", "Term two", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.revision(pid, "description"), 1)
        self.assertEqual(self.revision(pid, "title"), 0,
                         "one decision moves one conflict unit")

    def test_the_value_is_not_echoed_back(self):
        pid, _, _ = self.create("Lectures")
        _, result = self.field(pid, "description", "x" * 200, 0)
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)

    def test_a_stale_base_against_a_different_value_conflicts(self):
        pid, _, _ = self.create("Lectures")
        self.field(pid, "title", "Seminars", 0)
        status, result = self.field(pid, "title", "Workshops", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_revision"], 1)
        self.assertEqual(result["current_value"], "Seminars")
        self.assertEqual(self.stored(pid)["title"], "Seminars")

    def test_a_stale_base_that_agrees_converges(self):
        pid, _, _ = self.create("Lectures")
        self.field(pid, "title", "Seminars", 0)
        status, result = self.field(pid, "title", "Seminars", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(result["server_revision"], 1,
                         "agreeing with the server is not a second change")

    def test_a_base_from_the_future_is_refused(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.field(pid, "title", "Seminars", 7)
        self.assertEqual((status, result["code"]), (400, "FUTURE_REVISION"))
        self.assertEqual(self.stored(pid)["title"], "Lectures")

    def test_an_unknown_playlist_is_a_domain_answer(self):
        status, result = self.field(entity_ids.generate("PL"), "title", "X", 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_only_the_three_editable_columns_are_fields(self):
        pid, _, _ = self.create("Lectures")
        for bad in ("item_count", "created_at", "updated_at", "id"):
            with self.subTest(field=bad):
                status, result = self.field(pid, bad, "x", 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_oversized_field_is_refused_before_it_is_stored(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.field(pid, "description", "x" * 5000, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertEqual(self.stored(pid)["description"], "")

    def test_a_field_operation_must_carry_a_base(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.field(pid, "title", "X", None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_the_ordinary_endpoint_advances_the_same_revision(self):
        """Without this an offline device holding the old title could never
        discover it had been overtaken."""
        pid, _, _ = self.create("Lectures")
        self.db.update_playlist(pid, {"title": "Renamed elsewhere"})
        self.assertEqual(self.revision(pid, "title"), 1)
        status, result = self.field(pid, "title", "Seminars", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Renamed elsewhere")

    def test_an_empty_url_is_stored_as_null_on_both_paths(self):
        pid, _, _ = self.create("Lectures", original_url="https://example.test/x")
        self.field(pid, "original_url", "", 0)
        self.assertIsNone(self.stored(pid)["original_url"])
        # And the value the durable path compares against is "" either way, so
        # clearing an already-empty url is not a second change.
        _, result = self.field(pid, "original_url", "", 1)
        self.assertFalse(result["changed"])

    # ---- membership -------------------------------------------------------

    def test_membership_is_a_scalar_on_the_work(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        status, result = self.file_in(work, pid, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["playlist_title"], "Lectures")
        self.assertEqual(self.order(pid), [work])
        self.assertEqual(self.work_revision(work), 1)

    def test_moving_a_video_between_playlists_advances_both_orders(self):
        """Modelling membership from the playlist's end would have made this
        move a change neither playlist's revision described."""
        first, _, _ = self.create("Lectures")
        second, _, _ = self.create("Seminars")
        work = self.work("Episode one")
        self.file_in(work, first, 0)
        before = (self.order_revision(first), self.order_revision(second))
        status, _ = self.file_in(work, second, self.work_revision(work))
        self.assertEqual(status, 200)
        self.assertEqual(self.order(first), [])
        self.assertEqual(self.order(second), [work])
        after = (self.order_revision(first), self.order_revision(second))
        self.assertEqual(after, (before[0] + 1, before[1] + 1))

    def test_removing_a_video_is_the_same_scalar_with_an_empty_value(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        status, result = self.file_in(work, "", self.work_revision(work))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["playlist_id"], "")
        self.assertEqual(result["playlist_title"], "")
        self.assertEqual(self.order(pid), [])

    def test_filing_a_video_where_it_already_is_changes_nothing(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        revision = self.work_revision(work)
        status, result = self.file_in(work, pid, revision)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.work_revision(work), revision)

    def test_a_stale_membership_base_against_a_different_playlist_conflicts(self):
        first, _, _ = self.create("Lectures")
        second, _, _ = self.create("Seminars")
        work = self.work("Episode one")
        self.file_in(work, first, 0)
        status, result = self.file_in(work, second, 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], first)
        self.assertEqual(self.order(first), [work])

    def test_an_unknown_playlist_refuses_the_filing(self):
        work = self.work("Episode one")
        status, result = self.file_in(work, entity_ids.generate("PL"), 0)
        self.assertEqual((status, result["code"]), (404, "PLAYLIST_NOT_FOUND"))

    def test_an_unknown_work_is_a_domain_answer(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.file_in("W-" + "A" * 32, pid, 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_the_ordinary_membership_endpoints_advance_the_same_revision(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.db.add_work_to_playlist(pid, work)
        self.assertEqual(self.work_revision(work), 1)
        self.db.remove_work_from_playlist(pid, work)
        self.assertEqual(self.work_revision(work), 2)
        self.assertEqual(self.order(pid), [])

    def test_removing_a_video_from_a_playlist_it_is_not_in_does_nothing(self):
        first, _, _ = self.create("Lectures")
        second, _, _ = self.create("Seminars")
        work = self.work("Episode one")
        self.db.add_work_to_playlist(first, work)
        revision = self.work_revision(work)
        self.db.remove_work_from_playlist(second, work)
        self.assertEqual(self.order(first), [work],
                         "a removal aimed at the wrong playlist must not unfile it")
        self.assertEqual(self.work_revision(work), revision)

    # ---- ordering ---------------------------------------------------------

    def test_the_order_is_one_aggregate_under_one_revision(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(3)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        desired = [works[2], works[0], works[1]]
        status, result = self.reorder(pid, desired, base)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["work_ids"], desired)
        self.assertEqual(self.order(pid), desired)
        self.assertEqual(self.order_revision(pid), base + 1)
        # And no field moved: an order is not a property of the title.
        for name in playlists.FIELDS:
            self.assertEqual(self.revision(pid, name), 0, name)

    def test_a_reorder_is_not_a_membership_change(self):
        """An id the playlist does not hold is ignored, and one it holds that
        the request omitted keeps its relative place at the end -- the rule
        `reorder_playlist` has always applied."""
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(3)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        stranger = self.work("Not in this playlist")
        status, result = self.reorder(pid, [works[1], stranger],
                                         self.order_revision(pid))
        self.assertEqual(status, 200)
        self.assertEqual(result["work_ids"], [works[1], works[0], works[2]])
        self.assertEqual(self.order(pid), [works[1], works[0], works[2]])

    def test_a_reorder_that_already_holds_is_not_a_second_change(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(2)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        status, result = self.reorder(pid, works, base)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.order_revision(pid), base)

    def test_a_stale_order_base_conflicts_rather_than_merging(self):
        """Two devices that each dragged one video produced two whole orders.
        Merging them index by index would invent a third neither chose."""
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(3)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        self.reorder(pid, [works[2], works[1], works[0]], base)
        status, result = self.reorder(pid, [works[1], works[0], works[2]], base)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_count"], 3)
        self.assertEqual(self.order(pid), [works[2], works[1], works[0]])

    def test_an_order_conflict_carries_counts_rather_than_the_ids(self):
        """A long playlist's ids would not fit the client's durable result
        bound, and the client re-reads the playlist to see what the server
        has."""
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(3)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        self.reorder(pid, [works[2], works[1], works[0]], base)
        _, result = self.reorder(pid, [works[1], works[0], works[2]], base)
        self.assertNotIn("current_value", result)
        self.assertNotIn("work_ids", result)
        self.assertEqual(result["requested_count"], 3)

    def test_a_stale_order_base_that_agrees_converges(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(2)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        desired = [works[1], works[0]]
        self.reorder(pid, desired, base)
        status, result = self.reorder(pid, desired, base)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_an_order_base_from_the_future_is_refused(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        status, result = self.reorder(pid, [work], 99)
        self.assertEqual((status, result["code"]), (400, "FUTURE_REVISION"))

    def test_a_long_order_is_not_echoed_back(self):
        """The resulting order is carried so the client can reorder its cached
        playlist in place. Past a point that stops being worth putting in every
        acknowledgement and in the ledger, and the client re-reads instead."""
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n)
                 for n in range(playlists.MAX_ECHOED_ITEMS + 1)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        _, result = self.reorder(pid, list(reversed(works)), base)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertNotIn("work_ids", result)
        self.assertEqual(self.order(pid), list(reversed(works)))
        # And a short one still is.
        short, _, _ = self.create("Short")
        pair = works[:2]
        for work in pair:
            self.file_in(work, short, self.work_revision(work))
        _, result = self.reorder(short, list(reversed(pair)),
                                 self.order_revision(short))
        self.assertEqual(result["work_ids"], list(reversed(pair)))

    def test_a_repeated_id_in_an_order_is_counted_once(self):
        """Both sides resolve a request against what the playlist holds, so a
        duplicate is not a second place in the order -- and the conflict count
        must describe the resolved order, not the raw request."""
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(2)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        status, result = self.reorder(pid, [works[1], works[1], works[0]], base)
        self.assertEqual(status, 200)
        self.assertEqual(result["work_ids"], [works[1], works[0]])
        self.assertEqual(self.order(pid), [works[1], works[0]])

    def test_an_order_payload_must_be_a_list_of_ids(self):
        pid, _, _ = self.create("Lectures")
        for bad in ({"work_ids": "abc"}, {"work_ids": [""]}, {"work_ids": [1]},
                    {"work_ids": ["a"], "extra": 1}, {}):
            with self.subTest(bad=bad):
                status, result = self.send(
                    "REORDER_PLAYLIST_ITEMS", "playlist", pid, bad, 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_ordinary_reorder_advances_the_same_revision(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(2)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        base = self.order_revision(pid)
        self.db.reorder_playlist(pid, [works[1], works[0]])
        self.assertEqual(self.order_revision(pid), base + 1)
        status, _ = self.reorder(pid, works, base)
        self.assertEqual(status, 409)

    def test_the_ordinary_reorder_keeps_omitted_videos_in_their_relative_order(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(4)]
        for work in works:
            self.file_in(work, pid, self.work_revision(work))
        self.db.reorder_playlist(pid, [works[3]])
        self.assertEqual(self.order(pid), [works[3], works[0], works[1], works[2]])

    def test_an_explicit_position_on_the_ordinary_add_is_honoured(self):
        pid, _, _ = self.create("Lectures")
        works = [self.work("Episode %d" % n) for n in range(2)]
        for work in works:
            self.db.add_work_to_playlist(pid, work)
        late = self.work("Inserted first")
        self.db.add_work_to_playlist(pid, late, 0)
        self.assertEqual(self.order(pid), [late, works[0], works[1]])

    # ---- deletion ---------------------------------------------------------

    def test_deleting_a_playlist_takes_its_memberships_with_it(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        before = self.work_revision(work)
        status, result = self.send("DELETE_PLAYLIST", "playlist", pid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertIsNone(self.stored(pid))
        self.assertEqual(self.work_revision(work), before + 1,
                         "a device holding 'this video is in that playlist' "
                         "has to be able to discover it was overtaken")
        self.assertIsNotNone(self.db.get_work(work), "the video itself survives")

    def test_deleting_a_playlist_twice_is_idempotent(self):
        pid, _, _ = self.create("Lectures")
        self.send("DELETE_PLAYLIST", "playlist", pid, {}, None)
        status, result = self.send("DELETE_PLAYLIST", "playlist", pid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_deletion_carries_no_base_revision(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.send("DELETE_PLAYLIST", "playlist", pid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        self.assertIsNotNone(self.stored(pid))

    def test_deletion_takes_no_payload(self):
        pid, _, _ = self.create("Lectures")
        status, result = self.send("DELETE_PLAYLIST", "playlist", pid,
                                      {"cascade": True}, None)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_ordinary_delete_advances_the_same_revisions(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        before = self.work_revision(work)
        self.db.delete_playlist(pid)
        self.assertEqual(self.work_revision(work), before + 1)

    # ---- state ------------------------------------------------------------

    def test_the_sync_state_reports_revisions_only(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        self.file_in(work, pid, 0)
        self.field(pid, "description", "Term two", 0)
        state = self.db.get_playlist_sync_state(pid)
        self.assertEqual(sorted(state["fields"]), sorted(playlists.FIELDS))
        self.assertEqual(state["fields"]["description"]["revision"], 1)
        self.assertEqual(state["fields"]["title"]["revision"], 0)
        self.assertEqual(state["order_revision"], 1)
        self.assertNotIn("work_ids", state,
                         "the playlist detail already carries its items in order")

    def test_the_sync_state_of_an_unknown_playlist_is_none(self):
        self.assertIsNone(self.db.get_playlist_sync_state(entity_ids.generate("PL")))

    def test_the_work_playlist_state_names_the_playlist_and_its_revision(self):
        pid, _, _ = self.create("Lectures")
        work = self.work("Episode one")
        state = self.db.get_work_playlist_state(work)
        self.assertEqual(state, {"work_id": work, "playlist_id": "", "revision": 0},
                         "a video in no playlist is a known empty, not an unknown")
        self.file_in(work, pid, 0)
        self.assertEqual(self.db.get_work_playlist_state(work),
                         {"work_id": work, "playlist_id": pid, "revision": 1})

    def test_the_work_playlist_state_of_an_unknown_work_is_none(self):
        self.assertIsNone(self.db.get_work_playlist_state("W-" + "A" * 32))


if __name__ == "__main__":
    unittest.main()
