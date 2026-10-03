"""Set-based Person Group catalog counts (#153).

The catalog must return exactly what the per-row correlated counts returned
while reading each relationship once, however many groups there are.
"""
import tempfile
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

# The pre-#153 catalog query, kept as the output oracle.
LEGACY_CATALOG_SQL = """
SELECT g.*,
    (SELECT COUNT(*) FROM person_group_members m WHERE m.group_id = g.id) AS member_count,
    (SELECT COUNT(*) FROM person_groups c WHERE c.parent_id = g.id) AS child_count
FROM person_groups g
ORDER BY g.name COLLATE NOCASE, g.id ASC
"""


class PersonGroupCatalogCountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-group-counts-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))

    def legacy(self):
        return self.db.execute_query(LEGACY_CATALOG_SQL)

    def catalog_plan(self):
        sqls = []
        real_get = self.db.get_connection

        def traced():
            conn = real_get()
            conn.set_trace_callback(sqls.append)
            return conn

        self.db.get_connection = traced
        try:
            self.db.get_all_person_groups()
        finally:
            self.db.get_connection = real_get
        selects = [s for s in sqls if s.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1)
        with self.db.connection() as conn:
            return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + selects[0])]

    def seed(self, groups=6, people=8):
        ids = []
        for i in range(groups):
            parent = ids[i // 2] if i else None
            ids.append(self.db.add_person_group(name=f"Group {i:03d}", parent_id=parent))
        persons = [self.db.add_person(first_name="P", last_name=f"Person {i}") for i in range(people)]
        for i, person_id in enumerate(persons):
            for group_id in ids[: i % len(ids)]:
                self.db.add_person_to_group(person_id, group_id)
        return ids

    def test_catalog_matches_correlated_counts(self):
        ids = self.seed()
        # Name ties ordered by id, an empty leaf and an empty parent.
        empty_parent = self.db.add_person_group(name="aa empty parent")
        self.db.add_person_group(name="AB leaf", parent_id=empty_parent)
        rows = self.db.get_all_person_groups()
        self.assertEqual(rows, self.legacy())
        self.assertEqual([list(row) for row in rows], [list(row) for row in self.legacy()])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[empty_parent]["member_count"], 0)
        self.assertEqual(by_id[empty_parent]["child_count"], 1)
        self.assertEqual(by_id[ids[-1]]["child_count"], 0)
        self.assertTrue(all(isinstance(row["member_count"], int) for row in rows))
        self.assertTrue(all(isinstance(row["child_count"], int) for row in rows))

    def test_empty_catalog(self):
        self.assertEqual(self.db.get_all_person_groups(), [])

    def test_catalog_reads_each_relationship_once(self):
        self.seed()
        plan = self.catalog_plan()
        self.assertFalse([step for step in plan if "CORRELATED" in step.upper()], plan)
        # One aggregate pass over memberships and one over the hierarchy,
        # plus the ordered walk of the groups themselves.
        self.assertEqual(sum("SCAN PERSON_GROUP_MEMBERS" in step.upper() for step in plan), 1, plan)
        self.assertEqual(sum(step.upper().startswith("SCAN PERSON_GROUPS") for step in plan), 1, plan)

    def test_plan_stays_set_based_as_catalog_grows(self):
        self.seed(groups=4, people=4)
        small = self.catalog_plan()
        self.seed_more(groups=200, people=150)
        large = self.catalog_plan()
        self.assertEqual(small, large)
        self.assertEqual(self.db.get_all_person_groups(), self.legacy())

    def seed_more(self, groups, people):
        ids = []
        for i in range(groups):
            parent = ids[i // 3] if i else None
            ids.append(self.db.add_person_group(name=f"Bulk {i:04d}", parent_id=parent))
        with self.db.connection() as conn:
            for i in range(people):
                person_id = f"PBULK{i:05d}"
                conn.execute(
                    "INSERT INTO persons (id, first_name, last_name) VALUES (?, ?, ?)",
                    (person_id, "Bulk", f"Person {i}"),
                )
                conn.executemany(
                    "INSERT INTO person_group_members (person_id, group_id) VALUES (?, ?)",
                    [(person_id, group_id) for group_id in ids[i % 7:: 7]],
                )
            conn.commit()


if __name__ == "__main__":
    unittest.main()
