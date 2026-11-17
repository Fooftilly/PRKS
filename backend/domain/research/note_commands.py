"""The Research Note save for one actor: what the Work PATCH calls (PG-C3 #642).

``PATCH /api/works/:id`` with ``text_content`` saves the Work's Research
Note through :func:`save` (``backend/api/research_notes.py``). SQLite's
refusals come first (``research_note_refusal``), then
:func:`backend.domain.works.notes.set_note` in one SERIALIZABLE
:func:`~backend.domain.identity.library_scope.library_command` transaction
needing ``EDIT_LIBRARY`` (a ``work_notes`` row is a peer table of the Work
merge, §7.1). The change enqueues ``research.reindex_work`` in the same
transaction, so there is no index to update afterwards. A save that lost a
Concept key to a concurrent writer on its index runs once more.
"""

from __future__ import annotations

from typing import Any

from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.identity.request_context import RequestContext
from backend.domain.research import concepts
from backend.domain.research.rules import ResearchError, research_note_refusal
from backend.domain.works import notes


def save(db: Database, ctx: RequestContext, work_id: str, text_content: Any) -> None:
    """Replace the Work's Research Note; refusals raise ``ResearchError``."""
    try:
        notes.validate(notes.RESEARCH, text_content)
    except ValueError as exc:
        raise research_note_refusal(text_content) from exc
    wid = (work_id or "").strip()

    def write(scope: LibraryScope) -> None:
        if "\x00" in wid or notes.set_note(scope, wid, notes.RESEARCH, text_content) is None:
            raise ResearchError("not_found", "Work not found.", 404)

    def run() -> None:
        library_command(db, ctx, Capability.EDIT_LIBRARY, write, isolation=Isolation.SERIALIZABLE)

    try:
        run()
    except ConstraintViolationError as error:
        if error.constraint != concepts.KEY_INDEX:
            raise
        run()
