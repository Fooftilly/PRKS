---
name: Bug report
about: Report a reproducible malfunction, regression, or incorrect behavior that needs investigation or fixing
title: ""
labels: "bug"
assignees: ""
---

<!--
Use this template when PRKS is behaving incorrectly.

Use the other templates instead when appropriate:
- UX / UI finding: the problem is primarily usability, accessibility, discoverability, feedback, interaction consistency, or visual behavior.
- Engineering audit finding: an audit/review discovered an engineering concern that is not automatically approved implementation work.
- Engineering / implementation task: the work is already understood as a concrete implementation task rather than a malfunction report.
- Research / evaluation: the main outcome is a decision or evidence, not a predetermined implementation.
- Roadmap / planning epic: the issue describes a broader direction that should be decomposed.

Before submitting:
- Search both open and closed issues for the same symptom, workflow, error text, and likely root cause.
- Use testing/synthetic library data in logs, screenshots, and reproductions. Do not expose private library content.
- Do not file suspected vulnerabilities or sensitive security reproductions publicly; use SECURITY.md.
- Apply the appropriate priority:* and area:* labels when known.
-->

## Summary

Describe the concrete malfunction or regression.

## Reproduction

1.
2.
3.

State whether the behavior is deterministic, intermittent, or not yet reliably reproduced.

## Current behavior

Describe exactly what PRKS currently does, including relevant error text or visible state.

## Expected behavior

Describe what should happen instead.

## Frequency / regression

- Frequency: always / intermittent / once / unknown
- Regression status: known regression / never known to work / unknown
- Last known good commit/version, if known:

## Environment

- Tested commit:
- OS:
- Browser/runtime, when relevant:
- Relevant configuration or feature state:

## Evidence

- Failing test or reproduction:
- Logs / stack trace, when safe:
- Screenshot or artifact, when useful:
- Other evidence:

Do not include real library content, filenames, paths, notes, search terms, URLs, or other private data.

## Likely affected area

List components, files, or flows only when supported by evidence. Clearly mark hypotheses as hypotheses.

## Acceptance criteria

- [ ] The original reproduction no longer fails.
- [ ] A regression test covers the failure when reasonably automatable.
- [ ] Relevant existing behavior remains intact.
- [ ] Error/recovery behavior is correct for the affected boundary.

## Validation

List the focused unit, integration, API, static, or browser-level checks needed to prove the fix. Use browser E2E only when the behavior genuinely requires browser verification.

## Related work

Link overlapping bugs, audit/UX findings, roadmap items, dependencies, or PRs. State when none are known.
