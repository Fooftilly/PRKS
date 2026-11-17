"""Settings rules shared by every Library backend (PG-C2 #641).

The BibTeX export field list and its default, parse and patch rules. Moved
from ``backend/db_manager.py``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

# Optional BibTeX/BibLaTeX lines (title + entry shell always exported).
PRKS_BIBTEX_EXPORT_FIELD_IDS: Tuple[str, ...] = (
    "author",
    "editor",
    "translator",
    "introduction",
    "foreword",
    "afterword",
    "year",
    "publisher",
    "location",
    "edition",
    "journal",
    "volume",
    "number",
    "pages",
    "isbn",
    "doi",
    "url",
    "abstract",
)


# The longest stored value of one application setting, in characters.
APP_SETTING_MAX_LEN = 500

PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT: Dict[str, bool] = {k: True for k in PRKS_BIBTEX_EXPORT_FIELD_IDS}


def _prks_parse_bibtex_export_fields_json(raw: str) -> Dict[str, bool]:
    """Load stored JSON; invalid or missing → all True. Unknown keys ignored."""
    out = dict(PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT)
    if not raw or not str(raw).strip():
        return out
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        if k not in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            continue
        if isinstance(v, bool):
            out[k] = v
    return out


def _prks_merge_bibtex_export_fields_patch(
    current: Dict[str, bool], patch: Dict[str, Any]
) -> Dict[str, bool]:
    merged = dict(current)
    for k, v in patch.items():
        if k not in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            raise ValueError(f"unknown bibtex_export_fields key: {k}")
        if not isinstance(v, bool):
            raise ValueError(f"bibtex_export_fields.{k} must be boolean")
        merged[k] = v
    return merged
