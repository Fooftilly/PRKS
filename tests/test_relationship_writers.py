"""Only the revision-aware boundary writes a revisioned PostgreSQL relationship.

A device's conflict detection is built on the revision of each relationship
scope (``docs/local-first-sync.md``): a write that changes a link without
advancing its revision is a change another device can overwrite without ever
being told there was a conflict. So each table below is written only by the
module that advances the revision with the write, and by the deletes that
tombstone every scope of what they delete. This is the PostgreSQL form of the
SQLite writer audits PG-C2 (#641) retires; the source is read, nothing runs.
"""

from __future__ import annotations

import pathlib
import re
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# table -> {module: why it may write the table}
WRITERS: dict[str, dict[str, str]] = {
    "work_tags": {
        "backend/domain/organization/links.py": "the Work-Tag boundary advances the scope with the write",
    },
    "folder_tags": {
        "backend/domain/organization/links.py": "the Folder-Tag boundary advances the scope with the write",
        "backend/domain/organization/folders.py": "a Folder delete tombstones every scope of the Folder",
    },
}


def _writers(table: str) -> set[str]:
    write = re.compile(r"\b(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+prks\.%s\b" % table, re.IGNORECASE)
    found = set()
    for path in sorted((_ROOT / "backend").rglob("*.py")):
        if write.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(_ROOT).as_posix())
    return found


class RelationshipWriterTests(unittest.TestCase):
    def test_only_the_revision_aware_boundary_writes_each_relationship(self) -> None:
        for table, allowed in WRITERS.items():
            with self.subTest(table=table):
                self.assertEqual(_writers(table), set(allowed),
                                 "write %s through its revision-aware boundary" % table)

    def test_the_audit_sees_a_write(self) -> None:
        self.assertIn("backend/domain/organization/links.py", _writers("work_tags"))
        self.assertEqual(_writers("no_such_table"), set())


if __name__ == "__main__":
    unittest.main()
