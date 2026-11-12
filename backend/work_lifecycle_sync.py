"""Work lifecycle: CREATE_WORK (video) and DELETE_WORK destruction.

Construction is video / YouTube only. PDF binary ingestion stays intentionally
online-only until a durable Blob design exists. Creation shares
`_canonical_new_source` / `canonical_source` with ordinary `POST /api/works` and
`SET_WORK_SOURCE`, so identity cannot diverge across paths.

Deletion is a null-base destruction operation: cascade like the ordinary
DELETE, absence is convergence, filesystem and derived-index cleanup stay
post-commit best-effort via `backend.work_deletion`.
"""
from __future__ import annotations


from backend.domain.works.rules import normalize_doc_type
from backend.entity_ids import is_distributed
from backend import work_metadata_sync
from backend import work_role_sync
from backend import work_source_sync


# Construction payload: exact keys. Source is INTENT form {kind, url}; provider
# columns are derived inside the mutation boundary, never asserted by the client.
CREATE_FIELDS = (
    "title",
    "status",
    "doc_type",
    "abstract",
    "author_text",
    "year",
    "published_date",
    "urldate",
    "private_notes",
    "thumb_url",
    "source",
    "folder_id",
    "playlist_id",
    "roles",
)
CREATE_FIELD_SET = frozenset(CREATE_FIELDS)

# Scalar fields stored as strings (empty allowed).
_STRING_FIELDS = (
    "title",
    "status",
    "doc_type",
    "abstract",
    "author_text",
    "year",
    "published_date",
    "urldate",
    "private_notes",
    "thumb_url",
    "folder_id",
    "playlist_id",
)


def _string_field(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("INVALID_ENVELOPE")
    return value


def canonical_create_payload(payload):
    """Validate and normalize a CREATE_WORK payload, or raise INVALID_ENVELOPE."""
    if not isinstance(payload, dict) or set(payload) != CREATE_FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    out = {}
    for name in _STRING_FIELDS:
        out[name] = _string_field(payload[name])
    source = work_source_sync.canonical_source(payload["source"])
    if source is None:
        raise ValueError("INVALID_ENVELOPE")
    out["source"] = {
        "kind": "video",
        "url": source["source_url"],
    }
    # Persist-ready identity columns for insert (derived, not client-asserted).
    out["_identity"] = source
    status = out["status"].strip() or "Not Started"
    if status not in work_metadata_sync.WORK_STATUS_SET:
        raise ValueError("INVALID_ENVELOPE")
    out["status"] = status
    out["doc_type"] = normalize_doc_type(out["doc_type"] or "online")
    if out["doc_type"] != "online":
        # Video construction always stores online; refuse other claims rather
        # than silently rewriting a deliberate non-video doc_type.
        if (payload.get("doc_type") or "").strip() and normalize_doc_type(
                payload["doc_type"]) != "online":
            raise ValueError("INVALID_ENVELOPE")
        out["doc_type"] = "online"
    roles = payload["roles"]
    if not isinstance(roles, list):
        raise ValueError("INVALID_ENVELOPE")
    normalized_roles = []
    seen = set()
    for entry in roles:
        if not isinstance(entry, dict):
            raise ValueError("INVALID_ENVELOPE")
        person_id = entry.get("person_id")
        role_type = entry.get("role_type")
        credit = entry.get("credit_name", "")
        if credit is None:
            credit = ""
        if not isinstance(person_id, str) or not person_id.strip():
            raise ValueError("INVALID_ENVELOPE")
        if not isinstance(role_type, str) or not isinstance(credit, str):
            raise ValueError("INVALID_ENVELOPE")
        try:
            work_role_sync.validate_role_type(role_type)
            work_role_sync.validate_credit_name(credit)
        except ValueError:
            raise ValueError("INVALID_ENVELOPE") from None
        key = (person_id.strip(), role_type)
        if key in seen:
            continue
        seen.add(key)
        normalized_roles.append({
            "person_id": person_id.strip(),
            "role_type": role_type,
            "credit_name": credit,
        })
    out["roles"] = normalized_roles
    # Bound thumb_url like other small strings — no network fetch on create.
    if len(out["thumb_url"].encode("utf-8")) > 2048:
        raise ValueError("INVALID_ENVELOPE")
    return out


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "W"):
        raise ValueError("INVALID_ENVELOPE")
    canonical_create_payload(op["payload"])


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


