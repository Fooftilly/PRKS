"""Work-Tag domain: relationship revisions, Tag lifecycle, and the
ADD_WORK_TAG / REMOVE_WORK_TAG handler.

The generic envelope, request hash, ledger and dispatch live in
`sync_protocol`. Everything here is specific to what a Work-Tag relationship
means. All canonical writes share the caller's transaction, so one `set_state()`
boundary serves the sync handler, the direct endpoints, bulk edits, Tag merge
and Tag delete -- a relationship change and its revision always advance
together.
"""
import json

MAX_TAG_ID_CHARS = 200


def scope_key(work_id, tag_id):
    # Structural encoding: IDs need not exclude any delimiter.
    return json.dumps([work_id, tag_id], ensure_ascii=True, separators=(",", ":"))


def validate(op):
    """Work-Tag payload and concurrency rules.

    A Work-Tag edit is optimistic-concurrency controlled, so a null
    `base_revision` is not "no opinion" -- it is a client that cannot detect a
    conflict, and accepting it would silently overwrite another device.
    """
    payload = op["payload"]
    if set(payload) != {"tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    tag_id = payload["tag_id"]
    if not isinstance(tag_id, str) or not tag_id.strip() or tag_id != tag_id.strip() \
            or len(tag_id) > MAX_TAG_ID_CHARS:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


