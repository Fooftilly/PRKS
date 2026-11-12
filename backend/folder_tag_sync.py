"""Folder-Tag domain: relationship revisions and the ADD_FOLDER_TAG /
REMOVE_FOLDER_TAG handler.

Mirrors `work_tag_sync` with conflict unit `(folder, tag)`. The Tag lifecycle
table is shared. Canonical writes share the caller's transaction so one
`set_state()` boundary serves the sync handler, the direct endpoints, Tag merge
and Tag delete.
"""
import json

MAX_TAG_ID_CHARS = 200


def scope_key(folder_id, tag_id):
    return json.dumps([folder_id, tag_id], ensure_ascii=True, separators=(",", ":"))


def validate(op):
    """Folder-Tag payload and concurrency rules (same posture as Work-Tag)."""
    payload = op["payload"]
    if set(payload) != {"tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    tag_id = payload["tag_id"]
    if (not isinstance(tag_id, str) or not tag_id.strip() or tag_id != tag_id.strip()
            or len(tag_id) > MAX_TAG_ID_CHARS):
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


