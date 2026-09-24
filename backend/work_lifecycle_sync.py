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

import sqlite3

from backend.db_manager import (
    DeletedWorkRecord,
    _canonical_new_source,
    managed_basenames_protected_by,
    normalize_doc_type,
    row_strongly_references_managed_pdf,
)
from backend.entity_ids import generate as generate_entity_id
from backend.entity_ids import is_distributed
from backend import folder_sync
from backend import work_metadata_sync
from backend import work_role_sync
from backend import work_source_sync

_UNCATEGORIZED_TITLE = "Uncategorized"

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


def ensure_uncategorized_on_conn(conn):
    """Top-level Uncategorized folder id; create inside this transaction if needed."""
    row = conn.execute(
        "SELECT id FROM folders WHERE parent_id IS NULL AND title = ? LIMIT 1",
        (_UNCATEGORIZED_TITLE,),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    folder_id = generate_entity_id("F")
    folder_sync.insert_folder_on_conn(conn, folder_id, _UNCATEGORIZED_TITLE, "", "")
    return folder_id


def insert_work_on_conn(conn, work_id, fields, identity):
    """Insert a video Work row with a client-minted id. Construction = rev 0."""
    title = (fields["title"] or "").strip() or "Untitled"
    conn.execute(
        """
        INSERT INTO works (
            id, title, status, abstract, text_content, published_date, file_path,
            author_text, year, publisher, location, edition, journal, volume, issue,
            pages, isbn, doi, doc_type,
            source_kind, source_url, source_mime, thumb_url, provider, provider_id,
            urldate, thumb_page, private_notes
        )
        VALUES (
            ?, ?, ?, ?, '', ?, NULL,
            ?, ?, NULL, NULL, NULL, NULL, NULL, NULL,
            NULL, NULL, NULL, ?,
            ?, ?, NULL, ?, ?, ?,
            ?, NULL, ?
        )
        """,
        (
            work_id,
            title,
            fields["status"],
            fields["abstract"] or None,
            (fields["published_date"] or "").strip() or None,
            fields["author_text"] or None,
            (fields["year"] or "").strip() or None,
            fields["doc_type"],
            identity["source_kind"],
            identity["source_url"],
            (fields["thumb_url"] or "").strip() or None,
            identity["provider"],
            identity["provider_id"],
            (fields["urldate"] or "").strip() or None,
            (fields["private_notes"] or "").strip() or None,
        ),
    )


def apply_create(db, conn, op, received_at):
    work_id = op["entity_id"]
    fields = canonical_create_payload(op["payload"])
    identity = fields["_identity"]
    # Re-check through the shared creation boundary (provider contradiction etc.).
    kind, url, provider, provider_id = _canonical_new_source(
        "video", identity["source_url"], "", "", "")
    identity = {
        "source_kind": kind,
        "source_url": url,
        "provider": provider,
        "provider_id": provider_id,
    }
    existing = conn.execute(
        "SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone()
    folder_id = (fields["folder_id"] or "").strip()
    if not folder_id:
        folder_id = ensure_uncategorized_on_conn(conn)
    elif conn.execute(
            "SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is None:
        return 404, {
            "code": "FOLDER_NOT_FOUND",
            "work_id": work_id,
        }
    playlist_id = (fields["playlist_id"] or "").strip()
    if playlist_id and conn.execute(
            "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        return 404, {
            "code": "PLAYLIST_NOT_FOUND",
            "work_id": work_id,
        }
    for role in fields["roles"]:
        if conn.execute(
                "SELECT 1 FROM persons WHERE id = ?",
                (role["person_id"],)).fetchone() is None:
            return 404, {
                "code": "PERSON_NOT_FOUND",
                "work_id": work_id,
                "person_id": role["person_id"],
            }
    from backend import person_metadata_sync
    aliases_before = {
        role["person_id"]: person_metadata_sync.get_revision(
            conn, role["person_id"], "aliases")
        for role in fields["roles"]
    }
    if existing is None:
        try:
            insert_work_on_conn(conn, work_id, fields, identity)
        except sqlite3.IntegrityError:
            # Concurrent insert of the same id: still "this Work exists".
            existing = conn.execute(
                "SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone()
        else:
            # Filing is construction, not mutation: no folder/playlist revision.
            conn.execute(
                "INSERT INTO folder_files (folder_id, work_id) VALUES (?, ?) "
                "ON CONFLICT DO NOTHING",
                (folder_id, work_id),
            )
            if playlist_id:
                row = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) AS m FROM playlist_items "
                    "WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                conn.execute(
                    "INSERT INTO playlist_items (playlist_id, work_id, position) "
                    "VALUES (?, ?, ?)",
                    (playlist_id, work_id, int(row[0]) + 1),
                )
                conn.execute(
                    "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (playlist_id,),
                )
            for index, role in enumerate(fields["roles"]):
                work_role_sync.insert_initial_role(
                    conn, work_id, role["person_id"], role["role_type"],
                    order_index=index, credit_name=role["credit_name"])
    # Construction may promote role credits into Person aliases. Report every
    # Person whose aliases revision advanced so reconcileCreatedWork can patch
    # person-metadata-state rather than leave a stale offline base.
    aliases_revisions = {}
    if existing is None:
        for pid, before in aliases_before.items():
            after = person_metadata_sync.get_revision(conn, pid, "aliases")
            if after != before:
                aliases_revisions[pid] = after
    result = {
        "code": "ACKNOWLEDGED",
        "work_id": work_id,
        "changed": existing is None,
        "folder_id": folder_id,
        "playlist_id": playlist_id,
        "role_count": len(fields["roles"]),
    }
    if aliases_revisions:
        result["aliases_revisions"] = aliases_revisions
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def delete_work_record_on_conn(conn, work_id, *, claim_pdf=True):
    """Remove the Work row on the caller's transaction. Cascades handle links.

    ``claim_pdf=False`` is for undoing a create that ADOPTED existing managed
    bytes: those were never this Work's to reclaim, so no claim is written and
    they stay exactly as they were before the create.

    A managed PDF no surviving row references is claimed for cleanup in this
    same transaction. `works.file_path` is the only thing that ties those bytes
    to this Work, and it stops existing here -- so the claim has to be written
    before the commit that destroys it, not after the `os.remove()` that may
    never happen. Filesystem work stays outside the transaction; only the
    identity is recorded inside it.
    """
    from backend.work_deletion import record_pending_pdf_cleanup_on_conn

    row = conn.execute(
        "SELECT file_path FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        return None
    file_path = "" if row["file_path"] is None else str(row["file_path"])
    # Strong ownership / serving identities only. Weak fail-closed aliases
    # (traversal / nested / %2F) must not mint claims, and must not prevent
    # minting either: when only a weak alias survives, the claim is still
    # written so cleanup can finish after that alias disappears.
    protected = managed_basenames_protected_by(file_path)
    conn.execute("DELETE FROM works WHERE id = ?", (work_id,))
    still_referenced = False
    if protected and claim_pdf:
        survivors = conn.execute(
            "SELECT file_path FROM works WHERE file_path IS NOT NULL"
        ).fetchall()
        for name in protected:
            if any(
                row_strongly_references_managed_pdf(r["file_path"], name)
                for r in survivors
            ):
                still_referenced = True
            else:
                record_pending_pdf_cleanup_on_conn(conn, name)
    return DeletedWorkRecord(
        work_id=work_id,
        file_path=file_path,
        managed_pdf_still_referenced=still_referenced,
    )


def apply_delete(db, conn, op, received_at):
    work_id = op["entity_id"]
    existed = conn.execute(
        "SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is not None
    record = delete_work_record_on_conn(conn, work_id)
    result = {
        "code": "ACKNOWLEDGED",
        "work_id": work_id,
        "changed": bool(record is not None and existed),
    }
    # file_path is ephemeral cleanup state for the first post-commit pass only.
    # sync_protocol redacts it before ledger insert; never immortalised.
    if record is not None and record.file_path:
        result["file_path"] = record.file_path
    return 200, result


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
