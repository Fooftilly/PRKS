"""Deterministic library seed for real-app E2E. Uses public PRKS domain methods."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from backend.db_manager import PRKSDatabase
from backend.research_index import PRKSResearchIndex
from backend.research_network import (
    create_argument,
    create_concept,
    create_position,
    replace_concept_aliases,
    replace_concept_parents,
    save_work_notes,
)
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
    dest_pdf_b = os.path.join(cfg.pdfs_dir, "e2e-related.pdf")
    shutil.copy2(str(MINIMAL_PDF), dest_pdf_b)
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
        file_path="/api/pdfs/e2e-related.pdf",
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


def seed_person_profile_draft_library(storage_root: str) -> dict:
    """Two independently editable Persons and four deterministic Groups."""
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    person_a = db.add_person(
        first_name="Ada",
        last_name="Alpha",
        aliases="A. Alpha",
        about="Saved biography A",
        birth_date="1972-03-12",
    )
    person_b = db.add_person(
        first_name="Bea",
        last_name="Beta",
        aliases="B. Beta",
        about="Saved biography B",
        birth_date="1980",
    )
    group_alpha = db.add_person_group("Group Alpha")
    group_beta = db.add_person_group("Group Beta")
    group_gamma = db.add_person_group("Group Gamma")
    group_delta = db.add_person_group("Group Delta")
    db.add_person_to_group(person_a, group_alpha)
    db.add_person_to_group(person_b, group_beta)
    ids.update(
        {
            "person_a": person_a,
            "person_b": person_b,
            "group_alpha": group_alpha,
            "group_beta": group_beta,
            "group_gamma": group_gamma,
            "group_delta": group_delta,
        }
    )
    return ids


GRAPH_CONCEPT_A = "Culture"
GRAPH_CONCEPT_B = "Philosophy"
GRAPH_UNRELATED_POSITION = "Unrelated Position"
GRAPH_UNRELATED_ARGUMENT = "Unrelated Argument"
GRAPH_SOURCED_ARGUMENT = "Sourced Argument"


def seed_graph_context_library(storage_root: str) -> dict:
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    notes = "[[concept:%s]]\n\n[[concept:%s]]\n" % (GRAPH_CONCEPT_A, GRAPH_CONCEPT_B)
    save_work_notes(db, ids["work_a"], notes)
    PRKSResearchIndex(storage=cfg).sync_work(ids["work_a"], notes, db)
    pos = create_position(db, GRAPH_UNRELATED_POSITION)
    arg = create_argument(db, name=GRAPH_UNRELATED_ARGUMENT, kind="argument")
    # Deliberately separate from GRAPH_UNRELATED_ARGUMENT and sourced to work_b (not work_a):
    # this gives tests a deterministic argument_source edge (e.g. relation-filter selection
    # regressions) without perturbing edge counts incident to work_a, which other graph tests
    # (e.g. the node-selection dimming test) assert exactly.
    sourced_arg = create_argument(
        db,
        name=GRAPH_SOURCED_ARGUMENT,
        kind="argument",
        sources=[{"work_id": ids["work_b"], "pages": "1-2"}],
    )
    ids["position"] = pos["id"]
    ids["argument"] = arg["id"]
    ids["sourced_argument"] = sourced_arg["id"]
    ids["concept_a"] = GRAPH_CONCEPT_A
    ids["concept_b"] = GRAPH_CONCEPT_B
    return ids


CONCEPT_PARENT_NAME = "E2E Parent Concept"
CONCEPT_CHILD_NAME = "E2E Child Concept"
CONCEPT_UNVISITED_NAME = "E2E Unvisited Concept"
CONCEPT_CHILD_ALIAS = "E2E Child Alias"
CONCEPT_CHILD_DEFINITION = "Child concept definition used by offline Concept detail assertions."


def seed_concepts_library(storage_root: str) -> dict:
    """seed_library plus an explicit Concept hierarchy for offline Concept scenarios.

    Gives the offline tests a parent/child pair to navigate between, a Concept
    that is deliberately never opened online (so "cached index, uncached detail"
    is testable), and a child Concept that is mentioned by Work A's research
    notes so a Concept -> Work mention link can be followed offline.
    """
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    parent = create_concept(db, CONCEPT_PARENT_NAME, "Parent concept definition.")
    child = create_concept(db, CONCEPT_CHILD_NAME, CONCEPT_CHILD_DEFINITION)
    unvisited = create_concept(db, CONCEPT_UNVISITED_NAME, "Never opened while online.")
    replace_concept_parents(db, child["id"], [parent["id"]])
    replace_concept_aliases(db, child["id"], [CONCEPT_CHILD_ALIAS])
    notes = "Initial research notes.\n\n[[concept:%s]]\n" % CONCEPT_CHILD_NAME
    save_work_notes(db, ids["work_a"], notes)
    PRKSResearchIndex(storage=cfg).sync_work(ids["work_a"], notes, db)
    ids.update(
        {
            "concept_parent": parent["id"],
            "concept_child": child["id"],
            "concept_unvisited": unvisited["id"],
            "concept_parent_name": CONCEPT_PARENT_NAME,
            "concept_child_name": CONCEPT_CHILD_NAME,
            "concept_unvisited_name": CONCEPT_UNVISITED_NAME,
            "concept_child_alias": CONCEPT_CHILD_ALIAS,
        }
    )
    return ids


POSITION_A_NAME = "E2E Cached Position"
POSITION_A_DESCRIPTION = "Position description used by offline Position detail assertions."
POSITION_B_NAME = "E2E Unvisited Position"
POSITION_ARGUMENT_NAME = "E2E Targeting Argument"
POSITION_ARGUMENT_VERDICT_LABEL = "Supports"


def seed_positions_library(storage_root: str) -> dict:
    """seed_concepts_library plus Positions and a targeting Argument.

    Gives the offline tests a Position whose detail embeds an Argument summary
    (name/kind/verdict) — the cross-domain dependency Position coherence has to
    handle — a Position that is deliberately never opened online, and the
    Concept fixtures alongside them so domain independence can be observed with
    two live domains at once.
    """
    ids = seed_concepts_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    position_a = create_position(db, POSITION_A_NAME, POSITION_A_DESCRIPTION)
    position_b = create_position(db, POSITION_B_NAME, "Never opened while online.")
    argument = create_argument(
        db,
        name=POSITION_ARGUMENT_NAME,
        kind="argument",
        targets=[{"type": "position", "id": position_a["id"], "verdict_id": "supports"}],
    )
    ids.update(
        {
            "position_a": position_a["id"],
            "position_b": position_b["id"],
            "position_argument": argument["id"],
            "position_a_name": POSITION_A_NAME,
            "position_b_name": POSITION_B_NAME,
            "position_argument_name": POSITION_ARGUMENT_NAME,
        }
    )
    return ids


ARGUMENT_A_NAME = "E2E Cached Argument"
ARGUMENT_A_TEXT = "Argument main text used by offline Argument detail assertions."
ARGUMENT_B_NAME = "E2E Target Argument"
ARGUMENT_C_NAME = "E2E Response Argument"
ARGUMENT_UNVISITED_NAME = "E2E Unvisited Argument"
STANCE_NAME = "E2E Cached Stance"
STANCE_TEXT = "Stance main text used by offline Stance detail assertions."
ARGUMENT_SOURCE_PAGES = "11-22"


def seed_arguments_library(storage_root: str) -> dict:
    """seed_positions_library plus a full Argument/Stance relationship set.

    Argument A is the interesting one: it targets a Position *and* another
    Argument, sources a Work (whose Author is a real Person), is answered by a
    response Argument, and is mentioned by a second Work's research notes. That
    single record therefore depends on five different canonical record families,
    which is exactly the coherence surface this milestone has to get right.
    """
    ids = seed_positions_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))
    target = create_argument(db, name=ARGUMENT_B_NAME, kind="argument")
    stance = create_argument(
        db,
        name=STANCE_NAME,
        kind="stance",
        main_text=STANCE_TEXT,
        targets=[{"type": "position", "id": ids["position_a"], "verdict_id": "holds"}],
    )
    create_argument(db, name=ARGUMENT_UNVISITED_NAME, kind="argument")
    argument_a = create_argument(
        db,
        name=ARGUMENT_A_NAME,
        kind="argument",
        main_text=ARGUMENT_A_TEXT,
        sources=[{"work_id": ids["work_a"], "pages": ARGUMENT_SOURCE_PAGES}],
        targets=[
            {"type": "position", "id": ids["position_a"], "verdict_id": "supports"},
            {"type": "argument", "id": target["id"], "verdict_id": "opposes"},
        ],
    )
    response = create_argument(
        db,
        name=ARGUMENT_C_NAME,
        kind="argument",
        targets=[{"type": "argument", "id": argument_a["id"], "verdict_id": "opposes"}],
    )
    # Work B's research notes mention Argument A, giving it a mention backlink.
    notes = "Related notes.\n\n[[argument:%s|%s]]\n" % (argument_a["id"], ARGUMENT_A_NAME)
    save_work_notes(db, ids["work_b"], notes)
    PRKSResearchIndex(storage=cfg).sync_work(ids["work_b"], notes, db)
    ids.update(
        {
            "argument_a": argument_a["id"],
            "argument_target": target["id"],
            "argument_response": response["id"],
            "argument_unvisited": [
                row["id"]
                for row in db.execute_query("SELECT id FROM arguments WHERE name = ?", (ARGUMENT_UNVISITED_NAME,))
            ][0],
            "stance": stance["id"],
            "argument_a_name": ARGUMENT_A_NAME,
            "stance_name": STANCE_NAME,
        }
    )
    return ids
