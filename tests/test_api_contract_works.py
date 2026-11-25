"""The Work wire shapes that mirror a domain registry stay in step with it (#1431)."""

from __future__ import annotations

import unittest

from pydantic import TypeAdapter, ValidationError

from backend.api_contract.works import (
    BulkWorkRequest,
    MetadataFieldRevision,
    MetadataFieldValue,
    WorkMetadataFields,
)
from backend.domain.works import metadata_rules


class WorkMetadataFieldsTests(unittest.TestCase):
    def test_every_synchronized_field_is_named_and_byte_fields_carry_no_value(self) -> None:
        fields = WorkMetadataFields.model_fields
        self.assertEqual(set(fields), set(metadata_rules.SYNCED_FIELDS))
        for name, info in fields.items():
            with self.subTest(field=name):
                expected = (MetadataFieldRevision if name in metadata_rules.BYTE_LIMITED_FIELDS
                            else MetadataFieldValue)
                self.assertIs(info.annotation, expected)
                self.assertTrue(info.is_required())


class BulkWorkRequestTests(unittest.TestCase):
    adapter: TypeAdapter = TypeAdapter(BulkWorkRequest)

    def test_each_action_requires_its_own_field(self) -> None:
        for body in ({"action": "set_status", "work_ids": ["w"], "status": "Planned"},
                     {"action": "move_folder", "work_ids": ["w"], "folder_id": None},
                     {"action": "move_folder", "work_ids": ["w"], "folder_id": "f"},
                     {"action": "add_tags", "work_ids": ["w"], "tag_ids": ["t"]},
                     {"action": "remove_tags", "work_ids": ["w"], "tag_ids": ["t"]}):
            with self.subTest(body=body):
                self.adapter.validate_python(body)
        for body in ({"action": "set_status", "work_ids": ["w"]},
                     {"action": "move_folder", "work_ids": ["w"]},
                     {"action": "add_tags", "work_ids": ["w"]},
                     {"action": "rename", "work_ids": ["w"]}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                self.adapter.validate_python(body)


if __name__ == "__main__":
    unittest.main()
