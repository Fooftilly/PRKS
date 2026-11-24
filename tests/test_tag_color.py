"""Tag color validation independent of database backend."""
import unittest

from backend.domain.organization.tag_color import DEFAULT_COLOR, normalized_tag_color


class TagColorTests(unittest.TestCase):
    def test_hex_colors_and_default(self):
        for safe in ("#123", "#abcd", "#AAbbCC", "#001122ff"):
            self.assertEqual(normalized_tag_color(safe), safe)
        self.assertEqual(normalized_tag_color(None, blank_defaults=True), DEFAULT_COLOR)
        self.assertEqual(normalized_tag_color(" ", blank_defaults=True), DEFAULT_COLOR)

    def test_blank_defaults_keeps_a_given_color(self):
        self.assertEqual(normalized_tag_color(" #abc ", blank_defaults=True), "#abc")
        self.assertEqual(normalized_tag_color("\t#001122\n"), "#001122")

    def test_rejects_css_declarations_and_invalid_colors(self):
        for unsafe in ("red;position:fixed", "red", "x;background:url(https://example.test)",
                       "#12", "#12345", "#1234567", "#fff;", None, 7):
            # The API answers 400 with this message, so it is part of the contract.
            with self.subTest(value=unsafe), self.assertRaisesRegex(ValueError, r"\Ainvalid tag color\Z"):
                normalized_tag_color(unsafe)
