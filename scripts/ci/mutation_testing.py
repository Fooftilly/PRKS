#!/usr/bin/env python3
"""Advisory mutation testing of selected pure PRKS modules (#651 B2).

``mutmut`` changes one small piece of a module at a time (a comparison, a
constant, a boolean operator) and reruns that module's unit tests. A mutant
the tests still pass on ("survived") is a behaviour change no test notices:
either a missing assertion or a redundant line of code. Each target in
:data:`TARGETS` names the modules to mutate and the fast, database-free tests
that cover them.

The run never touches the checkout. It copies the tracked files into a
temporary workspace, writes mutmut's ``[mutmut]`` configuration there (mutmut
reads only ``setup.cfg`` or ``pyproject.toml``), and runs with the
``mutation`` Hypothesis profile: derandomized, so a survivor is reproducible,
and without the health check that mutmut's repeated in-process runs trip.

Results are counts plus surviving mutant names (module and function), which
name code, never data. Survivors never fail the run: they need review, and
some are equivalent mutants. The run fails only when it is incomplete (a
failing clean test run, a crash, or stats that do not add up).

Usage::

    python scripts/ci/mutation_testing.py list
    python scripts/ci/mutation_testing.py run docs-only-ci [--max-children N] [--keep DIR]
    python scripts/ci/mutation_testing.py show docs-only-ci MUTANT --workspace DIR
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess  # nosec B404 - fixed git and mutmut argv only
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.docs_only import AUDITED_CONFIG_FILES  # noqa: E402

# The keys of mutmut 3.8.1's ``export-cicd-stats`` JSON.
STATS_KEYS = (
    "total", "killed", "survived", "timeout", "skipped", "suspicious",
    "no_tests", "segfault", "check_was_interrupted_by_user",
)
# The docs-only guard test (tests/test_ci_docs_only.py) requires that nothing
# in the tree names these external-tool configs. The workspace's generated
# setup.cfg would name them in ``also_copy``, and no target's tests read them,
# so they are not copied.
_UNCOPIED = AUDITED_CONFIG_FILES
RUN_TIMEOUT_S = 1500


@dataclass(frozen=True)
class Target:
    """Modules to mutate and the in-process unit tests that cover them."""

    mutate: tuple[str, ...]
    tests: tuple[str, ...]
    # Tests that run a module as a subprocess: mutmut's trampolines only work
    # in-process, so these cannot judge a mutant and would fail the clean run.
    deselect: tuple[str, ...] = ()


TARGETS: dict[str, Target] = {
    "text-identity": Target(
        mutate=("backend/domain/text_identity.py",),
        tests=("tests/test_text_identity.py",),
    ),
    "sync-envelope": Target(
        mutate=("backend/sync_protocol.py",),
        tests=("tests/test_sync_envelope_rules.py", "tests/test_sync_envelope_properties.py"),
    ),
    "research-markup": Target(
        mutate=("backend/research_markup.py",),
        tests=("tests/test_research_markup.py", "tests/test_research_markup_differential.py"),
    ),
    "docs-only-ci": Target(
        mutate=("scripts/ci/docs_only.py", "scripts/ci/check_docs_only.py", "scripts/ci/wiki_links.py"),
        tests=("tests/test_ci_docs_only.py", "tests/test_wiki_source_links.py"),
        deselect=(
            "tests/test_ci_docs_only.py::DocsOnlyCIClassifierTests::test_cli_scope_defaults_narrow_and_rejects_unknown",
            "tests/test_ci_docs_only.py::DocsOnlyCIClassifierTests::test_a_stale_event_base_uses_the_merge_commits_first_parent",
            "tests/test_ci_docs_only.py::DocsOnlyCIClassifierTests::test_the_markdown_validator_uses_the_same_base_as_the_classifier",
            "tests/test_ci_docs_only.py::DocsOnlyCIClassifierTests::test_a_stale_base_merge_with_a_pr_code_change_still_runs_full_ci",
            "tests/test_wiki_source_links.py::WikiLinkRuleTests::test_the_cli_rewrites_in_place_and_fails_on_a_bad_link",
        ),
    ),
}


def _tracked(root: Path) -> list[str]:
    out = subprocess.run(  # nosec B603,B607 - fixed git argv
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
    ).stdout
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def mutmut_config(target: Target, top_level: list[str]) -> str:
    """The ``[mutmut]`` section for ``target`` in a copy of the tree."""
    roots = sorted({path.rsplit("/", 1)[0] + "/" for path in target.mutate})
    if len(roots) != 1:
        raise ValueError("a target mutates files of one directory")
    lines = ["[mutmut]", f"source_paths={roots[0]}", "only_mutate="]
    lines += [f"    {path}" for path in target.mutate]
    lines.append("pytest_add_cli_args_test_selection=")
    lines += [f"    {path}" for path in target.tests]
    if target.deselect:
        lines.append("pytest_add_cli_args=")
        lines += [f"    --deselect={test}" for test in target.deselect]
    lines.append("also_copy=")
    lines += [f"    {entry}" for entry in top_level]
    return "\n".join(lines) + "\n"


def prepare_workspace(root: Path, target: Target, workspace: Path) -> None:
    """Copy the tracked tree into ``workspace`` and write its mutmut config."""
    tracked = _tracked(root)
    for name in target.mutate + target.tests:
        if name not in tracked:
            raise ValueError(f"target file is not tracked: {name}")
    top_level: set[str] = set()
    for name in tracked:
        top = name.split("/", 1)[0]
        if top in _UNCOPIED or name == "setup.cfg":
            continue
        source = root / name
        if not source.is_file():
            continue  # deleted in the working tree
        dest = workspace / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        top_level.add(top)
    (workspace / "setup.cfg").write_text(
        mutmut_config(target, sorted(top_level)), encoding="utf-8"
    )


def validate_stats(stats: object) -> dict[str, int]:
    """mutmut's counts, or ValueError when the run did not complete."""
    if not isinstance(stats, dict) or set(stats) != set(STATS_KEYS):
        raise ValueError("unexpected stats schema")
    clean: dict[str, int] = {}
    for key in STATS_KEYS:
        value = stats[key]
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid count for {key}")
        clean[key] = value
    if clean["total"] < 1 or clean["killed"] < 1:
        raise ValueError("no mutant was killed: the tests never ran against a mutant")
    if sum(value for key, value in clean.items() if key != "total") != clean["total"]:
        raise ValueError("unaccounted mutants")
    if clean["check_was_interrupted_by_user"] or clean["segfault"]:
        raise ValueError("interrupted or crashed mutants")
    return clean


def survivors(results: str) -> list[str]:
    """Surviving mutant names from ``mutmut results`` output."""
    found = []
    for line in results.splitlines():
        name, _, outcome = line.strip().rpartition(": ")
        if outcome == "survived" and name:
            found.append(name)
    return sorted(found)


def by_function(names: list[str]) -> list[tuple[str, int]]:
    """Survivor counts per mutated function (mutmut names are ``x_<func>__mutmut_<n>``)."""
    counts = Counter(name.rsplit("__mutmut_", 1)[0] for name in names)
    return sorted(counts.items())


def summary(name: str, target: Target, stats: dict[str, int], names: list[str]) -> str:
    lines = [
        f"## Mutation testing: `{name}` (advisory)",
        "",
        "Mutated: " + ", ".join(f"`{path}`" for path in target.mutate),
        "Tests: " + ", ".join(f"`{path}`" for path in target.tests),
        "",
        "| Outcome | Count |",
        "| --- | ---: |",
        *(f"| {key} | {stats[key]} |" for key in STATS_KEYS),
        "",
        "`no_tests` mutants are in code the selected tests never call.",
        "A survivor is a change no test noticed; review it before adding a test,",
        "because some survivors are equivalent (they cannot change behaviour).",
    ]
    if names:
        lines += ["", "| Surviving mutants in | Count |", "| --- | ---: |"]
        lines += [f"| `{func}` | {count} |" for func, count in by_function(names)]
        lines += ["", f"Reproduce locally with `run {name} --keep DIR`, then "
                  f"`show {name} <mutant> --workspace DIR`."]
    return "\n".join(lines) + "\n"


def _mutmut(workspace: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run mutmut in its own process group, so a timeout also stops its forked workers."""
    env = {**os.environ, "PRKS_HYPOTHESIS_PROFILE": "mutation"}
    argv = [sys.executable, "-m", "mutmut", *args]
    with subprocess.Popen(  # nosec B603 - fixed interpreter and mutmut argv
        argv, cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Its own session also keeps it from Ctrl+C, so stop the group here.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run(name: str, workspace: Path, max_children: int) -> int:
    target = TARGETS[name]
    prepare_workspace(ROOT, target, workspace)
    try:
        ran = _mutmut(workspace, "run", "--max-children", str(max_children), timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"Mutation run incomplete: {name} exceeded {RUN_TIMEOUT_S} s", file=sys.stderr)
        return 1
    exported = _mutmut(workspace, "export-cicd-stats")
    stats_file = workspace / "mutants" / "mutmut-cicd-stats.json"
    if ran.returncode or exported.returncode or not stats_file.is_file():
        # The clean (unmutated) test run failed, or mutmut crashed. Its last
        # lines name the failing test; they hold no data beyond test output.
        print(f"Mutation run incomplete: {name}", file=sys.stderr)
        print("\n".join(ran.stdout.splitlines()[-40:]), file=sys.stderr)
        return 1
    try:
        stats = validate_stats(json.loads(stats_file.read_text(encoding="utf-8")))
    except ValueError as exc:
        print(f"Mutation run incomplete: {name}: {exc}", file=sys.stderr)
        return 1
    results = _mutmut(workspace, "results")
    if results.returncode:
        print(f"Mutation run incomplete: {name}: mutmut results failed", file=sys.stderr)
        return 1
    names = survivors(results.stdout)
    text = summary(name, target, stats, names)
    print(text)
    print("\n".join(names))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("target", choices=sorted(TARGETS))
    run_parser.add_argument("--max-children", type=int, default=os.cpu_count() or 2)
    run_parser.add_argument("--keep", type=Path, help="keep the workspace here (for show)")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("target", choices=sorted(TARGETS))
    show_parser.add_argument("mutant")
    show_parser.add_argument("--workspace", type=Path, required=True, help="the --keep folder of a run")
    args = parser.parse_args(argv)
    if args.command == "list":
        for name, target in sorted(TARGETS.items()):
            print(f"{name}: {', '.join(target.mutate)}")
        return 0
    if args.command == "show":
        shown = _mutmut(args.workspace / args.target, "show", args.mutant)
        print(shown.stdout or shown.stderr)
        return shown.returncode
    if args.keep is not None:
        workspace = args.keep / args.target
        try:
            workspace.mkdir(parents=True)
        except FileExistsError:
            # Never delete a folder this run did not create.
            print(f"Mutation workspace already exists: {workspace}; remove it or pick another --keep",
                  file=sys.stderr)
            return 1
        return run(args.target, workspace, args.max_children)
    with tempfile.TemporaryDirectory(prefix="prks-mutation-") as folder:
        return run(args.target, Path(folder), args.max_children)


if __name__ == "__main__":
    raise SystemExit(main())
