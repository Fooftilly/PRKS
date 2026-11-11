"""Field-scoped Work metadata on PostgreSQL (the retired ``tests/test_work_metadata_sync.py``).

Each synchronized field has its own revision scope, so different fields
never conflict and only a change advances one. The ordinary PATCH and the
bulk Status action advance the same revisions as ``SET_WORK_METADATA_FIELD``,
values and revisions commit or roll back together, every path enforces one
size and allowlist contract, and a large value never becomes a second copy in
the ledger or a conflict the browser cannot store. The pure rules (registry,
codecs, previews, the shared result bound, the writer audit) are in
``tests/test_work_metadata_rules.py``.

The Work under test has a PDF: ``thumb_page`` belongs to the primary Asset on
PostgreSQL, and a Work with no file or stream has nowhere to keep it.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest import mock

from backend import work_metadata_sync as rules
from backend.domain.works import metadata, revisions
from backend.domain.works.rules import BulkWorkError
from tests.pg.test_works_sync import SyncTestCase

_MISSING = "W-" + "E" * 32
_WATCH = "https://www.youtube.com/watch?v=%s"
_OPERATION = "SET_WORK_METADATA_FIELD"


class MetadataSyncTestCase(SyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Metadata work", file_path=self.store_pdf("m.pdf", b"%PDF-1.4\n"))

    def op(self, field: Any, value: Any, base: Optional[int] = 0, work: Optional[str] = None,
           **changes: Any) -> dict[str, Any]:
        return self.envelope(_OPERATION, work or self.work, {"field": field, "value": value}, base, **changes)

    def put(self, field: str, value: Any, base: Optional[int] = 0,
            work: Optional[str] = None) -> tuple[int, dict[str, Any]]:
        return self.send(self.op(field, value, base, work))

    def state(self, work: Optional[str] = None) -> dict[str, Any]:
        return self.library.get_work_metadata_state(work or self.work)["fields"]

    def value(self, field: str, work: Optional[str] = None) -> Any:
        """The Work record's value: typed for ``thumb_page``, as stored for every other field."""
        return self.library.get_work(work or self.work)[field]

    def wire_value(self, field: str) -> str:
        return rules.database_to_wire(field, self.value(field))

    def patch(self, fields: dict[str, Any], work: Optional[str] = None) -> None:
        self.library.update_work_metadata(work or self.work, fields)

    def bulk_status(self, work_ids: list[str], status: str) -> None:
        self.library.bulk_update_works({"work_ids": work_ids, "action": "set_status", "status": status})

    def sample(self, field: str, index: int) -> str:
        """A value the field accepts and the Work does not already hold."""
        if field in rules.FIELD_CODECS:
            current = self.wire_value(field)
            return str((int(current) if current else 0) + 1 + index)
        if field in rules.FIELD_ALLOWLISTS:
            current = self.value(field)
            allowed = [v for v in sorted(rules.FIELD_ALLOWLISTS[field]) if v != current]
            return allowed[index % len(allowed)]
        return "value-%d" % index

    def ledger_results(self) -> list[str]:
        return [json.dumps(row["result"]) for row in self.ledger()]

    @staticmethod
    def durable_bytes(result: dict[str, Any]) -> int:
        """What the browser measures: ``JSON.stringify`` keeps non-ASCII as is."""
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def conflict_over(self, field: str, server_value: str, device_value: str = "D" * 64) -> dict[str, Any]:
        self.patch({field: server_value})
        status, result = self.put(field, device_value, base=0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        return result

    def fail_on_advance(self, call: int) -> Any:
        """Make the ``call``-th revision advance raise, after the earlier ones wrote."""
        real = revisions.advance
        calls = {"n": 0}

        def advance(*args: Any, **kwargs: Any) -> int:
            calls["n"] += 1
            if calls["n"] == call:
                raise RuntimeError("injected")
            return real(*args, **kwargs)

        return mock.patch.object(revisions, "advance", side_effect=advance)


class FieldRegistryTests(MetadataSyncTestCase):
    def test_every_supported_field_round_trips(self) -> None:
        for index, field in enumerate(sorted(rules.SYNCED_FIELDS)):
            with self.subTest(field=field):
                wanted = self.sample(field, index + 1)
                status, result = self.put(field, wanted)
                self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                                 (200, "ACKNOWLEDGED", True, 1))
                self.assertEqual(self.wire_value(field), wanted)

    def test_an_unsupported_or_malformed_envelope_is_refused_unledgered(self) -> None:
        for field in ("id", "doi; DROP TABLE works", "", None, 7, "hide_pdf_link_annotations"):
            with self.subTest(field=field):
                self.assertEqual(self.send(self.op(field, "x"))[0], 400)
        for value in (None, 7, ["x"], {"a": 1}, True, "x" * (rules.SYNCED_FIELDS["doi"] + 1)):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(self.send(self.op("doi", value))[0], 400)
        for changes in ({"base_revision": None}, {"payload": {"field": "doi"}},
                        {"payload": {"field": "doi", "value": "x", "extra": 1}}, {"payload": {}},
                        {"entity_type": "concept"}):
            with self.subTest(changes=str(changes)[:50]):
                envelope = self.op("doi", "x")
                envelope.update(changes)
                self.assertEqual(self.send(envelope)[0], 400)
        self.assertEqual(self.value("title"), "Metadata work")
        self.assertEqual((self.ledger(), self.revisions_of(self.work)), ([], {}))
        # Exactly at the limit is legitimate.
        self.assertEqual(self.put("doi", "d" * rules.SYNCED_FIELDS["doi"])[0], 200)

    def test_values_are_stored_exactly_as_patch_stores_them(self) -> None:
        for value in ("10.1234/Example-MiXeD", "978-0-306-40615-7", " leading and trailing ", "12–34"):
            with self.subTest(value=value):
                self.put("doi", value, base=self.state()["doi"]["revision"])
                self.assertEqual(self.value("doi"), value)
        second = self.library.add_work("Patch mirror")
        self.patch({"doi": "10.1234/Example-MiXeD"}, second)
        self.assertEqual(self.value("doi", second), "10.1234/Example-MiXeD")

    def test_null_and_empty_are_one_logical_value(self) -> None:
        self.sql("UPDATE prks.manifestations SET doi = NULL WHERE work_id = %s", (self.work,))
        self.assertEqual(self.state()["doi"], {"value": "", "revision": 0})
        status, result = self.put("doi", "")
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 0))
        self.assertEqual(self.sql("SELECT doi FROM prks.manifestations WHERE work_id = %s", (self.work,)),
                         [{"doi": None}])


class ConflictRuleTests(MetadataSyncTestCase):
    def test_scalar_conflict_matrix_and_exact_replay(self) -> None:
        cases = (
            (0, "first", "ACKNOWLEDGED", True, 1),
            (1, "first", "ACKNOWLEDGED", False, 1),
            (1, "second", "ACKNOWLEDGED", True, 2),
            (0, "second", "ACKNOWLEDGED", False, 2),
            (0, "third", "REVISION_CONFLICT", None, 2),
            (9, "fourth", "FUTURE_REVISION", None, 2),
        )
        for base, desired, code, changed, revision in cases:
            with self.subTest(base=base, desired=desired):
                data = self.op("doi", desired, base)
                answer = self.send(data)
                self.assertEqual(answer[1]["code"], code)
                if changed is not None:
                    self.assertEqual(answer[1]["changed"], changed)
                self.assertEqual(self.state()["doi"]["revision"], revision)
                self.assertEqual(self.send(data), answer)
        conflict = self.put("doi", "third", 0)[1]
        self.assertEqual((conflict["current_value"], conflict["requested_value"], conflict["current_revision"]),
                         ("second", "third", 2))

    def test_a_reused_op_id_is_refused_and_changes_nothing(self) -> None:
        first = self.send(self.op("doi", "10.1/once", op_id="00000000-0000-4000-8000-000000000001"))
        self.assertEqual((first[0], first[1]["code"]), (200, "ACKNOWLEDGED"))
        again = self.send(self.op("doi", "10.1/once", op_id="00000000-0000-4000-8000-000000000001"))
        self.assertEqual(again, first)
        reused = self.send(self.op("doi", "10.1/other", op_id="00000000-0000-4000-8000-000000000001"))
        self.assertEqual((reused[0], reused[1]["code"]), (409, "OP_ID_REUSE"))
        self.assertEqual((self.value("doi"), self.state()["doi"]["revision"]), ("10.1/once", 1))

    def test_a_stale_but_convergent_edit_is_not_a_conflict(self) -> None:
        self.put("doi", "10.1/agreed")
        status, result = self.put("doi", "10.1/agreed", base=0)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["doi"]["revision"], 1)

    def test_a_missing_work_is_terminal_and_replays(self) -> None:
        data = self.op("doi", "x", work=_MISSING)
        answer = self.send(data)
        self.assertEqual((answer[0], answer[1]["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(self.send(data), answer)

    def test_different_fields_never_conflict_with_each_other(self) -> None:
        self.put("doi", "10.1/a")
        self.put("isbn", "111")
        self.put("isbn", "222", base=1)
        self.assertEqual((self.state()["doi"]["revision"], self.state()["isbn"]["revision"]), (1, 2))
        self.assertEqual(self.put("doi", "10.1/b", base=1)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("doi"), self.value("isbn")), ("10.1/b", "222"))
        # One field conflicting leaves the others writable.
        self.assertEqual(self.put("doi", "device-doi", base=0)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.put("journal", "device-journal", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("journal"), "device-journal")


class PatchBoundaryTests(MetadataSyncTestCase):
    def test_patch_advances_only_the_fields_it_changed(self) -> None:
        self.patch({"doi": "10.1/patched", "isbn": "555"})
        self.assertEqual((self.state()["doi"]["revision"], self.state()["isbn"]["revision"]), (1, 1))
        self.patch({"doi": "10.1/patched", "isbn": "666"})
        state = self.state()
        self.assertEqual((state["doi"]["revision"], state["isbn"]["revision"]), (1, 2))
        self.assertEqual((state["doi"]["value"], state["isbn"]["value"]), ("10.1/patched", "666"))
        self.assertEqual(self.put("doi", "10.1/device", base=0)[1]["code"], "REVISION_CONFLICT")

    def test_every_synchronized_field_advances_its_revision_through_the_patch(self) -> None:
        for index, field in enumerate(sorted(rules.SYNCED_FIELDS)):
            with self.subTest(field=field):
                before = self.state()[field]["revision"]
                wanted = self.sample(field, index + 1)
                self.patch({field: wanted})
                self.assertEqual(self.state()[field]["revision"], before + 1)
                self.assertEqual(self.wire_value(field), wanted)
                self.assertEqual(self.put(field, self.sample(field, index + 2), base=before)[1]["code"],
                                 "REVISION_CONFLICT")

    def test_unrelated_metadata_never_advances_a_field_revision(self) -> None:
        self.put("doi", "10.1/keep")
        before = self.state()
        self.patch({"text_content": "Notes", "hide_pdf_link_annotations": True})
        self.assertEqual(self.state(), before)

    def test_patch_commits_values_and_revisions_together(self) -> None:
        with self.fail_on_advance(2), self.assertRaises(RuntimeError):
            self.patch({"title": "Rolled back", "doi": "10.1/rolled"})
        self.assertEqual((self.value("title"), self.value("doi")), ("Metadata work", ""))
        self.assertEqual(self.revisions_of(self.work), {})

    def test_creation_manufactures_no_revision(self) -> None:
        created = self.library.add_work("Fresh Title", status="Planned", doc_type="book", author_text="Jane Smith",
                                        doi="10.1/initial", isbn="999",
                                        file_path=self.store_pdf("f.pdf", b"%PDF-1.4\n"), thumb_page=4)
        state = self.state(created)
        self.assertEqual({field: entry["revision"] for field, entry in state.items()},
                         {field: 0 for field in rules.SYNCED_FIELDS})
        self.assertEqual((state["status"]["value"], state["doc_type"]["value"], state["doi"]["value"],
                          state["isbn"]["value"], state["thumb_page"]["value"]),
                         ("Planned", "book", "10.1/initial", "999", "4"))
        self.assertEqual((state["title"], state["author_text"]), ({"revision": 0}, {"revision": 0}))
        work = self.library.get_work(created)
        self.assertEqual((work["title"], work["author_text"], work["thumb_page"]),
                         ("Fresh Title", "Jane Smith", 4))


class StatusTests(MetadataSyncTestCase):
    def test_bulk_status_advances_the_revisions_every_other_path_uses(self) -> None:
        other = self.library.add_work("Second", status="Planned")
        self.patch({"status": "Planned"})
        before = self.state()["status"]["revision"]
        self.bulk_status([self.work, other], "Completed")
        self.assertEqual(self.value("status"), "Completed")
        self.assertEqual(self.state()["status"]["revision"], before + 1)
        self.assertEqual(self.state(other)["status"], {"value": "Completed", "revision": 1})
        for field in ("doi", "year", "abstract"):
            self.assertEqual(self.state()[field]["revision"], 0, field)

    def test_a_no_op_bulk_status_manufactures_no_revision(self) -> None:
        self.bulk_status([self.work], "Completed")
        after_first = self.state()["status"]["revision"]
        self.bulk_status([self.work], "Completed")
        self.assertEqual(self.state()["status"]["revision"], after_first)
        self.assertEqual(self.put("status", "Paused", base=after_first)[1]["code"], "ACKNOWLEDGED")

    def test_bulk_status_values_and_revisions_roll_back_together(self) -> None:
        other = self.library.add_work("Second", status="Planned")
        self.patch({"status": "Planned"})
        before = (self.state(), self.state(other))
        with self.fail_on_advance(2), self.assertRaises(RuntimeError):
            self.bulk_status([self.work, other], "Completed")
        self.assertEqual((self.value("status"), self.value("status", other)), ("Planned", "Planned"))
        self.assertEqual((self.state(), self.state(other)), before)

    def test_a_bulk_change_makes_an_offline_device_conflict(self) -> None:
        self.patch({"status": "Planned"})
        observed = self.state()["status"]["revision"]
        self.bulk_status([self.work], "Completed")
        self.assertEqual(self.state()["status"]["revision"], observed + 1)
        status, result = self.put("status", "Paused", base=observed)
        self.assertEqual((status, result["code"], result["current_value"]), (409, "REVISION_CONFLICT", "Completed"))
        self.assertEqual(self.value("status"), "Completed")

    def test_status_is_validated_by_allowlist_on_every_mutation_path(self) -> None:
        for bad in ("Finished", "completed", "", "Done", " "):
            with self.subTest(value=bad):
                self.assertEqual(self.send(self.op("status", bad))[0], 400)
                with self.assertRaises(ValueError):
                    self.patch({"status": bad})
                with self.assertRaises(BulkWorkError):
                    self.bulk_status([self.work], bad)
        self.assertEqual((self.value("status"), self.state()["status"]["revision"]), ("Not Started", 0))
        self.assertEqual(self.ledger(), [])

    def test_a_status_edit_converges_and_its_conflict_blocks_nothing_else(self) -> None:
        self.patch({"status": "Planned"})
        base = self.state()["status"]["revision"]
        self.patch({"status": "Completed"})
        advanced = self.state()["status"]["revision"]
        status, result = self.put("status", "Completed", base=base)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["status"]["revision"], advanced)
        self.assertEqual(self.put("status", "Paused", base=base)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.put("year", "1998", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.put("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("status"), "Completed")


class LargeFieldTests(MetadataSyncTestCase):
    def test_author_text_has_one_byte_limit_on_both_paths(self) -> None:
        limit = rules.MAX_AUTHOR_TEXT_UTF8_BYTES
        status, result = self.put("author_text", "a" * limit)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.value("author_text")), limit)
        self.assertEqual(self.send(self.op("author_text", "a" * (limit + 1), base=1))[0], 400)
        with self.assertRaises(ValueError):
            self.patch({"author_text": "a" * (limit + 1)})
        self.assertEqual(len(self.value("author_text")), limit)
        # Bytes, not characters: 30k CJK characters are 90k bytes.
        self.assertEqual(self.send(self.op("author_text", "漢" * 30000, base=1))[0], 400)
        self.assertEqual(self.put("author_text", "漢" * 20000, base=1)[1]["code"], "ACKNOWLEDGED")
        # Whitespace is stored as sent: the editor trims, the server does not.
        self.assertEqual(self.put("author_text", "  Jane  ", base=2)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("author_text"), "  Jane  ")

    def test_abstract_has_one_byte_limit_on_both_paths(self) -> None:
        limit = rules.MAX_ABSTRACT_UTF8_BYTES
        self.assertEqual(self.put("abstract", "x" * limit)[0], 200)
        self.assertEqual(self.value("abstract"), "x" * limit)
        self.assertEqual(self.send(self.op("abstract", "x" * (limit + 1), base=1))[0], 400)
        self.patch({"abstract": "x" * 1000})
        with self.assertRaises(ValueError):
            self.patch({"abstract": "x" * (limit + 1)})
        multibyte = "日" * (limit // 3 + 10)
        self.assertEqual(self.send(self.op("abstract", multibyte, base=2))[0], 400)
        with self.assertRaises(ValueError):
            self.patch({"abstract": multibyte})
        self.assertEqual(self.value("abstract"), "x" * 1000)
        self.assertEqual(self.put("abstract", "日" * 1000, base=2)[0], 200)
        self.assertEqual(self.value("abstract"), "日" * 1000)

    def test_the_small_scalars_keep_their_code_point_limits(self) -> None:
        cjk = "日" * rules.SYNCED_FIELDS["journal"]
        self.assertEqual(self.put("journal", cjk)[0], 200)
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                limit = rules.SYNCED_FIELDS[field]
                self.assertEqual(self.put(field, "x" * limit)[0], 200)
                self.assertEqual(self.send(self.op(field, "x" * (limit + 1), base=1))[0], 400)

    def test_a_large_acknowledgement_stays_out_of_the_ledger(self) -> None:
        for field, body in (("author_text", "Q" * (32 * 1024)), ("abstract", "R" * (300 * 1024)),
                            ("title", "A Renamed Work"), ("source_url", "https://example.com/" + "a" * 1000)):
            with self.subTest(field=field):
                data = self.op(field, body)
                status, result = self.send(data)
                self.assertEqual((status, result["code"], result["value_omitted"]), (200, "ACKNOWLEDGED", True))
                self.assertNotIn("value", result)
                self.assertEqual(self.value(field), body)
                self.assertEqual(self.state()[field], {"revision": 1})
                self.assertEqual(self.send(data), (status, result))
        stored = self.ledger_results()
        self.assertEqual(len(stored), 4)
        for row in stored:
            self.assertLess(len(row), 1024)
            self.assertNotIn("QQQQQQQQ", row)
            self.assertNotIn("RRRRRRRR", row)
        # A small field still echoes its value, and the projection carries it.
        result = self.put("doi", "10.1/echoed")[1]
        self.assertEqual(result["value"], "10.1/echoed")
        self.assertNotIn("value_omitted", result)
        self.assertEqual(self.state()["doi"], {"value": "10.1/echoed", "revision": 1})
        self.assertEqual(self.library.get_work(self.work)["title"], "A Renamed Work")

    def test_large_conflicts_report_sizes_and_previews_within_the_bound(self) -> None:
        for field, server, device in (("author_text", "S" * (40 * 1024), "D" * (40 * 1024)),
                                      ("title", "S" * 40000, "D" * 40000),
                                      ("abstract", "s" * (64 * 1024), "m" * (64 * 1024))):
            with self.subTest(field=field):
                result = self.conflict_over(field, server, device)
                self.assertNotIn("current_value", result)
                self.assertNotIn("requested_value", result)
                self.assertEqual((result["current_bytes"], result["requested_bytes"]), (len(server), len(device)))
                self.assertEqual(result["current_preview"], server[:rules.CONFLICT_PREVIEW_CHARS])
                self.assertLessEqual(self.durable_bytes(result), rules.MAX_DURABLE_RESULT_BYTES)
        small = self.conflict_over("doi", "10.1/server", "10.1/device")
        self.assertEqual((small["current_value"], small["requested_value"]), ("10.1/server", "10.1/device"))
        self.assertNotIn("current_preview", small)

    def test_large_fields_follow_the_ordinary_scalar_rule(self) -> None:
        for field in ("author_text", "abstract", "title"):
            with self.subTest(field=field):
                self.patch({field: "Alpha"})
                base = self.state()[field]["revision"]
                self.assertFalse(self.put(field, "Alpha", base=base)[1]["changed"])
                self.patch({field: "Bob"})
                result = self.put(field, "Alice", base=base)[1]
                self.assertEqual((result["code"], result["current_preview"]), ("REVISION_CONFLICT", "Bob"))
                self.assertEqual(self.put(field, "Bob", base=base)[1]["code"], "ACKNOWLEDGED")
        for field in ("doi", "status", "doc_type"):
            self.assertEqual(self.put(field, self.sample(field, 1), base=0)[1]["code"], "ACKNOWLEDGED", field)

    def test_source_url_is_a_field_only_where_it_is_provenance(self) -> None:
        status, result = self.put("source_url", "https://example.com/paper")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.value("source_url"), "https://example.com/paper")
        video = self.library.add_work("Clip", source_kind="video", source_url=_WATCH % "AAA",
                                      provider="youtube", provider_id="AAA")
        status, result = self.put("source_url", _WATCH % "BBB", work=video)
        self.assertEqual((status, result["code"]), (409, "WRONG_OPERATION_FOR_SOURCE"))
        self.assertNotIn("current_value", result)
        with self.assertRaises(ValueError):
            self.patch({"source_url": "https://x/B"}, video)
        work = self.library.get_work(video)
        self.assertEqual((work["source_url"], work["provider_id"]), (_WATCH % "AAA", "AAA"))
        self.patch({"source_url": "https://example.com/other"})
        self.assertEqual(self.value("source_url"), "https://example.com/other")


class ThumbPageTests(MetadataSyncTestCase):
    def column(self) -> list[dict[str, Any]]:
        return self.sql("SELECT a.thumb_page, pg_typeof(a.thumb_page)::text AS type FROM prks.assets a"
                        " JOIN prks.manifestations m ON a.id = m.primary_asset_id WHERE m.work_id = %s",
                        (self.work,))

    def test_an_invalid_page_never_reaches_the_column(self) -> None:
        for bad in ("0", "-1", "1.5", "abc", "+3"):
            with self.subTest(wire=bad):
                self.assertEqual(self.send(self.op("thumb_page", bad))[0], 400)
                with self.assertRaises(ValueError):
                    self.patch({"thumb_page": bad})
        self.assertIsNone(self.value("thumb_page"))
        self.assertEqual(self.ledger(), [])

    def test_the_column_stays_typed_whichever_path_writes_it(self) -> None:
        self.assertEqual(self.put("thumb_page", "4")[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.column(), [{"thumb_page": 4, "type": "integer"}])
        for value, stored in ((7, 7), ("9", 9), (None, None)):
            with self.subTest(value=value):
                self.patch({"thumb_page": value})
                self.assertEqual(self.column(), [{"thumb_page": stored, "type": "integer"}])

    def test_representation_differences_are_not_changes(self) -> None:
        self.patch({"thumb_page": 3})
        self.assertEqual(self.state()["thumb_page"]["revision"], 1)
        for same in ("3", "003", " 3 ", 3):
            with self.subTest(value=repr(same)):
                if isinstance(same, str):
                    status, result = self.put("thumb_page", same, base=1)
                    self.assertEqual((status, result["changed"]), (200, False))
                else:
                    self.patch({"thumb_page": same})
                self.assertEqual(self.state()["thumb_page"]["revision"], 1)
        self.patch({"thumb_page": None})
        self.assertFalse(self.put("thumb_page", "", base=2)[1]["changed"])
        self.assertEqual(self.state()["thumb_page"]["revision"], 2)

    def test_a_stale_but_convergent_page_is_not_a_conflict(self) -> None:
        self.patch({"thumb_page": 2})
        base = self.state()["thumb_page"]["revision"]
        self.patch({"thumb_page": 5})
        for spelling in ("5", "005", " 5 "):
            with self.subTest(wire=spelling):
                status, result = self.put("thumb_page", spelling, base=base)
                self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        status, result = self.put("thumb_page", "7", base=base)
        self.assertEqual((status, result["current_value"], result["requested_value"]), (409, "5", "7"))
        self.assertEqual(self.put("thumb_page", "", base=base)[1]["code"], "REVISION_CONFLICT")

    def test_metadata_state_reports_the_wire_form_and_the_work_the_typed_one(self) -> None:
        self.put("thumb_page", "6")
        self.assertEqual((self.state()["thumb_page"], self.value("thumb_page")), ({"value": "6", "revision": 1}, 6))
        self.put("thumb_page", "", base=1)
        self.assertEqual((self.state()["thumb_page"], self.value("thumb_page")), ({"value": "", "revision": 2}, None))


class DocTypeTests(MetadataSyncTestCase):
    def test_doc_type_normalizes_on_patch_and_is_strict_on_the_wire(self) -> None:
        self.patch({"doc_type": "BOOK"})
        self.assertEqual(self.value("doc_type"), "book")
        self.patch({"doc_type": "nonsense"})
        self.assertEqual(self.value("doc_type"), "misc")
        for bad in ("BOOK", "bogus", "", "  article"):
            with self.subTest(wire=bad):
                self.assertEqual(self.send(self.op("doc_type", bad, base=2))[0], 400)
        self.assertEqual(self.value("doc_type"), "misc")

    def test_doc_type_revisions_behave_like_every_other_scalar(self) -> None:
        self.patch({"doc_type": "book"})
        base = self.state()["doc_type"]["revision"]
        status, result = self.put("doc_type", "online", base=base)
        self.assertEqual((status, result["changed"], result["value"]), (200, True, "online"))
        self.assertFalse(self.put("doc_type", "online", base=base + 1)[1]["changed"])
        self.assertEqual(self.state()["doc_type"]["revision"], base + 1)
        self.assertEqual(self.put("doc_type", "online", base=base)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.put("doc_type", "manual", base=base)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.put("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")


class ConflictBoundTests(MetadataSyncTestCase):
    def test_every_conflict_result_fits_what_the_browser_can_store(self) -> None:
        cases = [
            ("author_text", "\x01\x1f" * 2500), ("author_text", "\x7f" * 5000),
            ("author_text", "\U0001f9ea" * 5000), ("author_text", "日" * 5000), ("author_text", '"\\' * 2500),
            ("author_text", "é" * 2500), ("abstract", "\x01" * 20000),
            ("journal", "\x01" * rules.SYNCED_FIELDS["journal"]),
            ("publisher", "\x02" * rules.SYNCED_FIELDS["publisher"]),
            ("doi", "\x03" * rules.SYNCED_FIELDS["doi"]),
        ]
        for field, server_value in cases:
            with self.subTest(field=field, value=repr(server_value[:4])):
                result = self.conflict_over(field, server_value)
                self.assertLessEqual(self.durable_bytes(result), rules.MAX_DURABLE_RESULT_BYTES)
        # PostgreSQL text holds no NUL, so no stored value carries one; the
        # protocol refuses one on the wire before any handler runs.
        self.assertEqual(self.send(self.op("author_text", "\x00" * 5000, base=9))[0], 400)

    def test_a_small_scalar_degrades_to_previews_only_when_it_must(self) -> None:
        ordinary = self.conflict_over("journal", "Nature Physics")
        self.assertEqual((ordinary["current_value"], ordinary["requested_value"]), ("Nature Physics", "D" * 64))
        self.assertNotIn("current_preview", ordinary)
        pathological = self.conflict_over("publisher", "\x01" * 500)
        self.assertFalse({"current_value", "requested_value"} & set(pathological))
        self.assertEqual(pathological["current_bytes"], 500)
        self.assertLessEqual(self.durable_bytes(pathological), rules.MAX_DURABLE_RESULT_BYTES)

    def test_the_preview_is_the_longest_whole_prefix_that_fits(self) -> None:
        ascii_case = self.conflict_over("author_text", "A" * 5000)
        self.assertEqual(len(ascii_case["current_preview"]), rules.CONFLICT_PREVIEW_CHARS)
        control = self.conflict_over("abstract", "\x01" * 5000)
        self.assertGreater(len(control["current_preview"]), 0)
        self.assertLess(len(control["current_preview"]), rules.CONFLICT_PREVIEW_CHARS)
        longer = dict(control, current_preview=control["current_preview"] + "\x01")
        self.assertGreater(self.durable_bytes(longer), rules.MAX_DURABLE_RESULT_BYTES)
        astral = self.conflict_over("title", "\U0001f9ea" * 5000)["current_preview"]
        self.assertTrue(astral)
        self.assertEqual(astral, "\U0001f9ea" * len(astral))

    def test_the_server_measures_at_least_what_the_browser_stores(self) -> None:
        stored_keys = ("code", "current_revision", "current_value", "requested_value",
                       "current_preview", "current_bytes", "requested_bytes")
        result = self.conflict_over("author_text", "\x01" * 5000)
        stored = {key: value for key, value in result.items() if key in stored_keys}
        self.assertLess(len(stored), len(result))
        self.assertLessEqual(self.durable_bytes(stored), rules.serialized_result_bytes(result))
        self.assertLessEqual(self.durable_bytes(stored), rules.MAX_DURABLE_RESULT_BYTES)


class ProjectionTests(MetadataSyncTestCase):
    def test_projection_lists_every_field_of_its_own_work_only(self) -> None:
        state = self.library.get_work_metadata_state(self.work)
        self.assertEqual((state["work_id"], sorted(state["fields"])), (self.work, sorted(rules.SYNCED_FIELDS)))
        self.assertIsNone(self.library.get_work_metadata_state(_MISSING))
        noisy = [self.library.add_work("Noise %d" % i) for i in range(5)]
        for work in noisy:
            self.patch({"doi": "10.1/%s" % work, "isbn": work}, work)
        self.patch({"doi": "10.1/mine"})
        with mock.patch.object(revisions, "read", wraps=revisions.read) as read:
            state = self.library.get_work_metadata_state(self.work)["fields"]
        self.assertEqual((state["doi"], state["isbn"]), ({"value": "10.1/mine", "revision": 1},
                                                          {"value": "", "revision": 0}))
        self.assertEqual(read.call_count, 1, "one query, not one per field")
        scope_ids = read.call_args.args[2]
        self.assertEqual(sorted(scope_ids), sorted(metadata.scope_id(self.work, f) for f in rules.SYNCED_FIELDS))
        for work in noisy:
            self.assertEqual(self.state(work)["isbn"]["revision"], 1)

    def test_etag_moves_for_any_value_or_revision_change(self) -> None:
        seen: set[str] = set()

        def snapshot(label: str) -> None:
            etag = self.library.etag_for_representation("work-metadata-state",
                                                         self.library.get_work_metadata_state(self.work))
            self.assertNotIn(etag, seen, label)
            seen.add(etag)

        snapshot("baseline")
        self.put("doi", "10.1/aaaa")
        snapshot("doi value")
        self.put("doi", "10.1/bbbb", base=1)
        snapshot("same-length replacement")
        self.put("isbn", "111")
        snapshot("another field")
        self.sql("UPDATE prks.entity_revisions SET revision = revision + 5"
                 " WHERE scope_type = 'work-field' AND scope_id = %s", (metadata.scope_id(self.work, "doi"),))
        snapshot("revision only")

    def test_recently_added_carries_publisher_for_its_local_filter(self) -> None:
        self.patch({"publisher": "Fixture Press"})
        row = next(r for r in self.library.get_recently_added_browse() if r["id"] == self.work)
        self.assertEqual(row["publisher"], "Fixture Press")

    def test_year_and_published_date_are_independent_conflict_units(self) -> None:
        self.put("year", "1998")
        self.put("published_date", "2020-05-01")
        state = self.state()
        self.assertEqual((state["year"], state["published_date"]),
                         ({"value": "1998", "revision": 1}, {"value": "2020-05-01", "revision": 1}))
        self.put("published_date", "2021-06-02", base=1)
        self.assertEqual(self.put("year", "2001", base=1)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("year"), self.value("published_date")), ("2001", "2021-06-02"))
        self.assertEqual(self.put("year", "1975", base=1)[1]["code"], "REVISION_CONFLICT")
        self.assertTrue(self.put("published_date", "", base=2)[1]["changed"])
        self.assertEqual((self.value("published_date"), self.state()["published_date"]),
                         ("", {"value": "", "revision": 3}))

    def test_publisher_and_location_are_ordinary_independent_fields(self) -> None:
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                self.assertEqual(self.put(field, "first")[1]["code"], "ACKNOWLEDGED")
                self.assertEqual(self.state()[field], {"value": "first", "revision": 1})
                self.assertFalse(self.put(field, "first", base=1)[1]["changed"])
                self.assertEqual(self.put(field, "second", base=0)[1]["code"], "REVISION_CONFLICT")
                self.assertEqual(self.put(field, "second", base=1)[1]["code"], "ACKNOWLEDGED")
        self.put("location", "Cambridge, UK; Paris; Berlin", base=2)
        self.assertEqual(self.put("publisher", "Springer", base=2)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("publisher"), self.value("location")),
                         ("Springer", "Cambridge, UK; Paris; Berlin"))


class SearchTests(MetadataSyncTestCase):
    def found(self, term: str) -> list[str]:
        return [row["id"] for row in self.library.search_works(term)]

    def test_search_finds_a_synchronized_title_and_author_text(self) -> None:
        for field, old, new in (("title", "Alpha Treatise", "Beta Treatise"),
                                ("author_text", "Gamma Person", "Delta Person")):
            with self.subTest(field=field):
                self.patch({field: old})
                self.assertIn(self.work, self.found(old))
                self.assertEqual(self.put(field, new, base=self.state()[field]["revision"])[1]["code"],
                                 "ACKNOWLEDGED")
                self.assertIn(self.work, self.found(new))
                self.assertNotIn(self.work, self.found(old))

    def test_a_value_never_sent_is_not_a_search_term(self) -> None:
        self.patch({"author_text": "Alpha Person"})
        self.assertEqual(self.found("Beta Person"), [])
        self.assertIn(self.work, self.found("Alpha Person"))
