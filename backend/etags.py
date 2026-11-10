"""Weak ETags for JSON API representations (PG-C2 #641).

Moved from ``PRKSDatabase`` in ``backend/db_manager.py`` so every Library
backend derives ETags the same way.
"""

from __future__ import annotations

import hashlib
import json
from typing import List


def etag_for_representation(label: str, rows: List[dict]) -> str:
    """Weak ETag derived from the serialized response itself.

    The invariant is absolute: if the body can change, the ETag must
    change. Hand-maintained table-count/MAX(updated_at) probes have already
    shipped two classes of defect here -- a count that a *move* leaves
    identical, and `CURRENT_TIMESTAMP`'s one-second granularity hiding a
    same-second edit. Hashing the representation makes the invariant true
    by construction and cannot drift when a projection gains a field.
    """
    blob = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    return f'W/"prks-{label}-{len(rows)}-{digest}"'
