"""CREATE_WORK: video Work construction under a client-minted id."""
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_lifecycle_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


def _payload(**overrides):
    base = {
        "title": "Talk",
        "status": "Not Started",
        "doc_type": "online",
        "abstract": "",
        "author_text": "Channel",
        "year": "2024",
        "published_date": "",
        "urldate": "",
        "private_notes": "",
        "thumb_url": "",
        "source": {"kind": "video", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        "folder_id": "",
        "playlist_id": "",
        "roles": [],
    }
    base.update(overrides)
    return base


class WorkCreateSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-work-create-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    def work_id(self):
        return "W-" + uuid.uuid4().hex.upper()

    def send(self, work_id, payload=None, base=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device,
            operation="CREATE_WORK", entity_type="work", entity_id=work_id,
            payload=_payload() if payload is None else payload,
            base_revision=base,
            occurred_at="2026-09-16T10:00:00Z", created_at="2026-09-16T10:00:00Z",
            depends_on=[]))

    def test_promotable_credit_reports_aliases_revisions_on_create(self):
        """Construction advances Person aliases revisions; the ACK must say so
        so reconcileCreatedWork can patch person-metadata-state."""
        from backend import person_metadata_sync
        person = self.db.add_person("Jane", "Doe")
        with self.db.connection() as conn:
            self.assertEqual(person_metadata_sync.get_revision(conn, person, "aliases"), 0)
        wid = self.work_id()
        status, result = self.send(wid, _payload(roles=[{
            "person_id": person, "role_type": "Author", "credit_name": "Mark Twain",
        }]))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["aliases_revisions"], {person: 1})
        self.assertEqual(self.db.get_person(person)["aliases"], "Mark Twain")

        # Comma-bearing credits stay on the role and must not appear here.
        other = self.db.add_person("Ed", "Smith")
        wid2 = self.work_id()
        status, result = self.send(wid2, _payload(
            source={"kind": "video", "url": "https://www.youtube.com/watch?v=oHg5SJYRHA0"},
            roles=[{
                "person_id": other, "role_type": "Author", "credit_name": "Smith, John",
            }]))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertNotIn("aliases_revisions", result)
        self.assertEqual(self.db.get_person(other)["aliases"] or "", "")

    def test_creates_video_work_and_files_uncategorized(self):
        wid = self.work_id()
        status, result = self.send(wid)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        work = self.db.get_work(wid)
        self.assertIsNotNone(work)
        self.assertEqual(work["source_kind"], "video")
        self.assertEqual(work["provider_id"], "dQw4w9WgXcQ")
        self.assertEqual(work["doc_type"], "online")
        folders = self.db.execute_query(
            "SELECT folder_id FROM folder_files WHERE work_id = ?", (wid,))
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0]["folder_id"], result["folder_id"])

    def test_replay_is_idempotent(self):
        wid = self.work_id()
        self.send(wid)
        status, result = self.send(wid)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_construction_carries_no_base_revision(self):
        status, result = self.send(self.work_id(), base=0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_pdf_source_is_refused(self):
        with self.assertRaises(ValueError):
            work_lifecycle_sync.canonical_create_payload(_payload(
                source={"kind": "pdf", "url": "https://example.com/a.pdf"}))

    def test_missing_folder_is_named_refusal(self):
        status, result = self.send(self.work_id(), _payload(folder_id="F-missing"))
        self.assertEqual((status, result["code"]), (404, "FOLDER_NOT_FOUND"))

    def test_roles_and_playlist_attach_at_construction(self):
        person = self.db.add_person(first_name="Ada", last_name="Lovelace")
        playlist = self.db.add_playlist("Watch later")
        wid = self.work_id()
        status, result = self.send(wid, _payload(
            playlist_id=playlist,
            roles=[{"person_id": person, "role_type": "Author", "credit_name": ""}],
        ))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["playlist_id"], playlist)
        self.assertEqual(result["role_count"], 1)
        items = self.db.execute_query(
            "SELECT work_id FROM playlist_items WHERE playlist_id = ?", (playlist,))
        self.assertEqual([r["work_id"] for r in items], [wid])
        roles = self.db.get_work_roles(wid)
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["id"], person)

    def test_short_id_is_refused(self):
        status, result = sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device,
            operation="CREATE_WORK", entity_type="work", entity_id="W-ABCD1234",
            payload=_payload(), base_revision=None,
            occurred_at="2026-09-16T10:00:00Z", created_at="2026-09-16T10:00:00Z",
            depends_on=[]))
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "INVALID_ENVELOPE")


if __name__ == "__main__":
    unittest.main()
