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

    def _add_second_manifestation(
        self,
        work_id,
        *,
        mf_id="MF-SECOND",
        title="Secondary Title",
        publisher="Secondary Press",
        asset_id=None,
        file_path=None,
        thumb_page=None,
        make_primary=True,
    ):
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO manifestations (
                    id, work_id, kind, title, abstract, doc_type, year, publisher
                ) VALUES (?, ?, 'translation', ?, 'Secondary abstract', 'book', '2026', ?)
                """,
                (mf_id, work_id, title, publisher),
            )
            if asset_id and file_path:
                locator = file_path.rsplit("/", 1)[-1]
                conn.execute(
                    """
                    INSERT INTO assets (
                        id, manifestation_id, work_id, kind, role, storage_locator,
                        media_type, origin, thumb_page
                    ) VALUES (?, ?, ?, 'managed_file', 'document', ?,
                              'application/pdf', 'adopted', ?)
                    """,
                    (asset_id, mf_id, work_id, locator, thumb_page),
                )
                conn.execute(
                    "UPDATE manifestations SET primary_asset_id = ? WHERE id = ?",
                    (asset_id, mf_id),
                )
            if make_primary:
                conn.execute(
                    "UPDATE works SET primary_manifestation_id = ? WHERE id = ?",
                    (mf_id, work_id),
                )

    def test_projected_credits_exclude_other_manifestation_roles(self):
        work_id = self.db.add_work(title="Credit Work")
        primary_person = self.db.add_person("Ada", "Lovelace")
        other_person = self.db.add_person("Charles", "Babbage")
        self.db.add_role(primary_person, work_id, "Author")
        self._add_second_manifestation(work_id, title="Secondary Edition")
        # Origin MF is no longer primary; scope Editor to the abandoned origin.
        with self.db.connection() as conn:
            origin_mf = conn.execute(
                "SELECT id FROM manifestations WHERE work_id = ? AND id != 'MF-SECOND'",
                (work_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index, manifestation_id) "
                "VALUES (?, ?, 'Editor', 1, ?)",
                (other_person, work_id, origin_mf),
            )
            # Translator on the selected primary must remain visible.
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index, manifestation_id) "
                "VALUES (?, ?, 'Translator', 2, 'MF-SECOND')",
                (other_person, work_id),
            )

        work = self.db.get_work(work_id)
        role_types = {(r["role_type"], r.get("last_name")) for r in work["roles"]}
        self.assertIn(("Author", "Lovelace"), role_types)
        self.assertIn(("Translator", "Babbage"), role_types)
        self.assertNotIn(("Editor", "Babbage"), role_types)

        catalog = self.db.get_works_browse_catalog()
        row = next(item for item in catalog if item["id"] == work_id)
        self.assertEqual(row["primary_author"], "Ada Lovelace")
        self.assertIsNone(row.get("primary_editor"))
        people = row["linked_people"]
        self.assertTrue(any(p["role_type"] == "Author" for p in people))
        self.assertTrue(any(p["role_type"] == "Translator" for p in people))
        self.assertFalse(any(p["role_type"] == "Editor" for p in people))

    def test_bibtex_credits_follow_citation_manifestation(self):
        work_id = self.db.add_work(title="Primary Title", year="2020")
        primary_author = self.db.add_person("Ada", "Lovelace")
        cited_author = self.db.add_person("Charles", "Babbage")
        self.db.add_role(primary_author, work_id, "Author")
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
                "INSERT INTO roles (person_id, work_id, role_type, order_index, manifestation_id) "
                "VALUES (?, ?, 'Editor', 1, 'MF-CITED')",
                (cited_author, work_id),
            )
            conn.execute(
                "UPDATE works SET citation_manifestation_id = 'MF-CITED' WHERE id = ?",
                (work_id,),
            )

        bibtex = self.db.generate_bibtex(work_id)
        self.assertIn("  author = {Lovelace, Ada},\n", bibtex)
        self.assertIn("  editor = {Babbage, Charles},\n", bibtex)
        self.assertIn("  title = {Cited Edition},\n", bibtex)
        # Primary-only Translator on a different Manifestation must not appear.
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO manifestations (id, work_id, kind, title) "
                "VALUES ('MF-OTHER', ?, 'translation', 'Other')",
                (work_id,),
            )
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index, manifestation_id) "
                "VALUES (?, ?, 'Translator', 2, 'MF-OTHER')",
                (cited_author, work_id),
            )
        bibtex2 = self.db.generate_bibtex(work_id)
        self.assertNotIn("translator", bibtex2.lower())

    def test_primary_thumbnail_fields_follow_primary_asset(self):
        work_id = self.db.add_work(
            title="Thumb Work",
            file_path="/api/pdfs/original.pdf",
            source_kind="pdf",
            thumb_page=1,
        )
        self._add_second_manifestation(
            work_id,
            asset_id="AS-SECOND",
            file_path="/api/pdfs/secondary.pdf",
            thumb_page=7,
        )
        fields = self.db.get_primary_thumbnail_fields(work_id)
        self.assertEqual(fields["file_path"], "/api/pdfs/secondary.pdf")
        self.assertEqual(fields["thumb_page"], 7)
        self.assertEqual(fields["primary_asset_id"], "AS-SECOND")
        work = self.db.get_work(work_id)
        self.assertEqual(work["file_path"], "/api/pdfs/secondary.pdf")
        self.assertEqual(work["thumb_page"], 7)

    def test_get_person_excludes_non_primary_manifestation_roles(self):
        work_id = self.db.add_work(title="Person Scope Work")
        primary_person = self.db.add_person("Ada", "Lovelace")
        other_person = self.db.add_person("Charles", "Babbage")
        self.db.add_role(primary_person, work_id, "Author")
        self._add_second_manifestation(work_id, title="Secondary Edition")
        with self.db.connection() as conn:
            origin_mf = conn.execute(
                "SELECT id FROM manifestations WHERE work_id = ? AND id != 'MF-SECOND'",
                (work_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index, manifestation_id) "
                "VALUES (?, ?, 'Editor', 1, ?)",
                (other_person, work_id, origin_mf),
            )

        # Primary Author remains visible on the Person page.
        ada = self.db.get_person(primary_person)
        self.assertTrue(any(w["id"] == work_id for w in ada["works"]))
        # Person only credited on the abandoned (non-primary) Manifestation
        # must not appear on get_person works — matches credits_scope_sql /
        # primary_author / get_work roles.
        charles = self.db.get_person(other_person)
        self.assertFalse(any(w["id"] == work_id for w in charles["works"]))
        work = self.db.get_work(work_id)
        self.assertFalse(
            any(
                r.get("last_name") == "Babbage" and r.get("role_type") == "Editor"
                for r in work["roles"]
            )
        )

    def test_search_uses_displayed_primary_metadata(self):
        work_id = self.db.add_work(
            title="Legacy Hidden Title",
            abstract="Legacy hidden abstract",
            publisher="Legacy Press",
        )
        self._add_second_manifestation(
            work_id,
            title="Displayed Search Title",
            publisher="Displayed Press",
        )
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE manifestations SET abstract = 'Displayed search abstract' "
                "WHERE id = 'MF-SECOND'"
            )

        from backend.db_manager import _prks_search_tokens

        tokens = _prks_search_tokens("Displayed Search Title")
        # FTS index still carries the legacy Work title; the FTS selection path
        # must still surface the Work via primary-Manifestation matching.
        fts_hits = self.db._search_works_fts_tokens(tokens)
        self.assertTrue(any(r["id"] == work_id for r in fts_hits))
        like_hits = self.db._search_works_like_tokens(tokens)
        self.assertTrue(any(r["id"] == work_id for r in like_hits))

        by_title = self.db.search_works("Displayed Search Title")
        self.assertTrue(any(r["id"] == work_id for r in by_title))
        # Primary title must be discoverable even when FTS only has legacy spelling.
        self.assertTrue(
            any(r["id"] == work_id for r in self.db.search_works("Displayed"))
        )
        by_pub = self.db.search_works("", publisher_filter="Displayed Press")
        self.assertTrue(any(r["id"] == work_id for r in by_pub))
        row = next(r for r in by_title if r["id"] == work_id)
        self.assertEqual(row["title"], "Displayed Search Title")
        self.assertEqual(row["publisher"], "Displayed Press")

    def test_browse_orders_by_effective_displayed_title(self):
        early = self.db.add_work(title="AAA Legacy")
        late = self.db.add_work(title="ZZZ Legacy")
        # Flip displayed order vs legacy: early Work shows "ZZZ Displayed",
        # late Work shows "AAA Displayed".
        self._add_second_manifestation(
            early, mf_id="MF-EARLY", title="ZZZ Displayed", make_primary=True
        )
        self._add_second_manifestation(
            late, mf_id="MF-LATE", title="AAA Displayed", make_primary=True
        )
        catalog = self.db.get_works_browse_catalog()
        ids = [row["id"] for row in catalog if row["id"] in (early, late)]
        self.assertEqual(ids, [late, early])
        titles = [row["title"] for row in catalog if row["id"] in (early, late)]
        self.assertEqual(titles, ["AAA Displayed", "ZZZ Displayed"])


if __name__ == "__main__":
    unittest.main()
