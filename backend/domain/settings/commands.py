"""The application settings for one actor: what ``backend/api/settings.py`` calls (PG-C3 #642).

A read runs in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot (``READ_LIBRARY``); a patch is one
:func:`~backend.domain.identity.library_scope.library_command` transaction
needing ``ADMIN_LIBRARY`` (§9.4), so both are authorized and fenced by
maintenance and the epoch. Refusals are ``ValueError`` (a refused key) and
``AccessRefused``.
"""

from __future__ import annotations

from typing import Any

from backend.db.database import Database
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.settings import app_settings


def response(db: Database, ctx: RequestContext) -> dict[str, Any]:
    """The ``/api/settings`` body: ``annotation_author`` and the BibTeX export profile."""
    return library_query(db, ctx, app_settings.settings_response)


def patch(db: Database, ctx: RequestContext, data: Any) -> None:
    """Apply a settings patch, checked in full before it writes (``app_settings.patch``)."""
    library_command(db, ctx, Capability.ADMIN_LIBRARY, lambda scope: app_settings.patch(scope, data))
