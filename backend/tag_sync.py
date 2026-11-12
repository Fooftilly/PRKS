"""The Tag VOCABULARY: CREATE_TAG, DELETE_TAG, and MERGE_TAG.

The Work-Tag *relationship* has been durable since Phase 2 (`work_tag_sync`).
This module is the other half: the Tags themselves.

Three shapes, and only three. PRKS has no rename and no colour editor -- `POST
/api/tags` either creates a Tag or hands back the existing one, and every
caller sends the same default colour -- so a `SET_TAG_FIELD` family would be
inventing product semantics rather than moving existing ones off the network.
That decision is recorded in `docs/local-first-rollout-status.md` rather than
guessed at here.

Tag identity is PERSISTENT. `delete_tag` and `merge_tags_into` are the only
operations that may destroy or transform it, and this module adds no third one:
an unused Tag stays in the catalogue.

A name is unique across canonical names AND aliases, case-insensitively, and
only the server sees every Tag -- so a client that minted an id for a name
somebody else already used is refused with `NAME_TAKEN`. It is deliberately not
converged onto the existing Tag: operations already queued behind the creation
name the id this device minted, and silently redirecting them to a different
Tag is exactly what the Work-Tag family refuses to do when a Tag turns out to
have been merged.
"""

from backend.entity_ids import is_distributed
from backend.tag_color import normalized_tag_color

MAX_NAME_BYTES = 200
def canonical_name(value):
    return ("" if value is None else str(value)).strip()


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "T"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != {"name", "color"}:
        raise ValueError("INVALID_ENVELOPE")
    name = payload["name"]
    if not isinstance(name, str) or not canonical_name(name):
        raise ValueError("INVALID_ENVELOPE")
    if len(canonical_name(name).encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError("INVALID_ENVELOPE")
    try:
        normalized_tag_color(payload["color"])
    except ValueError as exc:
        raise ValueError("INVALID_ENVELOPE") from exc


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    # Destruction addresses an IDENTITY, not a value: absence is idempotent.
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_merge(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    payload = op["payload"]
    if set(payload) != {"target_tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    target = payload["target_tag_id"]
    if (not isinstance(target, str) or not target.strip() or target != target.strip()
            or len(target) > 200):
        raise ValueError("INVALID_ENVELOPE")
    if target == op["entity_id"]:
        raise ValueError("INVALID_ENVELOPE")


