"""The Work projection is importable without a database driver (L-W-1 #628).

``backend/domain/works/projection.py`` is pure: it shapes rows already read.
The browse excerpt contract (``tests/test_abstract_excerpt.py``) and other
driver-free checks import it, so it must not pull in psycopg. Moved here
from ``tests/test_works_projection_shape.py`` (PG-C2 #641).
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProjectionImportTests(unittest.TestCase):
    def test_importing_the_projection_needs_no_psycopg(self) -> None:
        code = ("import sys; import backend.domain.works.projection; "
                "sys.exit(1 if any(m.split('.')[0] in ('psycopg', 'psycopg_pool') for m in sys.modules) else 0)")
        proc = subprocess.run([sys.executable, "-c", code], cwd=_PROJECT_DIR, check=False)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
