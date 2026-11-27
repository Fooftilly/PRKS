---
name: blast-radius
description: Trace the impact of a proposed PRKS change across callers, contracts, persistence, browser owners, tests and rollout boundaries without editing.
disable-model-invocation: true
---

# Impact analysis

Target: $ARGUMENTS

Read root AGENTS.md, then only the scoped rules and authoritative design sections touched by the target. Resolve the actual symbol, feature, issue or diff. If unspecified, use the current change scope; if it cannot be established, report that rather than scanning everything.

- Identify the owner and direct callers, consumers, entry points and data flows. Trace definitions, not just matching names.
- Trace relevant contracts across HTTP DTOs, domain commands, PostgreSQL and storage, sync and revision semantics, frontend bridges, generated assets and test fixtures. Include only boundaries the change can actually reach.
- For changed behavior, identify failure, retry, concurrency, stale-state and authorization paths where applicable.
- Distinguish existing contracts from target architecture. Name any migration ordering, compatibility consumer or prerequisite. Do not invent a second owner.
- Identify tests that prove each affected contract, gaps, and the smallest appropriate validation tier.

Report confirmed impact with file/symbol references, conditional impact with its condition, unknowns, and a bounded implementation/validation scope. Read-only; do not edit files, open issues, post or merge.
