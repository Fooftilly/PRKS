"""A Work's source identity on PostgreSQL: one aggregate conflict unit (L-W-1 #628).

``backend/work_source_sync.py``'s rules, imported rather than copied: the
payload carries intent (``{"kind": "video", "url": …}``), ``provider`` and
``provider_id`` are derived by the one canonical parser, identity is
provider plus id (three spellings of one video are one source), and the
scope is ``work-source / ["W-…"]``.

The source is the primary Manifestation's primary Asset: a video is an
``external_stream`` Asset, a PDF a ``managed_file`` one. Only an existing
video's source may be replaced (``UNSUPPORTED_SOURCE_TRANSITION`` otherwise,
as on SQLite). Replacing it rewrites the stream's URL and identity, clears
its thumbnail (the previous video's image) and sets the Manifestation's
``urldate`` to today's UTC date, in one transaction with the revision.
There are no legacy rows to repair and no inferred videos here.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend import work_metadata_sync as meta
from backend import work_source_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import metadata, revisions

SCOPE_TYPE = rules.SCOPE_TYPE
INVALID_SOURCE_STATE = rules.INVALID_SOURCE_STATE

_SET_STREAM: LiteralString = """
UPDATE prks.assets SET provider = %(provider)s, provider_id = %(provider_id)s, url = %(url)s,
       thumb_url = NULL, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""
_SET_URLDATE: LiteralString = """
UPDATE prks.manifestations
SET urldate = to_char((now() AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD'), updated_at = now()
WHERE library_id = %(library_id)s AND id = %(manifestation_id)s
RETURNING urldate
"""
_TOUCH_WORK: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)


def current_source(row: dict[str, Any]) -> dict[str, Any] | str:
    """``work_source_sync.current_source`` for a :func:`metadata.target` row."""
    kind = row["asset_kind"]
    if kind == "external_stream":
        source = {
            "source_kind": "video",
            "provider": (row["provider"] or "").strip().lower(),
            "provider_id": (row["provider_id"] or "").strip(),
            "source_url": row["stream_url"] or "",
        }
        if source["provider"] == "youtube" and rules.is_provider_id(source["provider_id"]):
            return source
        return INVALID_SOURCE_STATE
    return {
        "source_kind": "pdf" if kind == "managed_file" else "",
        "provider": "",
        "provider_id": "",
        "source_url": row["url"] or "",
    }


def stored_source(row: dict[str, Any]) -> dict[str, Any]:
    """``work_source_sync.stored_source``: what an acknowledgement reports."""
    stream = row["asset_kind"] == "external_stream"
    return {
        "source_kind": "video" if stream else "pdf" if row["asset_kind"] == "managed_file" else "",
        "provider": (row["provider"] or "").strip().lower() if stream else "",
        "provider_id": (row["provider_id"] or "").strip() if stream else "",
        "source_url": (row["stream_url"] if stream else row["url"]) or "",
        "thumb_url": row["thumb_url"] if row["asset_id"] is not None else None,
        "urldate": row["urldate"],
    }


def _target(scope: LibraryScope, work_id: str, *, for_update: bool) -> Optional[dict[str, Any]]:
    return metadata.target(scope, work_id, for_update=for_update)


def set_source(scope: LibraryScope, row: dict[str, Any], source: dict[str, Any]) -> tuple[bool, int]:
    """``set_source_on_conn``: replace a video's identity; ``(changed, revision_after)``."""
    key = rules.scope_key(row["id"])
    revision = revisions.lock(scope, SCOPE_TYPE, key)
    existing = current_source(row)
    if not isinstance(existing, dict) or existing["source_kind"] != "video":
        return False, revision
    if rules.identity_of(existing) == rules.identity_of(source):
        return False, revision
    params = {"asset_id": row["asset_id"], "manifestation_id": row["manifestation_id"],
              "work_id": row["id"], "provider": source["provider"],
              "provider_id": source["provider_id"], "url": source["source_url"]}
    scope.execute(_SET_STREAM, params)
    dated = scope.execute(_SET_URLDATE, params).fetchone()
    scope.execute(_TOUCH_WORK, params)
    row.update(provider=source["provider"], provider_id=source["provider_id"],
               stream_url=source["source_url"], thumb_url=None,
               urldate=None if dated is None else dated["urldate"])
    return True, revisions.advance(scope, SCOPE_TYPE, key)


def apply_source_operation(scope: LibraryScope, work_id: str, payload_source: object,
                           base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_WORK_SOURCE``: the SQLite handler's decision table, result for result."""
    desired = rules.canonical_source(payload_source)
    if desired is None:
        raise ValueError("INVALID_ENVELOPE")
    result: dict[str, Any] = {"work_id": work_id}
    row = _target(scope, work_id, for_update=True)
    if row is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    existing = current_source(row)
    if not isinstance(existing, dict):
        result["code"] = INVALID_SOURCE_STATE
        return 409, result
    if existing["source_kind"] != "video":
        result["code"] = "UNSUPPORTED_SOURCE_TRANSITION"
        return 409, result
    revision = revisions.lock(scope, SCOPE_TYPE, rules.scope_key(work_id))
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **rules.disagreement(existing, desired))
        return 400, meta.fit_terminal_result(result, existing["source_url"], desired["source_url"])
    if base_revision < revision and rules.identity_of(existing) != rules.identity_of(desired):
        result.update(code="SOURCE_REVISION_CONFLICT", current_revision=revision,
                      **rules.disagreement(existing, desired))
        return 409, meta.fit_terminal_result(result, existing["source_url"], desired["source_url"])
    changed, after = set_source(scope, row, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, **stored_source(row))
    return 200, result


def source_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any] | str]:
    """``work_source_sync.get_source_state``: the revision only, or the refusal."""
    row = _target(scope, work_id, for_update=False)
    if row is None:
        return None
    if current_source(row) == INVALID_SOURCE_STATE:
        return INVALID_SOURCE_STATE
    key = rules.scope_key(work_id)
    return {"work_id": work_id, "revision": revisions.read(scope, SCOPE_TYPE, [key])[key]}
