---
name: architect
description: Compare bounded implementation designs against PRKS ownership, current contracts and accepted roadmap before coding.
disable-model-invocation: true
---

# Design review

Design problem: $ARGUMENTS

Read root AGENTS.md, applicable scoped rules and the relevant design authority. Inspect current implementation and accepted roadmap only as needed. Establish the actual requirement and constraints; distinguish implemented behavior from future architecture.

Describe the current owner, interfaces and failure modes. Compare the smallest extension of an existing owner with other genuinely viable shapes. Prefer eliminating duplication over adding layers. For each viable design, assess data integrity, authorization, privacy, sync, migration/caller impact, testability and operational cost where relevant. Cite file or contract references and identify assumptions.

Recommend one design with a bounded sequence of verifiable changes, explicit non-goals, migration ordering if needed, and acceptance tests. If there is only one viable shape, explain why; do not invent alternatives.

Stop at the design. Do not edit files, create tracking items or change repository state. An architectural sketch is not authorization to rewrite unrelated code.
