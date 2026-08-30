"""Concept/Argument/Position domain: notes, hierarchy, verdicts, cycles."""
import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.research_index import PRKSResearchIndex
from backend.research_network import (
    ResearchError,
    create_argument,
    create_concept,
    create_position,
    delete_argument,
    delete_concept,
    get_argument,
    get_concept,
    list_concepts,
    replace_argument_targets,
    replace_concept_aliases,
    replace_concept_parents,
    save_work_notes,
    update_concept,
)
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


class ResearchNetworkTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-research-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.index = PRKSResearchIndex(storage=self.storage)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _work(self, title="Dialectic"):
        return self.db.add_work(title=title)

    def test_note_save_creates_concept(self):
        wid = self._work()
        save_work_notes(
            self.db,
            wid,
            "Adorno's account of [[concept:Culture Industry]] depends upon standardization.",
        )
        self.index.sync_work(
            wid,
            self.db.get_work(wid)["text_content"],
            self.db,
        )
        concepts = list_concepts(self.db)
        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0]["name"], "Culture Industry")
        self.assertEqual(concepts[0]["description"], "")
        cid = concepts[0]["id"]
        save_work_notes(
            self.db,
            wid,
            "Same idea as [[concept:culture industry]] and [[concept:  Culture   Industry  ]].",
        )
        self.assertEqual(list_concepts(self.db)[0]["id"], cid)
        note = self.db.get_work(wid)["text_content"]
        self.assertIn("[[concept:culture industry]]", note)

    def test_same_concept_multiple_works_and_last_ref_keeps_record(self):
        a = self._work("A")
        b = self._work("B")
        save_work_notes(self.db, a, "See [[concept:Culture Industry]].")
        save_work_notes(self.db, b, "Also [[concept:Culture Industry]].")
        self.index.sync_work(a, self.db.get_work(a)["text_content"], self.db)
        self.index.sync_work(b, self.db.get_work(b)["text_content"], self.db)
        cid = list_concepts(self.db)[0]["id"]
        self.assertEqual(self.index.mention_count_for_concept(cid), 2)
        save_work_notes(self.db, a, "No concept here.")
        self.index.sync_work(a, self.db.get_work(a)["text_content"], self.db)
        self.assertEqual(self.index.mention_count_for_concept(cid), 1)
        save_work_notes(self.db, b, "Gone.")
        self.index.sync_work(b, self.db.get_work(b)["text_content"], self.db)
        self.assertEqual(self.index.mention_count_for_concept(cid), 0)
        self.assertIsNotNone(get_concept(self.db, cid))

    def test_rename_preserves_old_name_as_alias(self):
        wid = self._work()
        save_work_notes(self.db, wid, "[[concept:Culture Industry]]")
        cid = list_concepts(self.db)[0]["id"]
        update_concept(self.db, cid, name="Cultural Industry")
        item = get_concept(self.db, cid)
        self.assertEqual(item["name"], "Cultural Industry")
        self.assertIn("Culture Industry", item["aliases"])
        save_work_notes(self.db, wid, "still [[concept:Culture Industry]]")
        self.assertEqual(list_concepts(self.db)[0]["id"], cid)

    def test_alias_resolves(self):
        c = create_concept(self.db, "Cultural Industry")
        replace_concept_aliases(self.db, c["id"], ["Culture Industry", "Kulturindustrie"])
        wid = self._work()
        save_work_notes(self.db, wid, "[[concept:Kulturindustrie]]")
        self.assertEqual(list_concepts(self.db)[0]["id"], c["id"])

    def test_multi_parent_and_cycle_rejected(self):
        a = create_concept(self.db, "Critical Theory")
        b = create_concept(self.db, "Mass Culture")
        c = create_concept(self.db, "Culture Industry")
        replace_concept_parents(self.db, c["id"], [a["id"], b["id"]])
        item = get_concept(self.db, c["id"])
        self.assertEqual({p["id"] for p in item["parents"]}, {a["id"], b["id"]})
        replace_concept_parents(self.db, b["id"], [a["id"]])
        with self.assertRaises(ResearchError) as ctx:
            replace_concept_parents(self.db, a["id"], [c["id"]])
        self.assertEqual(ctx.exception.code, "concept_cycle")

    def test_self_parent_rejected(self):
        a = create_concept(self.db, "Loop")
        with self.assertRaises(ResearchError) as ctx:
            replace_concept_parents(self.db, a["id"], [a["id"]])
        self.assertEqual(ctx.exception.code, "concept_cycle")

    def test_delete_in_use_rejected(self):
        wid = self._work()
        save_work_notes(self.db, wid, "[[concept:Culture Industry]]")
        self.index.sync_work(wid, self.db.get_work(wid)["text_content"], self.db)
        cid = list_concepts(self.db)[0]["id"]
        with self.assertRaises(ResearchError) as ctx:
            delete_concept(self.db, cid, mention_count=self.index.mention_count_for_concept(cid))
        self.assertEqual(ctx.exception.code, "concept_in_use")
        self.assertEqual(ctx.exception.http_status, 409)

    def test_ambiguous_dormant_duplicates(self):
        self.db.execute_query(
            "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
            ("C-aaa", "Culture Industry", ""),
        )
        self.db.execute_query(
            "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
            ("C-bbb", "culture industry", ""),
        )
        wid = self._work()
        with self.assertRaises(ResearchError) as ctx:
            save_work_notes(self.db, wid, "[[concept:Culture Industry]]")
        self.assertEqual(ctx.exception.code, "ambiguous_concept")
        self.assertEqual(self.db.get_work(wid)["text_content"] or "", "")

    def test_code_and_escape_do_not_create(self):
        wid = self._work()
        save_work_notes(
            self.db,
            wid,
            "code `[[concept:Culture Industry]]` and \\[[concept:Mass Culture]]",
        )
        self.assertEqual(list_concepts(self.db), [])

    def test_argument_sources_targets_responses_cycles(self):
        w1 = self._work("Work One")
        w2 = self._work("Work Two")
        pos = create_position(self.db, "Mass-produced culture necessarily standardizes experience.")
        a = create_argument(
            self.db,
            name="Standardization argument",
            kind="argument",
            main_text="Adorno argues cultural commodities become standardized.",
            sources=[{"work_id": w1, "pages": "94–136"}, {"work_id": w2, "pages": "§3"}],
            targets=[{"type": "position", "id": pos["id"], "verdict_id": "supports"}],
        )
        self.assertEqual(a["kind"], "argument")
        self.assertEqual(len(a["sources"]), 2)
        self.assertEqual(a["sources"][0]["pages"], "94–136")
        self.assertEqual(a["targets"][0]["verdict_id"], "supports")
        stance = create_argument(
            self.db,
            name="Holds the position",
            kind="stance",
            targets=[{"type": "position", "id": pos["id"], "verdict_id": "holds"}],
        )
        self.assertEqual(stance["kind"], "stance")
        b = create_argument(
            self.db,
            name="Habermas response",
            kind="argument",
            targets=[{"type": "argument", "id": a["id"], "verdict_id": "opposes"}],
        )
        got = get_argument(self.db, a["id"])
        self.assertEqual(got["responses"][0]["id"], b["id"])
        with self.assertRaises(ResearchError) as ctx:
            replace_argument_targets(
                self.db, a["id"], [{"type": "argument", "id": a["id"], "verdict_id": "supports"}]
            )
        self.assertEqual(ctx.exception.code, "argument_cycle")
        with self.assertRaises(ResearchError):
            replace_argument_targets(
                self.db, a["id"], [{"type": "argument", "id": b["id"], "verdict_id": "supports"}]
            )
        c = create_argument(self.db, name="C", kind="argument")
        replace_argument_targets(
            self.db, b["id"], [{"type": "argument", "id": c["id"], "verdict_id": "qualifies"}]
        )
        replace_argument_targets(
            self.db, a["id"], [{"type": "argument", "id": b["id"], "verdict_id": "qualifies"}]
        )
        with self.assertRaises(ResearchError):
            replace_argument_targets(
                self.db, c["id"], [{"type": "argument", "id": a["id"], "verdict_id": "supports"}]
            )

    def test_missing_verdict_rejected(self):
        pos = create_position(self.db, "A claim")
        with self.assertRaises(ResearchError) as ctx:
            create_argument(
                self.db,
                name="No verdict",
                kind="argument",
                targets=[{"type": "position", "id": pos["id"]}],
            )
        self.assertIn("verdict", ctx.exception.code)

    def test_delete_argument_blocked_by_note_and_target(self):
        wid = self._work()
        arg = create_argument(self.db, name="Linked", kind="argument")
        save_work_notes(self.db, wid, "See [[argument:" + arg["id"] + "|label]].")
        self.index.sync_work(wid, self.db.get_work(wid)["text_content"], self.db)
        with self.assertRaises(ResearchError) as ctx:
            delete_argument(
                self.db, arg["id"], mention_count=self.index.mention_count_for_argument(arg["id"])
            )
        self.assertEqual(ctx.exception.code, "argument_in_use")
        save_work_notes(self.db, wid, "")
        self.index.sync_work(wid, "", self.db)
        other = create_argument(
            self.db,
            name="Response",
            kind="argument",
            targets=[{"type": "argument", "id": arg["id"], "verdict_id": "opposes"}],
        )
        with self.assertRaises(ResearchError) as ctx:
            delete_argument(self.db, arg["id"], mention_count=0)
        self.assertEqual(ctx.exception.code, "argument_targeted")
        self.assertIsNotNone(get_argument(self.db, other["id"]))

    def test_unknown_argument_ref_does_not_create(self):
        wid = self._work()
        save_work_notes(self.db, wid, "[[argument:A-MISSING]]")
        rows = self.db.execute_query("SELECT id FROM arguments")
        self.assertEqual(rows, [])

    def test_private_notes_do_not_create_concepts(self):
        wid = self._work()
        self.db.update_work_metadata(wid, {"private_notes": "[[concept:Secret Idea]]"})
        save_work_notes(self.db, wid, "public prose only")
        self.assertEqual(list_concepts(self.db), [])

    def test_logs_omit_concept_names(self):
        with self.assertLogs("prks.research", level="INFO") as cm:
            create_concept(self.db, "Culture Industry SECRETNAME")
        blob = "\n".join(cm.output)
        self.assertNotIn("Culture Industry", blob)
        self.assertNotIn("SECRETNAME", blob)
        self.assertIn("concept_created", blob)


if __name__ == "__main__":
    unittest.main()
