# Cursor Projects workflow for PRKS

This is an operator guide for Cursor Projects/coordinator usage. It is not an always-on engineering rule.

## Delegation model
- Keep one long-lived PRKS Project so shared context survives across tasks.
- Give one worker one bounded deliverable.
- When a GitHub issue exists, treat it as the task source of truth and avoid restating the entire repository history in the worker prompt.
- Prefer independent parallel work. Do not assign several workers to rediscover the same subsystem unless independent review is intentional.
- Let workers read root and scoped `AGENTS.md` files, then only the canonical documentation relevant to their task.
- Use `docs/agent-context/sync-map.md` before loading the large local-first/offline specifications.
- For UI work, search `DESIGN.md` for the affected component/interaction and read the relevant sections rather than loading the whole file.

## Worker task packet
Use a compact handoff:

```text
Goal:
GitHub issue:
Scope:
Relevant context:
Likely files:
Must preserve:
Acceptance criteria:
Targeted tests:
Stop/escalation condition:
```

Do not preload large folders or copy long canonical documents into the packet; point to the source instead.

## Implementation and verification
- Prefer targeted unit/API/Node/static tests during implementation.
- Run affected or feature E2E once a coherent vertical slice is complete.
- Run the full E2E gate only as final validation when the task warrants it.
- If two substantially identical fix attempts fail, stop repeating the loop. Reassess the hypothesis or delegate a focused debugging task with the failure evidence.
- A worker completion report should state: changed files, tests run, unresolved risks, and any follow-up dependency.

## Model/usage discipline
- Use the Project's normal/default implementation model for routine coding and tool use.
- Escalate to a stronger/more expensive model only when the task actually requires difficult architecture, debugging, or cross-cutting reasoning.
- Parallelism is primarily a speed tool, not a token-saving mechanism: avoid duplicated investigation.
- Evaluate usage from Cursor's model/usage breakdown, not raw context-token totals alone, because cached context may dominate the displayed token count.
