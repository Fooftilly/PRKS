"""The Library's application settings: read and patch (L-O-1 #633).

The rules are ``backend/db_manager.py``'s, imported rather than copied: the
stored BibTeX export profile is parsed by ``_prks_parse_bibtex_export_fields_json``
(missing or invalid → every field on; unknown keys ignored), a patch of it
is merged by ``_prks_merge_bibtex_export_fields_patch`` (a partial patch keeps
the other fields; an unknown field or a non-boolean is refused), and a value
is at most ``PRKSDatabase._PRKS_APP_SETTING_MAX_LEN`` characters. Top-level
keys the API does not know are ignored, as SQLite does.

What moves:

- **Atomic.** A patch is one transaction and is checked in full before it
  writes: a refused ``bibtex_export_fields`` no longer leaves a new
  ``annotation_author`` behind (SQLite commits each key on its own).
- **No lost update.** A BibTeX patch merges into the stored profile under
  that row's lock: the row is created first if missing (``ON CONFLICT DO
  NOTHING``, which waits for a concurrent creator), then read ``FOR UPDATE``,
  so two patches of different fields both land whichever commits first.
- **Library administration.** A patch needs ``ADMIN_LIBRARY`` (the facade
  passes it; ``/api/settings`` already admits only that), a read
  ``READ_LIBRARY``. Each Library has its own settings.
- **NUL** in ``annotation_author`` is refused (PostgreSQL text cannot hold it).
"""

from __future__ import annotations

import json
from typing import Any, LiteralString

from backend.db_manager import (
    PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT,
    PRKSDatabase,
    _prks_merge_bibtex_export_fields_patch,
    _prks_parse_bibtex_export_fields_json,
)
from backend.domain.identity.library_scope import LibraryScope

ANNOTATION_AUTHOR = "annotation_author"
BIBTEX_EXPORT_FIELDS = "bibtex_export_fields"
MAX_LEN = PRKSDatabase._PRKS_APP_SETTING_MAX_LEN

_MAP_SQL: LiteralString = "SELECT key, value FROM prks.library_settings WHERE library_id = %(library_id)s"
_UPSERT_SQL: LiteralString = """
INSERT INTO prks.library_settings (library_id, key, value) VALUES (%(library_id)s, %(key)s, %(value)s)
ON CONFLICT (library_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""
_ENSURE_SQL: LiteralString = """
INSERT INTO prks.library_settings (library_id, key) VALUES (%(library_id)s, %(key)s)
ON CONFLICT (library_id, key) DO NOTHING
"""
_LOCK_SQL: LiteralString = """
SELECT value FROM prks.library_settings WHERE library_id = %(library_id)s AND key = %(key)s FOR UPDATE
"""
_SET_SQL: LiteralString = """
UPDATE prks.library_settings SET value = %(value)s, updated_at = now()
WHERE library_id = %(library_id)s AND key = %(key)s
"""


def settings_map(scope: LibraryScope) -> dict[str, str]:
    """``get_app_settings_map``: every stored key of this Library, as text."""
    return {str(row["key"]): str(row["value"] or "") for row in scope.execute(_MAP_SQL).fetchall()}


def settings_response(scope: LibraryScope) -> dict[str, Any]:
    """``get_app_settings_response``: the two keys the API has, with their defaults."""
    stored = settings_map(scope)
    return {
        ANNOTATION_AUTHOR: stored.get(ANNOTATION_AUTHOR, ""),
        BIBTEX_EXPORT_FIELDS: _prks_parse_bibtex_export_fields_json(stored.get(BIBTEX_EXPORT_FIELDS, "")),
    }


def _author(raw: Any) -> str:
    value = "" if raw is None else str(raw).strip()
    if len(value) > MAX_LEN:
        raise ValueError("annotation_author too long")
    if "\x00" in value:
        raise ValueError("annotation_author cannot contain NUL")
    return value


def _profile_patch(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("bibtex_export_fields must be an object")
    # Refuses an unknown field or a non-boolean, with SQLite's message, before any write.
    _prks_merge_bibtex_export_fields_patch(dict(PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT), raw)
    return raw


def patch(scope: LibraryScope, data: Any) -> None:
    """``patch_app_settings``: SQLite's checks, in its order, then every write in this transaction."""
    if not isinstance(data, dict):
        raise ValueError("invalid body")
    author = _author(data.get(ANNOTATION_AUTHOR)) if ANNOTATION_AUTHOR in data else None
    profile = _profile_patch(data.get(BIBTEX_EXPORT_FIELDS)) if BIBTEX_EXPORT_FIELDS in data else None
    if author is not None:
        scope.execute(_UPSERT_SQL, {"key": ANNOTATION_AUTHOR, "value": author})
    if profile is not None:
        _merge_profile(scope, profile)


def _merge_profile(scope: LibraryScope, profile: dict[str, Any]) -> None:
    key = {"key": BIBTEX_EXPORT_FIELDS}
    scope.execute(_ENSURE_SQL, key)
    row = scope.execute(_LOCK_SQL, key).fetchone()
    current = _prks_parse_bibtex_export_fields_json("" if row is None else str(row["value"] or ""))
    merged = _prks_merge_bibtex_export_fields_patch(current, profile)
    blob = json.dumps(merged, separators=(",", ":"), sort_keys=True)
    if len(blob) > MAX_LEN:  # pragma: no cover - eighteen booleans fit
        raise ValueError("bibtex_export_fields too long")
    scope.execute(_SET_SQL, {**key, "value": blob})
