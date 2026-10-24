"""PostgreSQL handlers of the semantic-operation families, one module per family.

A handler validates with its SQLite family's rule (imported, so a payload
legal on one backend is legal on the other) and applies by calling the same
domain command as the REST path. Registration is in
:mod:`backend.sync.protocol`.
"""
