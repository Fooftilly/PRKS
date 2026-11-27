---
name: no-comments
description: Review changed comments and suppressions, remove redundant narration, and preserve or report real constraints.
disable-model-invocation: true
---

# Review comments

Scope and options: $ARGUMENTS

1. Read root AGENTS.md and the applicable scoped instructions. If paths or a diff are supplied, respect that scope. Otherwise inspect the current branch against its merge base with the available master tracking ref, including staged, unstaged and relevant untracked changes. Use the actual PR base if known. If the base cannot be established, stop and report it; do not expand to the repository. Never overwrite pre-existing user edits.
2. Ask the prks-change-auditor subagent for an independent comment review. Pass the changed hunks, paths, base, relevant contracts and requested scope. Ask for exact file/line, classification, evidence and a suggested action. If the agent is unavailable, perform a local review and disclose that it is not independent. A review-only request must not edit anything.
3. Classify each finding:
   - REMOVE: narration of obvious code, stale commentary proven false, redundant change history, or an unused commented-out code block with no contract value.
   - KEEP: license or attribution, meaningful public API/module docstrings, reasons for security checks, privacy/storage/transaction/lock-order/sync rules, compatibility boundaries, or any non-obvious behavior that still matters. Preserve tool directives and suppressions until their purpose is verified.
   - INVESTIGATE: comments that expose a workaround, unexplained lint/type suppressions, unclear invariants, or a constraint that code/tests might enforce. Trace definitions, callers, tests and authoritative docs. If evidence is incomplete, keep the comment and report the uncertainty.
4. Independently verify the subagent's findings. Unless asked for report-only, remove only proven redundant comments inside scope. Do not remove or change executable behavior, useful documentation, directives, generated files, or unrelated comments. Treat Python docstrings as potentially observable, not ordinary comments.
5. For a workaround or suppression, propose the smallest root-cause fix or an executable contract (type, validation, regression test or existing static gate). Do not change application behavior or delete a real constraint merely to reduce comment count. Implement such changes only when the caller's task already authorizes that behavior change; otherwise report them separately.
6. Inspect the resulting diff for unintended changes. Run the relevant fast checks for any edited files under the repository test policy. Do not run browser E2E just to check comment cleanup.

Report removed and retained comments, unresolved constraints, proposed enforcement, checks and whether independent review ran. Never commit, post or merge as a consequence of this skill alone.

Inspired by the Cursor pstack [no-comments workflow](https://github.com/cursor/plugins/blob/main/pstack/skills/no-comments/SKILL.md), adapted to PRKS rather than imported.
