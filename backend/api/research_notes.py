"""The Work PATCH's Research Note save (PG-C3 #642).

``PATCH /api/works/:id`` with ``text_content`` calls :func:`save` before
any other field and answers a ``ResearchError`` with its own status,
message and code. The save goes to
``backend/domain/research/note_commands`` for the admitted request's
context on the served Library's database, which opens the transaction, so
authorization and the maintenance and epoch fence stay in
``library_command``. The Research index is the database's: the save
enqueues the reindex in its own transaction.
"""

from __future__ import annotations

from typing import Any

from backend.web.http import Request


def save(req: Request, work_id: str, text_content: Any) -> None:
    """Save the Work's Research Note for this request; refusals raise ``ResearchError``."""
    # Imported here: the served app loads no PostgreSQL module at import time
    # (``tests/test_e2e_policy.py``).
    from backend.domain.research import note_commands

    note_commands.save(*req.actor, work_id, text_content)
