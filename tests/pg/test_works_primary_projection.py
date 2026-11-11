"""A Work reads through its primary Manifestation on PostgreSQL (PG-C2 #641).

The Work projection shows the primary Manifestation's metadata and its
primary Asset, whichever Manifestation that is, and only the credits that
hold for the Work or for that Manifestation. These tests move the primary
to a second Manifestation (no command does that yet, so the rows are written
directly and the search row refreshed, as a future command would) and check
every reader: the Work, the browse families, a Folder, a Person, a Playlist,
the thumbnail fields, the browse order, the search filters and keyword
search. They replace the SQLite ``tests/test_work_projection.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Optional

from tests.pg.test_search_refresh import SearchTestCase

_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


class PrimaryProjectionTests(SearchTestCase):
    def manifestation(self, work_id: str, *, title: str = "Secondary Title",
                      publisher: Optional[str] = "Secondary Press",
                      abstract: str = "Secondary abstract", primary: bool = True) -> str:
        """A second Manifestation of ``work_id``, made primary unless ``primary`` is false."""
        mf = new_id("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id, kind, title, abstract, doc_type,"
                 " year, publisher) VALUES (%s, %s, %s, 'translation', %s, %s, 'book', '2026', %s)",
                 (self.actor.library_id, mf, work_id, title, abstract, publisher))
        if primary:
            self.make_primary(work_id, mf)
        return mf

    def make_primary(self, work_id: str, mf: str) -> None:
        self.sql("UPDATE prks.works SET primary_manifestation_id = %s WHERE id = %s", (mf, work_id))
        self.refresh(work_id)

    def stream(self, work_id: str, mf: str) -> str:
        asset = new_id("AS")
        self.sql("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, provider,"
                 " provider_id, url, media_type) VALUES (%s, %s, %s, %s, 'external_stream', 'youtube',"
                 " 'dQw4w9WgXcQ', %s, 'video/mp4')", (self.actor.library_id, asset, mf, work_id, _VIDEO))
        self.sql("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s", (asset, mf))
        return asset

    def managed_file(self, work_id: str, mf: str, name: str, thumb_page: int) -> str:
        data = name.encode()
        self.store_pdf(name, data)
        asset = new_id("AS")
        self.sql("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator,"
                 " content_sha256, media_type, origin, thumb_page) VALUES (%s, %s, %s, %s, 'managed_file', %s,"
                 " %s, 'application/pdf', 'adopted', %s)",
                 (self.actor.library_id, asset, mf, work_id, name, hashlib.sha256(data).hexdigest(), thumb_page))
        self.sql("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s", (asset, mf))
        return asset

    def credit(self, work_id: str, person_id: str, role_type: str, mf: str, order_index: int) -> None:
        self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index,"
                 " manifestation_id) VALUES (%s, %s, %s, %s, %s, %s)",
                 (self.actor.library_id, work_id, person_id, role_type, order_index, mf))
        self.refresh(work_id)

    def origin(self, work_id: str) -> str:
        return str(self.library.get_work(work_id)["primary_manifestation_id"])

    @staticmethod
    def identity(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in
                ("primary_manifestation_id", "primary_asset_id", "manifestation_count", "asset_count")}

    def browse_row(self, work_id: str) -> dict[str, Any]:
        return next(row for row in self.library.get_works_browse_catalog() if row["id"] == work_id)

    def ids(self, rows: list[dict[str, Any]]) -> list[str]:
        return [row["id"] for row in rows]

    def test_the_projection_follows_the_primary_manifestation_and_its_asset(self) -> None:
        work_id = self.library.add_work("Canonical Work", abstract="Canonical abstract", publisher="Old Press",
                                        file_path=self.store_pdf("original.pdf", b"original"))
        origin = self.origin(work_id)
        self.assertNotIn("citation_manifestation_id", self.library.get_work(work_id))
        mf = self.manifestation(work_id, title="Translated Version", publisher="New Press",
                                abstract="Translated abstract", primary=False)
        asset = self.stream(work_id, mf)
        self.assertEqual(self.library.get_work(work_id)["title"], "Canonical Work")  # not primary yet
        self.make_primary(work_id, mf)

        work = self.library.get_work(work_id)
        self.assertEqual({key: work[key] for key in ("title", "abstract", "publisher", "year", "source_url",
                                                     "source_kind", "provider", "provider_id", "file_path",
                                                     "source_mime")},
                         {"title": "Translated Version", "abstract": "Translated abstract",
                          "publisher": "New Press", "year": "2026", "source_url": _VIDEO,
                          "source_kind": "video", "provider": "youtube", "provider_id": "dQw4w9WgXcQ",
                          "file_path": "", "source_mime": "video/mp4"})  # a stream has no file
        self.assertEqual(self.identity(work), {"primary_manifestation_id": mf, "primary_asset_id": asset,
                                               "manifestation_count": 2, "asset_count": 2})
        self.assertNotEqual(origin, mf)
        self.assertNotIn("citation_manifestation_id", work)

    def test_every_reader_family_carries_the_same_identity(self) -> None:
        work_id = self.library.add_work("Reader Family Work", file_path=self.store_pdf("family.pdf", b"family"))
        self.stream(work_id, self.manifestation(work_id))
        folder_id = self.library.add_folder("Projection Folder")
        self.library.add_work_to_folder(folder_id, work_id)
        person_id = self.library.add_person("Ada", "Lovelace")
        self.library.add_role(person_id, work_id, "Author")
        playlist_id = self.library.add_playlist("Projection Playlist")
        self.library.add_work_to_playlist(playlist_id, work_id)
        self.library.mark_work_opened(work_id)

        expected = self.identity(self.library.get_work(work_id))
        self.assertEqual((expected["manifestation_count"], expected["asset_count"]), (2, 2))
        folder, person, playlist = (self.library.get_folder(folder_id), self.library.get_person(person_id),
                                    self.library.get_playlist(playlist_id))
        assert folder is not None and person is not None and playlist is not None
        families = {
            "all": self.library.get_all_works(),
            "browse": self.library.get_works_browse_catalog(),
            "recent": self.library.get_recent_browse(),
            "added": self.library.get_recently_added_browse(),
            "folder": folder["works"],
            "person": person["works"],
            "playlist": playlist["items"],
        }
        for family, rows in families.items():
            with self.subTest(family=family):
                row = next(item for item in rows if item["id"] == work_id)
                self.assertEqual(self.identity(row), expected)
                self.assertEqual(row["title"], "Secondary Title")
                self.assertNotIn("citation_manifestation_id", row)

    def test_the_thumbnail_fields_follow_the_primary_asset(self) -> None:
        work_id = self.library.add_work("Thumb Work", file_path=self.store_pdf("original.pdf", b"original"),
                                        thumb_page=1)
        mf = self.manifestation(work_id, primary=False)
        asset = self.managed_file(work_id, mf, "secondary.pdf", 7)
        self.make_primary(work_id, mf)
        fields = self.library.get_primary_thumbnail_fields(work_id)
        assert fields is not None
        self.assertEqual((fields["file_path"], fields["thumb_page"], fields["primary_asset_id"]),
                         ("/api/pdfs/secondary.pdf", 7, asset))
        work = self.library.get_work(work_id)
        self.assertEqual((work["file_path"], work["thumb_page"]), ("/api/pdfs/secondary.pdf", 7))

    def test_the_browse_catalog_orders_by_the_displayed_title(self) -> None:
        early = self.library.add_work("AAA Legacy")
        late = self.library.add_work("ZZZ Legacy")
        self.manifestation(early, title="ZZZ Displayed")
        self.manifestation(late, title="AAA Displayed")
        rows = [row for row in self.library.get_works_browse_catalog() if row["id"] in (early, late)]
        self.assertEqual([(row["id"], row["title"]) for row in rows],
                         [(late, "AAA Displayed"), (early, "ZZZ Displayed")])

    def test_credits_of_a_manifestation_that_is_no_longer_primary_reach_no_reader(self) -> None:
        work_id = self.library.add_work("Credit Work")
        ada = self.library.add_person("Ada", "Lovelace")
        charles = self.library.add_person("Charles", "Babbage")
        self.library.add_role(ada, work_id, "Author")  # Work scope: holds for every Manifestation
        origin = self.origin(work_id)
        mf = self.manifestation(work_id, title="Secondary Edition")
        self.credit(work_id, charles, "Editor", origin, 1)
        self.credit(work_id, charles, "Author", origin, 2)

        def credits() -> set[tuple[str, str]]:
            return {(role["role_type"], role["last_name"]) for role in self.library.get_work(work_id)["roles"]}

        self.assertEqual(credits(), {("Author", "Lovelace")})
        row = self.browse_row(work_id)
        self.assertEqual((row["primary_author"], row.get("primary_editor")), ("Ada Lovelace", None))
        self.assertEqual([person["display_name"] for person in row["linked_people"]], ["Ada Lovelace"])
        self.assertNotIn("Babbage", row.get("linked_authors") or "")
        charles_page, ada_page = self.library.get_person(charles), self.library.get_person(ada)
        assert charles_page is not None and ada_page is not None
        self.assertNotIn(work_id, self.ids(charles_page["works"]))
        self.assertIn(work_id, self.ids(ada_page["works"]))

        self.assertNotIn(work_id, self.library.work_ids_matching_author("Babbage"))
        self.assertNotIn(work_id, self.ids(self.library.search_works("", author_filter="Babbage")))
        self.assertNotIn(work_id, self.ids(self.library.search_works("Babbage")))
        self.assertIn(work_id, self.library.work_ids_matching_author("Lovelace"))
        self.assertIn(work_id, self.ids(self.library.search_works("", author_filter="Lovelace")))
        self.assertIn(work_id, self.ids(self.library.search_works("Lovelace")))

        # A credit on the primary Manifestation itself is shown everywhere.
        self.credit(work_id, charles, "Translator", mf, 3)
        self.assertEqual(credits(), {("Author", "Lovelace"), ("Translator", "Babbage")})
        self.assertEqual([person["role_type"] for person in self.browse_row(work_id)["linked_people"]],
                         ["Author", "Translator"])
        charles_page = self.library.get_person(charles)
        assert charles_page is not None
        self.assertEqual([(w["id"], w["role_type"]) for w in charles_page["works"]], [(work_id, "Translator")])

    def test_a_primary_without_a_publisher_hides_the_one_before_it(self) -> None:
        work_id = self.library.add_work("Legacy Pub Work", publisher="Legacy Press")
        self.assertIn(work_id, self.library.work_ids_matching_publisher("Legacy Press"))
        self.manifestation(work_id, title="Null Publisher Edition", publisher=None)
        self.assertIsNone(self.library.get_work(work_id)["publisher"])
        self.assertNotIn(work_id, self.library.work_ids_matching_publisher("Legacy Press"))
        self.assertNotIn(work_id, self.ids(self.library.search_works("", publisher_filter="Legacy Press")))

    def test_search_finds_the_displayed_metadata_and_not_the_hidden(self) -> None:
        work_id = self.library.add_work("Legacy Hidden Title", abstract="Legacy hidden abstract",
                                        publisher="Legacy Press")
        self.manifestation(work_id, title="Displayed Search Title", publisher="Displayed Press",
                           abstract="Displayed search abstract")
        for query in ("Displayed Search Title", "Displayed", "displayed search abstract"):
            with self.subTest(query=query):
                self.assertIn(work_id, self.ids(self.library.search_works(query)))
        self.assertNotIn(work_id, self.ids(self.library.search_works("Hidden")))
        self.assertIn(work_id, self.ids(self.library.search_works("", publisher_filter="Displayed Press")))
        row = next(row for row in self.library.search_works("Displayed Search Title") if row["id"] == work_id)
        self.assertEqual((row["title"], row["publisher"]), ("Displayed Search Title", "Displayed Press"))
