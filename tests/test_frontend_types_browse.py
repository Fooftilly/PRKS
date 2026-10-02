"""File types browse model: grouping stays in the classic helper Vue paints."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_types_browse_selftest.js")
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendTypesBrowseTests(unittest.TestCase):
    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for the types browse model")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)

    def test_route_publishes_sidebar_from_the_same_model(self):
        app = _read(_APP)
        types_at = app.index("case 'types':")
        detail_at = app.index("case 'type-detail':")
        self.assertGreater(types_at, 0)
        types_body = app[types_at:detail_at]
        self.assertIn("prksTypesIndexModel(works)", types_body)
        self.assertIn("prksPresentVueTypesIndex", types_body)
        self.assertNotIn("renderTypesIndex", types_body)
        detail_body = app[detail_at:app.index("case 'work':", detail_at)]
        self.assertIn("prksTypesDetailModel(works, route.params.docType)", detail_body)
        self.assertIn("prksPresentVueTypeDetail", detail_body)
        self.assertNotIn("renderWorksByDocType", detail_body)
        self.assertIn("publishSidebar", types_body)
        self.assertIn("publishSidebar", detail_body)


if __name__ == "__main__":
    unittest.main()
