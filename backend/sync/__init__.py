"""Semantic operations on PostgreSQL (``docs/postgresql-architecture.md`` §7.3).

``protocol`` is the ledger and dispatch every family shares; each family's
handler lives in ``handlers/`` with its lane (§15.2). The SQLite protocol
(``backend/sync_protocol.py``) stays the running app's until cutover.
"""
