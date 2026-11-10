"""People rules shared by every Library backend (PG-C2 #641).

Pure: no store. Moved from ``PRKSDatabase`` in ``backend/db_manager.py``.
"""

from __future__ import annotations

from backend import work_role_sync


def eligible_initial_roles(roles) -> list:
    """The requested roles a new Work would actually be born with.

    One definition for the pre-create check and the insert, so a role that
    creation skips (no Person, no or unknown role type) cannot block it.
    Returns (order_index, person_id, role_type, credit_name) tuples.
    """
    wanted = []
    for idx, r in enumerate(roles if isinstance(roles, list) else []):
        if not isinstance(r, dict) or not r.get('person_id') or not r.get('role_type'):
            continue
        if r['role_type'] not in work_role_sync.ROLE_TYPE_SET:
            continue
        credit = r.get('credit_name', '')
        if credit is not None and not isinstance(credit, str):
            credit = ''
        wanted.append((idx, str(r['person_id']), r['role_type'], credit or ''))
    return wanted
