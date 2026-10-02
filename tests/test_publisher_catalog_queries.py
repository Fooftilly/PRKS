"""Publisher catalog built with a bounded number of statements (#186).

``get_publishers_in_use`` must return exactly what the per-Publisher
implementation returned while issuing the same few statements however many
Publishers the library has.
"""
import tempfile
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


def legacy_get_publishers_in_use(db):
    """The pre-#186 per-Publisher implementation, kept as the output oracle."""
    rows = db.execute_query(
        "SELECT id, name, created_at FROM publishers ORDER BY LOWER(name) ASC"
    )
    out = []
    for r in rows:
        pid = r["id"]
        arows = db.execute_query(
            "SELECT alias FROM publisher_aliases WHERE publisher_id = ? ORDER BY LOWER(alias) ASC",
            (pid,),
        )
        aliases = [x["alias"] for x in arows if (x["alias"] or "").strip()]
        name = (r["name"] or "").strip()
        labels = [name] + aliases if name else list(aliases)
        cleaned = [x.strip() for x in labels if x and x.strip()]
        work_count = 0
        if cleaned:
            lows = [x.lower() for x in cleaned]
            ph = ",".join("?" * len(lows))
            wc = db.execute_query(
                f"""
                SELECT COUNT(DISTINCT id) AS c FROM works
                WHERE TRIM(COALESCE(publisher,'')) != ''
                  AND LOWER(TRIM(publisher)) IN ({ph})
                """,
                tuple(lows),
            )
            work_count = int(wc[0]["c"]) if wc else 0
        out.append({"id": pid, "name": name, "aliases": aliases, "work_count": work_count})
    return out


class PublisherCatalogQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-publishers-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self._n = 0

    def publisher(self, pid, name, *aliases):
        with self.db.connection() as conn:
            conn.execute("INSERT INTO publishers (id, name) VALUES (?, ?)", (pid, name))
            for i, alias in enumerate(aliases):
                conn.execute(
                    "INSERT INTO publisher_aliases (id, publisher_id, alias) VALUES (?, ?, ?)",
                    (f"{pid}-A{i}", pid, alias),
                )
            conn.commit()

    def works(self, *publishers):
        with self.db.connection() as conn:
            for value in publishers:
                self._n += 1
                conn.execute(
                    "INSERT INTO works (id, title, publisher) VALUES (?, ?, ?)",
                    (f"W-{self._n:05d}", f"Work {self._n}", value),
                )
            conn.commit()

    def selects(self, call):
        sqls = []
        real_get = self.db.get_connection

        def traced():
            conn = real_get()
            conn.set_trace_callback(sqls.append)
            return conn

        self.db.get_connection = traced
        try:
            result = call()
        finally:
            self.db.get_connection = real_get
        return result, [s for s in sqls if s.lstrip().upper().startswith("SELECT")]

    def seed(self):
        self.publisher("PUB-OUP", "Oxford University Press", "OUP", "Oxford UP")
        self.publisher("PUB-MIT", "  MIT Press  ")
        self.publisher("PUB-ALIAS-ONLY", "Zeta House", "   ", "Zeta")
        self.publisher("PUB-NONE", "Nobody Prints")
        self.publisher("PUB-EDS", "Éditions Lumière", "editions lumiere")
        self.works(
            "Oxford University Press",
            "oxford university press",
            "  OUP ",
            "oup",
            "Oxford UP",
            "MIT Press",
            "mit press",
            "\tMIT Press",  # SQLite TRIM keeps tabs: not a match, before or after.
            "Zeta",
            "Zeta House",
            "ÉDITIONS LUMIÈRE",  # SQLite LOWER is ASCII-only: not a match either way.
            "Éditions Lumière",
            "Editions Lumiere",
            "Unknown Publisher",
            "",
            "   ",
            None,
        )

    def test_catalog_matches_per_publisher_implementation(self):
        self.seed()
        rows = self.db.get_publishers_in_use()
        self.assertEqual(rows, legacy_get_publishers_in_use(self.db))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["PUB-OUP"]["work_count"], 5)
        self.assertEqual(by_id["PUB-OUP"]["aliases"], ["OUP", "Oxford UP"])
        self.assertEqual(by_id["PUB-MIT"]["name"], "MIT Press")
        self.assertEqual(by_id["PUB-MIT"]["work_count"], 2)
        self.assertEqual(by_id["PUB-ALIAS-ONLY"]["aliases"], ["Zeta"])
        self.assertEqual(by_id["PUB-ALIAS-ONLY"]["work_count"], 2)
        self.assertEqual(by_id["PUB-NONE"], {
            "id": "PUB-NONE", "name": "Nobody Prints", "aliases": [], "work_count": 0,
        })
        self.assertEqual(by_id["PUB-EDS"]["work_count"], 1)
        self.assertEqual(
            [row["id"] for row in rows],
            ["PUB-MIT", "PUB-NONE", "PUB-OUP", "PUB-ALIAS-ONLY", "PUB-EDS"],
        )

    def test_name_repeated_as_alias_counts_each_work_once(self):
        self.publisher("PUB-DUP", "Verso", "VERSO", "Verso Books")
        self.works("Verso", "verso", "Verso Books")
        rows = self.db.get_publishers_in_use()
        self.assertEqual(rows, legacy_get_publishers_in_use(self.db))
        self.assertEqual(rows[0]["work_count"], 3)

    def test_empty_catalog_issues_one_statement(self):
        rows, selects = self.selects(self.db.get_publishers_in_use)
        self.assertEqual(rows, [])
        self.assertEqual(len(selects), 1)

    def test_statement_count_is_bounded_as_publishers_grow(self):
        self.seed()
        _, small = self.selects(self.db.get_publishers_in_use)
        for i in range(150):
            self.publisher(f"PUB-BULK-{i:04d}", f"Bulk Press {i}", f"Bulk {i}")
            self.works(f"Bulk Press {i}", f"bulk {i}")
        rows, large = self.selects(self.db.get_publishers_in_use)
        self.assertEqual(len(small), 3)
        self.assertEqual(len(large), 3)
        self.assertEqual(rows, legacy_get_publishers_in_use(self.db))
        self.assertTrue(all(
            row["work_count"] == 2 for row in rows if row["id"].startswith("PUB-BULK-")
        ))


if __name__ == "__main__":
    unittest.main()
