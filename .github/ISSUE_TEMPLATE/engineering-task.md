---
name: Engineering / implementation task
about: Define focused implementation work for a feature slice, refactor, tooling, testing, maintenance, or other already-decided change
title: ""
labels: ""
assignees: ""
---

<!--
Use this template for concrete, bounded implementation work.

Use the other templates instead when appropriate:
- Bug report: PRKS is malfunctioning or has regressed and the issue should capture reproduction/current/expected behavior.
- Research / evaluation: the primary outcome is evidence and a decision, and implementation is not yet settled.
- Engineering audit finding: an observation/finding that is not automatically approved implementation work.
- UX / UI finding: a user-facing usability, accessibility, discoverability, interaction, feedback, or visual problem.
- Roadmap / planning epic: a broader direction that should later be split into focused implementation work.

Before submitting:
- Search both open and closed issues for overlapping work.
- Keep one issue focused on one implementation outcome or tightly coupled change set.
- Apply the appropriate priority:* and area:* labels.
- Add enhancement or other classification labels only when they accurately describe the work.
-->

## Goal

Describe the concrete outcome this issue should deliver.

## Context / why

Explain the current limitation, maintenance cost, or product need that makes this work useful. Link evidence, an approved finding, a bug report, or completed research when relevant.

## Scope

- [ ] List the implementation work that belongs in this issue.
- [ ] Keep the scope bounded enough for a focused PR or small PR sequence.

## Acceptance criteria

- [ ] State observable conditions that must be true before this issue is complete.
- [ ] Include compatibility, correctness, durability, UX, or performance requirements where they matter.
- [ ] Require regression coverage for any invariant or previously failing behavior being changed.

## Validation

- [ ] Name the unit, integration, static-analysis, API-contract, or other focused checks that should prove the change.
- [ ] Add browser E2E coverage only when the behavior genuinely requires browser-level verification.
- [ ] Record any manual verification that cannot reasonably be automated.

## Non-goals / boundaries

State what is deliberately out of scope, including architectural or behavioral boundaries that must not change.

## Related work

Link parent roadmap items, audit/UX findings, dependencies, superseded issues, or related PRs. State when none are known.

## Implementation notes

Record useful design direction, sequencing constraints, migration considerations, or approved helper/module boundaries. Keep solution details flexible unless a constraint is load-bearing.
