"""Contract tests for the advisory mutation-testing runner (#651 B2)."""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

from scripts.ci.docs_only import AUDITED_CONFIG_FILES
from scripts.ci.mutation_testing import (
    STATS_KEYS,
    TARGETS,
    Target,
    by_function,
    mutmut_config,
    prepare_workspace,
    survivors,
    validate_stats,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "mutation-testing.yml"


def _stats(**overrides: int) -> dict[str, int]:
    stats = {key: 0 for key in STATS_KEYS}
    stats.update(total=10, killed=7, survived=2, no_tests=1)
    stats.update(overrides)
    return stats


def _test_ids(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        f"{path.relative_to(ROOT).as_posix()}::{cls.name}::{fn.name}"
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for fn in cls.body if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test")
    }


class MutationTargetTests(unittest.TestCase):
    def test_every_target_names_existing_files_and_tests(self):
        for name, target in TARGETS.items():
            with self.subTest(target=name):
                for path in target.mutate + target.tests:
                    self.assertTrue((ROOT / path).is_file(), path)
                for test in target.deselect:
                    file = test.split("::", 1)[0]
                    self.assertIn(file, target.tests)
                    self.assertIn(test, _test_ids(ROOT / file))

    def test_the_workflow_matrix_runs_every_target(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"target: \[([^\]]*)\]", text)
        assert match is not None
        self.assertEqual([t.strip() for t in match.group(1).split(",")], sorted(TARGETS))
        self.assertIn("python scripts/ci/install_pinned_python.py mutation", text)
        self.assertNotIn("pip install", text)

    def test_config_lists_targets_selection_and_deselection(self):
        target = Target(
            mutate=("scripts/ci/a.py", "scripts/ci/b.py"),
            tests=("tests/test_a.py",),
            deselect=("tests/test_a.py::A::test_cli",),
        )
        self.assertEqual(mutmut_config(target, ["backend", "tests"]), (
            "[mutmut]\n"
            "source_paths=scripts/ci/\n"
            "only_mutate=\n    scripts/ci/a.py\n    scripts/ci/b.py\n"
            "pytest_add_cli_args_test_selection=\n    tests/test_a.py\n"
            "pytest_add_cli_args=\n    --deselect=tests/test_a.py::A::test_cli\n"
            "also_copy=\n    backend\n    tests\n"
        ))
        self.assertNotIn("pytest_add_cli_args=", mutmut_config(
            Target(mutate=("backend/x.py",), tests=("tests/test_x.py",)), [],
        ))

    def test_a_target_mutates_one_directory(self):
        with self.assertRaises(ValueError):
            mutmut_config(Target(mutate=("backend/a.py", "scripts/b.py"), tests=()), [])

    def test_the_workspace_copies_tracked_files_but_never_the_audited_configs(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            prepare_workspace(ROOT, TARGETS["text-identity"], workspace)
            self.assertTrue((workspace / "backend/domain/text_identity.py").is_file())
            self.assertTrue((workspace / "tests/test_text_identity.py").is_file())
            config = (workspace / "setup.cfg").read_text(encoding="utf-8")
            for name in AUDITED_CONFIG_FILES:
                self.assertFalse((workspace / name).exists(), name)
                self.assertNotIn(name, config)
            self.assertTrue(config.startswith("[mutmut]\nsource_paths=backend/domain/\n"))
            self.assertIn("    backend\n", config)

    def test_an_untracked_target_file_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "not tracked"):
                prepare_workspace(ROOT, Target(mutate=("backend/nope.py",), tests=()), Path(folder))


class MutationStatsTests(unittest.TestCase):
    def test_complete_stats_pass(self):
        self.assertEqual(validate_stats(_stats()), _stats())

    def test_incomplete_or_malformed_stats_fail(self):
        missing = _stats()
        del missing["segfault"]
        for label, stats in (
            ("not a dict", []),
            ("missing key", missing),
            ("extra key", {**_stats(), "other": 0}),
            ("negative", _stats(timeout=-1, killed=8)),
            ("bool", _stats(segfault=True)),
            ("nothing killed", _stats(killed=0, survived=9)),
            ("unaccounted", _stats(total=11)),
            ("segfault", _stats(segfault=1, total=11)),
            ("interrupted", _stats(check_was_interrupted_by_user=1, total=11)),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                validate_stats(stats)

    def test_survivors_are_read_from_mutmut_results(self):
        output = (
            "    scripts.ci.docs_only.x__git__mutmut_3: survived\n"
            "    scripts.ci.docs_only.x__git__mutmut_1: killed\n"
            "    scripts.ci.wiki_links.x_map_prose__mutmut_2: survived\n"
            "    backend.x.x_f__mutmut_9: no tests\n"
        )
        names = survivors(output)
        self.assertEqual(names, [
            "scripts.ci.docs_only.x__git__mutmut_3",
            "scripts.ci.wiki_links.x_map_prose__mutmut_2",
        ])
        self.assertEqual(by_function(names + ["scripts.ci.docs_only.x__git__mutmut_7"]), [
            ("scripts.ci.docs_only.x__git", 2),
            ("scripts.ci.wiki_links.x_map_prose", 1),
        ])


if __name__ == "__main__":
    unittest.main()
