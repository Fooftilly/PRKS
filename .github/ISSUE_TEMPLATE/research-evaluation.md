---
name: Research / evaluation
about: Run a bounded investigation or proof of concept where the decision or implementation direction is not yet settled
title: "[Research] "
labels: ""
assignees: ""
---

<!--
Use this template when the primary deliverable is evidence and a decision.

A successful research issue may conclude that the evaluated approach should NOT be adopted.

Use the other templates instead when appropriate:
- Engineering / implementation task: the desired implementation outcome is already decided.
- Engineering audit finding: an audit/review observation is being recorded for maintainer triage.
- Roadmap / planning epic: the issue defines a broader approved/proposed direction rather than a bounded investigation.
- Bug report: PRKS is malfunctioning and needs investigation/fixing.
- UX / UI finding: the issue records a user-facing UX/UI problem.

Before submitting:
- Search both open and closed issues for prior evaluations and overlapping work.
- Keep the investigation bounded enough to end with a clear decision.
- State the invariants that an experiment or dependency must preserve.
- Do not treat a proof of concept as authorization for broad production migration.
-->

## Question / decision

State the concrete question this research should answer or the decision it should enable.

## Why this needs investigation

Explain why the answer is not already clear and what decision or future work depends on it.

## Current state

Describe the relevant existing implementation, constraints, known pain points, and any evidence already available.

## Options to evaluate

- Option A:
- Option B:
- Other viable alternatives:

Include "keep the current approach" when that is a meaningful outcome.

## Constraints / invariants

List architectural, product, compatibility, local-first, privacy, durability, performance, dependency, or UX constraints that must not be weakened by the evaluation.

## Evaluation criteria

Define how the options will be compared, for example:

- correctness and invariant preservation;
- amount of custom code removed;
- complexity introduced;
- maintainability;
- performance or resource cost;
- dependency health/license/footprint;
- migration and rollback cost;
- testability.

Use only criteria relevant to this investigation.

## Deliverables

- [ ] Findings are documented with evidence.
- [ ] The evaluated options are compared against the stated criteria.
- [ ] A bounded proof of concept is produced if evidence cannot be obtained otherwise.
- [ ] The conclusion explicitly says adopt, reject, defer, or keep the current approach, with rationale.
- [ ] Follow-up implementation issue(s) are created or linked if adoption is approved and further work is needed.

## Validation / evidence

Describe measurements, tests, prototypes, documentation review, compatibility checks, or other evidence required for a credible conclusion.

## Non-goals / boundaries

State what this investigation will not implement or redesign.

## Related work

Link parent roadmap items, audit/UX findings, prior research, dependencies, and related PRs. State when none are known.

Research completion does not by itself authorize a broad production migration beyond any explicitly approved proof-of-concept scope.
