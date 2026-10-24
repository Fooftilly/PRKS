"""A real signed-in actor for facade tests (L-W-1 #628).

The facade acts for the :class:`RequestContext` bound with
:mod:`backend.pg_facade.context`. Tests get one the way a browser does: a
Library, an owner with a membership, a device of that owner and a session
resolved from its token, so every facade call is authorized by the same
``authorize()`` checks a request is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from backend.db.database import Database, Transaction
from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext


@dataclass(frozen=True)
class Actor:
    library_id: str
    user_id: str
    device_id: uuid.UUID
    context: RequestContext


def provision_actor(db: Database, *, name: str = "Library", role: str = "owner") -> Actor:
    """A new Library and a signed-in member of it, with an epoch-1 browser context."""
    library_id = "LIB-" + uuid.uuid4().hex.upper()
    user_id = "U-" + uuid.uuid4().hex.upper()
    device_id = uuid.uuid4()

    def run(tx: Transaction) -> sessions.NewSession:
        tx.execute("INSERT INTO prks.libraries (id, name) VALUES (%s, %s)", (library_id, name))
        tx.execute(
            "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, %s, %s)",
            (user_id, user_id, user_id),
        )
        tx.execute(
            "INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, %s)",
            (library_id, user_id, role),
        )
        tx.execute("INSERT INTO prks.devices (id, user_id) VALUES (%s, %s)", (device_id, user_id))
        return sessions.create_session(tx, user_id=user_id, library_id=library_id)

    new = db.command(run)
    valid = sessions.resolve_session(db, new.token.reveal())
    if valid is None:  # pragma: no cover - the session was just created
        raise RuntimeError("the provisioned session does not resolve")
    ctx = RequestContext.for_session(valid, epoch=1, device_id=device_id)
    return Actor(library_id, user_id, device_id, ctx)
