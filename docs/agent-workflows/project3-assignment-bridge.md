# Project #3 assignment command bridge (legacy connector fallback)

This is the **legacy compatibility fallback** while the current ChatGPT GitHub connector lacks workflow-dispatch and Project V2 field-write actions. For the new comment-free authenticated workflow writer, use `docs/agent-workflows/project3-assignment-dispatch.md`. Do not disable this bridge until the direct writer is verified for every relevant caller.

The bridge lets the maintainer request Project #3 **Agent**, **Status**, and **Execution** changes from an ordinary ChatGPT conversation. ChatGPT posts an authenticated comment through the connected GitHub App to [assignment inbox #982](https://github.com/Fooftilly/PRKS/issues/982). The Action validates and applies only the requested, permitted field changes.

**Assignment is not agent execution.** Cursor, Claude, and ChatGPT/Codex still require their respective sessions to start.

## One-time activation

1. Merge the implementation PR. The listener on new comments to #982 is active but defaults to **no-write validation**.
2. Confirm the existing GitHub Environment named project-automation admits only master and contains its existing PROJECT_SYNC_TOKEN secret (classic project scope). Do not move that secret to repository level.
3. Ask ChatGPT to send a check-only command for an existing assignment such as #815. Confirm the resulting Actions job posts a CHECK ONLY receipt. This validates that the GitHub connector comments are attributed to Fooftilly (user ID 46610174), performed by ChatGPT Codex Connector (App ID 1144995), and can read the Project.
4. Enable writes once in repository Settings → Secrets and variables → Actions → Variables. Set PROJECT3_ASSIGN_BRIDGE_MODE to the exact value apply. Alternatively, from a local shell:

    gh variable set PROJECT3_ASSIGN_BRIDGE_MODE --repo Fooftilly/PRKS --body apply

Any other value (including unset) keeps the workflow read-only. To disable writes:

    gh variable delete PROJECT3_ASSIGN_BRIDGE_MODE --repo Fooftilly/PRKS

## Daily use

Tell ChatGPT, for example:

> Assign #815 to Cursor Cloud and mark it Ready, Execution Agent. Leave other assignments unchanged.

ChatGPT checks whether the issue is accepted, its target code is still relevant to the approved roadmap, there are overlapping PRs, and there are native open blockers. It then submits a comment to #982 through the connected GitHub app, and reads the Actions run and audit receipt before reporting the actual outcome. A submitted command is **not** proof that an assignment succeeded.

The exact command protocol is a literal marker line followed by a JSON object; no Markdown formatting, explanatory prose, or other commands are permitted inside the comment:

    PRKS_PROJECT3_ASSIGN_V1
    {"version":1,"operation":"apply","items":[{"number":815,"set":{"Agent":"cursor","Status":"Ready","Execution":"Agent"},"reason":"Confirmed current frontend bug; roadmap and overlapping PRs checked.","roadmap_checked":true,"overlap_checked":true}]}

- operation can be check (read-only even when writes enabled) or apply. One comment contains 1–10 distinct existing PRKS issues; no PR or Project settings changes.
- Agent accepts aliases including codex / chatgpt / ChatGPT/Codex, cursor / Cursor Cloud, and claude / Claude Code.
- Every row needs a reason and truthful roadmap_checked and overlap_checked attestations. The checks must be performed, not guessed.
- A different existing Agent cannot be replaced unless the comment includes the explicit field "allow_reassignment": true for that row.
- Assignments do not imply readiness. Unaccepted candidates can remain Inbox with an Agent, but cannot be promoted to Ready or In Progress. Existing Blocked and Done statuses are protected.
- Field state, issue update timestamp, candidate acceptance, and native dependencies are rechecked before and after writes. Changes during a batch cause a visible error.

## Authorization, safety and audit

The workflow accepts only **new issue comments** on #982, on master. It fetches the live REST record of the comment before any mutation. The immutable user ID of Fooftilly (46610174) and the originating GitHub App ID (1144995) must both match. Ordinary prose, PR comments, edited comments, comments from other apps, and stale commands are refused. The JSON is treated as data, never as shell instructions.

A single serialized workflow checks for a previous claim receipt for the command ID and posts a new single-use claim **before** any Project mutation. On completion, the receipt is updated to APPLIED, CHECK ONLY, DRY RUN, or FAILED / POSSIBLY PARTIAL. Re-running a claimed command cannot replay it. An interrupted or partially applied batch must be inspected before submitting a new command.

The workflow uses the built-in GITHUB_TOKEN with scoped issues-write permission for private PRKS issue/dependency REST reads and receipt comments. The existing protected PROJECT_SYNC_TOKEN handles only Project V2 GraphQL reads/writes, keeping the classic project-scoped credential separate from repository issue access. No new secret, agent label, GitHub Assignee, or automatic agent launcher is introduced.

Read current agent ownership with scripts/agent-queue.sh. For manually reviewed batches, see docs/agent-workflows/project3-triage.md.
