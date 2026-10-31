"""Privacy-safe, scope-aware GitHub Actions summary for the E2E aggregate gate.

The required job is named "Full E2E gate" for branch protection even when its
planner selects an affected subset or skips browser tests. Do not infer actual
test execution counts from planner metadata.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

_FEATURE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_JOB_RESULTS = {"success", "failure", "cancelled", "skipped", "neutral", "timed_out"}


def _job_result(value: str | None) -> str:
    return value if value in _JOB_RESULTS else "unknown"


def _test_count(value: str | None) -> str:
    if not value or len(value) > 9 or not value.isascii() or not value.isdecimal():
        return "unknown"
    return str(int(value))


def _features(value: str | None) -> str:
    try:
        names = json.loads(value or "")
    except (TypeError, ValueError):
        return "unavailable"
    if (
        not isinstance(names, list)
        or not names
        or len(names) > 40
        or any(type(name) is not str or not _FEATURE.fullmatch(name) for name in names)
    ):
        return "unavailable"
    return ", ".join(f"`{name}`" for name in names)


def format_e2e_gate_summary(env: Mapping[str, str], *, passed: bool) -> str:
    """Explain exactly what the stable-named required check did (or did not) run.

    Only fixed labels and strictly validated planner fields enter Markdown.
    The plan's arbitrary reason string is deliberately not rendered.
    """
    run = (env.get("PLAN_RUN") or "").strip()
    mode = (env.get("PLAN_MODE") or "").strip()
    plan_result = _job_result(env.get("PLAN_RESULT"))
    if plan_result != "success":
        scope = "Unverified: planner did not succeed"
        caveat = "No browser coverage can be inferred from this result."
    elif mode == "full" and run == "true":
        scope = "Full discovered E2E suite selected"
        caveat = "Full selection is planned; confirm actual execution in the shard jobs."
    elif mode == "affected" and run == "true":
        scope = "Affected subset only — NOT the full E2E suite"
        caveat = "A passing required check does not certify every discovered browser test."
    elif mode == "skip" and run == "false":
        scope = "Skipped — no browser E2E tests selected"
        caveat = "A passing required check here means the selection policy skipped browser E2E."
    else:
        scope = "Unverified: inconsistent or unknown selection"
        caveat = "Inspect the E2E plan job; do not infer browser coverage."

    pointer = (env.get("PLAN_POINTER") or "").strip()
    pointer_planned = {"true": "yes", "false": "no"}.get(pointer, "unknown")
    features = _features(env.get("PLAN_FEATURES")) if mode == "affected" else "not applicable"
    return (
        "## E2E gate execution scope\n\n"
        f"**Selection:** {scope}\n\n"
        f"**Aggregate result:** {'PASS' if passed else 'FAIL'}\n\n"
        "| Field | Value |\n| --- | --- |\n"
        f"| Planner job | {plan_result} |\n"
        f"| Planned test IDs | {_test_count(env.get('PLAN_COUNT'))} |\n"
        f"| Selected feature groups | {features} |\n"
        f"| Pointer capture planned | {pointer_planned} |\n"
        f"| Browser shard matrix | {_job_result(env.get('E2E_RESULT'))} |\n"
        f"| Pointer job | {_job_result(env.get('POINTER_RESULT'))} |\n\n"
        f"{caveat}\n\n"
        "Planned test IDs are selection metadata, **not** independently observed "
        "pass counts. Required check names are kept stable for branch protection.\n"
    )
