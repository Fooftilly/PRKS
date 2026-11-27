---
name: root-cause
description: Diagnose a reproducible defect, distinguish symptoms from causes and propose or implement a bounded regression-tested fix.
disable-model-invocation: true
---

# Root-cause diagnosis

Problem: $ARGUMENTS

1. Read AGENTS.md and the affected scoped rules. Identify the tested revision, expected contract, actual behavior and reproducible trigger. If the problem is intermittent, record the evidence and uncertainty. Use synthetic data and disposable test storage only.
2. Trace from the observed failure to its first violated invariant. Check callers, state ownership, error handling, data shape, transaction/lock order, revisions or lifecycle when relevant. Test competing explanations with a focused reproduction or existing evidence.
3. Prefer a failing regression at the lowest layer that can prove the contract. Follow the browser test cadence and coverage-layer rules; do not start with the full E2E suite.
4. Select the smallest fix at the authoritative owner that prevents the cause, not just the observed symptom. Reject catch-all guards, swallowed errors, arbitrary retries, increased timeouts and duplicate state. Do not widen scope into unrelated defects or change architecture without authorization.
5. If implementation is authorized by the caller, apply the fix and verify the regression plus adjacent contracts. Otherwise stop at diagnosis and a proposed fix. Never claim a root cause is confirmed when the evidence is only suggestive.

Report the reproduction, violated invariant, evidence, fix or recommendation, exact checks and remaining uncertainties. Do not change GitHub state or merge.
