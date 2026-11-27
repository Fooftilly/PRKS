---
name: encode-constraint
description: Convert a confirmed recurring engineering correction into a proportionate enforceable PRKS constraint.
disable-model-invocation: true
---

# Encode a correction

Correction or review finding: $ARGUMENTS

Read root AGENTS.md and the applicable scoped rules. Verify the correction against the current code and tests. Search existing issues, PRs and checks for the same contract. Identify the canonical owner and a concrete counterexample; do not turn a style preference or unverified finding into a mandatory check.

Prefer, in order:

1. Make invalid states or operations unavailable through the existing API, types, ownership or directory boundary.
2. Use an existing static checker for a clear forbidden source pattern with an approved alternative. Extend the existing PRKS invariant checker, Ruff, Pyright or ESLint as appropriate. Test accepted and rejected cases. Avoid broad bans and false-positive-prone heuristics.
3. Use semantic tests for data, state and timing contracts. Choose the lowest layer that proves the behavior.
4. Keep prose when a rule depends on judgment or external constraints that cannot be encoded reliably.

Propose the smallest useful mechanism, its failure message, test evidence, owner and expected CI cost. Do not delete meaningful comments unless their replacement enforces the actual contract. Do not create a competing registry, new required gate or dependency without authorization.

Implement only when the caller's task authorizes the change. Demonstrate that the counterexample fails and a correct alternative passes where feasible. Preserve unrelated tests and rules. Report measured results separately from expectations and unverified assumptions. Do not merge or change branch protection.

Source of inspiration: [All Code Is Slop (Except Mine)](https://www.youtube.com/watch?v=O_zcc6Xl12g).
