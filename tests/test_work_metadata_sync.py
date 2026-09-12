"""Field-scoped Work metadata: per-field revisions, conflicts and independence."""
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_metadata_sync as meta
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class WorkMetadataSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-meta-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Metadata work")

    def op(self, field, value, base=0, **changes):
        envelope = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                        operation="SET_WORK_METADATA_FIELD", entity_type="work",
                        entity_id=self.work, payload={"field": field, "value": value},
                        base_revision=base, occurred_at="2026-09-12T10:00:00Z",
                        created_at="2026-09-12T10:00:00Z", depends_on=[])
        envelope.update(changes)
        return envelope

    def send(self, field, value, base=0):
        return sync_protocol.process_operation(self.db, self.op(field, value, base))

    def state(self, work=None):
        return self.db.get_work_metadata_state(work or self.work)["fields"]

    def value(self, field):
        return self.db.execute_query(
            "SELECT %s AS v FROM works WHERE id = ?" % field, (self.work,))[0]["v"]

    # ---- the field registry ----

    def test_every_supported_field_round_trips(self):
        self.assertEqual(sorted(meta.SYNCED_FIELDS),
                         ["abstract", "doi", "edition", "isbn", "issue", "journal",
                          "location", "pages", "publisher", "volume"])
        for index, field in enumerate(sorted(meta.SYNCED_FIELDS)):
            with self.subTest(field=field):
                status, result = self.send(field, "value-%d" % index)
                self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
                self.assertEqual(result["server_revision"], 1)
                self.assertEqual(self.value(field), "value-%d" % index)

    def test_an_unsupported_field_never_reaches_the_column(self):
        """An arbitrary column name from a client is both an injection surface
        and a way to reach fields this milestone deliberately does not
        synchronize."""
        for field in ("title", "status", "year", "id",
                      "doi; DROP TABLE works", "", None, 7):
            with self.subTest(field=field):
                self.assertEqual(sync_protocol.process_operation(self.db, self.op(field, "x")),
                                 (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.db.execute_query(
            "SELECT title FROM works WHERE id = ?", (self.work,))[0]["title"], "Metadata work")
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_value_shape_is_bounded_and_typed(self):
        for value in (None, 7, ["x"], {"a": 1}, True, "x" * (meta.SYNCED_FIELDS["doi"] + 1)):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(sync_protocol.process_operation(self.db, self.op("doi", value))[0], 400)
        # Exactly at the limit is legitimate.
        self.assertEqual(self.send("doi", "d" * meta.SYNCED_FIELDS["doi"])[0], 200)

    def test_envelope_requires_a_base_revision_and_an_exact_payload(self):
        for changes in ({"base_revision": None},
                        {"payload": {"field": "doi"}},
                        {"payload": {"field": "doi", "value": "x", "extra": 1}},
                        {"payload": {}},
                        {"entity_type": "concept"}):
            with self.subTest(changes=str(changes)[:50]):
                envelope = self.op("doi", "x")
                envelope.update(changes)
                self.assertEqual(sync_protocol.process_operation(self.db, envelope)[0], 400)
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_values_are_stored_exactly_as_patch_would_store_them(self):
        """Synchronization is not a licence to start normalizing values PRKS
        has never normalized: the two write paths must agree byte for byte."""
        for value in ("10.1234/Example-MiXeD", "978-0-306-40615-7", " leading and trailing ", "12–34"):
            with self.subTest(value=value):
                self.send("doi", value, base=self.state()["doi"]["revision"])
                self.assertEqual(self.value("doi"), value)
        second = self.db.add_work("Patch mirror")
        self.db.update_work_metadata(second, {"doi": "10.1234/Example-MiXeD"})
        self.assertEqual(self.db.execute_query(
            "SELECT doi FROM works WHERE id = ?", (second,))[0]["doi"], "10.1234/Example-MiXeD")

    def test_null_and_empty_are_one_logical_value(self):
        """A column left NULL by an older row and one a user cleared mean the
        same thing, so clearing an already-empty field is not a change."""
        self.db.execute_query("UPDATE works SET doi = NULL WHERE id = ?", (self.work,))
        self.assertEqual(self.state()["doi"], {"value": "", "revision": 0})
        status, result = self.send("doi", "")
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 0))
        self.assertIsNone(self.value("doi"))

    # ---- the conflict rule ----

    def test_scalar_conflict_matrix(self):
        cases = (
            # (base, desired, code, changed, revision after)
            (0, "first", "ACKNOWLEDGED", True, 1),
            (1, "first", "ACKNOWLEDGED", False, 1),
            (1, "second", "ACKNOWLEDGED", True, 2),
            (0, "second", "ACKNOWLEDGED", False, 2),
            (0, "third", "REVISION_CONFLICT", None, 2),
            (9, "fourth", "FUTURE_REVISION", None, 2),
        )
        for base, desired, code, changed, revision in cases:
            with self.subTest(base=base, desired=desired, code=code):
                op = self.op("doi", desired, base)
                status, result = sync_protocol.process_operation(self.db, op)
                self.assertEqual(result["code"], code)
                if changed is not None:
                    self.assertEqual(result["changed"], changed)
                self.assertEqual(self.state()["doi"]["revision"], revision)
                # Replay is exact, whatever the outcome was.
                self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))
        conflict = sync_protocol.process_operation(self.db, self.op("doi", "third", 0))[1]
        self.assertEqual(conflict["current_value"], "second")
        self.assertEqual(conflict["requested_value"], "third")
        self.assertEqual(conflict["current_revision"], 2)

    def test_a_stale_but_convergent_edit_is_not_a_conflict(self):
        """Two people typing the same DOI have converged, not collided."""
        self.send("doi", "10.1/agreed")
        status, result = self.send("doi", "10.1/agreed", base=0)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["doi"]["revision"], 1, "convergence advances nothing")

    def test_missing_work_is_terminal(self):
        op = self.op("doi", "x", entity_id="W-gone")
        status, result = sync_protocol.process_operation(self.db, op)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))

    # ---- the point of the milestone ----

    def test_different_fields_never_conflict_with_each_other(self):
        """Two devices editing DOI and ISBN have not disagreed about anything.
        A single Work-level revision would tell them they had."""
        self.send("doi", "10.1/a")
        self.send("isbn", "111")
        self.send("isbn", "222", base=1)
        self.assertEqual(self.state()["doi"]["revision"], 1)
        self.assertEqual(self.state()["isbn"]["revision"], 2)
        # A device that has been offline since DOI revision 1 still applies
        # cleanly, even though ISBN moved twice underneath it.
        status, result = self.send("doi", "10.1/b", base=1)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual((self.value("doi"), self.value("isbn")), ("10.1/b", "222"))

    def test_one_field_conflicting_leaves_the_others_writable(self):
        self.send("doi", "server-doi")
        self.assertEqual(self.send("doi", "device-doi", base=0)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send("journal", "device-journal", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("journal"), "device-journal")

    # ---- the ordinary PATCH boundary ----

    def test_patch_advances_only_the_fields_it_actually_changed(self):
        """Revisions are canonical history, not sync-endpoint history: an
        offline device can only detect an online edit if the online path moved
        the same counter."""
        self.db.update_work_metadata(self.work, {"doi": "10.1/patched", "isbn": "555"})
        state = self.state()
        self.assertEqual((state["doi"]["revision"], state["isbn"]["revision"]), (1, 1))
        self.db.update_work_metadata(self.work, {"doi": "10.1/patched", "isbn": "666"})
        state = self.state()
        self.assertEqual((state["doi"]["revision"], state["isbn"]["revision"]), (1, 2),
                         "an unchanged field must not manufacture staleness")
        self.assertEqual((state["doi"]["value"], state["isbn"]["value"]), ("10.1/patched", "666"))
        # And a device holding the pre-PATCH DOI now correctly conflicts.
        self.assertEqual(self.send("doi", "10.1/device", base=0)[1]["code"], "REVISION_CONFLICT")

    def test_unrelated_metadata_never_advances_a_field_revision(self):
        self.send("doi", "10.1/keep")
        before = self.state()
        self.db.update_work_metadata(self.work, {
            "title": "Renamed", "status": "Paused", "year": "1999",
            "author_text": "Someone", "doc_type": "book"})
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.execute_query(
            "SELECT title FROM works WHERE id = ?", (self.work,))[0]["title"], "Renamed")

    def test_patch_commits_values_and_revisions_together(self):
        """One transaction: a stored value whose revision did not advance is
        exactly the state that makes every other device's staleness check lie."""
        import sqlite3
        self.db.execute_query(
            "CREATE TRIGGER reject_revisions BEFORE INSERT ON sync_entity_revisions "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.update_work_metadata(self.work, {"title": "Rolled back", "doi": "10.1/rolled"})
        row = self.db.execute_query(
            "SELECT title, doi FROM works WHERE id = ?", (self.work,))[0]
        self.assertEqual((row["title"], row["doi"]), ("Metadata work", ""))
        self.assertEqual(self.state()["doi"]["revision"], 0)

    def test_work_creation_does_not_manufacture_revisions(self):
        created = self.db.add_work("Fresh", doi="10.1/initial", isbn="999")
        state = self.db.get_work_metadata_state(created)["fields"]
        self.assertEqual(state["doi"], {"value": "10.1/initial", "revision": 0})
        self.assertEqual(state["isbn"], {"value": "999", "revision": 0})

    # ---- the projection ----

    def test_projection_lists_every_supported_field_and_nothing_else(self):
        state = self.db.get_work_metadata_state(self.work)
        self.assertEqual(state["work_id"], self.work)
        self.assertEqual(sorted(state["fields"]), sorted(meta.SYNCED_FIELDS))
        self.assertIsNone(self.db.get_work_metadata_state("W-gone"))

    def test_projection_is_scoped_to_its_own_work(self):
        other = self.db.add_work("Other")
        self.db.update_work_metadata(other, {"doi": "10.1/other"})
        self.assertEqual(self.state()["doi"], {"value": "", "revision": 0})
        self.assertEqual(self.state(other)["doi"], {"value": "10.1/other", "revision": 1})

    def test_etag_moves_for_any_value_or_revision_change(self):
        seen = set()

        def snapshot(label):
            etag = self.db.etag_for_representation(
                "work-metadata-state", self.db.get_work_metadata_state(self.work))
            self.assertNotIn(etag, seen, label)
            seen.add(etag)

        snapshot("baseline")
        self.send("doi", "10.1/aaaa"); snapshot("doi value")
        self.send("doi", "10.1/bbbb", base=1); snapshot("same-length replacement")
        self.send("isbn", "111"); snapshot("another field")
        # A revision moving without the value moving still changes the ETag.
        self.db.execute_query(
            "UPDATE sync_entity_revisions SET revision = revision + 5 "
            "WHERE scope_type = 'work-field' AND scope_id = ?",
            (meta.scope_key(self.work, "doi"),))
        snapshot("revision only")

    # ---- the cross-projection dependency this milestone exists for ----

    def test_recently_added_carries_publisher_for_its_local_filter(self):
        """The pin behind `FIELD_PROJECTIONS`. Recently Added filters locally
        over Publisher, so the value is part of that cached projection's
        semantics even though no Work card renders it -- being invisible is not
        the same as being unused. If this projection ever stops selecting it,
        the client overlay built on top becomes dead weight rather than a bug
        anyone would notice."""
        self.db.update_work_metadata(self.work, {"publisher": "Fixture Press"})
        rows = self.db.get_recently_added_browse()
        row = next(r for r in rows if r["id"] == self.work)
        self.assertEqual(row["publisher"], "Fixture Press")
        self.assertEqual(meta.FIELD_PROJECTIONS["publisher"], ("recently-added",))

    def test_only_publisher_and_abstract_claim_another_projection(self):
        """Location is the control case: a detail-only field must not drag an
        unrelated cached read model into its reconciliation. Publisher copies a
        value into one; Abstract DERIVES one."""
        self.assertEqual(sorted(meta.FIELD_PROJECTIONS), ["abstract", "publisher"])
        self.assertEqual(meta.FIELD_PROJECTIONS["abstract"], ("works-browse",))
        for field in meta.SYNCED_FIELDS:
            if field in ("publisher", "abstract"):
                continue
            self.assertNotIn(field, meta.FIELD_PROJECTIONS, field)

    def test_publisher_and_location_behave_like_every_other_field(self):
        """No new machinery: the expanded registry reuses the scalar path."""
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                self.assertEqual(self.send(field, "first")[1]["code"], "ACKNOWLEDGED")
                self.assertEqual(self.state()[field], {"value": "first", "revision": 1})
                self.assertFalse(self.send(field, "first", base=1)[1]["changed"])
                self.assertEqual(self.send(field, "second", base=0)[1]["code"], "REVISION_CONFLICT")
                self.assertEqual(self.send(field, "second", base=1)[1]["code"], "ACKNOWLEDGED")
                self.assertEqual(self.value(field), "second")

    def test_publisher_and_location_are_independent_of_each_other(self):
        self.send("publisher", "Elsevier")
        self.send("location", "Amsterdam")
        self.send("location", "Amsterdam; Boston", base=1)
        # A device stale on Location still applies its Publisher edit cleanly.
        self.assertEqual(self.send("publisher", "Springer", base=1)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("publisher"), self.value("location")),
                         ("Springer", "Amsterdam; Boston"))

    def test_new_fields_keep_their_documented_bounds(self):
        """Ordinary PATCH imposes no length limit; the sync envelope does, so
        the bound is a deliberate, tested number rather than an accident."""
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                self.assertEqual(meta.SYNCED_FIELDS[field], 500)
                limit = meta.SYNCED_FIELDS[field]
                self.assertEqual(self.send(field, "x" * limit)[0], 200)
                over = self.op(field, "x" * (limit + 1), base=1)
                self.assertEqual(sync_protocol.process_operation(self.db, over)[0], 400)

    def test_multi_place_locations_are_stored_verbatim(self):
        """PRKS joins semicolon-separated places at BibLaTeX export time and
        stores what the user typed; the sync path must not start parsing."""
        value = "Cambridge, UK; Paris; Berlin"
        self.send("location", value)
        self.assertEqual(self.value("location"), value)

    def test_the_projection_reads_only_this_works_scopes(self):
        """A library-wide scan would grow with the library while the answer
        stays nine rows, and it would hand another Work's revisions to this
        one if a scope key were ever ambiguous."""
        noisy = [self.db.add_work("Noise %d" % i) for i in range(5)]
        for work in noisy:
            self.db.update_work_metadata(work, {"doi": "10.1/%s" % work, "isbn": work})
        self.db.update_work_metadata(self.work, {"doi": "10.1/mine"})

        state = self.state()
        self.assertEqual(state["doi"], {"value": "10.1/mine", "revision": 1})
        self.assertEqual(state["isbn"], {"value": "", "revision": 0},
                         "another Work's ISBN revision must not leak in")
        for work in noisy:
            self.assertEqual(self.db.get_work_metadata_state(work)["fields"]["isbn"]["revision"], 1)

        scanned = []

        class Watched:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                if "sync_entity_revisions" in sql:
                    scanned.append(sql)
                return self._conn.execute(sql, *args)

        with self.db.connection() as conn:
            observed = meta.get_field_state_on_conn(Watched(conn), self.work)
        self.assertEqual(observed, self.db.get_work_metadata_state(self.work))
        self.assertEqual(len(scanned), 1, "one query, not one per field")
        self.assertIn("scope_id IN", scanned[0], "bounded to this Work's own scope keys")

    # ---- Abstract: one canonical limit, measured in bytes ----

    def test_abstract_limit_is_the_same_contract_on_both_paths(self):
        """If PATCH accepted an Abstract the durable queue would refuse, the
        same edit would be savable online and impossible offline -- the split
        contract that moving a field to local-first exists to remove."""
        limit = meta.MAX_ABSTRACT_UTF8_BYTES
        self.assertEqual(limit, 1024 * 1024)
        at_limit = "x" * limit
        over = "x" * (limit + 1)

        self.assertEqual(self.send("abstract", at_limit)[0], 200)
        self.assertEqual(self.value("abstract"), at_limit)
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("abstract", over, base=1)), (400, {"code": "INVALID_ENVELOPE"}))

        self.db.update_work_metadata(self.work, {"abstract": "x" * 1000})
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"abstract": over})
        self.assertEqual(self.value("abstract"), "x" * 1000,
                         "a refused PATCH changes nothing")

    def test_the_abstract_limit_counts_utf8_bytes_not_characters(self):
        """`len()` counts code points. A limit documented in bytes but enforced
        in characters does not exist for the users most likely to reach it."""
        limit = meta.MAX_ABSTRACT_UTF8_BYTES
        # Three bytes per character: well under the limit by character count,
        # well over it in bytes.
        multibyte = "\u65e5" * (limit // 3 + 10)
        self.assertLess(len(multibyte), limit)
        self.assertGreater(len(multibyte.encode("utf-8")), limit)
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("abstract", multibyte)), (400, {"code": "INVALID_ENVELOPE"}))
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"abstract": multibyte})
        # One that genuinely fits is accepted.
        fits = "\u65e5" * 1000
        self.assertEqual(self.send("abstract", fits)[0], 200)
        self.assertEqual(self.value("abstract"), fits)

    def test_the_small_scalars_keep_their_code_point_limits(self):
        """Switching them to bytes would quietly shorten every one by a factor
        of three for anyone writing CJK, which nothing here asked for."""
        self.assertNotIn("journal", meta.BYTE_LIMITED_FIELDS)
        self.assertEqual(sorted(meta.BYTE_LIMITED_FIELDS), ["abstract"])
        cjk = "\u65e5" * meta.SYNCED_FIELDS["journal"]
        self.assertGreater(len(cjk.encode("utf-8")), meta.SYNCED_FIELDS["journal"])
        self.assertEqual(self.send("journal", cjk)[0], 200)

    def test_abstract_uses_the_ordinary_scalar_conflict_path(self):
        self.send("abstract", "device text")
        # The projection carries Abstract's REVISION only: the Work record
        # already holds the value, and echoing a megabyte of it here would
        # double every read for something the client already has.
        self.assertEqual(self.state()["abstract"], {"revision": 1})
        self.assertEqual(self.value("abstract"), "device text")
        self.assertFalse(self.send("abstract", "device text", base=1)[1]["changed"])
        self.db.update_work_metadata(self.work, {"abstract": "server text"})
        self.assertEqual(self.state()["abstract"]["revision"], 2)
        conflict = self.send("abstract", "other text", base=1)[1]
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        # Convergent: the same text from a stale base is not a collision.
        self.assertEqual(self.send("abstract", "server text", base=1)[1]["code"], "ACKNOWLEDGED")

    def test_an_abstract_conflict_is_small_enough_to_persist(self):
        """The durable operation row bounds a structured result to 2 KB. A
        conflict the browser cannot store is a conflict the user never sees."""
        import json as _json
        big = "s" * (64 * 1024)
        self.db.update_work_metadata(self.work, {"abstract": big})
        conflict = self.send("abstract", "m" * (64 * 1024), base=0)[1]
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        self.assertNotIn("current_value", conflict)
        self.assertNotIn("requested_value", conflict)
        self.assertEqual(conflict["current_preview"], "s" * meta.CONFLICT_PREVIEW_CHARS)
        self.assertEqual(conflict["current_bytes"], len(big))
        self.assertEqual(conflict["requested_bytes"], 64 * 1024)
        structured = {k: v for k, v in conflict.items()
                      if k not in ("work_id", "field", "code")}
        self.assertLess(len(_json.dumps(structured).encode()), 2048,
                        "must fit the durable structured-result bound")

    def test_small_fields_still_report_their_values_in_a_conflict(self):
        self.db.update_work_metadata(self.work, {"doi": "10.1/server"})
        conflict = self.send("doi", "10.1/device", base=0)[1]
        self.assertEqual(conflict["current_value"], "10.1/server")
        self.assertEqual(conflict["requested_value"], "10.1/device")
        self.assertNotIn("current_preview", conflict)

    def test_the_ledger_never_becomes_a_second_copy_of_the_abstract(self):
        """`sync_operations` has no retention policy: whatever lands in
        `result_json` stays for the life of the library. Echoing the Abstract
        back would make every edit a permanent duplicate of the text."""
        body = "Q" * (300 * 1024)
        op = self.op("abstract", body)
        status, result = sync_protocol.process_operation(self.db, op)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)
        # The Work itself did receive the whole thing.
        self.assertEqual(self.value("abstract"), body)

        rows = self.db.execute_query(
            "SELECT result_json FROM sync_operations WHERE operation_type = 'SET_WORK_METADATA_FIELD'")
        self.assertEqual(len(rows), 1)
        stored = rows[0]["result_json"]
        self.assertLess(len(stored), 2048, "the ledger row stays small")
        self.assertNotIn(body[:200], stored, "the Abstract body is not in the ledger")

        # Replay returns the same compact result, and mutates nothing further.
        self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))
        self.assertEqual(self.db.execute_query(
            "SELECT result_json FROM sync_operations WHERE op_id = ?",
            (op["op_id"],))[0]["result_json"], stored)
        self.assertEqual(self.state()["abstract"], {"revision": 1})

    def test_small_fields_still_echo_their_value(self):
        result = self.send("doi", "10.1/echoed")[1]
        self.assertEqual(result["value"], "10.1/echoed")
        self.assertNotIn("value_omitted", result)

    def test_scope_keys_are_structural(self):
        self.assertNotEqual(meta.scope_key("W-a:b", "doi"), meta.scope_key("W-a", "b:doi"))
