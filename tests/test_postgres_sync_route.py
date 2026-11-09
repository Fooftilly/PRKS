"""The PostgreSQL sync route's answer around the committed operation (PG-C1 #640)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from backend import server, work_note_sync


class _Handler:
    """Just enough of ``PRKSHandler`` for ``_handle_postgres_sync_operation``."""

    _prks_request_context = object()
    _prks_request_id = "req-1"

    def __init__(self) -> None:
        self.sent: list[tuple[int, dict]] = []

    def send_json(self, status: int, body: dict) -> None:
        self.sent.append((status, body))


class PostgresSyncRouteTests(unittest.TestCase):
    def answer(self, operation: str, refs) -> tuple[int, dict]:
        handler = _Handler()
        acked = (200, {"code": "ACKNOWLEDGED", "work_id": "W-1", "changed": True, "file_path": "x"})
        with mock.patch("backend.sync.protocol.process_operation", return_value=acked), \
                mock.patch.object(server, "db", SimpleNamespace(database=object())), \
                mock.patch.object(server, "research_index", SimpleNamespace(work_research_refs=refs)):
            server.PRKSHandler._handle_postgres_sync_operation(
                handler, {"operation": operation, "entity_id": "W-1"})  # type: ignore[arg-type]
        [sent] = handler.sent
        return sent

    def test_a_note_ack_carries_its_refs(self) -> None:
        refs = {"concepts": [], "arguments": [], "pending": True}
        status, body = self.answer(work_note_sync.RESEARCH_OPERATION, mock.Mock(return_value=refs))
        self.assertEqual((status, body["research_refs"]), (200, refs))

    def test_a_failed_refs_read_never_fails_a_committed_note(self) -> None:
        status, body = self.answer(work_note_sync.RESEARCH_OPERATION, mock.Mock(side_effect=RuntimeError("down")))
        self.assertEqual((status, body["code"]), (200, "ACKNOWLEDGED"))
        self.assertNotIn("research_refs", body)

    def test_a_delete_ack_carries_no_path(self) -> None:
        status, body = self.answer("DELETE_WORK", mock.Mock())
        self.assertEqual((status, body), (200, {"code": "ACKNOWLEDGED", "work_id": "W-1", "changed": True}))


if __name__ == "__main__":
    unittest.main()
