"""Which kind of PRKS distribution this is (storage-architecture §6, S7).

A packaged build replaces this module with a generated one that sets
``DISTRIBUTION = "packaged"``. A source checkout keeps ``"source"``, so its
default storage root stays the repository ``data/`` directory. The resolver
never guesses the distribution from the filesystem (for example from a ``.git``
directory): a wrong guess would silently open a different library.
"""

from __future__ import annotations

SOURCE = "source"
PACKAGED = "packaged"

DISTRIBUTION = SOURCE
