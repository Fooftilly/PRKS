---
name: verify-feature
description: Produce observable runtime evidence for a scoped PRKS feature, including failure and recovery paths.
disable-model-invocation: true
---

# Verify a feature

Feature and scope: $ARGUMENTS

Read root AGENTS.md and the relevant scoped rules. Use existing feature documentation, DESIGN.md and coverage maps as the source of truth. Do not invent a separate global feature inventory.

1. Identify the exact user-visible contract, entry point, state owner, persisted state and existing coverage.
2. Define observable success, refusal, cancellation and recovery behavior as applicable. Identify where each assertion belongs: pure logic, API, PostgreSQL, browser integration or real browser.
3. Use existing runners, synthetic data and disposable test storage/database only. Verify isolation before running. Follow the E2E test policy; do not use browser tests for deterministic logic or run the full suite as the first step. For runtime checks use the actual application or production functions, not a second implementation. Use observable readiness and controlled events instead of sleeps and retries.
4. Run the appropriate checks if prerequisites and authorization permit. If a real browser is required and unavailable, report that gap rather than claiming runtime evidence from source review.
5. When practical, ask the independent prks-change-auditor to inspect the assertions and changed paths. Pass the evidence; the auditor cannot execute tests. Check that the assertions would fail for the defect they are intended to catch.
6. Report exact tested revision, commands, observed results and test tier. Separate verified behavior, untested paths and blockers. Add regression coverage only within an authorized task.

No production data, new external telemetry, GitHub writes or merge authorization.
