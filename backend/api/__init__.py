"""HTTP controllers extracted from ``backend/server.py``.

Pattern (see AGENTS.md "HTTP adapter decomposition"):

- ``server.py`` owns transport lifecycle (host/origin, body size limits, access
  gate, JSON/ETag encoding, static files) and dispatches matching paths here.
- ``backend/api/<domain>.py`` owns parse/validate of that family's request
  shape, domain invocation, and HTTP error/status mapping.
- Domain modules (``db_manager``, ``*_sync``, ``services/``, …) keep SQL,
  filesystem, and sync semantics.

Extract one cohesive ``/api/...`` family at a time. Do not add a framework or
generic route registry.
"""
