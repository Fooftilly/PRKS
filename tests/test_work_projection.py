"""#60 Slice C: compatibility Work projection over primary Manifestation/Asset."""
import unittest

from tests.test_db_migrations import MigrationTestCase


YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class WorkProjectionTests(MigrationTestCase):
    def setUp(self):
        super().setUp()
        self.db = self._open()

    @staticmethod
    def _identity(row):
        return {
            key: row.get(key)
            for key in (
                "primary_manifestation_id",
                "primary_asset_id",
                "manifestation_count",
                "asset_count",
            )
        }

    def test_origin_projection_preserves_every_legacy_work_column(self):
        work_id = self.db.add_work(
            title="Projection Paper",
            abstract="Legacy abstract",
            year="2024",
            publisher="Projection Press",
            file_path="/api/pdfs/projection.pdf",
            source_kind="pdf",
            source_mime="application/x-projection-test",
            thumb_page=3,
        )
        raw = self.db.execute_query(
            "SELECT * FROM works WHERE id = ?", (work_id,)
        )[0]
        projected = self.db.get_work(work_id)

        for key, value in raw.items():
            if key in ("primary_manifestation_id", "citation_manifestation_id"):
                continue
            with self.subTest(key=key):
                self.assertEqual(projected.get(key), value)

        self.assertEqual(
            projected["primary_manifestation_id"],
            raw["primary_manifestation_id"],
        )
        self.assertNotIn("citation_manifestation_id", projected)
        self.assertIsNotNone(projected["primary_asset_id"])
        self.assertEqual(projected["manifestation_count"], 1)
        self.assertEqual(projected["asset_count"], 1)

    def test_reader_families_use_the_same_identity_projection(self):
        work_id = self.db.add_work(
            title="Reader Family Work",
            file_path="/api/pdfs/family.pdf",
            source_kind="pdf",
        )
        folder_id = self.db.add_folder("Projection Folder")
        self.db.add_work_to_folder(folder_id, work_id)
        person_id = self.db.add_person("Ada", "Lovelace")
        self.db.add_role(person_id, work_id, "Author")
        playlist_id = self.db.add_playlist("Projection Playlist")
        self.db.add_work_to_playlist(playlist_id, work_id)
        self.db.mark_work_opened(work_id)

        expected = self._identity(self.db.get_work(work_id))
        families = [
            self.db.get_all_works(),
            self.db.get_works_browse_catalog(),
            self.db.get_recent_browse(),
            self.db.get_recently_added_browse(),
            self.db.get_folder(folder_id)["works"],
            self.db.get_person(person_id)["works"],
            self.db.get_playlist(playlist_id)["items"],
        ]
        for rows in families:
            row = next(item for item in rows if item["id"] == work_id)
            self.assertEqual(self._identity(row), expected)
            self.assertNotIn("citation_manifestation_id", row)

    def test_projection_follows_non_origin_primary_version_and_file(self):
        work_id = self.db.add_work(
            title="Canonical Work",
            abstract="Canonical abstract",
            publisher="Old Press",
            file_path="/api/pdfs/original.pdf",
            source_kind="pdf",
        )
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO manifestations (
                    id, work_id, kind, title, abstract, doc_type, year,
                    publisher, url
                ) VALUES (
                    'MF-SECOND', ?, 'translation', 'Translated Version',
                    'Translated abstract', 'online', '2026', 'New Press',
                    'https://example.org/citation'
                )
                """,
                (work_id,),
            )
            conn.execute(
                """
                INSERT INTO assets (
                    id, manifestation_id, work_id, kind, role, provider,
                    provider_id, url, media_type, origin
                ) VALUES (
                    'AS-SECOND', 'MF-SECOND', ?, 'external_stream', 'document',
                    'youtube', 'dQw4w9WgXcQ', ?, 'video/mp4', 'adopted'
                )
                """,
                (work_id, YT),
            )
            conn.execute(
                "UPDATE manifestations SET primary_asset_id = 'AS-SECOND' "
                "WHERE id = 'MF-SECOND'"
            )
            conn.execute(
                "UPDATE works SET primary_manifestation_id = 'MF-SECOND' "
                "WHERE id = ?",
                (work_id,),
            )

        work = self.db.get_work(work_id)
        self.assertEqual(work["title"], "Translated Version")
        self.assertEqual(work["abstract"], "Translated abstract")
        self.assertEqual(work["publisher"], "New Press")
        self.assertEqual(work["year"], "2026")
        self.assertEqual(work["source_url"], YT)
        self.assertEqual(work["source_kind"], "video")
        self.assertEqual(work["provider"], "youtube")
        self.assertEqual(work["provider_id"], "dQw4w9WgXcQ")
        self.assertIsNone(work["file_path"])
        self.assertEqual(work["source_mime"], "video/mp4")
        self.assertEqual(work["primary_manifestation_id"], "MF-SECOND")
        self.assertEqual(work["primary_asset_id"], "AS-SECOND")
        self.assertEqual(work["manifestation_count"], 2)
        self.assertEqual(work["asset_count"], 2)

    def test_bibtex_is_byte_identical_for_origin_manifestation(self):
        work_id = self.db.add_work(title="Projection Paper", year="2024")
        person_id = self.db.add_person("Ada", "Lovelace")
        self.db.add_role(person_id, work_id, "Author")
        self.assertEqual(
            self.db.generate_bibtex(work_id),
            "@article{Lovelace2024,\n"
            "  title = {Projection Paper},\n"
            "  author = {Lovelace, Ada},\n"
            "  year = {2024},\n"
            "}",
        )

    def test_bibtex_uses_explicit_citation_manifestation(self):
        work_id = self.db.add_work(title="Primary Title", year="2024")
        person_id = self.db.add_person("Ada", "Lovelace")
        self.db.add_role(person_id, work_id, "Author")
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO manifestations (
                    id, work_id, kind, title, doc_type, year, publisher
                ) VALUES (
                    'MF-CITED', ?, 'published', 'Cited Edition',
                    'book', '2026', 'Citation Press'
                )
                """,
                (work_id,),
            )
            conn.execute(
                "UPDATE works SET citation_manifestation_id = 'MF-CITED' "
                "WHERE id = ?",
                (work_id,),
            )

        self.assertEqual(self.db.get_work(work_id)["title"], "Primary Title")
        bibtex = self.db.generate_bibtex(work_id)
        self.assertTrue(bibtex.startswith("@book{Lovelace2026,\n"))
        self.assertIn("  title = {Cited Edition},\n", bibtex)
        self.assertIn("  publisher = {Citation Press},\n", bibtex)


if __name__ == "__main__":
    unittest.main()
