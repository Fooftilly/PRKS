"""Immutable stored objects on PostgreSQL (L-A-1 #635; ``docs/postgresql-architecture.md`` §11.2).

The schema is ``backend/db/schema/150_objects.sql``; the lifecycle is
:mod:`backend.domain.objects.lifecycle`. Bytes live behind
``backend.storage.objects.StorageBackend`` and are never modified in place:
a write goes to a new key, one transaction adopts it, and a key the
catalogue stops naming gets a cleanup claim that is settled later.
"""
