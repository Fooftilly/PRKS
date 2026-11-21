"""Saved remote images over HTTP on the PostgreSQL Library (#1351).

``GET /api/remote-images?url=`` serves a video thumbnail or a note figure
from the Library's saved copy: an editor's first view fetches and saves it,
later views never fetch; a reader's miss is served uncached; a URL the
Library does not name, one PRKS never fetches and a failed fetch are 404;
and a backup carries the copy through a restore. The domain rules are in
``tests/pg/test_objects_remote_images.py``.
"""

from __future__ import annotations

import shutil
import tempfile
from io import BytesIO
from unittest import mock
from urllib.parse import quote

from backend.domain.maintenance import backup, restore
from backend.person_image import PortraitImage, decode_and_transcode
from backend.storage.objects import LocalFilesystemStorage
from tests.pg.server_api_fixtures import ServerApiTestCase

THUMB = "https://i.ytimg.example/vi/abc/hqdefault.jpg"
FIGURE = "https://figures.example/graph.png"
_FETCH = "backend.api.remote_images.fetch_and_prepare"


def picture(color: tuple[int, int, int] = (120, 80, 200)) -> PortraitImage:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (64, 32), color).save(buf, format="PNG")
    encoded = decode_and_transcode(buf.getvalue())
    assert encoded is not None
    return PortraitImage(*encoded)


class RemoteImageRouteTests(ServerApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.picture = picture()
        with self.acting():
            self.work_id = self.facade.add_work(
                "A talk", source_kind="video", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                thumb_url=THUMB)
        self.sql("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'research', %s)",
                 (self.lib, self.work_id, f"![graph]({FIGURE})"))

    def image(self, url: str, cookies=None):
        return self.call("GET", "/api/remote-images?url=" + quote(url, safe=""),
                         cookies=self.cookies if cookies is None else cookies)

    def saved(self) -> set[str]:
        return {row["source_url"] for row in self.sql("SELECT source_url FROM prks.remote_images")}

    def test_the_first_view_saves_the_copy_and_later_views_never_fetch(self) -> None:
        fetch = mock.Mock(return_value=self.picture)
        with mock.patch(_FETCH, fetch):
            first = self.image(THUMB)
        self.assertEqual((first.status, first.raw), (200, self.picture.body))
        self.assertIn("image/webp", first.header("Content-Type")[0])
        self.assertEqual(first.header("Cache-Control"), ["private, no-cache"])
        etag = first.header("ETag")[0]
        self.assertEqual(fetch.call_args.args[0], THUMB)
        self.assertEqual(self.saved(), {THUMB})
        with mock.patch(_FETCH, side_effect=AssertionError("must use the saved copy")):
            again = self.image(THUMB)
            figure_missing = self.image(FIGURE + "?not=named")
            unchanged = self.call("GET", "/api/remote-images?url=" + quote(THUMB, safe=""),
                                  cookies=self.cookies, headers=[("If-None-Match", etag)])
        self.assertEqual((again.status, again.raw), (200, self.picture.body))
        self.assertEqual(again.header("ETag"), [etag])
        self.assertEqual((unchanged.status, unchanged.raw), (304, b""))
        self.assertEqual(figure_missing.status, 404)

    def test_a_figure_in_a_note_is_saved(self) -> None:
        with mock.patch(_FETCH, return_value=self.picture):
            self.assertEqual(self.image(FIGURE).status, 200)
        self.assertEqual(self.saved(), {FIGURE})

    def test_a_url_the_library_does_not_name_or_prks_never_fetches_is_404(self) -> None:
        with mock.patch(_FETCH, side_effect=AssertionError("must not fetch")):
            for url in ("https://elsewhere.example/x.png", "http://127.0.0.1/x.png", "javascript:alert(1)"):
                self.assertEqual(self.image(url).status, 404, url)
            self.assertEqual(self.get("/api/remote-images").status, 404)
            both = "/api/remote-images?url=" + quote(THUMB, safe="") + "&url=" + quote(FIGURE, safe="")
            self.assertEqual(self.get(both).status, 404)
        self.assertEqual(self.saved(), set())

    def test_a_failed_fetch_is_404_and_saves_nothing(self) -> None:
        with mock.patch(_FETCH, return_value=None):
            self.assertEqual(self.image(THUMB).status, 404)
        self.assertEqual(self.saved(), set())

    def test_a_readers_miss_is_served_and_never_saved(self) -> None:
        self.user("Reader", library=self.lib, role="reader")
        reader = self.session_cookie("Reader")
        with mock.patch(_FETCH, return_value=self.picture):
            served = self.image(THUMB, cookies=reader)
            elsewhere = self.image("https://elsewhere.example/x.png", cookies=reader)
        self.assertEqual((served.status, served.raw), (200, self.picture.body))
        self.assertEqual(elsewhere.status, 404)
        self.assertEqual(self.saved(), set(), "a reader never writes")

    def test_the_saved_copy_survives_a_backup_and_restore(self) -> None:
        with mock.patch(_FETCH, return_value=self.picture):
            self.assertEqual(self.image(THUMB).status, 200)
        out = tempfile.mkdtemp(prefix="prks-pg-remote-image-backup-")
        self.addCleanup(shutil.rmtree, out, True)
        objects = LocalFilesystemStorage.for_config(self.storage)
        archive = backup.write_backup(self.db.config.conninfo, objects, self.lib, out,
                                      prks_version="0.8.0-test").path
        self.sql("DELETE FROM prks.remote_images")
        restore.restore_library(self.db.config.conninfo, objects, archive, self.lib,
                                owner_user_id=self.owner, staging_dir=out)
        self.assertEqual(self.saved(), {THUMB})
        with mock.patch(_FETCH, side_effect=AssertionError("must use the restored copy")):
            served = self.image(THUMB)
        self.assertEqual((served.status, served.raw), (200, self.picture.body))
