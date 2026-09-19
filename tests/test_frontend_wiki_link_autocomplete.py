"""Wiki-link autocomplete context: one opener, no super-linear scan."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKS = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "works.js")
_RUNNER = os.path.join(
    _PROJECT_DIR, "tests", "browser", "run_wiki_link_autocomplete_selftest.js"
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendWikiLinkAutocompleteTests(unittest.TestCase):
    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for wiki-link autocomplete tests")
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
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_context_reads_the_opener_it_reports(self):
        """`query` and `from` must come from one opener, and finding it must not scan."""
        works = _read(_WORKS)
        body = works.split("function prksGetWikiLinkAutocompleteContext(cm) {", 1)[
            1
        ].split("function prksFilterWorksForWikiHint(", 1)[0]
        self.assertIn("const openAt = before.lastIndexOf('[[');", body)
        self.assertIn("const startCh = openAt + 2;", body)
        self.assertIn("const query = before.slice(startCh);", body)
        # The reserved-prefix guard has to see the same slice the hint replaces.
        self.assertIn("/^(pdf:|concept:|argument:)/i.test(query)", body)
        # No regex re-scan of the line: that pattern reported the leftmost opener
        # while `from` used the last one, and was quadratic in line length.
        self.assertNotIn("before.match(", body)
        self.assertNotIn("[^\\]|]*$", body)


    def test_selftest_imports_the_module_rather_than_evaluating_it(self):
        """The selftest must import works.js, not execute a slice of its source."""
        runner = _read(_RUNNER)
        works = _read(_WORKS)
        # works.js carries the repo's standard Node export guard, so the helper
        # can be imported directly -- same shape as navigation.js/concepts.js.
        self.assertIn("if (typeof module !== 'undefined' && module.exports)", works)
        self.assertIn("prksGetWikiLinkAutocompleteContext,", works)
        self.assertIn(
            "require('../../frontend/js/components/works.js')",
            runner,
        )
        # No dynamic code execution: that is what javascript:S1523 flags, and
        # slicing source out of a file to eval it is also needlessly fragile.
        self.assertNotIn("vm.runInContext", runner)
        self.assertNotIn("require('vm')", runner)
        self.assertNotIn("readFileSync", runner)
        self.assertNotIn("eval(", runner)


if __name__ == "__main__":
    unittest.main()
