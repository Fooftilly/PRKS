# ChatGPT Project #3 assignment requests — comment-free transport

GitHub Project #3 remains the **source of truth** for Agent, Status and
Execution. `project3-agent-queue/agent-queue.json` remains a read-only published snapshot.

Unlike the owner-only workflow-dispatch UI, **this request mechanism is
usable from the currently connected ChatGPT GitHub plugin**, which can
`fetch_file` and `update_file` on a repository branch. No Project token,
manual workflow run, issue command or receipt comment is needed per assignment.

## Initial activation (after the PR is merged)

1. Keep the existing `project-automation` environment restricted to the
   `master` ref, with `PROJECT_SYNC_TOKEN` available as an environment
   secret. The existing repository variable
   `PROJECT3_ASSIGN_DISPATCH_MODE=apply` also gates the new writer.
2. **Once**, create the branch `project3-assignment-requests` from current
   `master` (not from a pull request). The file
   `project3-assignment-request.json` already exists on `master` with
   an inactive placeholder. The request branch is a data transport: do not
   merge it into master, add other files or make code changes on it.
3. **Before any request**, open Repository Settings → Rules → Rulesets →
   New branch ruleset. Name it `Project 3 assignment request branch` and
   configure:
   - Enforcement: **Active**.
   - Target branches: explicitly include only
     `project3-assignment-requests` (exact ref
     `refs/heads/project3-assignment-requests`).
   - Enable **Restrict deletions** and **Block force pushes**.
   - Leave bypass actors **empty**; normal fast-forward file updates from the
     authenticated GitHub plugin must remain possible.
   The privileged workflow checks the *live GitHub ruleset* on every request
   and refuses Project updates unless it finds both protections with active
   enforcement, an exact matching ref, and the precise reviewed ruleset
   revision. It does not install repository rules automatically: the current
   ChatGPT GitHub connector lacks repository-administration permissions.
   If other rules are added, verify they do not block ChatGPT's single-file
   fast-forward updates.
   **Important: GitHub's metadata-only ruleset API can omit bypass actors.**
   Use an administrator's authenticated GitHub CLI to inspect the newly created
   ruleset and explicitly verify `bypass_actors: []`. Copy its integer `id`
   and verbatim `updated_at` value, then pin them as **Environment** variables
   in the existing protected `project-automation` environment:
   ```bash
   gh api repos/Fooftilly/PRKS/rulesets/RULESET_ID \
     --jq '{id,updated_at,bypass_actors,conditions,rules,enforcement}'
   gh variable set PROJECT3_REQUEST_RULESET_ID \
     --repo Fooftilly/PRKS --env project-automation --body 'RULESET_ID'
   gh variable set PROJECT3_REQUEST_RULESET_UPDATED_AT \
     --repo Fooftilly/PRKS --env project-automation --body 'EXACT_UPDATED_AT'
   ```
   Replace the two placeholders using the verified API output. If
   `bypass_actors` is absent rather than explicitly empty for the administrator,
   do **not** activate this relay. Environment variables default to missing,
   which blocks processing safely. The processor uses only metadata read,
   verifies the exact ruleset ID and **timestamp instant** on every run
   (account-specific ISO-8601 timezone renderings may differ), and fails
   closed on any later ruleset edit. An administrator must review and repin
   a changed ruleset before processing resumes. No additional token is needed.
4. Submit a **check-only** update using the authenticated GitHub plugin, then
   verify both Actions runs: **Project 3 assignment request signal** and
   **Project 3 assignment request processor**. The processor summary must
   report `CHECK ONLY`, with no Project field changes.
5. Only then submit an explicitly approved `apply` request, verify the
   Project item, the processor log and the queue publisher's complete snapshot.
   If the signal does not start or the processor rejects authorization, leave
   #982 working while investigating rather than disabling safeguards.

## Dedicated data branch and merge isolation

The live request branch is kept intentionally minimal. Its current tree has
**only two tracked files**: `project3-assignment-request.json` (the command
data) and `.github/workflows/project3-assignment-request-signal.yml`
(the unprivileged push trigger). The signal workflow **must stay on this
branch** for future file updates to emit GitHub Actions push signals. Do not
copy application code into this branch, delete the signal workflow, or merge
`master` into it. The branch originally descended from `master`, so older
commits remain in Git history even though the current tree is data-only.

`master` requires the `Mergify Merge Protections` check. The reviewed
`.mergify.yml` rule **PRKS / Never merge Project 3 data branches** refuses
pull requests whose source branch is `project3-assignment-requests` or
`project3-agent-queue` and whose base is `master`. Do not exempt or
disable this required check. This blocks normal GitHub PR merges and API
merges subject to the active master ruleset; it is not a GitHub prohibition
on opening PRs or a replacement for the existing master protections.

## Agent protocol for each assignment

ChatGPT must first inspect the current open issue, its native **Blocked by**
relationships, accepted roadmap, active conflicting PRs, and the Project's
Agent/Status/Execution values. The read-only queue is a starting snapshot, not
approval to overwrite newer Project values. Never invent the two truthful
`roadmap_checked` and `overlap_checked` attestations.

Using the **connected GitHub plugin**:

1. Fetch `project3-assignment-request.json` on ref
   `project3-assignment-requests` and keep its **exact blob SHA**.
2. Replace the *entire* file using `update_file`, specifying
   `repository_full_name="Fooftilly/PRKS"`,
   `path="project3-assignment-request.json"`,
   `branch="project3-assignment-requests"`, the fetched `sha`,
   and the JSON command. This writes one commit to the request branch; it does
   **not** create a pull request or issue comment.
3. Read the **signal** and then the **processor** Actions runs. A successful
   file update is *not* proof that Project fields changed. Inspect the
   processor's `APPLIED` summary and the live Project/queue state.
   If processing fails, stop; don't repeatedly resubmit the same command.

Example JSON (replace **all** before-values with live ones; numbers and
values are illustrative, not an automatic authorization):

```json
{
  "version": 1,
  "operation": "check",
  "items": [
    {
      "number": 636,
      "set": {"Agent": "Claude Code", "Status": "Ready", "Execution": "Agent"},
      "expected_before": {"Agent": "Claude Code", "Status": "Ready", "Execution": "Agent"},
      "reason": "Previously approved PostgreSQL Processing assignment; verified live blockers and overlap.",
      "roadmap_checked": true,
      "overlap_checked": true
    }
  ]
}
```

Use `"operation": "apply"` only for the maintainer's explicitly authorized
field edits. A check-only request never changes Project fields even if
the write mode is enabled. The full three-field `expected_before`
snapshot is required for every issue; a stale or replayed update fails
closed. In-progress or Blocked issues, unaccepted candidates and open
native blockers retain all existing triage protections. Reassigning away
from an existing Agent requires `"allow_reassignment": true` and explicit
authorization. One command can contain 1–10 existing issues.

## Security boundary

The signal workflow is an **unprivileged** branch-push notification with no
checkout, secrets or write token. The processor is a `workflow_run` event
executed from trusted `master`, in the protected environment.

Before reading the JSON, it independently verifies against live GitHub REST:
the owner Fooftilly's immutable ID (46610174), successful first-attempt
signal run, exact signal workflow path/event, repository, request branch and
SHA, that the request SHA is still the branch's tip, that GitHub attributes
the commit author and committer to that owner, and that the commit
changed **only** the existing request JSON file. It fetches that small file
at the verified *commit SHA*, decodes it as data and passes it to the
already-reviewed `project3_assignment_dispatch` validator and
`project3_triage` field/issue guards. It never checks out or executes
untrusted request-branch code, reads upstream artifacts, runs shell commands
from the request, or exposes the Project token to branch workflows.

The branch-ruleset check intentionally **does not** interpret an omitted
`bypass_actors` field as evidence that bypass is disabled. An administrator
must inspect that field and pin the ruleset's `updated_at` in the protected
environment. The runtime detects a changed ruleset revision and stops
without Project mutation. This provides a reviewed-configuration gate without
granting repository Administration permissions to the workflow token.

The processor's `workflow_run` trigger has a narrowly scoped
`zizmor: ignore[dangerous-triggers]` annotation because the generic audit
cannot establish this specific trust boundary. This exception requires:
a trusted `master` checkout, the protected environment, fixed owner-ID
authorization, independent REST verification of the upstream run and exact
one-file pinned JSON commit, no upstream artifacts, no untrusted script or
PR checkout, and no interpolation of request data into a shell command.
If any requirement changes, remove the suppression and reassess security.

**One-time execution claim:** Before accessing Project V2 for a request, the
trusted master workflow reads the complete paginated GitHub commit-status
history for the request SHA and refuses any SHA already carrying the reserved
`prks/project3-assignment-request-claim` context. It then publishes a
`pending` status using its scoped `statuses:write` token, verifying the REST
response before invoking Project code, and appends `success` on completion.
These commit statuses are append-only and independent of the request branch:
a force push, restored commit, failed run or Project field values returning
to earlier values cannot authorize replaying the same commit. A failed/partial
run leaves a durable claim even if its result remains pending; review the
run and submit a new reviewed commit instead of retrying. All processors are
serialized with the dispatch and legacy bridge.

**GitHub App identity limitation:** GitHub's push/commit REST records expose
the GitHub user that caused the push and the commit author/committer, but
do not provide the originating App ID with the same authenticated semantics
as issue-comment `performed_via_github_app`. This transport verifies the
owner's immutable user ID, owner-attributed commit, protected request branch
and complete guarded command. It **cannot** restrict writes exclusively to
the ChatGPT Connector app. Any authenticated access acting as the repository
owner and permitted to push this branch would also be authorized, subject
to the same command, ruleset and replay checks. If strictly verifying the
ChatGPT App ID is a requirement, use a dedicated custom GitHub App
integration with a verifiable caller identity; do not claim this relay
has that property. The retired #982 listener had stronger App attribution.

The two assignment writers (manual dispatch and request branch) share the
same serialized concurrency group. A failed attempt
could be partially applied; the processor still refreshes the
read-only queue, and logs a failed/possibly-partial audit in the Actions
run summary. There is no atomic GitHub Projects transaction.

## Legacy #982 retirement

On 2026-10-30, the connected ChatGPT GitHub plugin submitted a successful
check-only request (processor run 41096256458) and a guarded apply request
(processor run 41097094867). The apply verified one Project #3 update on #629;
queue publisher run 41097256390 succeeded and a complete fresh snapshot
confirmed `Execution: Agent` with unchanged `Agent: Claude Code` and
`Status: Inbox`. The legacy issue-comment listener and its related code were
removed in the separate retirement cleanup. Keep the request-branch ruleset,
reviewed timestamp pin, one-time claims and live expected-before checks.
Manual dispatch and reviewed local triage remain independent fallbacks.
