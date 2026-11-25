"""Deterministic library seed for real-app E2E. Uses public PRKS domain methods."""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from backend.storage.config import StorageConfig
from tests.acting_context import current_context

REPO = Path(__file__).resolve().parents[2]
MINIMAL_PDF = REPO / "tests" / "browser" / "assets" / "minimal.pdf"

WORK_A_TITLE = "E2E Research Work"
WORK_B_TITLE = "E2E Related Work"
PERSON_FIRST = "E2E"
PERSON_LAST = "Author"
PERSON_DISPLAY = "E2E Author"
PDF_NAME = "e2e-research.pdf"


# The PostgreSQL Library a seed writes to, bound by ``seeding_into`` for one
# seed (tests/e2e/postgres_identity.py).
_POSTGRES_LIBRARY = None


@contextmanager
def seeding_into(library):
    """Run the seeds inside this block against ``library`` (a bound ``tests.library_adapter.TestLibrary``)."""
    global _POSTGRES_LIBRARY
    previous, _POSTGRES_LIBRARY = _POSTGRES_LIBRARY, library
    try:
        yield
    finally:
        _POSTGRES_LIBRARY = previous


def open_library(cfg: StorageConfig):
    """The Library a seed writes: the PostgreSQL facade ``seeding_into`` bound.

    ``cfg`` names the seed's storage root; the facade already serves it. A
    seed run outside ``seeding_into`` has no Library and is refused.
    """
    if _POSTGRES_LIBRARY is None:
        raise RuntimeError("E2E seeds run inside seeding_into(library)")
    return _POSTGRES_LIBRARY


def _sync_research(cfg: StorageConfig, work_id: str, notes: str, db) -> None:
    """Index a seeded Research Note before any test reads it.

    The note save queued ``research.reindex_work``, which the server's
    worker runs at some point after start; a test that opened the Research Graph or a mention count
    before then saw no mentions (a Work is a graph node only through them).
    So the seed runs that job's transaction itself, as the Library's owner:
    the Library a test opens already holds the note's mentions, and the
    queued job later rebuilds the same rows.
    """
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command
    from backend.domain.research import reindex
    from tests.acting_context import current_context

    library_command(db._database, current_context(), Capability.EDIT_LIBRARY,
                    lambda scope: reindex.reindex_work(scope, work_id))


def _managed_pdf(cfg: StorageConfig, name: str) -> str:
    """Copy the fixture PDF into the root as ``name``; the Work's create adopts it."""
    shutil.copy2(str(MINIMAL_PDF), os.path.join(cfg.pdfs_dir, name))
    return "/api/pdfs/%s" % name


def seed_library(storage_root: str, *, prepare_related_pdf=None) -> dict:
    """Create Works, a managed PDF, and an Author role. Returns generated IDs.

    ``prepare_related_pdf(path)`` changes Work B's PDF before any Work names
    it: on PostgreSQL the Work's create adopts the bytes as an immutable
    Asset, so they must be final by then.
    """
    from backend.domain.research import note_commands
    from backend.domain.people import role_commands
    from tests import people_fixtures
    if not MINIMAL_PDF.is_file():
        raise RuntimeError("missing fixture PDF: %s" % MINIMAL_PDF)
    cfg = StorageConfig.for_testing(storage_root)
    os.makedirs(cfg.pdfs_dir, exist_ok=True)
    os.makedirs(cfg.thumbs_dir, exist_ok=True)
    dest_pdf = os.path.join(cfg.pdfs_dir, PDF_NAME)
    shutil.copy2(str(MINIMAL_PDF), dest_pdf)
    dest_pdf_b = os.path.join(cfg.pdfs_dir, "e2e-related.pdf")
    shutil.copy2(str(MINIMAL_PDF), dest_pdf_b)
    if prepare_related_pdf is not None:
        prepare_related_pdf(dest_pdf_b)
    db = open_library(cfg)
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
    person_id = people_fixtures.add_person(db.database, current_context(), PERSON_FIRST, PERSON_LAST, about="E2E fixture author used for People profile and graph focus.", link_wikipedia="https://example.com/wiki/e2e-author", birth_date="1903", death_date="1969")
    role_commands.add(db.database, current_context(), person_id, work_a, "Author")
    # Work nodes appear in the graph via explicit note markup / argument sources.
    seed_notes = "Initial research notes.\n\n[[concept:E2E Fixture Concept]]"
    note_commands.save(db.database, current_context(), work_a, seed_notes)
    _sync_research(cfg, work_a, seed_notes, db)
    # Each managed PDF's create queued its text.extract job, which the
    # server's embedded worker runs (backend/domain/search/text.py).
    return {
        "work_a": work_a,
        "work_b": work_b,
        "person": person_id,
        "pdf_name": PDF_NAME,
        "work_a_title": WORK_A_TITLE,
        "work_b_title": WORK_B_TITLE,
        "person_display": PERSON_DISPLAY,
    }


PEOPLE_WORK_TITLE = "E2E People Work"
PEOPLE_CREDITED_TITLE = "E2E Credited Work"
PEOPLE_EDITOR_TITLE = "E2E Editor Fallback Work"
JANE_DISPLAY = "Jane Doe"
ED_DISPLAY = "Ed Smith"


def seed_work_people_library(storage_root: str) -> dict:
    """Three Works chosen for the credit precedence rule, plus two Persons.

    Each Work isolates one branch of `Authors -> author_text -> Editor`, so a
    test about one branch cannot pass because another happened to supply the
    same string.
    """
    from tests import people_fixtures
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    jane = people_fixtures.add_person(db.database, current_context(), "Jane", "Doe")
    ed = people_fixtures.add_person(db.database, current_context(), "Ed", "Smith")

    # No linked Author: `author_text` is what the credit shows today.
    people_work = db.add_work(
        title=PEOPLE_WORK_TITLE, author_text="Text Author", doc_type="article",
        file_path=_managed_pdf(cfg, "e2e-people.pdf"))
    # A linked Author AND `author_text`, so removing the Author must reveal it.
    credited = db.add_work(
        title=PEOPLE_CREDITED_TITLE, author_text="Text Author", doc_type="article",
        file_path=_managed_pdf(cfg, "e2e-credited.pdf"))
    people_fixtures.insert_initial_role(db.database, current_context(), credited, jane, "Author", order_index=0)
    # An Author and an Editor with NO `author_text`, so removing the Author
    # must fall through to the Editor rather than to an empty credit.
    editor_work = db.add_work(
        title=PEOPLE_EDITOR_TITLE, doc_type="article",
        file_path=_managed_pdf(cfg, "e2e-editor.pdf"))
    people_fixtures.insert_initial_role(db.database, current_context(), editor_work, jane, "Author", order_index=0)
    people_fixtures.insert_initial_role(db.database, current_context(), editor_work, ed, "Editor", order_index=1)

    ids.update({
        "jane": jane, "ed": ed, "people_work": people_work,
        "credited_work": credited, "editor_work": editor_work,
    })
    return ids


def seed_person_profile_draft_library(storage_root: str) -> dict:
    """Two independently editable Persons and four deterministic Groups."""
    from backend.domain.people import group_commands
    from tests import people_fixtures
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    person_a = people_fixtures.add_person(db.database, current_context(), "Ada", "Alpha", aliases="A. Alpha", about="Saved biography A", birth_date="1972-03-12")
    person_b = people_fixtures.add_person(db.database, current_context(), "Bea", "Beta", aliases="B. Beta", about="Saved biography B", birth_date="1980")
    group_alpha = group_commands.create(db.database, current_context(), "Group Alpha")
    group_beta = group_commands.create(db.database, current_context(), "Group Beta")
    group_gamma = group_commands.create(db.database, current_context(), "Group Gamma")
    group_delta = group_commands.create(db.database, current_context(), "Group Delta")
    group_commands.add_member(db.database, current_context(), group_alpha, person_a)
    group_commands.add_member(db.database, current_context(), group_beta, person_b)
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


IMPORTED_HIGHLIGHT_COMMENT = "Imported highlight comment"


def _add_imported_highlight(pdf_path: str) -> None:
    """Write a highlight made "elsewhere" into the PDF's bytes."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        page = doc[0]
        quads = page.search_for("Hello page one", quads=True)
        if not quads:
            raise RuntimeError("fixture text missing from minimal.pdf")
        annot = page.add_highlight_annot(quads)
        annot.set_info(content=IMPORTED_HIGHLIGHT_COMMENT)
        annot.update()
        doc.saveIncr()
    finally:
        doc.close()


def seed_imported_highlight_library(storage_root: str) -> dict:
    """The default library, but Work B's PDF carries a highlight made elsewhere.

    The highlight lives only in the PDF bytes, the way a file annotated in
    another reader arrives. Tests that need it to have no canonical row
    until the user edits it stop the open-time adoption from finding it.
    """
    ids = seed_library(storage_root, prepare_related_pdf=_add_imported_highlight)
    ids["imported_highlight_comment"] = IMPORTED_HIGHLIGHT_COMMENT
    return ids


def seed_graph_context_library(storage_root: str) -> dict:
    from backend.domain.research import argument_commands
    from backend.domain.research import note_commands
    from backend.domain.research import position_commands
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    notes = "[[concept:%s]]\n\n[[concept:%s]]\n" % (GRAPH_CONCEPT_A, GRAPH_CONCEPT_B)
    note_commands.save(db.database, current_context(), ids["work_a"], notes)
    _sync_research(cfg, ids["work_a"], notes, db)
    pos = position_commands.create(db.database, current_context(), GRAPH_UNRELATED_POSITION)
    arg = argument_commands.create(db.database, current_context(), name=GRAPH_UNRELATED_ARGUMENT, kind="argument")
    # Deliberately separate from GRAPH_UNRELATED_ARGUMENT and sourced to work_b (not work_a):
    # this gives tests a deterministic argument_source edge (e.g. relation-filter selection
    # regressions) without perturbing edge counts incident to work_a, which other graph tests
    # (e.g. the node-selection dimming test) assert exactly.
    sourced_arg = argument_commands.create(db.database, current_context(), name=GRAPH_SOURCED_ARGUMENT, kind="argument", sources=[{"work_id": ids["work_b"], "pages": "1-2"}])
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
    from backend.domain.research import concept_commands
    from backend.domain.research import note_commands
    ids = seed_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    parent = concept_commands.create(db.database, current_context(), CONCEPT_PARENT_NAME, "Parent concept definition.")
    child = concept_commands.create(db.database, current_context(), CONCEPT_CHILD_NAME, CONCEPT_CHILD_DEFINITION)
    unvisited = concept_commands.create(db.database, current_context(), CONCEPT_UNVISITED_NAME, "Never opened while online.")
    concept_commands.replace_parents(db.database, current_context(), child["id"], [parent["id"]])
    concept_commands.replace_aliases(db.database, current_context(), child["id"], [CONCEPT_CHILD_ALIAS])
    notes = "Initial research notes.\n\n[[concept:%s]]\n" % CONCEPT_CHILD_NAME
    note_commands.save(db.database, current_context(), ids["work_a"], notes)
    _sync_research(cfg, ids["work_a"], notes, db)
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
    from backend.domain.research import argument_commands
    from backend.domain.research import position_commands
    ids = seed_concepts_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    position_a = position_commands.create(db.database, current_context(), POSITION_A_NAME, POSITION_A_DESCRIPTION)
    position_b = position_commands.create(db.database, current_context(), POSITION_B_NAME, "Never opened while online.")
    argument = argument_commands.create(db.database, current_context(), name=POSITION_ARGUMENT_NAME, kind="argument", targets=[{"type": "position", "id": position_a["id"], "verdict_id": "supports"}])
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
    from backend.domain.research import argument_commands
    from backend.domain.research import note_commands
    ids = seed_positions_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    target = argument_commands.create(db.database, current_context(), name=ARGUMENT_B_NAME, kind="argument")
    stance = argument_commands.create(db.database, current_context(), name=STANCE_NAME, kind="stance", main_text=STANCE_TEXT, targets=[{"type": "position", "id": ids["position_a"], "verdict_id": "holds"}])
    unvisited = argument_commands.create(db.database, current_context(), name=ARGUMENT_UNVISITED_NAME, kind="argument")
    argument_a = argument_commands.create(db.database, current_context(), name=ARGUMENT_A_NAME, kind="argument", main_text=ARGUMENT_A_TEXT, sources=[{"work_id": ids["work_a"], "pages": ARGUMENT_SOURCE_PAGES}], targets=[
            {"type": "position", "id": ids["position_a"], "verdict_id": "supports"},
            {"type": "argument", "id": target["id"], "verdict_id": "opposes"},
        ])
    response = argument_commands.create(db.database, current_context(), name=ARGUMENT_C_NAME, kind="argument", targets=[{"type": "argument", "id": argument_a["id"], "verdict_id": "opposes"}])
    # Work B's research notes mention Argument A, giving it a mention backlink.
    notes = "Related notes.\n\n[[argument:%s|%s]]\n" % (argument_a["id"], ARGUMENT_A_NAME)
    note_commands.save(db.database, current_context(), ids["work_b"], notes)
    _sync_research(cfg, ids["work_b"], notes, db)
    ids.update(
        {
            "argument_a": argument_a["id"],
            "argument_target": target["id"],
            "argument_response": response["id"],
            "argument_unvisited": unvisited["id"],
            "stance": stance["id"],
            "argument_a_name": ARGUMENT_A_NAME,
            "stance_name": STANCE_NAME,
        }
    )
    return ids


PERSON_B_FIRST = "Bea"
PERSON_B_LAST = "Reviewer"
PERSON_B_DISPLAY = "Bea Reviewer"
PERSON_UNVISITED_FIRST = "Uma"
PERSON_UNVISITED_LAST = "Unvisited"
PERSON_UNVISITED_DISPLAY = "Uma Unvisited"
PERSON_A_ABOUT = "Biography text used by offline Person detail assertions."
PERSON_A_ALIASES = "E. Author"
PERSON_GROUP_NAME = "E2E Person Group"
PERSON_A_BIRTH = "1903"
PERSON_A_DEATH = "1969"


def seed_people_library(storage_root: str) -> dict:
    """seed_arguments_library plus a richer People shape.

    Person A keeps the seeded Author role on Work A and gains a biography,
    aliases, a lifespan and a Group membership; Person B carries a *non-Author*
    role so the People-vs-Arguments coherence boundary is observable; Person C is
    deliberately never opened so "cached index, uncached detail" is testable.
    """
    from backend.domain.people import group_commands
    from backend.domain.people import person_commands
    from backend.domain.people import role_commands
    from tests import people_fixtures
    ids = seed_arguments_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    person_commands.update_profile(db.database, current_context(), ids["person"], {
            "about": PERSON_A_ABOUT,
            "aliases": PERSON_A_ALIASES,
            "birth_date": PERSON_A_BIRTH,
            "death_date": PERSON_A_DEATH,
            "link_wikipedia": "https://example.com/wiki/e2e-author",
        })
    group_id = group_commands.create(db.database, current_context(), PERSON_GROUP_NAME)
    group_commands.add_member(db.database, current_context(), group_id, ids["person"])
    person_b = people_fixtures.add_person(db.database, current_context(), PERSON_B_FIRST, PERSON_B_LAST)
    role_commands.add(db.database, current_context(), person_b, ids["work_b"], "Reviewer")
    person_unvisited = people_fixtures.add_person(db.database, current_context(), PERSON_UNVISITED_FIRST, PERSON_UNVISITED_LAST)
    ids.update(
        {
            "person_a": ids["person"],
            "person_b": person_b,
            "person_unvisited": person_unvisited,
            "person_group": group_id,
            "person_b_display": PERSON_B_DISPLAY,
            "person_unvisited_display": PERSON_UNVISITED_DISPLAY,
        }
    )
    return ids


PLAYLIST_A_TITLE = "E2E Cached Playlist"
PLAYLIST_A_DESCRIPTION = "Ordered lecture series used by the offline Playlist tests."
PLAYLIST_A_URL = "https://example.com/playlist/e2e"
PLAYLIST_B_TITLE = "E2E Unvisited Playlist"
PLAYLIST_VIDEO_ONE_TITLE = "E2E Playlist Video One"
PLAYLIST_VIDEO_TWO_TITLE = "E2E Playlist Video Two"
PLAYLIST_CHANNEL = "E2E Lecture Channel"
PLAYLIST_VIDEO_ONE_DATE = "2021-03-04"


def seed_playlists_library(storage_root: str) -> dict:
    """seed_people_library plus an ordered Playlist and an unvisited one.

    Playlist A holds two videos in a deliberate order with channel/date
    subtitles, so cached ordering and the rendered item fields are both
    observable offline. Playlist B is never opened online so "cached index,
    uncached detail" is testable, and `work_a` stays outside every Playlist so
    the member-vs-non-member coherence boundary can be asserted.
    """
    from backend.domain.organization import playlist_commands
    ids = seed_people_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    # `provider_id` is written here because Work creation writes it: the server
    # derives it from the URL before the row exists, and the audit in
    # docs/work-source-identity.md found it populated on every video Work in
    # the real library. A fixture that left it blank would be testing a row
    # shape the product cannot produce -- and video IDENTITY is
    # provider + provider_id, so tests of it would be testing nothing.
    video_one = db.add_work(
        title=PLAYLIST_VIDEO_ONE_TITLE,
        doc_type="online",
        source_kind="video",
        source_url="https://www.youtube.com/watch?v=e2e0000001",
        provider="youtube",
        provider_id="e2e0000001",
        author_text=PLAYLIST_CHANNEL,
        published_date=PLAYLIST_VIDEO_ONE_DATE,
    )
    video_two = db.add_work(
        title=PLAYLIST_VIDEO_TWO_TITLE,
        doc_type="online",
        source_kind="video",
        source_url="https://www.youtube.com/watch?v=e2e0000002",
        provider="youtube",
        provider_id="e2e0000002",
        author_text=PLAYLIST_CHANNEL,
    )
    playlist_a = playlist_commands.create(db.database, current_context(), PLAYLIST_A_TITLE, PLAYLIST_A_DESCRIPTION, PLAYLIST_A_URL)
    playlist_b = playlist_commands.create(db.database, current_context(), PLAYLIST_B_TITLE, "Never opened while online.")
    playlist_commands.add_item(db.database, current_context(), playlist_a, video_one)
    playlist_commands.add_item(db.database, current_context(), playlist_a, video_two)
    ids.update(
        {
            "playlist_a": playlist_a,
            "playlist_b": playlist_b,
            "playlist_video_one": video_one,
            "playlist_video_two": video_two,
        }
    )
    return ids


FOLDER_PARENT_TITLE = "E2E Parent Folder"
FOLDER_CHILD_TITLE = "E2E Child Folder"
FOLDER_UNVISITED_TITLE = "E2E Unvisited Folder"
FOLDER_PARENT_DESCRIPTION = "Parent folder used for cached hierarchy assertions."
FOLDER_TAG_NAME = "E2E Folder Tag"


def seed_folders_library(storage_root: str) -> dict:
    """seed_playlists_library plus a real Folder hierarchy.

    The parent holds `work_a` (a PDF Work with an Author role, a status and a
    doc type, so a cached Folder detail's Work card has every field the card
    renderer reads) and has one child folder, so parent<->child navigation is
    observable offline. The child holds `work_b`. A third folder is never
    opened online, giving the "cached index, uncached detail" case. The parent
    also carries a tag so the right-panel tag list is part of the cached
    payload.
    """
    from backend.domain.organization import folder_commands
    from backend.domain.organization import tag_commands
    ids = seed_playlists_library(storage_root)
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    parent = folder_commands.create(db.database, current_context(), FOLDER_PARENT_TITLE, FOLDER_PARENT_DESCRIPTION, None)
    child = folder_commands.create(db.database, current_context(), FOLDER_CHILD_TITLE, "Child folder.", parent)
    unvisited = folder_commands.create(db.database, current_context(), FOLDER_UNVISITED_TITLE, "Never opened while online.", None)
    folder_commands.file_work(db.database, current_context(), parent, ids["work_a"])
    folder_commands.file_work(db.database, current_context(), child, ids["work_b"])
    tag_id = tag_commands.create(db.database, current_context(), FOLDER_TAG_NAME, "#556677")["id"]
    tag_commands.tag_folder(db.database, current_context(), parent, tag_id)
    ids.update(
        {
            "folder_parent": parent,
            "folder_child": child,
            "folder_unvisited": unvisited,
            "folder_tag": tag_id,
        }
    )
    return ids


LIBRARY_NAV_RESEARCH = "Research"
LIBRARY_NAV_PHILOSOPHY = "Philosophy"
LIBRARY_NAV_ETHICS = "Ethics"
LIBRARY_NAV_EPISTEMOLOGY = "Epistemology"
LIBRARY_NAV_COMPUTING = "Computing"
LIBRARY_NAV_AI = "AI"
LIBRARY_NAV_NETWORKING = "Networking"


def seed_library_nav_folders(storage_root: str) -> dict:
    """Multi-level Folder tree for Library Navigation V1 E2E.

    Research
    ├── Philosophy
    │   ├── Ethics
    │   └── Epistemology
    └── Computing
        ├── AI
        └── Networking
    """
    from backend.domain.organization import folder_commands
    cfg = StorageConfig.for_testing(storage_root)
    db = open_library(cfg)
    research = folder_commands.create(db.database, current_context(), LIBRARY_NAV_RESEARCH, "Library nav root.", None)
    philosophy = folder_commands.create(db.database, current_context(), LIBRARY_NAV_PHILOSOPHY, "Philosophy branch.", research)
    ethics = folder_commands.create(db.database, current_context(), LIBRARY_NAV_ETHICS, "Ethics leaf.", philosophy)
    epistemology = folder_commands.create(db.database, current_context(), LIBRARY_NAV_EPISTEMOLOGY, "Epistemology leaf.", philosophy)
    computing = folder_commands.create(db.database, current_context(), LIBRARY_NAV_COMPUTING, "Computing branch.", research)
    ai = folder_commands.create(db.database, current_context(), LIBRARY_NAV_AI, "AI leaf.", computing)
    networking = folder_commands.create(db.database, current_context(), LIBRARY_NAV_NETWORKING, "Networking leaf.", computing)
    return {
        "research": research,
        "philosophy": philosophy,
        "ethics": ethics,
        "epistemology": epistemology,
        "computing": computing,
        "ai": ai,
        "networking": networking,
    }

