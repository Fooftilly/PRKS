# Agent task discovery — Project #3

The Project #3 custom **Agent** field is the only executor-assignment source of truth. It is not the GitHub issue's Assignees field and it does not start an agent. **Status** is lifecycle, **Execution** identifies human/agent/mixed work, and neither is inferred from Agent. Do not mirror Agent into labels or native issue assignees.

From a PRKS clone with `gh` (authenticated with the `project` scope) and `jq` installed:

```bash
bash scripts/agent-queue.sh cursor   # Cursor Cloud
bash scripts/agent-queue.sh claude   # Claude Code
bash scripts/agent-queue.sh chatgpt  # ChatGPT/Codex, including Codex
```

The helper reads all Project #3 items once with `gh project item-list` and prints matching PRKS issues with their live Project Status and Execution fields. It rejects incomplete Project item lists, and never updates GitHub. It displays **all** assigned issues, including Inbox, Backlog, Blocked, In Progress and unaccepted candidates. Its output is **not** an actionable queue or authorization to start work.

Before starting any issue:

1. Read the issue's current body and labels. Candidate findings require an explicit acceptance/implementation decision; project assignment is not that decision. Check Project Status is **Ready**, Execution is **Agent** or **Mixed**, and there is no other owner. **In Progress** means coordinate with the existing owner rather than claim the work.
2. Recheck native `Blocked by` dependencies against live GitHub. An open prerequisite blocks starting; a missing or inaccessible dependency response does not imply no blocker.
3. Compare the affected files and behavior against open PRs and the accepted target roadmaps, particularly #310/#614 PostgreSQL and backend cutover, #230/#303 completed frontend migration, and active replacement/design plans. Skip code that is being retired, SQLite-specific fixes during cutover, duplicate PR work, or findings the planned architecture will eliminate.
4. Take one bounded task, implement with targeted tests, and open a PR for maintainer review. Do not merge without explicit approval. Return to the queue after that task is completed or deliberately parked.

For hosted agents without a shell or a GitHub Projects API, use the already-established validated, fresh Project #3 readiness snapshot workflow described in `docs/agent-workflows/project-3.md` §14. Recheck the live issue and blockers before acting. If the snapshot is unavailable/stale, obtain a new CLI queue output from the maintainer and treat readiness as unknown until verified. Do not infer ownership from issue labels, PR authors, or previous chat assignments.
