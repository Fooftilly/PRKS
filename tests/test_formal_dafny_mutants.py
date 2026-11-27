"""Outcome rules of the Dafny mutant harness (#1472)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dafny_mutants", ROOT / "scripts" / "formal" / "dafny_mutants.py"
)
assert _spec and _spec.loader
mutants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutants)

PROOF_FAILURE = (
    "Sequence.dfy(312,4): Error: a postcondition could not be proved on this return path\n"
    "\nDafny program verifier finished with 80 verified, 1 error\n"
)


class ClassifyTest(unittest.TestCase):
    def test_a_passing_run_survives(self) -> None:
        out = "Dafny program verifier finished with 81 verified, 0 errors\n"
        self.assertEqual(mutants.classify(0, out), "survived")

    def test_a_proof_failure_kills(self) -> None:
        self.assertEqual(mutants.classify(4, PROOF_FAILURE), "killed")

    def test_resource_exhaustion_is_inconclusive(self) -> None:
        out = "Dafny program verifier finished with 79 verified, 0 errors, 2 out of resource\n"
        self.assertEqual(mutants.classify(4, out), "inconclusive")
        out = "Dafny program verifier finished with 78 verified, 1 error, 2 out of resource\n"
        self.assertEqual(mutants.classify(4, out), "inconclusive")

    def test_a_time_out_is_inconclusive(self) -> None:
        out = "Dafny program verifier finished with 79 verified, 1 error, 1 time out\n"
        self.assertEqual(mutants.classify(4, out), "inconclusive")

    def test_a_resolution_error_is_inconclusive(self) -> None:
        out = "Sequence.dfy(10,2): Error: unresolved identifier: Foo\n1 resolution/type errors detected in Sequence.dfy\n"
        self.assertEqual(mutants.classify(2, out), "inconclusive")

    def test_a_crash_is_inconclusive(self) -> None:
        self.assertEqual(mutants.classify(4, "Unhandled exception\n"), "inconclusive")
        self.assertEqual(mutants.classify(-9, PROOF_FAILURE), "inconclusive")


if __name__ == "__main__":
    unittest.main()
