"""Authoritative research-note markup parser + JS agreement."""
import json
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys

sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.research_markup import (
    CONCEPT_REF_MAX,
    canonical_concept_name,
    normalize_concept_key,
    parse_research_markup,
)

_FIXTURES = os.path.join(_PROJECT_DIR, "tests", "fixtures", "research_markup.json")
_JS = os.path.join(_PROJECT_DIR, "frontend", "js", "research-links.js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_research_links_selftest.js")


def _load_fixtures():
    with open(_FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


class ResearchMarkupTests(unittest.TestCase):
    def test_normalize_identity(self):
        self.assertEqual(
            normalize_concept_key("  Culture   Industry  "),
            normalize_concept_key("culture industry"),
        )
        self.assertEqual(canonical_concept_name("  Culture   Industry  "), "Culture Industry")

    def test_fixtures(self):
        for row in _load_fixtures():
            markup = parse_research_markup(row["text"])
            names = [r.name for r in markup.concept_refs]
            args = [r.argument_id for r in markup.argument_refs]
            self.assertEqual(names, row["concepts"], row["id"])
            self.assertEqual(args, row["arguments"], row["id"])

    def test_bounds(self):
        too_long = "[[concept:" + ("A" * (CONCEPT_REF_MAX + 1)) + "]]"
        self.assertEqual(parse_research_markup(too_long).concept_refs, ())
        ok = "[[concept:" + ("A" * CONCEPT_REF_MAX) + "]]"
        self.assertEqual(len(parse_research_markup(ok).concept_refs), 1)
        long_label = "[[argument:A-1|" + ("x" * 241) + "]]"
        self.assertEqual(parse_research_markup(long_label).argument_refs, ())
        (longest,) = parse_research_markup("[[argument:A-1|" + ("x" * 240) + "]]").argument_refs
        self.assertEqual(len(longest.label), 240)

    def test_argument_ref_fields(self):
        text = "See [[argument: A-1 | first | second ]] now."
        (ref,) = parse_research_markup(text).argument_refs
        self.assertEqual(
            (ref.start, ref.end, ref.raw, ref.argument_id, ref.label),
            (4, 39, "[[argument: A-1 | first | second ]]", "A-1", "first | second"),
        )

    def test_ref_spans_cover_the_whole_marker(self):
        text = "See [[concept:A]] and [[concept-encoded:B%20C]]."
        plain, encoded = parse_research_markup(text).concept_refs
        self.assertEqual((plain.start, plain.end, plain.raw), (4, 17, "[[concept:A]]"))
        self.assertEqual(
            (encoded.start, encoded.end, encoded.raw, encoded.name),
            (22, 47, "[[concept-encoded:B%20C]]", "B C"),
        )
        self.assertEqual(text[encoded.start:encoded.end], encoded.raw)

    def test_python_js_agreement(self):
        node = shutil.which("node")
        assert node is not None, "node is required for research-links parser agreement"
        self.assertTrue(os.path.isfile(_JS))
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn(", 0 failed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
