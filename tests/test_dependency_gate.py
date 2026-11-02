"""Focused tests for the dependency consistency gate."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from backend.dependency_gate import (
    RequirementsPinError,
    assert_remediation_is_safe,
    default_venv_python_cmd,
    derived_inventory_names,
    detect_platform_context,
    dockerfile_apt_packages,
    dockerfile_python_base_version,
    format_pip_install_command,
    format_venv_create_commands,
    parse_requirements_pins,
    pinned_openapi_core_version,
    pinned_playwright_version,
    python_min_version,
    remediation_message,
    run_repo_gate,
    run_runtime_gate,
    run_unit_contract_gate,
    validate_dockerfile,
    validate_installed_pins,
    validate_inventory,
    validate_npm_island,
    validate_python_version,
    validate_requirements_file,
    validate_vue_production,
)

# Exact pins only for the CI test-gate install argv (see RepoGateLiveTests).
_TEST_GATE_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^#\s]+)$")
_TEST_GATE_APPROVED_OPTIONS = frozenset(
    {
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
    }
)
_TEST_GATE_APPROVED_OPTIONS_WITH_VALUE = {
    "--only-binary": frozenset({":all:"}),
}
_TEST_GATE_REJECTED_SOURCE_OPTIONS = frozenset(
    {
        "-r",
        "--requirement",
        "-e",
        "--editable",
    }
)
_TEST_GATE_APPROVED_INSTALL_STEP = "Install pinned runtime dependencies"
_TEST_GATE_RUN_TESTS_STEP = "Run PRKS unit, API, structural, and Node tests"
# The PostgreSQL helper job (#623): its own pinned install and exactly
# ``scripts/test-postgres``. Both steps or neither.
_TEST_GATE_PG_INSTALL_STEP = "Install pinned runtime and PostgreSQL dependencies"
_TEST_GATE_PG_RUN_STEP = "Run PostgreSQL helper and harness tests"
_TEST_GATE_PG_AGG_STEP = "Require both PostgreSQL helper shards"
_TEST_GATE_PG_AGG_BODY = (
    'set -euo pipefail\n'
    'if [[ "$SHARD_RESULT" != "success" ]]; then\n'
    'echo "::error::PostgreSQL helper shards did not all pass: $SHARD_RESULT"\n'
    'exit 1\n'
    'fi\n'
    'echo "All PostgreSQL helper shards passed."'
)
# PostgreSQL is a production runtime dependency from #630 onward.
# The backend parity job (#625): the same pinned install and exactly
# ``scripts/test-postgres --parity``. Both steps or neither.
_TEST_GATE_PARITY_INSTALL_STEP = "Install pinned runtime and PostgreSQL dependencies for parity"
_TEST_GATE_PARITY_RUN_STEP = "Run SQLite and PostgreSQL backend parity legs"
# (install step, run step, the run step's exact argv)
_TEST_GATE_PG_PAIRS = (
    (_TEST_GATE_PG_INSTALL_STEP, _TEST_GATE_PG_RUN_STEP, ("scripts/test-postgres",)),
    (
        _TEST_GATE_PARITY_INSTALL_STEP,
        _TEST_GATE_PARITY_RUN_STEP,
        ("scripts/test-postgres", "--parity"),
    ),
)

_TEST_GATE_DOCS_RUNS = {
    "Classify documentation-only PR (fail closed)": ("|", "set -euo pipefail\ndocs_only=false\nif [[ \"$PRKS_EVENT_NAME\" == \"pull_request\" && \"$PRKS_PR_BASE_SHA\" =~ ^[0-9a-fA-F]{40}$ ]]; then\nif git fetch --no-tags --depth=1 origin \"$PRKS_PR_BASE_SHA\"; then\nif base_classifier=\"$(mktemp)\"; then\nif git show \"$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py\" > \"$base_classifier\"; then\ndecision=\"$(python \"$base_classifier\" --base \"$PRKS_PR_BASE_SHA\")\" || decision=false\nif [[ \"$decision\" == \"true\" ]]; then\ndocs_only=true\nfi\nelse\necho \"::warning::Docs classifier absent from base; running full tests.\"\nfi\nrm -f \"$base_classifier\"\nelse\necho \"::warning::Failed to allocate classifier file; running full tests.\"\nfi\nelse\necho \"::warning::Could not fetch immutable PR base; running full tests.\"\nfi\nfi\necho \"docs_only=$docs_only\" >> \"$GITHUB_OUTPUT\""),
    "Install pinned Markdown validator (docs-only PR)": (
        "", "python scripts/ci/install_pinned_python.py docs"
    ),
    "Validate changed documentation links, anchors, and fences": (
        "", 'python -m scripts.ci.check_docs_only --base "$PRKS_PR_BASE_SHA"'
    ),
}
_TEST_GATE_ALLOWED_RUN_STEP_NAMES = frozenset(
    {
        _TEST_GATE_APPROVED_INSTALL_STEP,
        _TEST_GATE_RUN_TESTS_STEP,
        _TEST_GATE_PG_AGG_STEP,
        *_TEST_GATE_DOCS_RUNS,
        *(name for pair in _TEST_GATE_PG_PAIRS for name in pair[:2]),
    }
)
# Env vars that can inject requirements/config into pip even with an allowlisted argv.
_TEST_GATE_REJECTED_PIP_ENV = frozenset(
    {
        "PIP_REQUIREMENT",
        "PIP_EDITABLE",
        "PIP_CONSTRAINT",
        "PIP_CONFIG_FILE",
    }
)
_TEST_GATE_REJECTED_PIP_ENV_RE = re.compile(
    r"(?m)^[ \t]+("
    + "|".join(sorted(_TEST_GATE_REJECTED_PIP_ENV))
    + r")\s*:"
)


def parse_test_gate_pip_install_pins(pip_args: list[str]) -> dict[str, str]:
    """Parse `python -m pip install ...` argv into exact name==version pins.

    Every operand after ``install`` must be an approved pip option or an exact
    ``name==version`` pin. Bare names, ``-r``/``-e``, paths/wheels, and
    URL/VCS sources are rejected. ``--isolated`` is required so ``PIP_*``
    env vars and user config cannot inject requirements.
    """
    if pip_args[:4] != ["python", "-m", "pip", "install"]:
        raise ValueError("expected python -m pip install prefix")
    if "--isolated" not in pip_args[4:]:
        raise ValueError("approved install requires --isolated")
    pins: dict[str, str] = {}
    index = 4
    while index < len(pip_args):
        arg = pip_args[index]
        if (
            arg in _TEST_GATE_REJECTED_SOURCE_OPTIONS
            or arg.startswith("--requirement=")
            or arg.startswith("--editable=")
        ):
            raise ValueError(f"disallowed requirement source option: {arg}")
        if arg in _TEST_GATE_APPROVED_OPTIONS:
            index += 1
            continue
        if arg in _TEST_GATE_APPROVED_OPTIONS_WITH_VALUE:
            if index + 1 >= len(pip_args):
                raise ValueError(f"option {arg} missing value")
            value = pip_args[index + 1]
            allowed = _TEST_GATE_APPROVED_OPTIONS_WITH_VALUE[arg]
            if value not in allowed:
                raise ValueError(f"disallowed value for {arg}: {value}")
            index += 2
            continue
        if arg.startswith("-"):
            raise ValueError(f"unapproved pip option: {arg}")
        pin_match = _TEST_GATE_PIN_RE.fullmatch(arg)
        if pin_match is None:
            raise ValueError(f"non-exact package/requirement source: {arg}")
        name, version = pin_match.group(1), pin_match.group(2)
        if name in pins:
            raise ValueError(f"duplicate package pin for {name}")
        pins[name] = version
        index += 1
    return pins


def test_gate_install_argv_from_run_body(
    scalar_style: str, body_lines: list[str]
) -> list[str]:
    """Extract the approved Install-step ``python -m pip install`` argv.

    Literal ``|`` bodies may contain only one non-comment executable command
    (so ``pip install`` / ``python3 -m pip install`` / ``uv pip install``
    after the pinned install cannot hide). Folded ``>`` bodies keep a single
    folded command and the fail-closed argv parse.
    """
    if scalar_style == ">":
        # Folded: YAML turns newlines into spaces — one shell command.
        folded = " ".join(
            line for line in body_lines if line and not line.startswith("#")
        )
        if not folded:
            raise ValueError("empty Install step run body")
        args = shlex.split(folded)
    else:
        executable = [
            line for line in body_lines if line and not line.startswith("#")
        ]
        if len(executable) != 1:
            raise ValueError(
                "literal Install step must contain exactly one non-comment "
                f"executable command (found {len(executable)})"
            )
        args = shlex.split(executable[0])
    if args[:4] != ["python", "-m", "pip", "install"]:
        raise ValueError(
            "Install step command must be python -m pip install "
            f"(got {' '.join(args[:4]) if args else '<empty>'})"
        )
    return args


def _test_gate_run_body_without_shell_comments(body: str) -> str:
    """Drop blank and ``#`` comment lines from a run body."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _normalize_yaml_flow_scalar(value: str) -> str:
    """Strip one layer of YAML single/double quotes from an inline scalar."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def iter_test_gate_step_run_bodies(
    workflow: str, *, step_indent: int = 6
) -> list[tuple[str | None, str, str]]:
    """Return ``(step_name, scalar_style, run_body)`` for every step with ``run:``.

    ``scalar_style`` is ``"|"``, ``">"``, or ``""`` for a single-line ``run:``.
    ``step_name`` is None for unnamed ``- run:`` steps. Scoped to GitHub Actions
    list items at ``step_indent`` (PRKS test-gate uses 6).
    """
    dash = f"{' ' * step_indent}- "
    field_indent = step_indent + 2
    field = " " * field_indent
    body_indent = step_indent + 4
    starts = [m.start() for m in re.finditer(rf"(?m)^{re.escape(dash)}", workflow)]
    results: list[tuple[str | None, str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(workflow)
        block = workflow[start:end]
        first_nl = block.find("\n")
        first = block[:first_nl] if first_nl >= 0 else block
        name: str | None = None
        name_match = re.match(rf"^{re.escape(dash)}name:\s*(.+)$", first)
        if name_match:
            name = name_match.group(1).strip()
        # `- run: …` on the list item itself (no separate name key).
        inline_run = re.match(rf"^{re.escape(dash)}run:\s*(.*)$", first)
        if inline_run is not None:
            results.append((name, "", inline_run.group(1)))
            continue
        run_match = re.search(
            rf"(?m)^{re.escape(field)}run:\s*"
            rf"(?:([|>])[-+]?\n((?: {{{body_indent},}}[^\n]*\n)+)|([^\n]*))",
            block,
        )
        if run_match is None:
            continue
        if run_match.group(1) is not None:
            results.append((name, run_match.group(1), run_match.group(2)))
        else:
            results.append((name, "", run_match.group(3) or ""))
    return results


def count_test_gate_steps_named(
    workflow: str, step_name: str, *, step_indent: int = 6
) -> int:
    """Count steps[] entries whose ``name:`` equals ``step_name`` (with or without run)."""
    dash = f"{' ' * step_indent}- "
    starts = [m.start() for m in re.finditer(rf"(?m)^{re.escape(dash)}", workflow)]
    count = 0
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(workflow)
        block = workflow[start:end]
        first_nl = block.find("\n")
        first = block[:first_nl] if first_nl >= 0 else block
        name_match = re.match(rf"^{re.escape(dash)}name:\s*(.+)$", first)
        if name_match and name_match.group(1).strip() == step_name:
            count += 1
    return count


def unique_test_gate_install_step(workflow: str) -> tuple[str, str]:
    """Return ``(scalar_style, run_body)`` for the sole approved Install step.

    Exactly one step named ``Install pinned runtime dependencies`` must exist;
    a duplicate same-named step is refused so it cannot bypass allowlisting.
    """
    named = count_test_gate_steps_named(workflow, _TEST_GATE_APPROVED_INSTALL_STEP)
    if named == 0:
        raise ValueError(
            f"missing approved Install step {_TEST_GATE_APPROVED_INSTALL_STEP!r}"
        )
    if named > 1:
        raise ValueError(
            f"exactly one {_TEST_GATE_APPROVED_INSTALL_STEP!r} step required "
            f"(found {named})"
        )
    for name, style, body in iter_test_gate_step_run_bodies(workflow):
        if name == _TEST_GATE_APPROVED_INSTALL_STEP:
            return style, body
    raise ValueError(
        f"approved Install step {_TEST_GATE_APPROVED_INSTALL_STEP!r} has no run body"
    )


def unique_test_gate_run_tests_step(workflow: str) -> tuple[str, str]:
    """Return ``(scalar_style, run_body)`` for the sole ``run_tests.py`` step."""
    named = count_test_gate_steps_named(workflow, _TEST_GATE_RUN_TESTS_STEP)
    if named == 0:
        raise ValueError(f"missing run-tests step {_TEST_GATE_RUN_TESTS_STEP!r}")
    if named > 1:
        raise ValueError(
            f"exactly one {_TEST_GATE_RUN_TESTS_STEP!r} step required "
            f"(found {named})"
        )
    for name, style, body in iter_test_gate_step_run_bodies(workflow):
        if name == _TEST_GATE_RUN_TESTS_STEP:
            return style, body
    raise ValueError(
        f"run-tests step {_TEST_GATE_RUN_TESTS_STEP!r} has no run body"
    )


def assert_test_gate_run_tests_body(scalar_style: str, body: str) -> None:
    """Require the run-tests step body to be exactly ``python run_tests.py``."""
    text = _test_gate_run_body_without_shell_comments(body)
    if not text:
        raise ValueError("empty run-tests step run body")
    if scalar_style == "|":
        lines = text.splitlines()
        if len(lines) != 1:
            raise ValueError(
                "literal run-tests step must contain exactly one non-comment "
                f"command (found {len(lines)})"
            )
        command = _normalize_yaml_flow_scalar(lines[0])
    elif scalar_style == ">":
        command = _normalize_yaml_flow_scalar(" ".join(text.splitlines()))
    else:
        command = _normalize_yaml_flow_scalar(text)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"unparseable run-tests command: {exc}") from exc
    if argv != ["python", "run_tests.py"]:
        raise ValueError(
            "run-tests step must be exactly `python run_tests.py` "
            f"(got {argv!r})"
        )


def unique_test_gate_pg_pair(
    workflow: str, install_step: str, run_step: str, expected_argv: tuple[str, ...]
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Return one PostgreSQL job's ``(install, run)`` bodies, or None when absent.

    Both named steps must appear exactly once together, and the run body must
    be exactly ``expected_argv``.
    """
    expected = " ".join(expected_argv)
    install_named = count_test_gate_steps_named(workflow, install_step)
    run_named = count_test_gate_steps_named(workflow, run_step)
    if (install_named, run_named) == (0, 0):
        return None
    if (install_named, run_named) != (1, 1):
        raise ValueError(
            "the PostgreSQL job needs exactly one "
            f"{install_step!r} and one {run_step!r} step"
        )
    bodies = {
        name: (style, body)
        for name, style, body in iter_test_gate_step_run_bodies(workflow)
        if name in (install_step, run_step)
    }
    if len(bodies) != 2:
        raise ValueError("the PostgreSQL job steps need run bodies")
    run_style, run_body = bodies[run_step]
    text = _test_gate_run_body_without_shell_comments(run_body)
    if run_style == "|" and len(text.splitlines()) != 1:
        raise ValueError("the PostgreSQL run step must contain exactly one command")
    command = _normalize_yaml_flow_scalar(" ".join(text.splitlines()))
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"unparseable PostgreSQL run command: {exc}") from exc
    if tuple(argv) != expected_argv:
        raise ValueError(f"the PostgreSQL run step must be exactly `{expected}` (got {argv!r})")
    return bodies[install_step], bodies[run_step]


def unique_test_gate_pg_steps(workflow: str) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """The PostgreSQL helper job's pair (#623): exactly ``scripts/test-postgres``."""
    return unique_test_gate_pg_pair(workflow, *_TEST_GATE_PG_PAIRS[0])


def unique_test_gate_parity_steps(
    workflow: str,
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """The backend parity job's pair (#625): exactly ``scripts/test-postgres --parity``."""
    return unique_test_gate_pg_pair(workflow, *_TEST_GATE_PG_PAIRS[1])


def assert_test_gate_no_pip_inject_env(workflow: str) -> None:
    """Refuse job/step ``PIP_REQUIREMENT`` / ``PIP_EDITABLE`` / similar env keys.

    ``--isolated`` is still required on the Install argv; this rejects the
    inject vectors at the workflow YAML layer as well.
    """
    for match in _TEST_GATE_REJECTED_PIP_ENV_RE.finditer(workflow):
        line_start = workflow.rfind("\n", 0, match.start()) + 1
        line_end = workflow.find("\n", match.start())
        if line_end < 0:
            line_end = len(workflow)
        line = workflow[line_start:line_end]
        if line.lstrip().startswith("#"):
            continue
        raise ValueError(f"disallowed pip inject env: {match.group(1)}")


def assert_test_gate_pg_shards_guarded(workflow: str, pairs: list) -> bool:
    """Enforce reviewed shard layout and aggregator without changing policy."""
    # Only the explicitly reviewed two-shard form can have an aggregator.
    # Guard the matrix cardinality, wrapper command and dependency so a
    # successful required check cannot hide a missing/failed helper shard.
    sharded = "  postgres-helpers-shards:" in workflow
    aggregator = count_test_gate_steps_named(workflow, _TEST_GATE_PG_AGG_STEP)
    if sharded:
        requirements = (
            "    name: PostgreSQL helpers (${{ matrix.shard }}/2)",
            "        shard: [1, 2]",
            "          PRKS_PG_SHARD: ${{ matrix.shard }}/2",
            "    needs: postgres-helpers-shards",
            "    if: ${{ always() && github.event_name != 'schedule' }}",
            "    name: PostgreSQL helper tests",
        )
        if not all(text in workflow for text in requirements):
            raise ValueError("PostgreSQL helper shards require the reviewed matrix and aggregator")
        if pairs[0] is None or aggregator != 1:
            raise ValueError("PostgreSQL helper shards require one approved aggregator")
    elif aggregator:
        raise ValueError("Unexpected PostgreSQL helper aggregator without shards")
    return sharded


def assert_test_gate_single_run_step_allowed(name: str, style: str, body: str) -> None:
    """Allow only the explicitly reviewed run step name and command."""
    if name not in _TEST_GATE_ALLOWED_RUN_STEP_NAMES:
        label = name if name else "<unnamed step>"
        raise ValueError(f"disallowed run step: {label}")
    if name == _TEST_GATE_PG_AGG_STEP:
        if style != "|" or _test_gate_run_body_without_shell_comments(body) != _TEST_GATE_PG_AGG_BODY:
            raise ValueError("unreviewed PostgreSQL helper shard aggregator command")
    if name in _TEST_GATE_DOCS_RUNS:
        expected_style, expected_body = _TEST_GATE_DOCS_RUNS[name]
        normalized = (
            _test_gate_run_body_without_shell_comments(body)
            if style == "|" else body.strip()
        )
        if style != expected_style or normalized != expected_body:
            raise ValueError(f"unreviewed docs-only CI command: {name}")


def assert_test_gate_run_steps_allowlisted(workflow: str) -> None:
    """Invert policy: only Install + run_tests (plus the PostgreSQL helper and
    backend parity jobs' install + ``scripts/test-postgres`` pairs) may have
    ``run:`` bodies.

    ``uses:``-only steps are unrestricted. Any additional ``run:`` step is
    refused — including quoted inline installs and backslash-continued
    spellings — without enumerating pip/shell forms. Job/step pip-inject
    env vars are refused; Install argv must include ``--isolated``.
    """
    assert_test_gate_no_pip_inject_env(workflow)
    unique_test_gate_install_step(workflow)
    style, body = unique_test_gate_run_tests_step(workflow)
    assert_test_gate_run_tests_body(style, body)
    pairs = [unique_test_gate_pg_pair(workflow, *pair) for pair in _TEST_GATE_PG_PAIRS]
    run_steps = iter_test_gate_step_run_bodies(workflow)

    sharded = assert_test_gate_pg_shards_guarded(workflow, pairs)
    docs_present = {
        name for name, _style, _body in run_steps if name in _TEST_GATE_DOCS_RUNS
    }
    # Initial fixtures predate the docs-only pilot. For production workflows,
    # require all three guarded docs commands or none; partial setup fails.
    if docs_present and docs_present != set(_TEST_GATE_DOCS_RUNS):
        raise ValueError("incomplete required docs-only CI step set")
    for name, style, body in run_steps:
        assert_test_gate_single_run_step_allowed(name, style, body)
    expected = 2 + 2 * sum(pair is not None for pair in pairs) + len(docs_present) + int(sharded)
    if len(run_steps) != expected:
        raise ValueError(
            f"test-gate must have exactly {expected} approved run steps; "
            f"found {len(run_steps)}"
        )


class RequirementsParsingTests(unittest.TestCase):
    def test_parse_exact_pins_ignores_comments(self):
        text = "# comment\nPyMuPDF==1.28.2\n\nPillow==12.3.0\n"
        self.assertEqual(
            parse_requirements_pins(text),
            {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"},
        )

    def test_parse_allows_trailing_comment(self):
        text = "Pillow==12.3.0  # keep exact\n"
        self.assertEqual(parse_requirements_pins(text), {"Pillow": "12.3.0"})

    def test_parse_rejects_gte_operator(self):
        with self.assertRaises(RequirementsPinError) as ctx:
            parse_requirements_pins("Pillow>=12.3.0\n", source="requirements.txt")
        self.assertIn("exact name==version", str(ctx.exception))
        self.assertIn("Pillow>=12.3.0", str(ctx.exception))

    def test_parse_rejects_unpinned_and_malformed(self):
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("Pillow\n")
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("not a requirement!!!\n")
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("Pillow~=12.3.0\n")

    def test_validate_requirements_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            path.write_text("Pillow>=12.3.0\n", encoding="utf-8")
            result, pins = validate_requirements_file(path)
            self.assertFalse(result.ok)
            self.assertEqual(pins, {})
            self.assertEqual(result.issues[0].code, "non_exact_requirement")

    def test_runtime_gate_fails_on_non_exact_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "PyMuPDF==1.28.2\nPillow>=12.3.0\n",
                encoding="utf-8",
            )
            (root / "dependency-inventory.json").write_text(
                json.dumps({"python_min_version": [3, 12], "dependencies": []}),
                encoding="utf-8",
            )
            result = run_runtime_gate(
                repo_root=root,
                version_lookup=lambda _n: "1.28.2",
                current_python=(3, 12, 0),
                ctx=detect_platform_context(
                    prefix="/tmp/venv",
                    base_prefix="/usr",
                    executable="/tmp/venv/bin/python",
                    system="Linux",
                    environ={},
                    externally_managed=False,
                ),
            )
            self.assertFalse(result.ok)
            self.assertTrue(
                any(i.code == "non_exact_requirement" for i in result.issues)
            )

    def test_playwright_pin_from_repo(self):
        pin = pinned_playwright_version(_PROJECT)
        self.assertRegex(pin, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"playwright=={pin}", (_PROJECT / "requirements-dev.txt").read_text())


class PythonVersionTests(unittest.TestCase):
    def test_minimum_from_inventory(self):
        self.assertEqual(python_min_version(_PROJECT)[:2], (3, 12))

    def test_too_old_fails(self):
        result = validate_python_version(current=(3, 11, 0), minimum=(3, 12))
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "python_too_old")

    def test_current_ok(self):
        result = validate_python_version(current=(3, 12, 0), minimum=(3, 12))
        self.assertTrue(result.ok)


class InstalledPinTests(unittest.TestCase):
    def test_missing_package(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2"},
            version_lookup=lambda _n: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "missing_package")

    def test_wrong_version(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2"},
            version_lookup=lambda _n: "1.0.0",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "version_mismatch")

    def test_correct_version(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"},
            version_lookup=lambda n: {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"}[n],
        )
        self.assertTrue(result.ok)

    def test_unit_contract_gate_requires_contract_and_property_deps(self):
        """Unit preflight checks openapi-core and Hypothesis, not Playwright."""
        pin = pinned_openapi_core_version(_PROJECT)
        dev_pins = parse_requirements_pins(
            (_PROJECT / "requirements-dev.txt").read_text()
        )
        self.assertEqual(pin, dev_pins["openapi-core"])
        runtime_pins = parse_requirements_pins(
            (_PROJECT / "requirements.txt").read_text()
        )
        seen: list[str] = []

        def lookup(name: str) -> str | None:
            seen.append(name)
            if name == "openapi-core":
                return pin
            if name == "hypothesis":
                return dev_pins["hypothesis"]
            return runtime_pins.get(name)

        result = run_unit_contract_gate(
            repo_root=_PROJECT,
            version_lookup=lookup,
            current_python=(3, 12, 0),
        )
        self.assertTrue(result.ok, result.issues)
        self.assertIn("openapi-core", seen)
        self.assertIn("hypothesis", seen)
        self.assertNotIn("playwright", seen)

    def test_unit_contract_gate_fails_when_openapi_core_missing(self):
        runtime_pins = parse_requirements_pins(
            (_PROJECT / "requirements.txt").read_text()
        )

        dev_pins = parse_requirements_pins(
            (_PROJECT / "requirements-dev.txt").read_text()
        )

        def lookup(name: str) -> str | None:
            if name == "openapi-core":
                return None
            return dev_pins.get(name, runtime_pins.get(name))

        result = run_unit_contract_gate(
            repo_root=_PROJECT,
            version_lookup=lookup,
            current_python=(3, 12, 0),
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any(i.code == "missing_package" for i in result.issues),
            result.issues,
        )

    def test_unit_contract_gate_fails_when_hypothesis_missing(self):
        runtime_pins = parse_requirements_pins((_PROJECT / "requirements.txt").read_text())
        dev_pins = parse_requirements_pins((_PROJECT / "requirements-dev.txt").read_text())

        def lookup(name: str) -> str | None:
            if name == "hypothesis":
                return None
            return dev_pins.get(name, runtime_pins.get(name))

        result = run_unit_contract_gate(
            repo_root=_PROJECT, version_lookup=lookup, current_python=(3, 12, 0),
        )
        self.assertFalse(result.ok)
        self.assertTrue(any(i.code == "missing_package" for i in result.issues))

    def test_unit_contract_gate_missing_requirements_dev_is_missing_requirements(self):
        """Absent requirements-dev must not raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "PyMuPDF==1.28.2\nPillow==12.3.0\npydantic==2.13.5\n",
                encoding="utf-8",
            )
            (root / "dependency-inventory.json").write_text(
                json.dumps({"python_min_version": [3, 12], "dependencies": []}),
                encoding="utf-8",
            )

            def lookup(name: str) -> str | None:
                return {
                    "PyMuPDF": "1.28.2",
                    "Pillow": "12.3.0",
                    "pydantic": "2.13.5",
                }.get(name)

            result = run_unit_contract_gate(
                repo_root=root,
                version_lookup=lookup,
                current_python=(3, 12, 0),
            )
        self.assertFalse(result.ok)
        self.assertTrue(
            any(i.code == "missing_requirements" for i in result.issues),
            result.issues,
        )


class RemediationMessageTests(unittest.TestCase):
    def test_venv_recommends_same_interpreter_pip(self):
        ctx = detect_platform_context(
            prefix="/tmp/.venv",
            base_prefix="/usr",
            executable="/tmp/.venv/bin/python",
            system="Linux",
            environ={},
            externally_managed=False,
        )
        msg = remediation_message(
            missing=["Pillow"],
            ctx=ctx,
            requirements_file="requirements.txt",
        )
        self.assertIn("/tmp/.venv/bin/python -m pip install -r requirements.txt", msg)
        assert_remediation_is_safe(msg)

    def test_windows_quotes_paths(self):
        cmd = format_pip_install_command(
            r"C:\Users\Me\PRKS\.venv\Scripts\python.exe",
            r"C:\Users\Me\PRKS\requirements.txt",
            is_windows=True,
        )
        self.assertIn(".venv", cmd)
        self.assertIn("-m pip install -r", cmd)

    def test_windows_venv_commands_prefer_py_launcher(self):
        self.assertEqual(default_venv_python_cmd(is_windows=True), "py -3")
        self.assertEqual(default_venv_python_cmd(is_windows=False), "python3")
        cmds = format_venv_create_commands(is_windows=True)
        self.assertEqual(cmds[0], "py -3 -m venv .venv")
        self.assertIn(r".venv\Scripts\python.exe", cmds[1])
        self.assertNotIn("python3", cmds[0])
        # Explicit override still honored.
        overridden = format_venv_create_commands(
            is_windows=True, python_cmd=r"C:\Python312\python.exe"
        )
        self.assertTrue(overridden[0].startswith(r"C:\Python312\python.exe"))

    def test_windows_remediation_uses_py_not_python3(self):
        ctx = detect_platform_context(
            prefix=r"C:\Python312",
            base_prefix=r"C:\Python312",
            executable=r"C:\Python312\python.exe",
            system="Windows",
            environ={},
            externally_managed=False,
        )
        msg = remediation_message(missing=["Pillow"], ctx=ctx)
        assert_remediation_is_safe(msg)
        self.assertIn("py -3 -m venv .venv", msg)
        self.assertNotIn("python3 -m venv", msg)

    def test_externally_managed_never_sudo_or_break(self):
        ctx = detect_platform_context(
            prefix="/usr",
            base_prefix="/usr",
            executable="/usr/bin/python3",
            system="Linux",
            environ={},
            externally_managed=True,
        )
        msg = remediation_message(missing=["Pillow"], ctx=ctx)
        self.assertIn("EXTERNALLY-MANAGED", msg)
        self.assertIn(".venv", msg)
        self.assertIn("Do not use sudo pip", msg)
        assert_remediation_is_safe(msg)
        for cmd in format_venv_create_commands(is_windows=False):
            self.assertIn(cmd, msg)

    def test_docker_rebuild_not_pip(self):
        ctx = detect_platform_context(
            prefix="/usr",
            base_prefix="/usr",
            executable="/usr/local/bin/python",
            system="Linux",
            environ={"PRKS_CONTAINER": "1"},
            externally_managed=False,
        )
        msg = remediation_message(mismatched=[("Pillow", "1.0", "12.3.0")], ctx=ctx)
        assert_remediation_is_safe(msg)
        self.assertIn("PRKS_CONTAINER", msg)
        self.assertIn("docker-build", msg)
        self.assertNotIn("pip install", msg)

    def test_assert_safe_rejects_bad_advice(self):
        with self.assertRaises(AssertionError):
            assert_remediation_is_safe("  sudo pip install foo")
        with self.assertRaises(AssertionError):
            assert_remediation_is_safe("pip install --break-system-packages foo")
        # Negated warnings are fine.
        assert_remediation_is_safe("Do not use sudo pip or pip --break-system-packages.")


class ResolvePythonTests(unittest.TestCase):
    def test_builders_use_resolve_python_not_bare_python3(self):
        for rel in (
            "tools/frontend-vendor/build.mjs",
            "tools/pdf-viewer/build.mjs",
            "tools/research-graph/build.mjs",
            "frontend-app/scripts/build.mjs",
        ):
            text = (_PROJECT / rel).read_text(encoding="utf-8")
            self.assertIn("resolvePython()", text)
            self.assertNotIn('spawnSync(\n  "python3"', text)
            self.assertNotIn("spawnSync('python3'", text)
            self.assertNotIn('spawnSync("python3"', text)

    def test_resolve_python_module_windows_candidates(self):
        script = r"""
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const path = require("node:path");
const fs = require("node:fs");
const src = fs.readFileSync(path.resolve("tools/resolve-python3.mjs"), "utf8");
if (!src.includes("Windows") || !src.includes("py.exe")) {
  throw new Error("missing Windows py launcher candidate");
}
if (!src.includes("resolvePython")) throw new Error("missing resolvePython export");
if (src.includes('spawnSync("python3"')) throw new Error("bare python3 spawn");
console.log("ok");
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=str(_PROJECT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_resolve_python_runs_on_host(self):
        proc = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import { resolvePython } from './tools/resolve-python3.mjs'; "
                "const p = resolvePython(); "
                "if (!p.executable.startsWith('/')) process.exit(2); "
                "console.log(p.executable);",
            ],
            cwd=str(_PROJECT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().startswith("/"))


class NpmIslandTests(unittest.TestCase):
    def test_lock_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"cytoscape": "3.34.3"}}),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "node_modules/cytoscape": {"version": "3.30.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = validate_npm_island(
                {
                    "name": "tmp",
                    "dir": root,
                    "react_guard": False,
                    "types_guard": False,
                }
            )
            self.assertFalse(result.ok)
            self.assertTrue(any(i.code == "lock_version_mismatch" for i in result.issues))

    def test_react_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "18.3.1", "react-dom": "18.2.0"},
                        "devDependencies": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "node_modules/react": {"version": "18.3.1"},
                            "node_modules/react-dom": {"version": "18.2.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = validate_npm_island(
                {
                    "name": "tmp",
                    "dir": root,
                    "react_guard": True,
                    "types_guard": False,
                }
            )
            self.assertFalse(result.ok)
            self.assertTrue(any(i.code == "react_dom_mismatch" for i in result.issues))


class InventoryCoverageTests(unittest.TestCase):
    def test_derived_matches_live_inventory(self):
        inv = json.loads(
            (_PROJECT / "dependency-inventory.json").read_text(encoding="utf-8")
        )
        declared = {d["name"] for d in inv["dependencies"]}
        self.assertEqual(declared, derived_inventory_names(_PROJECT))

    def test_missing_inventory_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Minimal mirrors of authoritative manifests.
            (root / "requirements.txt").write_text(
                "PyMuPDF==1.28.2\nPillow==12.3.0\n", encoding="utf-8"
            )
            (root / "requirements-dev.txt").write_text(
                "playwright==1.63.0\n", encoding="utf-8"
            )
            for island, deps in (
                ("pdf-viewer", {"react": "18.3.1"}),
                ("research-graph", {"cytoscape": "3.34.3"}),
                ("frontend-vendor", {"dompurify": "3.0.0"}),
            ):
                d = root / "tools" / island
                d.mkdir(parents=True)
                (d / "package.json").write_text(
                    json.dumps({"dependencies": deps}), encoding="utf-8"
                )
            # Inventory omits Pillow and python structural entries.
            (root / "dependency-inventory.json").write_text(
                json.dumps(
                    {
                        "python_min_version": [3, 12],
                        "dependencies": [
                            {
                                "name": "PyMuPDF",
                                "authoritative_source": "requirements.txt",
                                "manifest": "requirements.txt",
                                "exact_installed_version_check": True,
                                "scope": "runtime",
                            },
                            {
                                "name": "playwright",
                                "authoritative_source": "requirements-dev.txt",
                                "manifest": "requirements-dev.txt",
                                "exact_installed_version_check": True,
                                "scope": "test",
                            },
                            {
                                "name": "react",
                                "authoritative_source": "tools/pdf-viewer/package.json",
                                "manifest": "tools/pdf-viewer/package.json",
                                "scope": "build",
                            },
                            {
                                "name": "cytoscape",
                                "authoritative_source": "tools/research-graph/package.json",
                                "manifest": "tools/research-graph/package.json",
                                "scope": "vendor",
                            },
                            {
                                "name": "dompurify",
                                "authoritative_source": "tools/frontend-vendor/package.json",
                                "manifest": "tools/frontend-vendor/package.json",
                                "scope": "vendor",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            missing = {
                i.message.split(" missing ", 1)[-1].split(" ", 1)[0]
                for i in result.issues
                if i.code == "inventory_missing"
            }
            self.assertIn("Pillow", missing)
            self.assertIn("python", missing)
            self.assertIn("inter", missing)

    def test_extra_inventory_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("Pillow==12.3.0\n", encoding="utf-8")
            (root / "requirements-dev.txt").write_text(
                "playwright==1.63.0\n", encoding="utf-8"
            )
            for island in ("pdf-viewer", "research-graph", "frontend-vendor"):
                d = root / "tools" / island
                d.mkdir(parents=True)
                (d / "package.json").write_text(
                    json.dumps({"dependencies": {}}), encoding="utf-8"
                )
            expected_structural = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "ghost-package",
            ]
            (root / "dependency-inventory.json").write_text(
                json.dumps(
                    {
                        "python_min_version": [3, 12],
                        "dependencies": [
                            {
                                "name": n,
                                "authoritative_source": "test",
                                "manifest": "requirements.txt",
                                "scope": "runtime",
                            }
                            for n in expected_structural
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_extra" and "ghost-package" in i.message
                    for i in result.issues
                )
            )


class DockerfileConsistencyTests(unittest.TestCase):
    def _write_minimal_manifests(self, root: Path) -> None:
        (root / "requirements.txt").write_text("Pillow==12.3.0\n", encoding="utf-8")
        (root / "requirements-dev.txt").write_text(
            "playwright==1.63.0\n", encoding="utf-8"
        )
        for island in ("pdf-viewer", "research-graph", "frontend-vendor"):
            d = root / "tools" / island
            d.mkdir(parents=True)
            (d / "package.json").write_text(
                json.dumps({"dependencies": {}}), encoding="utf-8"
            )

    def _inventory(self, *, names: list[str], python_min=(3, 12)) -> str:
        deps = []
        for n in names:
            entry = {
                "name": n,
                "authoritative_source": "test",
                "manifest": (
                    "Dockerfile"
                    if n in ("python-base-image", "qpdf", "curl")
                    else "requirements.txt"
                ),
                "scope": "system" if n in ("qpdf", "curl") else "runtime",
            }
            deps.append(entry)
        return json.dumps(
            {"python_min_version": list(python_min), "dependencies": deps}
        )

    def test_parse_from_and_apt_packages(self):
        text = (
            "FROM python:3.12-slim\n"
            "RUN apt-get update \\\n"
            "    && apt-get install -y --no-install-recommends qpdf curl \\\n"
            "    && rm -rf /var/lib/apt/lists/*\n"
        )
        self.assertEqual(dockerfile_python_base_version(text), (3, 12))
        self.assertEqual(dockerfile_apt_packages(text), ["qpdf", "curl"])

    def test_older_base_image_fails_repo_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            (root / "Dockerfile").write_text(
                "FROM python:3.11-slim\n"
                "RUN apt-get install -y --no-install-recommends qpdf\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names, python_min=(3, 12)),
                encoding="utf-8",
            )
            result = validate_dockerfile(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(i.code == "dockerfile_python_version" for i in result.issues)
            )
            inv_result = validate_inventory(root)
            self.assertFalse(inv_result.ok)
            self.assertTrue(
                any(i.code == "dockerfile_python_version" for i in inv_result.issues)
            )

    def test_removed_apt_package_leaves_inventory_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            # Dockerfile no longer installs qpdf.
            (root / "Dockerfile").write_text(
                "FROM python:3.12-slim\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_extra" and "qpdf" in i.message
                    for i in result.issues
                )
            )

    def test_new_apt_package_requires_inventory_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            (root / "Dockerfile").write_text(
                "FROM python:3.12-slim\n"
                "RUN apt-get install -y --no-install-recommends qpdf curl\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_missing" and "curl" in i.message
                    for i in result.issues
                )
            )

    def test_live_dockerfile_matches_inventory(self):
        docker = (_PROJECT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile_python_base_version(docker),
            tuple(python_min_version(_PROJECT)[:2]),
        )
        self.assertIn("qpdf", dockerfile_apt_packages(docker))
        self.assertTrue(validate_dockerfile(_PROJECT).ok)


class RepoGateLiveTests(unittest.TestCase):
    def test_current_repo_passes(self):
        result = run_repo_gate(repo_root=_PROJECT)
        self.assertTrue(result.ok, [i.message for i in result.issues])

    def test_runtime_gate_with_correct_lookup(self):
        pins = parse_requirements_pins((_PROJECT / "requirements.txt").read_text())
        result = run_runtime_gate(
            repo_root=_PROJECT,
            version_lookup=lambda n: pins.get(n),
            current_python=(3, 12, 0),
            ctx=detect_platform_context(
                prefix="/tmp/venv",
                base_prefix="/usr",
                executable="/tmp/venv/bin/python",
                system="Linux",
                environ={},
                externally_managed=False,
            ),
        )
        self.assertTrue(result.ok)

    def test_dockerfile_has_container_marker(self):
        docker = (_PROJECT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("PRKS_CONTAINER=1", docker)
        self.assertIn("requirements.txt", docker)
        # Startup runs ensure_runtime_or_exit → load_inventory; the image must
        # ship dependency-inventory.json or every container start fails.
        self.assertRegex(
            docker,
            r"(?m)^\s*COPY\s+dependency-inventory\.json\s+",
        )

    def test_test_gate_workflow_pins_match_requirements(self):
        """Test the exact authorized CI helper command and its real pip argv.

        Invert policy still forbids extra run steps and pip-injection env; the
        helper is invoked as a single non-shell command. Mock only pip execution
        while checking profiles against requirements files and approved options.
        """
        from unittest.mock import patch
        from scripts.ci.install_pinned_python import install, pins_for_profile

        runtime = parse_requirements_pins((_PROJECT / "requirements.txt").read_text())
        dev = parse_requirements_pins((_PROJECT / "requirements-dev.txt").read_text())
        self.assertIn("openapi-core", dev)
        self.assertIn("hypothesis", dev)
        # The Docker image installs only requirements.txt: a PostgreSQL
        # runtime must never depend on extra wheels from requirements-dev.txt.
        postgres_runtime = {"psycopg", "psycopg-binary", "psycopg-pool", "argon2-cffi"}
        self.assertTrue(postgres_runtime.issubset(runtime))
        self.assertTrue(postgres_runtime.isdisjoint(dev))
        # Pyright's separate import-resolution install must use the same
        # production pins, not the now-empty development selection.
        static = (_PROJECT / ".github" / "workflows" / "static-analysis.yml").read_text()
        static_install = static.split(
            "- name: Install pinned PostgreSQL and identity type dependencies", 1
        )[1].split("- name: Run Pyright data-flow checks", 1)[0]
        self.assertIn("psycopg(-binary|-pool)?==", static_install)
        self.assertIn("argon2-cffi==", static_install)
        self.assertEqual(static_install.count("requirements.txt"), 3)
        self.assertNotIn("requirements-dev.txt", static_install)
        expected_unit = dict(runtime, **{
            name: dev[name]
            for name in ("openapi-core", "hypothesis", "markdown-it-py", "mdurl", "requests")
        })
        expected_pg = dict(runtime)

        workflow = (_PROJECT / ".github" / "workflows" / "test-gate.yml").read_text(
            encoding="utf-8"
        )
        assert_test_gate_run_steps_allowlisted(workflow)
        unit_style, unit_body = unique_test_gate_install_step(workflow)
        profiles = [("unit", unit_style, unit_body, expected_unit)]
        for label, pg_steps in (
            ("PostgreSQL helper tests", unique_test_gate_pg_steps(workflow)),
            ("backend parity legs", unique_test_gate_parity_steps(workflow)),
        ):
            if pg_steps is None:
                self.fail(f"test-gate.yml must run the {label}")
            pg_style, pg_body = pg_steps[0]
            profiles.append(("postgres", pg_style, pg_body, expected_pg))

        for profile, style, body, expected in profiles:
            with self.subTest(profile=profile):
                lines = [line.strip() for line in body.splitlines() if line.strip()]
                if style == "|":
                    self.assertEqual(len(lines), 1)
                argv = shlex.split(" ".join(lines))
                self.assertEqual(
                    argv,
                    ["python", "scripts/ci/install_pinned_python.py", profile],
                    "CI must use only the approved exact-pin helper invocation",
                )
                self.assertEqual(
                    {pin.split("==", 1)[0]: pin.split("==", 1)[1]
                     for pin in pins_for_profile(_PROJECT, profile)},
                    expected,
                )
                with patch("scripts.ci.install_pinned_python.subprocess.run") as run:
                    install(_PROJECT, profile)
                pip_argv = run.call_args.args[0]
                self.assertEqual(pip_argv[0], sys.executable)
                self.assertEqual(
                    parse_test_gate_pip_install_pins(["python", *pip_argv[1:]]),
                    expected,
                )
                self.assertIn("--only-binary=:all:", pip_argv)
                self.assertIn("--isolated", pip_argv)
                self.assertEqual(run.call_args.kwargs["cwd"], _PROJECT)
                self.assertTrue(run.call_args.kwargs["check"])

    def test_test_gate_rejects_modified_ci_install_helper_invocation(self):
        workflow = (_PROJECT / ".github" / "workflows" / "test-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python scripts/ci/install_pinned_python.py unit", workflow)
        # A shell suffix must not be accepted as part of the reviewed command.
        altered = workflow.replace(
            "python scripts/ci/install_pinned_python.py unit",
            "python scripts/ci/install_pinned_python.py unit && pip install requests",
        )
        _style, body = unique_test_gate_install_step(altered)
        argv = shlex.split(body.strip())
        self.assertNotEqual(
            argv, ["python", "scripts/ci/install_pinned_python.py", "unit"]
        )

    def _minimal_allowlisted_workflow(self, extra_step: str = "") -> str:
        """Install + run_tests skeleton; optional extra YAML step(s) appended."""
        return (
            "jobs:\n"
            "  unit-api-contract:\n"
            "    steps:\n"
            "      - name: Install pinned runtime dependencies\n"
            "        run: >-\n"
            "          python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "PyMuPDF==1.28.2" "Pillow==12.3.0"\n'
            "      - name: Run PRKS unit, API, structural, and Node tests\n"
            "        run: python run_tests.py\n"
            f"{extra_step}"
        )

    def test_test_gate_rejects_extra_run_step(self):
        """Any additional run: step is refused (invert allowlist policy)."""
        for extra in (
            "      - name: Prepare test helper\n"
            "        run: pip install requests\n",
            "      - name: Prepare test helper\n"
            '        run: "pip install requests"\n',
            "      - name: Prepare test helper\n"
            "        run: |\n"
            "          pip \\\n"
            "            install requests\n",
        ):
            with self.subTest(extra=extra.strip().splitlines()[0]):
                with self.assertRaisesRegex(ValueError, r"disallowed run step:"):
                    assert_test_gate_run_steps_allowlisted(
                        self._minimal_allowlisted_workflow(extra)
                    )

    def _pg_steps(self, run: str = "scripts/test-postgres") -> str:
        return (
            "  postgres-helpers:\n"
            "    steps:\n"
            "      - name: Install pinned runtime and PostgreSQL dependencies\n"
            "        run: >-\n"
            "          python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "psycopg==3.3.6"\n'
            "      - name: Run PostgreSQL helper and harness tests\n"
            f"        run: {run}\n"
        )

    def test_test_gate_accepts_the_postgres_pair(self):
        assert_test_gate_run_steps_allowlisted(
            self._minimal_allowlisted_workflow(self._pg_steps())
        )

    def test_live_sharded_postgres_gate_is_explicitly_allowlisted(self):
        workflow = (_PROJECT / ".github" / "workflows" / "test-gate.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("  postgres-helpers-shards:", workflow)
        assert_test_gate_run_steps_allowlisted(workflow)
        # Removing any part of the fail-closed aggregator must fail the guard.
        with self.assertRaisesRegex(ValueError, "reviewed matrix"):
            assert_test_gate_run_steps_allowlisted(
                workflow.replace("        shard: [1, 2]", "        shard: [1]")
            )
        with self.assertRaisesRegex(ValueError, "aggregator"):
            assert_test_gate_run_steps_allowlisted(
                workflow.replace("    needs: postgres-helpers-shards", "    needs: []")
            )
        with self.assertRaisesRegex(ValueError, "aggregator command"):
            assert_test_gate_run_steps_allowlisted(
                workflow.replace(
                    'if [[ "$SHARD_RESULT" != "success" ]]; then',
                    'if [[ "$SHARD_RESULT" == "success" ]]; then',
                )
            )

    def test_test_gate_reruns_cannot_cancel_new_head(self):
        """Keep Mergify's old-SHA rerun separate from the latest required gate.

        A normal first attempt must still cancel superseded first attempts;
        a rerun receives its own run-id suffix and cannot evict a newer SHA.
        This is a workflow configuration contract, not a disabled CI check.
        """
        workflow = (_PROJECT / ".github" / "workflows" / "test-gate.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("github.run_attempt > 1 && github.run_id || 'first'", workflow)
        self.assertIn("  cancel-in-progress: true", workflow)

    def test_test_gate_postgres_run_step_is_exactly_the_script(self):
        for run in ("scripts/test-postgres && pip install requests", "bash -c 'pip install x'"):
            with self.subTest(run=run):
                with self.assertRaisesRegex(ValueError, r"exactly `scripts/test-postgres`"):
                    assert_test_gate_run_steps_allowlisted(
                        self._minimal_allowlisted_workflow(self._pg_steps(run))
                    )

    def test_test_gate_postgres_steps_come_as_one_pair(self):
        lone_run = (
            "      - name: Run PostgreSQL helper and harness tests\n"
            "        run: scripts/test-postgres\n"
        )
        for extra in (lone_run, self._pg_steps() + lone_run):
            with self.subTest(extra=extra.count("\n")):
                with self.assertRaisesRegex(ValueError, r"PostgreSQL job needs exactly one"):
                    assert_test_gate_run_steps_allowlisted(
                        self._minimal_allowlisted_workflow(extra)
                    )

    def test_test_gate_postgres_pair_still_refuses_extra_run_steps(self):
        extra = self._pg_steps() + (
            "      - name: Prepare test helper\n"
            "        run: pip install requests\n"
        )
        with self.assertRaisesRegex(ValueError, r"disallowed run step:"):
            assert_test_gate_run_steps_allowlisted(self._minimal_allowlisted_workflow(extra))

    def _parity_steps(self, run: str = "scripts/test-postgres --parity") -> str:
        return (
            "  backend-parity:\n"
            "    steps:\n"
            "      - name: Install pinned runtime and PostgreSQL dependencies for parity\n"
            "        run: >-\n"
            "          python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "psycopg==3.3.6"\n'
            "      - name: Run SQLite and PostgreSQL backend parity legs\n"
            f"        run: {run}\n"
        )

    def test_test_gate_accepts_the_parity_pair_beside_the_postgres_pair(self):
        assert_test_gate_run_steps_allowlisted(
            self._minimal_allowlisted_workflow(self._pg_steps() + self._parity_steps())
        )

    def test_test_gate_parity_run_step_is_exactly_the_parity_script(self):
        for run in (
            "scripts/test-postgres",
            "scripts/test-postgres --parity && pip install requests",
            "scripts/test-postgres --parity --json x",
        ):
            with self.subTest(run=run):
                with self.assertRaisesRegex(
                    ValueError, r"exactly `scripts/test-postgres --parity`"
                ):
                    assert_test_gate_run_steps_allowlisted(
                        self._minimal_allowlisted_workflow(self._parity_steps(run))
                    )

    def test_test_gate_parity_steps_come_as_one_pair(self):
        lone_run = (
            "      - name: Run SQLite and PostgreSQL backend parity legs\n"
            "        run: scripts/test-postgres --parity\n"
        )
        with self.assertRaisesRegex(ValueError, r"PostgreSQL job needs exactly one"):
            assert_test_gate_run_steps_allowlisted(self._minimal_allowlisted_workflow(lone_run))

    def test_test_gate_rejects_duplicate_install_step_name(self):
        """A second same-named Install step must fail (cannot bypass by name)."""
        workflow = self._minimal_allowlisted_workflow(
            "      - name: Install pinned runtime dependencies\n"
            "        run: pip install requests\n"
        )
        with self.assertRaisesRegex(
            ValueError, r"exactly one .*Install pinned runtime dependencies.* required"
        ):
            assert_test_gate_run_steps_allowlisted(workflow)
        with self.assertRaisesRegex(
            ValueError, r"exactly one .*Install pinned runtime dependencies.* required"
        ):
            unique_test_gate_install_step(workflow)

    def test_test_gate_requires_isolated(self):
        """Approved install argv without --isolated must fail closed."""
        with self.assertRaisesRegex(ValueError, "approved install requires --isolated"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "PyMuPDF==1.28.2",
                    "Pillow==12.3.0",
                ]
            )

    def test_test_gate_rejects_pip_requirement_env(self):
        """Job/step PIP_REQUIREMENT (and similar) must be refused."""
        step_env = (
            "jobs:\n"
            "  unit-api-contract:\n"
            "    steps:\n"
            "      - name: Install pinned runtime dependencies\n"
            "        env:\n"
            "          PIP_REQUIREMENT: extra.txt\n"
            "        run: >-\n"
            "          python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "PyMuPDF==1.28.2" "Pillow==12.3.0"\n'
            "      - name: Run PRKS unit, API, structural, and Node tests\n"
            "        run: python run_tests.py\n"
        )
        job_env = (
            "jobs:\n"
            "  unit-api-contract:\n"
            "    env:\n"
            "      PIP_REQUIREMENT: extra.txt\n"
            "    steps:\n"
            "      - name: Install pinned runtime dependencies\n"
            "        run: >-\n"
            "          python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "PyMuPDF==1.28.2" "Pillow==12.3.0"\n'
            "      - name: Run PRKS unit, API, structural, and Node tests\n"
            "        run: python run_tests.py\n"
        )
        with self.assertRaisesRegex(ValueError, r"disallowed pip inject env: PIP_REQUIREMENT"):
            assert_test_gate_run_steps_allowlisted(step_env)
        with self.assertRaisesRegex(ValueError, r"disallowed pip inject env: PIP_REQUIREMENT"):
            assert_test_gate_run_steps_allowlisted(job_env)

    def test_test_gate_literal_rejects_extra_pip_install_line(self):
        """Later `pip install` / `python3 -m pip` lines must fail the literal check."""
        pinned = (
            "python -m pip install --isolated --disable-pip-version-check "
            '"--only-binary=:all:" "PyMuPDF==1.28.2" "Pillow==12.3.0"'
        )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "pip install requests"]
            )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "python3 -m pip install requests"]
            )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "uv pip install requests"]
            )

    def test_test_gate_pip_install_rejects_bare_package(self):
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--isolated",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "PyMuPDF==1.28.2",
                    "requests",
                ]
            )

    def test_test_gate_pip_install_rejects_requirements_file(self):
        with self.assertRaisesRegex(ValueError, "disallowed requirement source option"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--isolated",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "PyMuPDF==1.28.2",
                    "-r",
                    "extra.txt",
                ]
            )

    def test_test_gate_pip_install_rejects_wheel_and_vcs(self):
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--isolated",
                    "--only-binary=:all:",
                    "./Pillow-12.3.0-py3-none-any.whl",
                ]
            )
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--isolated",
                    "--only-binary=:all:",
                    "git+https://example.invalid/pkg.git",
                ]
            )

    def test_inventory_lists_core_deps(self):
        inv = json.loads((_PROJECT / "dependency-inventory.json").read_text(encoding="utf-8"))
        names = {d["name"] for d in inv["dependencies"]}
        for required in (
            "PyMuPDF",
            "Pillow",
            "playwright",
            "cytoscape",
            "dompurify",
            "esbuild",
            "react",
            "inter",
            "qpdf",
            "python",
            "python-base-image",
        ):
            self.assertIn(required, names)

    def test_dependency_manifest_deterministic(self):
        from backend.dependency_gate import build_dependency_manifest

        a = build_dependency_manifest(_PROJECT)
        b = build_dependency_manifest(_PROJECT)
        self.assertEqual(a, b)
        blob = json.dumps(a)
        self.assertNotIn("builtAt", blob)
        self.assertNotIn("fetched", blob.lower())

    def test_sw_uses_dependency_revision(self):
        from backend.dependency_gate import (
            dependency_manifest_revision,
            read_sw_dependency_revision,
        )

        sw = (_PROJECT / "frontend" / "sw.js").read_text(encoding="utf-8")
        rev = read_sw_dependency_revision(sw)
        self.assertEqual(rev, dependency_manifest_revision(repo_root=_PROJECT))
        self.assertIn("prks-static-' + DEPENDENCY_REVISION", sw)
        self.assertIn("RETIRE_PREFIXES", sw)

    def test_workspace_model_bytes_change_dependency_revision(self):
        from unittest.mock import patch

        from backend import dependency_gate as gate

        real_sha = gate.sha256_file
        seen = {"model": 0}

        def sha_for_model_change(path: Path) -> str:
            digest = real_sha(path)
            if Path(path).name == "workspace-model.js":
                seen["model"] += 1
                if seen["model"] > 1:
                    return "ab" * 32
            return digest

        with patch.object(gate, "sha256_file", side_effect=sha_for_model_change):
            first = gate.build_dependency_manifest(_PROJECT)
            second = gate.build_dependency_manifest(_PROJECT)
        model = next(item for item in first["dependencies"] if item["name"] == "prks-workspace-model")
        self.assertEqual(model["runtime_files"][0]["path"], "/js/workspace-model.js")
        self.assertEqual(len(model["runtime_files"][0]["sha256"]), 64)
        self.assertNotEqual(
            gate.dependency_manifest_revision(first),
            gate.dependency_manifest_revision(second),
        )
        changed = {
            item["name"]: item
            for item in second["dependencies"]
            if item != next(old for old in first["dependencies"] if old["name"] == item["name"])
        }
        self.assertEqual(set(changed), {"prks-workspace-model"})

    def test_no_cdn_in_index(self):
        html = (_PROJECT / "frontend" / "index.html").read_text(encoding="utf-8")
        for marker in ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com"):
            self.assertNotIn(marker, html)


def _vue_gate_fixture(tmp: Path) -> Path:
    root = Path(tmp)
    for rel in (
        "frontend-app/package.json",
        "frontend-app/package-lock.json",
        "frontend/vue/BUILD-MANIFEST.json",
        "frontend/vue/prks-vue.js",
        "frontend/index.html",
        "frontend/sw.js",
    ):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((_PROJECT / rel).read_bytes())
    return root


class VueProductionGateTests(unittest.TestCase):
    def test_committed_vue_bundle_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_vue_production(_vue_gate_fixture(tmp))
        self.assertTrue(result.ok, result.issues)

    def test_dropped_pin_fails_when_both_sides_omit_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            pkg_path = root / "frontend-app" / "package.json"
            manifest_path = root / "frontend" / "vue" / "BUILD-MANIFEST.json"
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del pkg["devDependencies"]["typescript"]
            del manifest["typescript"]
            pkg_path.write_text(json.dumps(pkg), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        pin_issues = [issue for issue in result.issues if issue.code == "vue_manifest_pin"]
        self.assertEqual(len(pin_issues), 1)
        self.assertIn("typescript", pin_issues[0].message)
        self.assertIn("None", pin_issues[0].message)

    def test_empty_pin_fails_when_both_sides_are_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            pkg_path = root / "frontend-app" / "package.json"
            manifest_path = root / "frontend" / "vue" / "BUILD-MANIFEST.json"
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            pkg["devDependencies"]["typescript"] = "  "
            manifest["typescript"] = ""
            pkg_path.write_text(json.dumps(pkg), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        pin_issues = [issue for issue in result.issues if issue.code == "vue_manifest_pin"]
        self.assertTrue(any("typescript" in issue.message for issue in pin_issues))

    def test_node_24_below_jsdom_engines_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            pkg_path = root / "frontend-app" / "package.json"
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            pkg["engines"]["node"] = ">=24 <25"
            pkg_path.write_text(json.dumps(pkg), encoding="utf-8")
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == "vue_node_toolchain" for issue in result.issues))

    def test_lockfile_engines_must_match_jsdom_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            lock_path = root / "frontend-app" / "package-lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"][""]["engines"]["node"] = ">=24 <25"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == "vue_lock_engines" for issue in result.issues))

    def test_separate_css_artifact_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            css_path = root / "frontend" / "vue" / "prks-vue.css"
            css_path.write_text("body{color:red}\n", encoding="utf-8")
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == "vue_css_not_inlined" for issue in result.issues))

    def test_nested_unlisted_css_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _vue_gate_fixture(tmp)
            css_path = root / "frontend" / "vue" / "assets" / "style.css"
            css_path.parent.mkdir(parents=True)
            css_path.write_text("body{color:red}\n", encoding="utf-8")
            manifest_path = root / "frontend" / "vue" / "BUILD-MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("assets/style.css", manifest.get("outputSha256") or {})
            result = validate_vue_production(root)
        self.assertFalse(result.ok)
        css_issues = [issue for issue in result.issues if issue.code == "vue_css_not_inlined"]
        self.assertTrue(css_issues)
        self.assertTrue(any(issue.path and issue.path.endswith("assets/style.css") for issue in css_issues))


class StartupOrderTests(unittest.TestCase):
    def test_prks_app_validates_before_storage(self):
        src = (_PROJECT / "prks_app.py").read_text(encoding="utf-8")
        gate = src.index("ensure_runtime_or_exit")
        storage = src.index("StorageConfig.from_env")
        self.assertLess(gate, storage)


if __name__ == "__main__":
    unittest.main()
