"""Storage status object counts on PostgreSQL (storage-architecture §11.1, #720).

Counts only: referenced keys whose object is gone are ``missing``; cleanup
claims split into released (``pending_cleanup``), sweep orphans and held.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.api import storage as storage_api
from backend.domain.objects.status import object_counts
from backend.storage.objects import NAMESPACE_PORTRAITS
from tests.pg.test_objects_lifecycle import ObjectsTestCase, key


class StorageStatusCountsTests(ObjectsTestCase):
    def test_an_empty_instance_counts_nothing(self) -> None:
        counts = object_counts(self.db, self.objects)
        self.assertEqual(
            (counts.referenced, counts.missing, counts.pending_cleanup, counts.orphans, counts.held),
            (0, 0, 0, 0, 0),
        )

    def test_missing_objects_and_claims_are_counted(self) -> None:
        kept = self.put(b"%PDF-1.4 kept")
        lost = self.put(b"%PDF-1.4 lost")
        self.create(kept)
        self.create(lost)
        self.objects.delete(lost.key)
        self.claim(key("released.pdf"), self.actor.library_id)
        self.claim(key("orphan.pdf"))
        self.claim(key("held.jpg", NAMESPACE_PORTRAITS), self.actor.library_id, hold=True)

        counts = object_counts(self.db, self.objects)

        self.assertEqual(counts.referenced, 2)
        self.assertEqual(counts.missing, 1)
        self.assertEqual((counts.pending_cleanup, counts.orphans, counts.held), (1, 1, 1))

    def test_a_key_released_and_settled_during_the_scan_is_not_missing(self) -> None:
        kept = self.put(b"%PDF-1.4 kept")
        gone = self.put(b"%PDF-1.4 gone")
        self.create(kept)
        work_id = self.create(gone)
        replacement = self.put(b"%PDF-1.4 replacement")
        real_stat = self.objects.stat

        def stat(stat_key):
            if stat_key == gone.key:
                # After the first read, before this stat: the Work's PDF is
                # replaced and cleanup settles the old file.
                self.sql("UPDATE prks.assets SET storage_locator = %s WHERE work_id = %s",
                         (replacement.key.name, work_id))
                real_stat.__self__.delete(gone.key)
            return real_stat(stat_key)

        with patch.object(self.objects, "stat", side_effect=stat):
            counts = object_counts(self.db, self.objects)

        self.assertEqual((counts.referenced, counts.missing), (2, 0))

    def test_the_database_is_named_as_the_server_reports_it(self) -> None:
        summary = storage_api.live_database_summary(self.db, None)
        expected = self.sql("SELECT current_database() AS name, inet_server_port() AS port")[0]
        self.assertEqual(summary["name"], expected["name"])
        self.assertEqual(summary["port"], None if expected["port"] is None else str(expected["port"]))
        self.assertTrue(summary["host"])

    def test_the_body_names_no_key(self) -> None:
        lost = self.put(b"%PDF-1.4 secret-name")
        self.create(lost)
        self.objects.delete(lost.key)
        body = storage_api.body(
            configured_root=self.storage.root, resolved_root=self.storage.root, source=None, leased=False,
            database=self.db, storage=self.objects,
        )
        self.assertEqual(body["objects"]["missing"], 1)
        self.assertNotIn(lost.key.name, json.dumps(body))
