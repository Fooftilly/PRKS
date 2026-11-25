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

from backend.storage.config import StorageConfig

from tests.e2e.fixtures import (
    MINIMAL_PDF,
    PERSON_DISPLAY,
    WORK_A_TITLE,
    WORK_B_TITLE,
    open_library,
    seed_graph_context_library,
)
from tests.acting_context import current_context

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
    from backend.domain.people import group_commands
    from backend.domain.people import publisher_commands
    from backend.domain.people import role_commands
    from tests import people_fixtures
    from backend.domain.organization import folder_commands
    from backend.domain.organization import playlist_commands
    from backend.domain.organization import tag_commands
    from backend.domain.saved_views import commands as saved_view_commands
    ids = seed_graph_context_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)

    # --- Folder hierarchy: parent + child, each holding one of the two seed Works. ---
    parent_folder = folder_commands.create(db.database, current_context(), "UX Tour Library")
    child_folder = folder_commands.create(db.database, current_context(), "UX Tour Subfolder", '', parent_folder)
    folder_commands.file_work(db.database, current_context(), parent_folder, ids["work_a"])
    folder_commands.file_work(db.database, current_context(), child_folder, ids["work_b"])
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
    publisher_commands.create(db.database, current_context(), PUBLISHER_NAME)
    work_d = db.add_work(title=WORK_D_TITLE, doc_type="article", status="Planned")
    ids["work_c"] = work_c
    ids["work_d"] = work_d
    ids["publisher"] = PUBLISHER_NAME

    # --- Several Tags, applied across Works. ---
    tag_ids = {}
    for tag_name in TAG_NAMES:
        tag_ids[tag_name] = tag_commands.create(db.database, current_context(), tag_name)["id"]
    tag_commands.tag_work(db.database, current_context(), ids["work_a"], tag_ids[TAG_NAMES[0]])
    tag_commands.tag_work(db.database, current_context(), ids["work_a"], tag_ids[TAG_NAMES[1]])
    tag_commands.tag_work(db.database, current_context(), work_c, tag_ids[TAG_NAMES[2]])
    ids["tags"] = tag_ids

    # --- Playlist containing two Works. ---
    playlist_id = playlist_commands.create(db.database, current_context(), PLAYLIST_TITLE, "Playlist used by the UX tour.")
    playlist_commands.add_item(db.database, current_context(), playlist_id, ids["work_a"])
    playlist_commands.add_item(db.database, current_context(), playlist_id, work_c)
    ids["playlist"] = playlist_id

    # --- A second Person with a role, plus a second role for the first Person. ---
    role_commands.add(db.database, current_context(), ids["person"], work_c, "Editor")
    person2 = people_fixtures.add_person(db.database, current_context(), PERSON2_FIRST, PERSON2_LAST, about="Second Person fixture for the UX tour.")
    role_commands.add(db.database, current_context(), person2, ids["work_b"], "Author")
    ids["person2"] = person2

    # --- Person Group with a subgroup and cross-group memberships. ---
    group_parent = group_commands.create(db.database, current_context(), GROUP_PARENT_NAME, None, None, "Outer circle for the UX tour.")
    group_child = group_commands.create(db.database, current_context(), GROUP_CHILD_NAME, group_parent, None, "Inner circle.")
    group_commands.add_member(db.database, current_context(), group_parent, ids["person"])
    group_commands.add_member(db.database, current_context(), group_child, person2)
    ids["group_parent"] = group_parent
    ids["group_child"] = group_child

    # --- Concept parent/child hierarchy (in addition to the graph fixture's
    # Culture/Philosophy mentions already on work_a). ---
    concept_parent = db.create_concept(CONCEPT_PARENT_NAME, "Root concept exercised by the UX tour.")
    concept_child = db.create_concept(CONCEPT_CHILD_NAME, "Child concept exercised by the UX tour.")
    db.replace_concept_parents(concept_child["id"], [concept_parent["id"]])
    ids["concept_parent"] = concept_parent["id"]
    ids["concept_child"] = concept_child["id"]

    # --- A Position with a supporting Stance (a kind='stance' Argument targeting it). ---
    tour_position = db.create_position(TOUR_POSITION_NAME, "Position exercised by the UX tour.")
    tour_stance = db.create_argument(
        name=TOUR_STANCE_NAME,
        kind="stance",
        main_text="A stance supporting the UX tour position.",
        targets=[{"type": "position", "id": tour_position["id"], "verdict_id": "holds"}],
    )
    ids["tour_position"] = tour_position["id"]
    ids["tour_stance"] = tour_stance["id"]

    # --- Saved View. ---
    saved_view = saved_view_commands.create(db.database, current_context(), SAVED_VIEW_NAME, {"mode": "all", "q": WORK_A_TITLE, "tag": "", "author": "", "publisher": ""})
    ids["saved_view"] = saved_view["id"]

    ids["work_a_title"] = WORK_A_TITLE
    ids["work_b_title"] = WORK_B_TITLE
    ids["work_c_title"] = WORK_C_TITLE
    ids["work_d_title"] = WORK_D_TITLE
    ids["person_display"] = PERSON_DISPLAY
    ids["person2_display"] = PERSON2_DISPLAY
    return ids
