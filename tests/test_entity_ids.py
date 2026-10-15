"""Collision-resistant entity ids: new 32-hex form, legacy 8-hex still valid."""
import unittest

from backend import entity_ids


class EntityIdTests(unittest.TestCase):
    def test_generate_is_prefix_plus_32_uppercase_hex(self):
        value = entity_ids.generate("P")
        self.assertTrue(entity_ids.is_distributed(value, "P"), value)
        self.assertEqual(len(value), 2 + entity_ids.HEX_CHARS)
        self.assertEqual(value[:2], "P-")
        self.assertEqual(value[2:], value[2:].upper())

    def test_two_calls_do_not_collide(self):
        self.assertNotEqual(entity_ids.generate("P"), entity_ids.generate("P"))

    def test_legacy_eight_hex_is_still_a_generated_id(self):
        legacy = "P-A1B2C3D4"
        self.assertTrue(entity_ids.is_generated(legacy, "P"))
        self.assertFalse(entity_ids.is_distributed(legacy, "P"))

    def test_distributed_form_is_required_for_offline_creation(self):
        fresh = entity_ids.generate("P")
        self.assertTrue(entity_ids.is_distributed(fresh, "P"))
        self.assertFalse(entity_ids.is_distributed("P-A1B2C3D4", "P"))
        self.assertFalse(entity_ids.is_distributed(fresh, "W"))
        self.assertFalse(entity_ids.is_distributed("W-gone"))
        self.assertFalse(entity_ids.is_distributed("PF-KEEPIMP"))

    def test_positions_and_persons_share_a_prefix_on_purpose(self):
        person = entity_ids.generate("P")
        position = entity_ids.generate("P")
        self.assertTrue(entity_ids.is_distributed(person, "P"))
        self.assertTrue(entity_ids.is_distributed(position, "P"))

    def test_argument_markup_body_still_fits_32_alnum(self):
        argument = entity_ids.generate("A")
        body = argument.split("-", 1)[1]
        self.assertEqual(len(body), 32)
        self.assertRegex(body, r"^[A-Za-z0-9]{1,32}$")

    def test_parser_accepts_exact_legacy_and_distributed_forms(self):
        cases = (
            ("A-01234567", ("A", "01234567"), False),
            ("AB-ABCDEF12", ("AB", "ABCDEF12"), False),
            ("ABC-0123456789ABCDEF0123456789ABCDEF",
             ("ABC", "0123456789ABCDEF0123456789ABCDEF"), True),
        )
        for value, parts, distributed in cases:
            with self.subTest(value=value):
                self.assertEqual(entity_ids.parse(value), parts)
                self.assertTrue(entity_ids.is_generated(value, parts[0]))
                self.assertEqual(entity_ids.is_distributed(value, parts[0]), distributed)
                self.assertFalse(entity_ids.is_generated(value, "X"))
                self.assertFalse(entity_ids.is_distributed(value, "X"))

    def test_parser_rejects_malformed_ids_and_nontext_inputs(self):
        invalid = (
            "P-0123456",  # 7 hex characters, not a legacy ID
            "P-012345678",  # 9, neither supported length
            "P-" + "A" * 31,
            "P-" + "A" * 33,
            "P-abcdef12",  # lowercase must never be normalized implicitly
            "p-ABCDEF12",
            "ABCD-ABCDEF12",  # prefix has at most three uppercase letters
            "P-ABCDEF12\\n",  # an appended literal escape sequence
            "P-ABCDEF12\n",  # an actual trailing newline
            "P- ABCDEF12",
            "É-ABCDEF12",
            None,
            b"P-ABCDEF12",
            123,
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(entity_ids.parse(value))
                self.assertFalse(entity_ids.is_generated(value))
                self.assertFalse(entity_ids.is_distributed(value))

    def test_generator_rejects_unsupported_prefix_shapes(self):
        for prefix in ("abcd", "ABCD", "AB-", " A", "A ", "Ä", "A\n", 42, None):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                entity_ids.generate(prefix)

    def test_invalid_prefix_is_refused(self):
        with self.assertRaises(ValueError):
            entity_ids.generate("person")
        with self.assertRaises(ValueError):
            entity_ids.generate("")
