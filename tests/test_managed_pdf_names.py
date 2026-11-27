"""Managed PDF object names and the Asset content URL (#1427).

Pure rules with no database: a minted name is a valid, bounded storage key,
and a Work's bytes are addressed by its Asset id, never by that name.
"""

import unittest

from backend.domain.objects.content_url import asset_content_url, asset_id_from_url, is_current_version, parse_content_url
from backend.pdf.managed_pdf_names import bound_managed_pdf_basename, mint_managed_pdf_filename
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageKey

_ASSET = "AS-" + "0123456789ABCDEF" * 2
_SHA = "ab" * 32


class MintedNameTests(unittest.TestCase):
    def test_a_minted_name_is_a_storage_key(self):
        for original in ("paper.pdf", "../../etc/passwd", "C:evil.pdf", "", "a" * 400 + ".pdf", "é" * 300):
            with self.subTest(original=original[:20]):
                name = mint_managed_pdf_filename(original)
                self.assertEqual(StorageKey(NAMESPACE_ASSET_OBJECTS, name).name, name)
                self.assertTrue(name.endswith(".pdf"))
                self.assertLessEqual(len(name.encode("utf-8")), 255)

    def test_two_mints_of_one_name_differ(self):
        first = mint_managed_pdf_filename("same.pdf")
        second = mint_managed_pdf_filename("same.pdf")
        self.assertNotEqual(first, second)

    def test_a_re_prefixed_name_stays_bounded(self):
        name = mint_managed_pdf_filename("x" * 250)
        self.assertLessEqual(len(bound_managed_pdf_basename("1700000000_W-1_", name).encode("utf-8")), 255)


class ContentUrlTests(unittest.TestCase):
    def test_only_the_current_version_is_current(self):
        self.assertTrue(is_current_version(None, _SHA))
        self.assertTrue(is_current_version(None, None))
        self.assertTrue(is_current_version(_SHA[:16], _SHA))
        for version in (_SHA[:15], _SHA[:17], _SHA, "", "0" * 16):
            with self.subTest(version=version):
                self.assertFalse(is_current_version(version, _SHA))
        self.assertFalse(is_current_version(_SHA[:16], None))

    def test_the_url_names_the_asset_and_versions_the_bytes(self):
        url = asset_content_url(_ASSET, _SHA)
        self.assertEqual(url, f"/api/assets/{_ASSET}/content?v={_SHA[:16]}")
        self.assertEqual(asset_id_from_url(url), _ASSET)
        self.assertEqual(asset_id_from_url(asset_content_url(_ASSET, None)), _ASSET)
        self.assertEqual(parse_content_url(url), (_ASSET, _SHA[:16]))
        self.assertEqual(parse_content_url(asset_content_url(_ASSET, None)), (_ASSET, None))

    def test_other_spellings_name_no_asset(self):
        for url in ("", None, "/api/pdfs/paper.pdf", f"/api/assets/{_ASSET}", f"/api/assets/{_ASSET}/content/",
                    f"/api/assets/{_ASSET}/content?v=XYZ", f"/api/assets/{_ASSET}/content?w=ab",
                    "/api/assets/W-1/content", f"/api/assets/{_ASSET}%2F..%2Fx/content",
                    f"https://example.org/api/assets/{_ASSET}/content"):
            with self.subTest(url=url):
                self.assertIsNone(asset_id_from_url(url))


if __name__ == "__main__":
    unittest.main()
