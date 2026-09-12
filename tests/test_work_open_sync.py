"""MARK_WORK_OPENED: max-register event time, skew policy and precision."""
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from backend import sync_protocol, work_open_sync as opened
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class WorkOpenSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-open-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Opened work")

    def op(self, occurred_at, **changes):
        envelope = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                        operation="MARK_WORK_OPENED", entity_type="work",
                        entity_id=self.work, payload={}, base_revision=None,
                        occurred_at=occurred_at, created_at=occurred_at, depends_on=[])
        envelope.update(changes)
        return envelope

    def opened_at(self, work=None):
        rows = self.db.execute_query("SELECT last_opened_at FROM works WHERE id = ?", (work or self.work,))
        return rows[0]["last_opened_at"]

    def send(self, occurred_at, **changes):
        return sync_protocol.process_operation(self.db, self.op(occurred_at, **changes))

    # ---- the max-register itself ----

    def test_max_register_over_event_time(self):
        """The canonical value is the latest instant the Work was actually
        opened -- not the latest report to arrive. A device that reconnects
        with an older event must not drag Recent backwards."""
        self.assertIsNone(self.opened_at())
        status, result = self.send("2026-09-11T10:00:00Z")
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual(self.opened_at(), "2026-09-11 10:00:00.000")

        self.assertTrue(self.send("2026-09-11T11:00:00Z")[1]["changed"])
        self.assertEqual(self.opened_at(), "2026-09-11 11:00:00.000")

        status, result = self.send("2026-09-11T10:00:00Z")
        self.assertEqual(status, 200, "an obsolete event is a success, not an error")
        self.assertFalse(result["changed"])
        self.assertEqual(result["effective_opened_at"], "2026-09-11 11:00:00.000")
        self.assertEqual(self.opened_at(), "2026-09-11 11:00:00.000")

        self.assertFalse(self.send("2026-09-11T11:00:00Z")[1]["changed"])
        self.assertEqual(self.opened_at(), "2026-09-11 11:00:00.000")

    def test_arrival_order_does_not_decide(self):
        """Two independent events converge on the newer one either way round."""
        second = self.db.add_work("Mirror")
        self.send("2026-09-11T12:00:00Z")
        self.send("2026-09-11T13:00:00Z")
        self.assertEqual(self.opened_at(), "2026-09-11 13:00:00.000")
        for occurred in ("2026-09-11T13:00:00Z", "2026-09-11T12:00:00Z"):
            sync_protocol.process_operation(self.db, self.op(occurred, entity_id=second))
        self.assertEqual(self.opened_at(second), "2026-09-11 13:00:00.000")

    def test_timezones_normalize_to_one_instant(self):
        self.send("2026-09-11T14:00:00+02:00")
        self.assertEqual(self.opened_at(), "2026-09-11 12:00:00.000")
        self.assertFalse(self.send("2026-09-11T07:00:00-05:00")[1]["changed"])

    def test_old_events_are_legitimate(self):
        """Offline for a week is not a reason to refuse the event."""
        status, result = self.send("2019-01-02T03:04:05Z")
        self.assertEqual((status, result["changed"]), (200, True))
        self.assertEqual(self.opened_at(), "2019-01-02 03:04:05.000")

    # ---- clock skew ----

    def test_future_skew_is_clamped_to_arrival_not_discarded(self):
        """A fast clock must not pin a Work to the top of Recent, but the
        activity behind the bad timestamp is still real."""
        received = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        inside = received + timedelta(seconds=opened.MAX_CLIENT_FUTURE_SKEW_SECONDS - 1)
        outside = received + timedelta(hours=6)
        self.assertEqual(opened.effective_opened_at(inside.isoformat(), received),
                         "2026-09-11 12:04:59.000")
        self.assertEqual(opened.effective_opened_at(outside.isoformat(), received),
                         "2026-09-11 12:00:00.000")
        # Exactly at the horizon is still accepted as reported.
        horizon = received + timedelta(seconds=opened.MAX_CLIENT_FUTURE_SKEW_SECONDS)
        self.assertEqual(opened.effective_opened_at(horizon.isoformat(), received),
                         "2026-09-11 12:05:00.000")

    def test_a_far_future_client_cannot_pin_recent(self):
        far = datetime.now(timezone.utc) + timedelta(days=365)
        result = self.send(far.isoformat())[1]
        self.assertLess(result["effective_opened_at"],
                        opened.format_moment(datetime.now(timezone.utc) + timedelta(minutes=1)))
        self.assertTrue(result["changed"])

    # ---- precision ----

    def test_mixed_precision_sorts_chronologically(self):
        works = {}
        for label, value in (("a", "2026-09-11 12:00:00"), ("b", "2026-09-11 12:00:00.250"),
                             ("c", "2026-09-11 12:00:00.900"), ("d", "2026-09-11 12:00:01")):
            works[label] = self.db.add_work("Work " + label)
            self.db.execute_query("UPDATE works SET last_opened_at = ? WHERE id = ?", (value, works[label]))
        self.db.execute_query("UPDATE works SET last_opened_at = NULL WHERE id = ?", (self.work,))
        order = [row["id"] for row in self.db.get_recent_browse()]
        self.assertEqual(order, [works["d"], works["c"], works["b"], works["a"]])
        # A legacy second-resolution value reads as `.000`, so a re-open inside
        # the same second is correctly no change rather than a spurious write.
        self.assertEqual(opened.comparable("2026-09-11 12:00:00"), "2026-09-11 12:00:00.000")
        self.assertEqual(opened.comparable("2026-09-11 12:00:00.250"), "2026-09-11 12:00:00.250")

    def test_sub_second_events_do_not_tie(self):
        for offset in (0, 250, 900):
            moment = datetime(2026, 9, 11, 12, 0, 0, offset * 1000, tzinfo=timezone.utc)
            self.send(moment.isoformat())
        self.assertEqual(self.opened_at(), "2026-09-11 12:00:00.900")

    # ---- protocol shape ----

    def test_envelope_rules(self):
        for field, value in (("payload", {"tag_id": "T-1"}), ("base_revision", 0),
                             ("base_revision", 7), ("entity_type", "tag"),
                             ("occurred_at", "yesterday"), ("depends_on", [str(uuid.uuid4())])):
            with self.subTest(field=field, value=value):
                envelope = self.op("2026-09-11T10:00:00Z")
                envelope[field] = value
                self.assertEqual(sync_protocol.process_operation(self.db, envelope)[0], 400)
        self.assertIsNone(self.opened_at())
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_missing_work_is_terminal(self):
        op = self.op("2026-09-11T10:00:00Z", entity_id="W-gone")
        status, result = sync_protocol.process_operation(self.db, op)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))

    def test_acknowledgement_carries_the_canonical_recent_row(self):
        result = self.send("2026-09-11T10:00:00Z")[1]
        row = result["recent_item"]
        self.assertEqual(row["id"], self.work)
        self.assertEqual(row["last_opened_at"], "2026-09-11 10:00:00.000")
        canonical = [item for item in self.db.get_recent_browse() if item["id"] == self.work]
        self.assertEqual([row], canonical, "ACK row must match /api/recent exactly")

    # ---- idempotency ----

    def test_replay_is_exact_after_the_work_is_reopened(self):
        op = self.op("2026-09-11T10:00:00Z")
        original = sync_protocol.process_operation(self.db, op)
        self.assertTrue(original[1]["changed"])
        self.send("2026-09-11T18:00:00Z")
        self.assertEqual(sync_protocol.process_operation(self.db, op), original,
                         "the ledger answers, not the world as it is now")
        self.assertEqual(self.opened_at(), "2026-09-11 18:00:00.000")
        self.assertEqual(len(self.db.execute_query("SELECT * FROM sync_operations")), 2)

    def test_reused_op_id_with_a_new_envelope_never_executes(self):
        op = self.op("2026-09-11T10:00:00Z")
        sync_protocol.process_operation(self.db, op)
        op["occurred_at"] = "2026-09-11T20:00:00Z"
        self.assertEqual(sync_protocol.process_operation(self.db, op), (409, {"code": "OP_ID_REUSE"}))
        self.assertEqual(self.opened_at(), "2026-09-11 10:00:00.000")


class DirectOpenEndpointTests(unittest.TestCase):
    """The compatibility endpoint and the sync family share one rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-open-direct-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Direct")

    def opened_at(self):
        return self.db.execute_query(
            "SELECT last_opened_at FROM works WHERE id = ?", (self.work,))[0]["last_opened_at"]

    def test_direct_open_records_server_now_at_millisecond_precision(self):
        self.assertTrue(self.db.mark_work_opened(self.work))
        value = self.opened_at()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")

    def test_direct_open_uses_the_same_max_register(self):
        future = work_open_sync_future()
        self.db.execute_query("UPDATE works SET last_opened_at = ? WHERE id = ?", (future, self.work))
        self.assertTrue(self.db.mark_work_opened(self.work))
        self.assertEqual(self.opened_at(), future,
                         "server-now must not overwrite a newer recorded open")

    def test_direct_open_reports_a_missing_work(self):
        self.assertFalse(self.db.mark_work_opened("W-gone"))

    def test_work_read_stays_pure(self):
        self.db.mark_work_opened(self.work)
        before = self.opened_at()
        self.db.get_work(self.work)
        self.db.get_work(self.work)
        self.assertEqual(self.opened_at(), before, "GET /api/works/:id must never stamp last_opened_at")


def work_open_sync_future():
    return opened.format_moment(datetime.now(timezone.utc) + timedelta(days=1))
