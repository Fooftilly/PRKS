---
name: prks-test
description: Select and run proportionate PRKS regression checks without touching live storage or weakening test contracts.
disable-model-invocation: true
---

# Verify a change

Target or requested test scope: $ARGUMENTS

1. Read root AGENTS.md, tests/AGENTS.md and relevant production scopes. For browser tests also read tests/e2e/AGENTS.md. Identify the diff and behavior under test, including the base when applicable.
2. Map changed contracts to existing tests and add focused regression coverage only if the caller authorizes test edits. Prefer unit/API/Node/static checks; select commands from the checked-in runner and scoped documentation. Use scripts/test-postgres for PostgreSQL integration as documented. Check prerequisites and test isolation before running commands.
3. Use only disposable storage and databases; never target production data, live PRKS_STORAGE or a production database. If a test environment cannot be verified safe, skip that run and report the blocker.
4. Browser checks are for behavior requiring a real browser. Follow KEEP/SPLIT/MOVE and the documented E2E cadence. Use scripts/e2e agent once the implementation is coherent when browser validation applies. Run narrower selections to debug specific failures. The full gate is final validation only when appropriate; never run UX Interaction Tour unless explicitly requested.
5. On failure, retain the output, narrow to the failing contract and diagnose it. Do not weaken assertions, add retries, suppress errors or raise timeouts just to pass. Fix code only if the caller authorized implementation. Do not rerun the full browser gate immediately after failure.
6. Report the tested revision/working-tree state, exact commands, results and E2E tier (if any). Distinguish passed, failed, skipped and not run. A targeted pass does not imply the full gate passed.

Do not commit, post results to GitHub or merge.
