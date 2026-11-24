"""Durable whole-document Work notes.

Research Notes and Private Notes are two different canonical columns and two
different conflict units. Each body is one aggregate: PRKS does not attempt a
CRDT or character-level merge.

The Research Note write delegates to ``research_network``'s canonical
connection-aware boundary, preserving Concept auto-creation and research
markup validation. Private Notes deliberately do not participate in research
markup processing.
"""

from backend.domain.research import rules as research_rules

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
    if operation == RESEARCH_OPERATION and research_rules._CONTROL_RE.search(value):
        raise ValueError("INVALID_ENVELOPE")
    return value


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


