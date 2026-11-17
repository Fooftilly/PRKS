"""Folders: construction, fields, deletion, and which folder a Work is in.

Four shapes, and the split is a reading of the schema:

  * `CREATE_FOLDER` is construction -- a client-minted id, no base revision.
  * `SET_FOLDER_FIELD` is scalar mutation with a conflict unit of one FIELD.
    `title`, `description`, `private_notes` and `parent_id` are independent
    decisions; two devices that renamed a folder and moved it have not
    disagreed. Moving IS one of those fields: the hierarchy is a parent pointer
    on one row, so a move changes exactly one value.
  * `SET_WORK_FOLDER` is scalar mutation of the WORK. A Work is in at most one
    folder -- `move_work_to_folder` clears and reassigns in one step -- so this
    is a field on the Work, not membership of a set. Its conflict unit is the
    Work, and `''` means "in no folder".
  * `DELETE_FOLDER` is destruction, carrying no base revision because it
    addresses an identity rather than a value.

Three canonical rules are preserved exactly as the ordinary endpoints have them,
because only the server can decide any of them: a title is unique WITHIN ITS
PARENT, the hierarchy is acyclic, and a folder may only be deleted when it holds
no files and no subfolders. Each comes back as a named terminal code.

Nothing here imports the database layer; the ordinary endpoints share these same
helpers, so a folder can never change without its revision.
"""
import json

from backend.entity_ids import is_distributed

# The editable columns on `folders`. `id`, `created_at` and `updated_at` are not
# user-editable and are absent by construction rather than by filtering.
FIELDS = ("title", "description", "private_notes", "parent_id")
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "folder-field"
WORK_SCOPE_TYPE = "work-folder"

MAX_TITLE_BYTES = 200
MAX_TEXT_BYTES = 4000
DEFAULT_TITLE = "Untitled Folder"


class FolderRuleError(ValueError):
    """A canonical rule the user can act on, carrying its terminal code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def scope_key(folder_id, field):
    return json.dumps([folder_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """The spelling two devices must agree on.

    A title differing only in surrounding space is the same title, and an empty
    one is the placeholder the ordinary endpoint substitutes -- so both paths
    normalize before comparing, or a device would keep "disagreeing" with
    itself.
    """
    text = ("" if value is None else str(value)).strip()
    if field == "title":
        return text or DEFAULT_TITLE
    return text


def validate_field_value(field, value):
    if field not in FIELD_SET:
        raise FolderRuleError("INVALID_ENVELOPE", "unsupported field: %s" % field)
    if not isinstance(value, str):
        raise FolderRuleError("INVALID_ENVELOPE", "field values are strings")
    text = canonical_wire(field, value)
    limit = MAX_TITLE_BYTES if field in ("title", "parent_id") else MAX_TEXT_BYTES
    if len(text.encode("utf-8")) > limit:
        raise FolderRuleError("INVALID_ENVELOPE",
                              "%s exceeds %d bytes." % (field, limit))
    return text


# ---- handlers -------------------------------------------------------------


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "F"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    for field in FIELDS:
        try:
            validate_field_value(field, payload[field])
        except FolderRuleError:
            raise ValueError("INVALID_ENVELOPE") from None


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    if not isinstance(field, str) or field not in FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    try:
        validate_field_value(field, value)
    except FolderRuleError:
        raise ValueError("INVALID_ENVELOPE") from None
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_work_folder(op):
    payload = op["payload"]
    if set(payload) != {"folder_id"}:
        raise ValueError("INVALID_ENVELOPE")
    folder_id = payload["folder_id"]
    if not isinstance(folder_id, str) or folder_id != folder_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if len(folder_id) > MAX_TITLE_BYTES:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


