"""Deterministic library seed for real-app E2E. Uses public PRKS domain methods."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from backend.db_manager import PRKSDatabase
from backend.research_index import PRKSResearchIndex
from backend.research_network import create_argument, create_position, save_work_notes
from backend.storage.config import StorageConfig

REPO = Path(__file__).resolve().parents[2]
MINIMAL_PDF = REPO / "tests" / "browser" / "assets" / "minimal.pdf"
SCHEMA = REPO / "backend" / "db_schema.sql"

WORK_A_TITLE = "E2E Research Work"
WORK_B_TITLE = "E2E Related Work"
PERSON_FIRST = "E2E"
PERSON_LAST = "Author"
PERSON_DISPLAY = "E2E Author"
PDF_NAME = "e2e-research.pdf"


def seed_library(storage_root: str) -> dict:
    """Create Works, a managed PDF, and an Author role. Returns generated IDs."""
    if not MINIMAL_PDF.is_file():
        raise RuntimeError("missing fixture PDF: %s" % MINIMAL_PDF)
    cfg = StorageConfig.for_testing(storage_root)
    os.makedirs(cfg.pdfs_dir, exist_ok=True)
    os.makedirs(cfg.thumbs_dir, exist_ok=True)
    dest_pdf = os.path.join(cfg.pdfs_dir, PDF_NAME)
    shutil.copy2(str(MINIMAL_PDF), dest_pdf)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    work_a = db.add_work(
        title=WORK_A_TITLE,
        text_content="Initial research notes.",
        file_path="/api/pdfs/%s" % PDF_NAME,
        doc_type="book",
        status="In Progress",
    )
    work_b = db.add_work(
        title=WORK_B_TITLE,
        text_content="Related notes.",
        doc_type="article",
        status="Not Started",
    )
    person_id = db.add_person(
        first_name=PERSON_FIRST,
        last_name=PERSON_LAST,
        about="E2E fixture author used for People profile and graph focus.",
        link_wikipedia="https://example.com/wiki/e2e-author",
        birth_date="1903",
        death_date="1969",
    )
    db.add_role(person_id, work_a, "Author")
    # Work nodes appear in the graph via explicit note markup / argument sources.
    seed_notes = "Initial research notes.\n\n[[concept:E2E Fixture Concept]]"
    save_work_notes(db, work_a, seed_notes)
    PRKSResearchIndex(storage=cfg).sync_work(work_a, seed_notes, db)
    return {
        "work_a": work_a,
        "work_b": work_b,
        "person": person_id,
        "pdf_name": PDF_NAME,
        "work_a_title": WORK_A_TITLE,
        "work_b_title": WORK_B_TITLE,
        "person_display": PERSON_DISPLAY,
    }


GRAPH_CONCEPT_A = "Culture"
GRAPH_CONCEPT_B = "Philosophy"
GRAPH_UNRELATED_POSITION = "Unrelated Position"
GRAPH_UNRELATED_ARGUMENT = "Unrelated Argument"


def seed_graph_context_library(storage_root: str) -> dict:
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    notes = "[[concept:%s]]\n\n[[concept:%s]]\n" % (GRAPH_CONCEPT_A, GRAPH_CONCEPT_B)
    save_work_notes(db, ids["work_a"], notes)
    PRKSResearchIndex(storage=cfg).sync_work(ids["work_a"], notes, db)
    pos = create_position(db, GRAPH_UNRELATED_POSITION)
    arg = create_argument(db, name=GRAPH_UNRELATED_ARGUMENT, kind="argument")
    ids["position"] = pos["id"]
    ids["argument"] = arg["id"]
    ids["concept_a"] = GRAPH_CONCEPT_A
    ids["concept_b"] = GRAPH_CONCEPT_B
    return ids
