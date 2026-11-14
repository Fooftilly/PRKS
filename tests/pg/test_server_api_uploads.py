"""PDF uploads and YouTube oEmbed lookups over HTTP on the PostgreSQL Library (PG-C2 #641).

An upload is stored under a fresh managed name inside the PDF store whatever
``file_name`` it carries: never outside it, never shared with another
upload, sanitized without being mangled and within a filesystem's name
limit. A linearization that fails never fails the upload. The oEmbed lookup
is best effort for malformed input and sends only the canonical, validated
YouTube URL. Bytes a refused or failed create stored are left to the orphan
sweep on PostgreSQL (``tests/pg/test_objects_ingest.py``).
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest import mock
from urllib.parse import unquote

from tests.pg.server_api_fixtures import ServerApiTestCase

PDF = b"%PDF-1.4\n%%EOF\n"


class UploadTests(ServerApiTestCase):
    def upload(self, title: str, file_name: str, body: bytes = PDF) -> str:
        created = self.create_work(title, file_name=file_name, file_b64=base64.b64encode(body).decode("ascii"))
        path = self.ok(self.get(f"/api/works/{created['id']}"))["file_path"]
        self.assertTrue(path.startswith("/api/pdfs/"), path)
        return path[len("/api/pdfs/"):]

    def stored(self, name: str) -> bytes:
        with open(os.path.join(os.path.realpath(self.storage.pdfs_dir), name), "rb") as handle:
            return handle.read()

    def test_an_upload_is_stored_only_inside_the_pdf_store(self) -> None:
        parent = os.path.dirname(os.path.realpath(self.storage.pdfs_dir))
        before = set(os.listdir(parent))
        name = self.upload("Traversal Upload", "../../escaped.pdf")
        self.assertNotIn("/", name)
        self.assertEqual(self.stored(name), PDF)
        self.assertEqual(set(os.listdir(parent)) - before, set())

    def test_two_uploads_of_one_name_never_share_a_managed_pdf(self) -> None:
        first = self.upload("Collide A", "paper.pdf", b"%PDF-1.4\n%% FIRST-WORK-CONTENT\n%%EOF\n")
        second = self.upload("Collide B", "paper.pdf", b"%PDF-1.4\n%% SECOND-WORK-CONTENT\n%%EOF\n")
        self.assertNotEqual(first, second, "each Work owns its own managed PDF")
        self.assertIn(b"FIRST-WORK-CONTENT", self.stored(first))
        self.assertIn(b"SECOND-WORK-CONTENT", self.stored(second))

    def test_an_upload_wins_over_a_path_sent_beside_it(self) -> None:
        old = self.upload("Old Upload", "old.pdf", b"%PDF-1.4\n%% OLD-CONTENT\n%%EOF\n")
        created = self.create_work("New Upload", file_path=f"/api/pdfs/{old}", file_name="new.pdf",
                                   file_b64=base64.b64encode(b"%PDF-1.4\n%% NEW-CONTENT\n%%EOF\n").decode("ascii"))
        path = self.ok(self.get(f"/api/works/{created['id']}"))["file_path"]
        name = path[len("/api/pdfs/"):]
        self.assertNotEqual(name, old)
        self.assertIn(b"NEW-CONTENT", self.stored(name))
        self.assertIn(b"OLD-CONTENT", self.stored(old))

    def test_a_failed_linearization_never_fails_the_upload(self) -> None:
        from backend.domain.objects import ingest

        with mock.patch.object(ingest, "linearize_pdf_to", side_effect=OSError(28, "No space left on device")) as fail:
            name = self.upload("Linearize Fails", "paper.pdf")
        fail.assert_called_once()
        self.assertEqual(self.stored(name), PDF)

    def test_upload_names_are_sanitized_without_being_mangled(self) -> None:
        for raw, suffix in (("../../../../etc/passwd", "passwd.pdf"), ("***", "file.pdf"), ("..", "file.pdf"),
                            ("report.PDF", "report.pdf"), ("a" * 300 + ".pdf", ".pdf")):
            with self.subTest(raw=raw[:20]):
                name = self.upload("Upload " + raw[:12], raw)
                self.assertNotIn("/", name)
                self.assertTrue(name.endswith(suffix), f"{raw!r} -> {name}")
                self.assertLessEqual(len(name.encode("utf-8")), 255)
                self.assertEqual(self.stored(name), PDF)


class OembedTests(ServerApiTestCase):
    def test_a_malformed_source_url_keeps_the_lookup_best_effort(self) -> None:
        lone_surrogate = json.loads('"https://www.youtube.com/watch?v=\\ud800"')
        self.assertIsNone(self.server_module._fetch_youtube_oembed(lone_surrogate))
        res = self.send("POST", "/api/works", {"title": "Malformed Source URL", "source_url": lone_surrogate})
        self.assertIn(res.status, (200, 400), res.raw)
        if res.status == 200:
            self.ok(self.get(f"/api/works/{res.body['id']}"))

    def test_only_the_canonical_validated_url_is_looked_up(self) -> None:
        seen: list[str] = []

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"title": "T"}'

        def fake_urlopen(req: Any, timeout: Any = None) -> FakeResponse:
            seen.append(req.full_url)
            return FakeResponse()

        with mock.patch.object(self.server_module, "urlopen", fake_urlopen):
            meta = self.server_module._fetch_youtube_oembed("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1&t=30")
            rejected = self.server_module._fetch_youtube_oembed("https://example.invalid/watch?v=dQw4w9WgXcQ")
        self.assertEqual((meta, rejected), ({"title": "T"}, None))
        self.assertEqual(len(seen), 1)
        prefix = "https://www.youtube.com/oembed?format=json&url="
        self.assertTrue(seen[0].startswith(prefix), seen[0])
        value = seen[0][len(prefix):]
        self.assertNotIn("&", value)
        self.assertNotIn("#", value)
        self.assertEqual(unquote(value), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
