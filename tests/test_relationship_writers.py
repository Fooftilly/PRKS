"""Only the revision-aware boundary writes a revisioned PostgreSQL relationship.

A device's conflict detection is built on the revision of each relationship
scope (``docs/local-first-sync.md``): a write that changes a link without
advancing its revision is a change another device can overwrite without ever
being told there was a conflict. So each table below is written only by the
module that advances the revision with the write, and by the deletes that
tombstone every scope of what they delete. Positions, Arguments and Concepts
carry per-field and per-list revisions, so their tables are audited the same
way. This is the PostgreSQL form of the SQLite writer audits PG-C2 (#641)
retires; the source is read, nothing runs.
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
    "positions": {
        "backend/domain/research/positions.py": "each Position field advances its own scope with the write",
    },
    "arguments": {
        "backend/domain/research/arguments.py": "each Argument field advances its own scope with the write",
    },
    "argument_sources": {
        "backend/domain/research/arguments.py": "the citation list advances its one scope with the write",
    },
    "argument_target_positions": {
        "backend/domain/research/arguments.py": "the target list advances its one scope with the write",
    },
    "argument_target_arguments": {
        "backend/domain/research/arguments.py": "the target list advances its one scope with the write",
    },
    "concepts": {
        "backend/domain/research/concepts.py": "creates, renames and edits advance the identity and definition scopes",
    },
    "concept_aliases": {
        "backend/domain/research/concepts.py": "an alias change advances the identity scope it shares with the name",
    },
    "concept_keys": {
        "backend/domain/research/concepts.py": "the one namespace of names and aliases moves with the identity",
    },
    "concept_parents": {
        "backend/domain/research/concepts.py": "a hierarchy change advances both ends of each edge",
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
