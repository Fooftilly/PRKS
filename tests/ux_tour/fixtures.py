"""Richer deterministic seed for the UX Interaction Tour.

Extends tests.e2e.fixtures.seed_graph_context_library (reusing seed_library()'s
two PDF Works, its Person/Author role, and the graph fixture's Concept mentions
and Position/Argument pair) with enough additional representative data that no
tour scenario lands on an empty-state-only page: a folder hierarchy, several
Tags, a Publisher, a Playlist, a second Person, a Person Group with a subgroup
and memberships, a Concept parent/child pair, a Position with a supporting
Stance, different progress states, and a Saved View.

Uses public PRKS domain/database methods only, exactly like the existing E2E
fixtures -- no raw SQL. Every tour test gets a *fresh* copy of this seed (see
tests/ux_tour/test_tours.py): this function is deterministic, but it must
never be reused across scenarios within one server/database.
"""
from __future__ import annotations

import os
import shutil

from backend.db_manager import PRKSDatabase
from backend.research_network import (
    create_argument,
    create_concept,
    create_position,
    replace_concept_parents,
)
from backend.storage.config import StorageConfig

from tests.e2e.fixtures import (
    MINIMAL_PDF,
    PERSON_DISPLAY,
    SCHEMA,
    WORK_A_TITLE,
    WORK_B_TITLE,
    seed_graph_context_library,
)

WORK_C_TITLE = "E2E UX Tour PDF Work"
WORK_D_TITLE = "E2E UX Tour Planned Work"
PUBLISHER_NAME = "UX Tour Publishing House"
TAG_NAMES = ("UX Tag Alpha", "UX Tag Beta", "UX Tag Gamma")
PLAYLIST_TITLE = "UX Tour Playlist"
PERSON2_FIRST = "Second"
PERSON2_LAST = "UX Author"
PERSON2_DISPLAY = "Second UX Author"
GROUP_PARENT_NAME = "UX Tour Circle"
GROUP_CHILD_NAME = "UX Tour Inner Circle"
CONCEPT_PARENT_NAME = "UX Tour Root Concept"
CONCEPT_CHILD_NAME = "UX Tour Child Concept"
TOUR_POSITION_NAME = "UX Tour Position"
TOUR_STANCE_NAME = "UX Tour Stance"
SAVED_VIEW_NAME = "UX Tour Saved Search"
UX_PDF_NAME = "e2e-ux-tour.pdf"


def seed_ux_tour_library(storage_root: str) -> dict:
    ids = seed_graph_context_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = PRKSDatabase(storage=cfg, schema_path=str(SCHEMA))

    # --- Folder hierarchy: parent + child, each holding one of the two seed Works. ---
    parent_folder = db.add_folder("UX Tour Library")
    child_folder = db.add_folder("UX Tour Subfolder", parent_id=parent_folder)
    db.add_work_to_folder(parent_folder, ids["work_a"])
    db.add_work_to_folder(child_folder, ids["work_b"])
    ids["folder_parent"] = parent_folder
    ids["folder_child"] = child_folder

    # --- A second PDF Work (Completed, with a Publisher) and a non-PDF Work
    # (Planned) so progress/library views aren't limited to two entries. ---
    if not os.path.isfile(MINIMAL_PDF):
        raise RuntimeError("missing fixture PDF: %s" % MINIMAL_PDF)
    os.makedirs(cfg.pdfs_dir, exist_ok=True)
    dest_pdf_c = os.path.join(cfg.pdfs_dir, UX_PDF_NAME)
    shutil.copy2(str(MINIMAL_PDF), dest_pdf_c)
    work_c = db.add_work(
        title=WORK_C_TITLE,
        text_content="Third UX tour work notes.",
        file_path="/api/pdfs/%s" % UX_PDF_NAME,
        doc_type="book",
        status="Completed",
        publisher=PUBLISHER_NAME,
    )
    db.add_publisher(PUBLISHER_NAME)
    work_d = db.add_work(title=WORK_D_TITLE, doc_type="article", status="Planned")
    ids["work_c"] = work_c
    ids["work_d"] = work_d
    ids["publisher"] = PUBLISHER_NAME

    # --- Several Tags, applied across Works. ---
    tag_ids = {}
    for tag_name in TAG_NAMES:
        tag_ids[tag_name] = db.add_tag(tag_name)["id"]
    db.add_tag_to_work(ids["work_a"], tag_ids[TAG_NAMES[0]])
    db.add_tag_to_work(ids["work_a"], tag_ids[TAG_NAMES[1]])
    db.add_tag_to_work(work_c, tag_ids[TAG_NAMES[2]])
    ids["tags"] = tag_ids

    # --- Playlist containing two Works. ---
    playlist_id = db.add_playlist(PLAYLIST_TITLE, description="Playlist used by the UX tour.")
    db.add_work_to_playlist(playlist_id, ids["work_a"])
    db.add_work_to_playlist(playlist_id, work_c)
    ids["playlist"] = playlist_id

    # --- A second Person with a role, plus a second role for the first Person. ---
    db.add_role(ids["person"], work_c, "Editor")
    person2 = db.add_person(
        first_name=PERSON2_FIRST,
        last_name=PERSON2_LAST,
        about="Second Person fixture for the UX tour.",
    )
    db.add_role(person2, ids["work_b"], "Author")
    ids["person2"] = person2

    # --- Person Group with a subgroup and cross-group memberships. ---
    group_parent = db.add_person_group(GROUP_PARENT_NAME, description="Outer circle for the UX tour.")
    group_child = db.add_person_group(GROUP_CHILD_NAME, parent_id=group_parent, description="Inner circle.")
    db.add_person_to_group(ids["person"], group_parent)
    db.add_person_to_group(person2, group_child)
    ids["group_parent"] = group_parent
    ids["group_child"] = group_child

    # --- Concept parent/child hierarchy (in addition to the graph fixture's
    # Culture/Philosophy mentions already on work_a). ---
    concept_parent = create_concept(db, CONCEPT_PARENT_NAME, "Root concept exercised by the UX tour.")
    concept_child = create_concept(db, CONCEPT_CHILD_NAME, "Child concept exercised by the UX tour.")
    replace_concept_parents(db, concept_child["id"], [concept_parent["id"]])
    ids["concept_parent"] = concept_parent["id"]
    ids["concept_child"] = concept_child["id"]

    # --- A Position with a supporting Stance (a kind='stance' Argument targeting it). ---
    tour_position = create_position(db, TOUR_POSITION_NAME, "Position exercised by the UX tour.")
    tour_stance = create_argument(
        db,
        name=TOUR_STANCE_NAME,
        kind="stance",
        main_text="A stance supporting the UX tour position.",
        targets=[{"type": "position", "id": tour_position["id"], "verdict_id": "holds"}],
    )
    ids["tour_position"] = tour_position["id"]
    ids["tour_stance"] = tour_stance["id"]

    # --- Saved View. ---
    saved_view = db.create_saved_view(
        SAVED_VIEW_NAME,
        {"mode": "all", "q": WORK_A_TITLE, "tag": "", "author": "", "publisher": ""},
    )
    ids["saved_view"] = saved_view["id"]

    ids["work_a_title"] = WORK_A_TITLE
    ids["work_b_title"] = WORK_B_TITLE
    ids["work_c_title"] = WORK_C_TITLE
    ids["work_d_title"] = WORK_D_TITLE
    ids["person_display"] = PERSON_DISPLAY
    ids["person2_display"] = PERSON2_DISPLAY
    return ids
