"""Work source identity: an aggregate conflict unit, not a set of fields."""
import json
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_source_sync as src
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

WATCH = "https://www.youtube.com/watch?v=%s"
SHORT = "https://youtu.be/%s"


class WorkSourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-source-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work(
            "Clip", source_kind="video", source_url=WATCH % "AAA",
            provider="youtube", provider_id="AAA", thumb_url="https://img/AAA.jpg")

    def op(self, url, base=0, **changes):
        envelope = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                        operation="SET_WORK_SOURCE", entity_type="work",
                        entity_id=self.work, payload={"source": {"kind": "video", "url": url}},
                        base_revision=base, occurred_at="2026-09-12T10:00:00Z",
                        created_at="2026-09-12T10:00:00Z", depends_on=[])
        envelope.update(changes)
        return envelope

    def send(self, url, base=0):
        return sync_protocol.process_operation(self.db, self.op(url, base))

    def columns(self):
        row = self.db.get_work(self.work)
        return {k: row[k] for k in
                ("source_kind", "provider", "provider_id", "source_url", "thumb_url")}

    def revision(self):
        return src.get_source_state(self.db, self.work)["revision"]

    # ---- canonical identity ----

    def test_one_parser_accepts_every_spelling_creation_accepts(self):
        """Work creation and source synchronization share ONE parser. A second
        would eventually disagree, and the disagreement would surface as a Work
        whose stored URL and stored provider_id name different videos."""
        for url in (WATCH % "ABC", "https://youtu.be/ABC",
                    "https://www.youtube.com/embed/ABC", "https://m.youtube.com/watch?v=ABC"):
            with self.subTest(url=url):
                source = src.canonical_source({"kind": "video", "url": url})
                self.assertEqual(source["provider_id"], "ABC")
                self.assertEqual(source["provider"], "youtube")
        for bad in ("https://notyoutube.com/watch?v=ABC", "https://youtube.com.evil.org/watch?v=A",
                    "https://example.com/video", "ftp://youtube.com/watch?v=A", "", "not a url"):
            with self.subTest(url=bad):
                self.assertIsNone(src.canonical_source({"kind": "video", "url": bad}), bad)

    def test_the_payload_carries_intent_and_never_asserts_derived_values(self):
        """A client that could assert `provider_id` could assert an identity
        its own URL contradicts -- which is the defect this operation exists to
        prevent."""
        for payload in ({"kind": "video", "url": WATCH % "A", "provider_id": "B"},
                        {"kind": "video"}, {"url": WATCH % "A"},
                        {"kind": "pdf", "url": WATCH % "A"}, "not an object", None):
            with self.subTest(payload=repr(payload)[:40]):
                self.assertIsNone(src.canonical_source(payload))
        envelope = self.op(WATCH % "BBB")
        envelope["payload"] = {"source": {"kind": "video", "url": WATCH % "BBB"}, "extra": 1}
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))

    def test_a_different_spelling_of_the_same_video_is_not_a_change(self):
        """Identity is provider + provider_id, never the URL spelling. Telling
        a user they collided because they pasted a share link rather than a
        watch link would be inventing a disagreement."""
        for spelling in ("https://youtu.be/AAA", "https://www.youtube.com/embed/AAA",
                         WATCH % "AAA"):
            with self.subTest(url=spelling):
                code, result = self.send(spelling, base=self.revision())
                self.assertEqual((code, result["code"], result["changed"]),
                                 (200, "ACKNOWLEDGED", False))
                self.assertEqual(self.revision(), 0, "no revision for a spelling")
                self.assertEqual(self.columns()["source_url"], WATCH % "AAA",
                                 "and no write either")
                self.assertEqual(self.columns()["thumb_url"], "https://img/AAA.jpg",
                                 "the thumbnail is not disturbed")

    # ---- the aggregate write ----

    def test_one_source_change_is_one_revision_and_one_atomic_write(self):
        code, result = self.send(WATCH % "BBB")
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", True))
        self.assertEqual(self.revision(), 1, "ONE revision for the whole identity")
        columns = self.columns()
        self.assertEqual(columns["provider_id"], "BBB")
        self.assertEqual(columns["source_url"], WATCH % "BBB")
        self.assertEqual(columns["provider"], "youtube")
        self.assertEqual(columns["source_kind"], "video")
        # Never an intermediate row naming one video and playing another.
        self.assertEqual(src.youtube_video_id(columns["source_url"]), columns["provider_id"])

    def test_the_stale_thumbnail_does_not_survive_the_identity_change(self):
        """`thumb_url` is presentation, not identity -- but it is the PREVIOUS
        video's image, and a row claiming video B while serving video A's
        picture is a lie the user can see. Cleared rather than re-derived,
        because re-deriving means a network call and no canonical mutation may
        depend on one."""
        self.assertEqual(self.columns()["thumb_url"], "https://img/AAA.jpg")
        self.send(WATCH % "BBB")
        self.assertIsNone(self.columns()["thumb_url"])

    def test_the_acknowledgement_states_the_stored_row(self):
        """Every column the aggregate owns, read back after the write.

        The client copies these rather than deriving them -- it cannot derive
        `urldate` at all, and deriving the rest is only correct while the
        request and the stored row agree.
        """
        code, result = self.send(WATCH % "BBB")
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        row = self.db.get_work(self.work)
        self.assertEqual(result["source_url"], row["source_url"])
        self.assertEqual(result["provider_id"], "BBB")
        self.assertEqual(result["provider"], "youtube")
        self.assertEqual(result["source_kind"], "video")
        self.assertIsNone(result["thumb_url"], "the previous video's image is cleared")
        self.assertEqual(result["urldate"], row["urldate"])
        self.assertNotIn("value_omitted", result)

    def test_a_convergent_acknowledgement_reports_what_is_stored_not_what_was_asked(self):
        """The same video in another spelling stores nothing -- so the URL the
        client asked for is NOT the URL the server holds, and the
        acknowledgement has to say the second one."""
        base = self.revision()
        self.send(WATCH % "BBB", base=base)                       # another device
        code, result = self.send(SHORT % "BBB", base=base)        # this one, converging
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(result["source_url"], WATCH % "BBB",
                         "the stored spelling, not the requested one")
        self.assertEqual(result["provider_id"], "BBB")
        self.assertEqual(result["server_revision"], self.revision(),
                         "a convergent write advances nothing")

    # ---- the legacy PATCH surface ----

    def test_patch_cannot_write_source_identity_columns_independently(self):
        """The aggregate is only a boundary if nothing else can cross it.

        `PATCH /api/works/:id` accepted every one of these by name and
        validated none, so a client could write `provider_id` alone and
        recreate exactly the contradiction SET_WORK_SOURCE prevents -- the
        stored URL naming video B while the viewer, which reads `provider_id`
        first, plays video A -- without advancing the source revision, so no
        other device could ever discover it.
        """
        for column, value in (("provider_id", "CCC"), ("provider", "vimeo"),
                              ("source_kind", "pdf"), ("thumb_url", "https://img/CCC.jpg")):
            with self.subTest(column=column):
                with self.assertRaises(ValueError) as caught:
                    self.db.update_work_metadata(self.work, {column: value})
                self.assertIn(column, str(caught.exception))
                self.assertIn("SET_WORK_SOURCE", str(caught.exception))
        self.assertEqual(self.columns(), {
            "source_kind": "video", "provider": "youtube", "provider_id": "AAA",
            "source_url": WATCH % "AAA", "thumb_url": "https://img/AAA.jpg",
        }, "and nothing was written")
        self.assertEqual(self.revision(), 0)

    def test_the_refusal_names_every_identity_column_at_once(self):
        """A PATCH carrying the whole identity is still the wrong path: it
        advances no revision, so the change is invisible to every other
        device."""
        with self.assertRaises(ValueError) as caught:
            self.db.update_work_metadata(self.work, {
                "source_kind": "video", "provider": "youtube", "provider_id": "BBB",
                "title": "Renamed too"})
        message = str(caught.exception)
        for column in ("provider", "provider_id", "source_kind"):
            self.assertIn(column, message)
        self.assertEqual(self.db.get_work(self.work)["title"], "Clip",
                         "the whole PATCH is refused, never half-applied")

    def test_fields_beside_the_identity_columns_still_patch(self):
        """The bound is on source IDENTITY, not on editing video Works."""
        self.db.update_work_metadata(self.work, {"title": "Renamed", "year": "2021"})
        self.assertEqual(self.db.get_work(self.work)["title"], "Renamed")

    def test_creation_still_establishes_the_whole_identity(self):
        """The bound is on EDITING an existing Work. Creation and import write
        these columns through `add_work`, where the identity is established at
        once and there is no prior value to contradict."""
        made = self.db.add_work("New clip", source_kind="video", source_url=WATCH % "ZZZ",
                                provider="youtube", provider_id="ZZZ")
        row = self.db.get_work(made)
        self.assertEqual(row["provider_id"], "ZZZ")
        self.assertEqual(row["source_kind"], "video")

    # ---- conflicts ----

    def test_two_devices_choosing_different_videos_is_ONE_conflict(self):
        base = self.revision()
        self.send(WATCH % "BBB", base=base)            # someone else
        code, result = self.send(WATCH % "CCC", base=base)
        self.assertEqual((code, result["code"]), (409, "SOURCE_REVISION_CONFLICT"))
        self.assertEqual(result["current_revision"], 1)
        # Bounded, like every other large-value conflict.
        self.assertIn("current_preview", result)
        self.assertNotIn("current_value", result)
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 2048)
        # The IDENTITY is reported exactly, beside the bounded preview. A
        # client that resolved by reapplying its own choice would otherwise
        # have to parse the identity out of a value designed to be truncated --
        # and without it, its next edit is still measured against the video the
        # conflict replaced.
        self.assertEqual(result["current_provider"], "youtube")
        self.assertEqual(result["current_provider_id"], "BBB")
        # The server's choice stands until the user decides.
        self.assertEqual(self.columns()["provider_id"], "BBB")

    def test_a_conflict_reports_the_identity_even_when_the_preview_is_truncated(self):
        """The preview shrinks to keep the whole result inside the client's
        durable bound; the identity never does. It is tens of bytes and it is
        what the source actually IS."""
        long_id = "B" * 300
        self.db.execute_query(
            "UPDATE works SET source_url = ?, provider_id = ? WHERE id = ?",
            (WATCH % long_id + "&pad=" + "x" * 1800, long_id, self.work))
        base = self.revision()
        self.db.execute_query(
            "INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
            "VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id) DO UPDATE SET revision = 1",
            (src.SCOPE_TYPE, src.scope_key(self.work)))
        code, result = self.send(WATCH % "CCC", base=base)
        self.assertEqual((code, result["code"]), (409, "SOURCE_REVISION_CONFLICT"))
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 2048)
        self.assertEqual(result["current_provider_id"], long_id,
                         "the identity is exact even where the URL was cut")
        self.assertLess(len(result["current_preview"]), 2048)

    def test_a_stale_but_convergent_choice_is_not_a_conflict(self):
        base = self.revision()
        self.send(WATCH % "BBB", base=base)
        for spelling in (WATCH % "BBB", "https://youtu.be/BBB"):
            with self.subTest(url=spelling):
                code, result = self.send(spelling, base=base)
                self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))

    def test_a_future_base_revision_is_refused(self):
        code, result = self.send(WATCH % "BBB", base=7)
        self.assertEqual((code, result["code"]), (400, "FUTURE_REVISION"))
        self.assertEqual(self.columns()["provider_id"], "AAA")

    def test_a_conflict_with_enormous_urls_still_fits_the_durable_bound(self):
        """The generic terminal-result fitter applies here too: a result the
        client cannot store would leave the operation retrying forever and the
        user never reaching the conflict."""
        long_url = WATCH % ("A" * 3000)
        self.db.execute_query("UPDATE works SET source_url = ? WHERE id = ?",
                              (long_url, self.work))
        self.db.execute_query(
            "INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) VALUES (?, ?, 2)",
            (src.SCOPE_TYPE, src.scope_key(self.work)))
        code, result = self.send(WATCH % "CCC", base=0)
        self.assertEqual((code, result["code"]), (409, "SOURCE_REVISION_CONFLICT"))
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 2048)

    # ---- transitions this milestone deliberately does not support ----

    def test_changing_the_source_of_a_non_video_work_is_refused(self):
        """Turning a PDF into a video is a different decision with different
        consequences for `file_path` and for which viewer renders. There is no
        UI for it and no defined product semantics, so it is refused rather
        than invented."""
        pdf = self.db.add_work("Paper", source_kind="pdf", file_path="/api/pdfs/x.pdf")
        envelope = self.op(WATCH % "BBB")
        envelope["entity_id"] = pdf
        code, result = sync_protocol.process_operation(self.db, envelope)
        self.assertEqual((code, result["code"]), (409, "UNSUPPORTED_SOURCE_TRANSITION"))
        row = self.db.get_work(pdf)
        self.assertEqual(row["source_kind"], "pdf")
        self.assertEqual(row["file_path"], "/api/pdfs/x.pdf")
        self.assertFalse(row["provider_id"])

    def test_a_missing_work_is_terminal(self):
        envelope = self.op(WATCH % "BBB")
        envelope["entity_id"] = "W-gone"
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (404, {"work_id": "W-gone", "code": "ENTITY_NOT_FOUND"}))

    # ---- protocol invariants inherited from the generic layer ----

    def test_replay_is_exact_and_op_id_reuse_is_refused(self):
        envelope = self.op(WATCH % "BBB")
        first = sync_protocol.process_operation(self.db, envelope)
        again = sync_protocol.process_operation(self.db, dict(envelope))
        self.assertEqual(first, again, "a replay returns the ledgered outcome")
        self.assertEqual(self.revision(), 1, "and applies once")
        reused = dict(envelope)
        reused["payload"] = {"source": {"kind": "video", "url": WATCH % "CCC"}}
        self.assertEqual(sync_protocol.process_operation(self.db, reused),
                         (409, {"code": "OP_ID_REUSE"}))

    def test_a_null_base_revision_is_refused(self):
        envelope = self.op(WATCH % "BBB")
        envelope["base_revision"] = None
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_BASE_REVISION"}))

    def test_the_source_scope_is_its_own_and_does_not_touch_field_revisions(self):
        """A source change is not a field change. Sharing a scope would make
        one decision advance counters for values nobody edited."""
        self.send(WATCH % "BBB")
        fields = self.db.get_work_metadata_state(self.work)["fields"]
        for field, entry in fields.items():
            self.assertEqual(entry["revision"], 0, field)
        rows = self.db.execute_query(
            "SELECT scope_type FROM sync_entity_revisions WHERE scope_type = ?",
            (src.SCOPE_TYPE,))
        self.assertEqual(len(rows), 1)

    def test_creation_does_not_manufacture_a_source_revision(self):
        created = self.db.add_work("New", source_kind="video", source_url=WATCH % "ZZZ",
                                   provider="youtube", provider_id="ZZZ")
        self.assertEqual(src.get_source_state(self.db, created)["revision"], 0)
        self.assertIsNone(src.get_source_state(self.db, "W-nope"))
