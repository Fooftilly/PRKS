"""Storage status object counts on PostgreSQL (storage-architecture §11.1, #720).

Counts only: referenced keys whose object is gone are ``missing``; cleanup
claims split into released (``pending_cleanup``), sweep orphans and held.
"""

from __future__ import annotations

import json

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
