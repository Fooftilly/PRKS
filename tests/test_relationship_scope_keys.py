"""Relationship scope ids are structural (#854, kept by PG-C2 #641).

A Work-Tag or Folder-Tag scope id is a JSON pair, and a Work-Person role
scope a JSON triple, so quotes, backslashes, separators and Unicode in any id
never alias two scopes. PostgreSQL stores the same ids
(``backend/domain/organization/links.py``, ``backend/domain/people/roles.py``);
only the pure key functions are exercised here, so this needs no database.
"""

from __future__ import annotations

import json
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from backend import folder_tag_sync, work_role_sync, work_tag_sync


class ScopeKeyProperties(unittest.TestCase):
    @settings(max_examples=50, deadline=None, database=None)
    @given(left=st.text(max_size=32), right=st.text(max_size=32), other=st.text(max_size=32))
    def test_relation_scope_keys_are_structural_and_round_trip(self, left: str, right: str, other: str) -> None:
        for scope_key in (work_tag_sync.scope_key, folder_tag_sync.scope_key):
            self.assertEqual(json.loads(scope_key(left, right)), [left, right])
            if right != other:
                self.assertNotEqual(scope_key(left, right), scope_key(left, other))
            if left != other:
                self.assertNotEqual(scope_key(left, right), scope_key(other, right))

    @settings(max_examples=50, deadline=None, database=None)
    @given(work=st.text(max_size=32), person=st.text(max_size=32), other=st.text(max_size=32))
    def test_role_scope_keys_are_structural_and_round_trip(self, work: str, person: str, other: str) -> None:
        for role_type in ("Author", "Editor"):
            self.assertEqual(json.loads(work_role_sync.scope_key(work, person, role_type)), [work, person, role_type])
        self.assertNotEqual(work_role_sync.scope_key(work, person, "Author"),
                            work_role_sync.scope_key(work, person, "Editor"))
        if person != other:
            self.assertNotEqual(work_role_sync.scope_key(work, person, "Author"),
                                work_role_sync.scope_key(work, other, "Author"))


if __name__ == "__main__":
    unittest.main()
