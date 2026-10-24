"""PostgreSQL-era domain packages (``docs/postgresql-architecture.md`` §5).

Each lane adds its own subpackage. Shared pure helpers that every lane uses,
such as :mod:`backend.domain.text_identity`, live at this level.
"""
