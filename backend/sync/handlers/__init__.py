"""PostgreSQL handlers of the semantic-operation families, one module per family.

A handler validates with its family's rule module (``backend/*_sync.py``,
shared with the REST path so both accept the same payloads) and applies by
calling the same domain command as the REST path. Registration is in
:mod:`backend.sync.protocol`.
"""
