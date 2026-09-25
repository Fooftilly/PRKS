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

from backend import work_metadata_sync as meta
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


def get_revision(conn, folder_id, field):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (FIELD_SCOPE_TYPE, scope_key(folder_id, field))).fetchone()
    return row[0] if row else 0


def get_work_revision(conn, work_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (WORK_SCOPE_TYPE, work_id)).fetchone()
    return row[0] if row else 0


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def current_field(conn, folder_id, field):
    row = conn.execute(
        "SELECT %s FROM folders WHERE id = ?" % field, (folder_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def descendants(conn, folder_id):
    return {row[0] for row in conn.execute(
        """WITH RECURSIVE sub(id) AS (
               SELECT id FROM folders WHERE parent_id = ?
               UNION SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
           ) SELECT id FROM sub""", (folder_id,))}


def assert_title_free(conn, title, parent_id, exclude_folder_id=None):
    """Unique WITHIN ITS PARENT, which is the rule the ordinary path applies.

    Two folders called "Drafts" under different parents are not a collision;
    two under the same one are.
    """
    if parent_id:
        rows = conn.execute(
            "SELECT id FROM folders WHERE parent_id = ? AND LOWER(TRIM(title)) = LOWER(?)",
            (parent_id, title)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM folders WHERE parent_id IS NULL AND LOWER(TRIM(title)) = LOWER(?)",
            (title,)).fetchall()
    for row in rows:
        if row[0] != exclude_folder_id:
            raise FolderRuleError(
                "TITLE_TAKEN", "A folder with this name already exists in this location.")


def assert_parent_usable(conn, folder_id, parent_id):
    """Acyclicity is canonical: only the server sees the whole hierarchy."""
    if not parent_id:
        return
    if parent_id == folder_id:
        raise FolderRuleError("PARENT_CYCLE", "A folder cannot be its own parent.")
    if conn.execute("SELECT 1 FROM folders WHERE id = ?", (parent_id,)).fetchone() is None:
        raise FolderRuleError("PARENT_NOT_FOUND", "Parent folder not found.")
    if folder_id and parent_id in descendants(conn, folder_id):
        raise FolderRuleError("PARENT_CYCLE", "Cannot set parent to a subfolder (cycle).")


def set_field_on_conn(conn, folder_id, field, value):
    """Write one folder field and advance its revision together.

    A no-op write advances nothing: a revision records the value actually
    changing, and inflating it would manufacture staleness for every device
    that already holds the current value.
    """
    desired = validate_field_value(field, value)
    current = current_field(conn, folder_id, field)
    if current is None:
        raise FolderRuleError("ENTITY_NOT_FOUND", "Folder not found.")
    revision = get_revision(conn, folder_id, field)
    if current == desired:
        return False, revision
    if field == "parent_id":
        assert_parent_usable(conn, folder_id, desired)
    # A title is unique within its parent, so BOTH fields have to be judged
    # together: moving a folder can collide just as renaming it can.
    title = desired if field == "title" else current_field(conn, folder_id, "title")
    parent = desired if field == "parent_id" else current_field(conn, folder_id, "parent_id")
    if field in ("title", "parent_id"):
        assert_title_free(conn, canonical_wire("title", title), parent or None,
                          exclude_folder_id=folder_id)
    stored = (desired or None) if field == "parent_id" else desired
    conn.execute(
        "UPDATE folders SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (stored, folder_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(folder_id, field))
    return True, revision + 1


def insert_folder_on_conn(conn, folder_id, title, description="", parent_id="",
                          private_notes=""):
    """CONSTRUCTION. Deliberately not four `set_field_on_conn` calls.

    A folder created with a title and a parent has not "changed" twice, and
    manufacturing revision 1 for each field would make every device's first read
    look like a missed change it has to reconcile.
    """
    clean_title = validate_field_value("title", title)
    clean_parent = validate_field_value("parent_id", parent_id)
    clean_description = validate_field_value("description", description)
    clean_notes = validate_field_value("private_notes", private_notes)
    if clean_parent and conn.execute(
            "SELECT 1 FROM folders WHERE id = ?", (clean_parent,)).fetchone() is None:
        raise FolderRuleError("PARENT_NOT_FOUND", "Parent folder not found.")
    assert_title_free(conn, clean_title, clean_parent or None)
    conn.execute(
        "INSERT INTO folders (id, title, description, parent_id, private_notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (folder_id, clean_title, clean_description, clean_parent or None, clean_notes))
    return folder_id


def current_work_folder(conn, work_id):
    row = conn.execute(
        "SELECT folder_id FROM folder_files WHERE work_id = ? LIMIT 1", (work_id,)).fetchone()
    return "" if row is None else str(row[0])


def set_work_folder_on_conn(conn, work_id, folder_id):
    """ONE boundary for the handler and for the ordinary endpoints.

    A Work is in at most one folder, so this is a SCALAR: clearing and
    reassigning are the same operation with different values, and `''` means
    "in no folder".
    """
    desired = ("" if folder_id is None else str(folder_id)).strip()
    current = current_work_folder(conn, work_id)
    if current == desired:
        return False
    if desired and conn.execute(
            "SELECT 1 FROM folders WHERE id = ?", (desired,)).fetchone() is None:
        raise FolderRuleError("FOLDER_NOT_FOUND", "Folder not found.")
    conn.execute("DELETE FROM folder_files WHERE work_id = ?", (work_id,))
    if desired:
        conn.execute("INSERT INTO folder_files (folder_id, work_id) VALUES (?, ?)",
                     (desired, work_id))
    _advance(conn, WORK_SCOPE_TYPE, work_id)
    # A cached Work detail embeds `folder_title`, and both folders' counts move.
    conn.execute("UPDATE works SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (work_id,))
    return True


def delete_folder_on_conn(conn, folder_id):
    """Remove a folder, but only an EMPTY one.

    Shared by ``DELETE /api/folders/:id`` (via ``PRKSDatabase.delete_empty_folder``)
    and ``DELETE_FOLDER``. The caller's transaction owns commit/rollback; this
    helper never opens its own. A folder holding files or subfolders is refused
    rather than cascading something the ordinary endpoint would reject.
    Returns ``(deleted, code)`` where code names the refusal (or ``None``).
    """
    if conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is None:
        return False, None
    files = conn.execute(
        "SELECT COUNT(*) FROM folder_files WHERE folder_id = ?", (folder_id,)).fetchone()[0]
    if files:
        return False, "FOLDER_NOT_EMPTY"
    children = conn.execute(
        "SELECT COUNT(*) FROM folders WHERE parent_id = ?", (folder_id,)).fetchone()[0]
    if children:
        return False, "FOLDER_HAS_SUBFOLDERS"
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    return True, None


def get_folder_state_on_conn(conn, folder_id):
    """Field revisions for one folder: revisions only.

    The catalogue already carries every folder's title, parent and description,
    so echoing values here would make a second cached copy of the hierarchy.
    """
    if conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is None:
        return None
    by_key = {scope_key(folder_id, field): field for field in FIELDS}
    placeholders = ", ".join("?" * len(by_key))
    revisions = {
        by_key[scope_id]: revision
        for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions "
            "WHERE scope_type = ? AND scope_id IN (%s)" % placeholders,
            (FIELD_SCOPE_TYPE,) + tuple(by_key)).fetchall()
    }
    return {
        "folder_id": folder_id,
        "fields": {field: {"revision": revisions.get(field, 0)} for field in sorted(FIELDS)},
    }


def get_work_folder_state_on_conn(conn, work_id):
    """Which folder a Work is in, and the revision that says so."""
    if conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is None:
        return None
    return {"work_id": work_id, "folder_id": current_work_folder(conn, work_id),
            "revision": get_work_revision(conn, work_id)}


# ---- handlers -------------------------------------------------------------

def _rule_status(code):
    return 404 if code in ("ENTITY_NOT_FOUND", "PARENT_NOT_FOUND", "FOLDER_NOT_FOUND") else 409


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


def _folder_row(conn, folder_id):
    row = conn.execute(
        "SELECT id, title, description, parent_id, private_notes FROM folders WHERE id = ?",
        (folder_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["work_count"] = 0
    data["child_count"] = conn.execute(
        "SELECT COUNT(*) FROM folders WHERE parent_id = ?", (folder_id,)).fetchone()[0]
    return data


def apply_create(db, conn, op, received_at):
    folder_id = op["entity_id"]
    payload = op["payload"]
    existing = conn.execute(
        "SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if existing is None:
        try:
            insert_folder_on_conn(conn, folder_id, payload["title"], payload["description"],
                                  payload["parent_id"], payload["private_notes"])
        except FolderRuleError as error:
            return _rule_status(error.code), {"code": error.code, "folder_id": folder_id}
    row = _folder_row(conn, folder_id)
    if existing is not None:
        row["work_count"] = conn.execute(
            "SELECT COUNT(*) FROM folder_files WHERE folder_id = ?",
            (folder_id,)).fetchone()[0]
    return 200, {"code": "ACKNOWLEDGED", "folder_id": folder_id,
                 "changed": existing is None, "folder": row}


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


def apply_field(db, conn, op, received_at):
    folder_id = op["entity_id"]
    field = op["payload"]["field"]
    desired = canonical_wire(field, op["payload"]["value"])
    result = {"folder_id": folder_id, "field": field}
    current = current_field(conn, folder_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, folder_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field_on_conn(conn, folder_id, field, desired)
    except FolderRuleError as error:
        result.update(code=error.code, current_revision=revision)
        return _rule_status(error.code), result
    # A private note has no length bound worth echoing into a ledger with no
    # retention policy; the client holds the authoritative copy in its payload.
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    if field == "title" and changed:
        # A cached Work detail embeds `folder_title`, so a rename stales exactly
        # this folder's members. The answer NAMES them rather than making the
        # client guess from whatever page happened to be open -- the same thing
        # the ordinary PATCH boundary reports.
        result["member_work_ids"] = [
            row[0] for row in conn.execute(
                "SELECT work_id FROM folder_files WHERE folder_id = ?", (folder_id,))
            if row[0]
        ]
    return 200, result


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


def apply_work_folder(db, conn, op, received_at):
    work_id = op["entity_id"]
    desired = op["payload"]["folder_id"]
    result = {"work_id": work_id}
    if conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_work_revision(conn, work_id)
    current = current_work_folder(conn, work_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, result
    # Two devices that filed the same Work in the same folder have converged,
    # however many revisions apart they started.
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, result
    try:
        changed = set_work_folder_on_conn(conn, work_id, desired)
    except FolderRuleError as error:
        result.update(code=error.code, current_revision=revision)
        return _rule_status(error.code), result
    result.update(code="ACKNOWLEDGED", changed=changed, folder_id=desired,
                  server_revision=revision + int(changed))
    # The Work's cached detail renders the folder's TITLE, and a client whose
    # folder catalogue does not hold this one cannot build that row without it.
    if desired:
        row = conn.execute("SELECT title FROM folders WHERE id = ?", (desired,)).fetchone()
        result["folder_title"] = (row[0] if row is not None else "") or ""
    else:
        result["folder_title"] = ""
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    # Lifecycle/envelope outcomes stay here; the empty-only DELETE itself is
    # ``delete_folder_on_conn`` — never ``PRKSDatabase.delete_empty_folder``
    # (would nest a txn / re-enter HTTP-shaped errors).
    folder_id = op["entity_id"]
    existed = conn.execute(
        "SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is not None
    deleted, refusal = delete_folder_on_conn(conn, folder_id)
    if refusal:
        return 409, {"code": refusal, "folder_id": folder_id}
    # Deleting a folder that is already gone is CONVERGENCE.
    return 200, {"code": "ACKNOWLEDGED", "folder_id": folder_id,
                 "changed": bool(deleted and existed)}


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
FIELD_HANDLER = _handler(validate_field, apply_field)
WORK_FOLDER_HANDLER = _handler(validate_work_folder, apply_work_folder)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
