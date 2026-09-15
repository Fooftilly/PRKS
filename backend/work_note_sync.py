"""Durable whole-document Work notes.

Research Notes and Private Notes are two different canonical columns and two
different conflict units. Each body is one aggregate: PRKS does not attempt a
CRDT or character-level merge.

The Research Note write delegates to ``research_network``'s canonical
connection-aware boundary, preserving Concept auto-creation and research
markup validation. Private Notes deliberately do not participate in research
markup processing.
"""
import json

from backend import research_network

RESEARCH_OPERATION = "SET_WORK_RESEARCH_NOTE"
PRIVATE_OPERATION = "SET_WORK_PRIVATE_NOTE"
RESEARCH_SCOPE_TYPE = "work-research-note"
PRIVATE_SCOPE_TYPE = "work-private-note"

# The HTTP adapter accepts at most 50 MiB of JSON. Keep enough headroom for the
# immutable operation envelope while preserving genuinely long-form notes.
MAX_RESEARCH_NOTE_UTF8_BYTES = 32 * 1024 * 1024
# The UI currently limits reminders to 8,000 characters. This larger byte cap
# keeps API compatibility while bounding every durable operation explicitly.
MAX_PRIVATE_NOTE_UTF8_BYTES = 64 * 1024


def _utf8_size(value):
    return len(value.encode("utf-8"))


def validate_text(operation, value):
    if not isinstance(value, str):
        raise ValueError("INVALID_ENVELOPE")
    limit = (MAX_RESEARCH_NOTE_UTF8_BYTES
             if operation == RESEARCH_OPERATION else MAX_PRIVATE_NOTE_UTF8_BYTES)
    if _utf8_size(value) > limit:
        raise ValueError("INVALID_ENVELOPE")
    if operation == RESEARCH_OPERATION and research_network._CONTROL_RE.search(value):
        raise ValueError("INVALID_ENVELOPE")
    return value


def _scope_type(operation):
    return RESEARCH_SCOPE_TYPE if operation == RESEARCH_OPERATION else PRIVATE_SCOPE_TYPE


def _column(operation):
    return "text_content" if operation == RESEARCH_OPERATION else "private_notes"


def _revision(conn, operation, work_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (_scope_type(operation), work_id),
    ).fetchone()
    return row[0] if row else 0


def _advance(conn, operation, work_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (_scope_type(operation), work_id),
    )


def current_text(conn, operation, work_id):
    row = conn.execute(
        "SELECT %s FROM works WHERE id = ?" % _column(operation), (work_id,)
    ).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def set_research_note_on_conn(conn, db, work_id, text):
    """Canonical Research Note boundary, including markup side effects."""
    validate_text(RESEARCH_OPERATION, text)
    before = current_text(conn, RESEARCH_OPERATION, work_id)
    if before is None:
        raise research_network.ResearchError("not_found", "Work not found.", 404)
    if before == text:
        return False, _revision(conn, RESEARCH_OPERATION, work_id)
    research_network.save_work_notes_on_conn(conn, db, work_id, text)
    _advance(conn, RESEARCH_OPERATION, work_id)
    return True, _revision(conn, RESEARCH_OPERATION, work_id)


def set_private_note_on_conn(conn, work_id, text):
    validate_text(PRIVATE_OPERATION, text)
    before = current_text(conn, PRIVATE_OPERATION, work_id)
    if before is None:
        raise research_network.ResearchError("not_found", "Work not found.", 404)
    if before == text:
        return False, _revision(conn, PRIVATE_OPERATION, work_id)
    conn.execute(
        "UPDATE works SET private_notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (text or None, work_id),
    )
    _advance(conn, PRIVATE_OPERATION, work_id)
    return True, _revision(conn, PRIVATE_OPERATION, work_id)


def set_research_note(db, work_id, text):
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return set_research_note_on_conn(conn, db, (work_id or "").strip(), text)


def get_notes_state_on_conn(conn, work_id):
    if conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is None:
        return None
    return {
        "work_id": work_id,
        "research_note_revision": _revision(conn, RESEARCH_OPERATION, work_id),
        "private_note_revision": _revision(conn, PRIVATE_OPERATION, work_id),
    }


def validate(op):
    if op["operation"] not in (RESEARCH_OPERATION, PRIVATE_OPERATION):
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")
    if set(op["payload"]) != {"text"}:
        raise ValueError("INVALID_ENVELOPE")
    validate_text(op["operation"], op["payload"]["text"])


def _disagreement(current, desired):
    # Note prose is private and can be very large. Terminal durable results
    # retain only revision/size metadata; explicit resolution re-reads the
    # canonical Work and keeps the immutable operation as the local choice.
    return {
        "current_bytes": _utf8_size(current),
        "requested_bytes": _utf8_size(desired),
    }


def apply(db, conn, op, received_at):
    del received_at
    operation = op["operation"]
    work_id = op["entity_id"]
    desired = op["payload"]["text"]
    current = current_text(conn, operation, work_id)
    result = {"work_id": work_id, "note_kind": _scope_type(operation)}
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = _revision(conn, operation, work_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **_disagreement(current, desired))
        return 400, result
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **_disagreement(current, desired))
        return 409, result
    if operation == RESEARCH_OPERATION:
        changed, after = set_research_note_on_conn(conn, db, work_id, desired)
    else:
        changed, after = set_private_note_on_conn(conn, work_id, desired)
    result.update(code="ACKNOWLEDGED", changed=changed,
                  server_revision=after, value_omitted=True)
    return 200, result


HANDLER = type("_Handler", (), {
    "validate": staticmethod(validate),
    "apply": staticmethod(apply),
})()
