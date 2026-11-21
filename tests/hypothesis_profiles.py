"""Hypothesis settings for the fuzzing property tests.

Three profiles, chosen by ``PRKS_HYPOTHESIS_PROFILE``:

``pr`` (the default)
    The Unit / API / contract gate. ``derandomize=True`` draws the same inputs
    on every run, so a red pull request is caused by that pull request, never by
    a new random draw. No example database: results do not depend on a
    developer's cached examples.
``nightly``
    The scheduled advisory workflow. Random seeds and many more examples, so it
    keeps searching; a failure prints the seed and the shrunk input. Pin that
    input as an ``@example`` in the fix so the ``pr`` profile replays it.
``mutation``
    ``scripts/ci/mutation_testing.py``. Derandomized with fewer examples, since
    every mutant reruns the tests. mutmut runs the same test methods more than
    once in one process, which Hypothesis reports as ``differing_executors``;
    with no example database and fixed draws, that rerun cannot replay stale
    examples, so the check is off for this profile only.

Applied per test with ``@FUZZ`` rather than registered globally, so the
older Hypothesis tests keep their own settings.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

_PROFILES = {
    "pr": settings(max_examples=300, derandomize=True, database=None, deadline=None,
                   suppress_health_check=[HealthCheck.too_slow]),
    "nightly": settings(max_examples=5000, derandomize=False, database=None, deadline=None,
                        print_blob=True, suppress_health_check=[HealthCheck.too_slow]),
    "mutation": settings(max_examples=100, derandomize=True, database=None, deadline=None,
                         suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors]),
}

PROFILE = os.environ.get("PRKS_HYPOTHESIS_PROFILE", "pr")
if PROFILE not in _PROFILES:
    raise RuntimeError(f"PRKS_HYPOTHESIS_PROFILE must be one of {sorted(_PROFILES)}, not {PROFILE!r}")

FUZZ = _PROFILES[PROFILE]
