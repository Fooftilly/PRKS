"""People errors shared by every Library backend (PG-C2 #641)."""

from __future__ import annotations


class MissingPersonError(ValueError):
    """A role names a Person that does not exist (deleted, or never synced)."""
