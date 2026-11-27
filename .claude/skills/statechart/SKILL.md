---
name: statechart
description: Model a PRKS lifecycle or protocol from observable states, events and invariants before proposing bounded implementation tests.
disable-model-invocation: true
---

# State and event analysis

Lifecycle or protocol: $ARGUMENTS

Read root AGENTS.md, scoped instructions and the current owner contract. Trace real entry points, state owners, transitions and failure paths. For UI work start from the user's observable behavior; do not infer a new runtime state owner from a diagram.

Describe a small state table: states, events, guards, next state, observable outcome and invariants. Include relevant cancellation, replacement, retry, duplicate delivery, stale responses and invalid events. Distinguish actual states from proposed model abstractions. Use a Mermaid state diagram only if it improves clarity.

Find the existing tests and model/property test ownership. Propose bounded event schedules or generated sequences that exercise the actual implementation, not merely a model that duplicates its code. Include a counterexample and a way to reproduce it. Implement tests only if the caller authorizes edits, using existing tools and disposable test data.

If the model needs formal verification beyond bounded implementation tests, explain the precise invariant, state-space limits, assumptions, tooling cost and how checked model traces would map to implementation tests. Do not introduce TLA+, Lean or another dependency without an approved evaluation. Never claim a finite test or an abstract model proves the whole implementation.

Report the state table, invariants, evidence, unresolved transitions and test recommendations. Do not widen scope, add a parallel state model to production or merge.
