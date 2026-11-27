---
name: prks-review
description: Review a PR or local diff for verified regressions, unsafe changes and missing contract coverage without posting or merging.
disable-model-invocation: true
---

# Change review

Review target: $ARGUMENTS

1. Establish the exact PR head and base or local change scope. Inspect the diff and surrounding code. For a PR, read its description, linked acceptance criteria, existing review findings and latest checks when accessible. Recheck the head before finalizing. If access is missing, state what cannot be verified.
2. Read root AGENTS.md and the relevant scoped rules. Follow the repository design authorities, not general style preferences.
3. Ask prks-change-auditor for independent read-only analysis of the changed hunks and relevant context if the agent is available. Include exact scope and applicable rules. Validate each candidate yourself; distinguish independent analysis from a fallback local review.
4. Check for actual regressions in behavior, data integrity, authorization, privacy, storage, transactions, sync and UI lifecycle as applicable. Trace callers and failure paths. Verify whether a test actually covers the contract. Avoid speculative concerns and pre-existing unrelated defects.
5. Deduplicate against existing review findings; report only new or still-unresolved actionable defects, with severity, location, triggering condition, consequence and required correction. Label uncertainty instead of treating it as a finding. Report CI/check status separately and never infer success from missing results.

Return findings highest impact first, then checks and limitations. If none are verified, say so without claiming exhaustive correctness. This skill is read-only: do not edit, post review comments, approve, merge, enable auto-merge or change issue/project state. Any separately authorized posting must include only verified actionable findings.
