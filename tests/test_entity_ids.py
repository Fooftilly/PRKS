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

    def test_invalid_prefix_is_refused(self):
        with self.assertRaises(ValueError):
            entity_ids.generate("person")
        with self.assertRaises(ValueError):
            entity_ids.generate("")
