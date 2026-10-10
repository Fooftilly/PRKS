# Project #3 contract

This is the checked-in record of how GitHub Project #3 ("PRKS Roadmap", owned by the `Fooftilly` account) is configured and automated, and of the rules automation must keep (#441). Project-side settings live in GitHub's UI, not in this repository, so this document is the reference for reasoning about them together with the checked-in workflows.

> **Status of this document: proposal.** Section 1 (existing configuration) is pending the read-only inventory. Everything marked **Proposed** needs the maintainer's approval of the specific configuration before anyone changes Project #3. Until then nothing in this document is applied, and no Project #3 field, status, workflow or view may be changed.

## Governance (always applies)

- Automation never merges a pull request, never enables auto-merge, and never reads green CI or a bot approval as merge authorization.
- Status is lifecycle only. It never encodes CI health, priority, category or who executes the work.
- Pushing commits after a "changes requested" review does not imply approval or move the item to Review.
- Merging a PR completes the PR item only. Automation never completes, closes or moves its parent, controller or roadmap issue, and PRs reference those issues with `Refs #N`, never a closing keyword (which would make GitHub close them).
- Controller and roadmap issues stay active until their own acceptance criteria are met, and are closed by a person.
- Where automation cannot know intent, a manually set Status wins. Automation never moves an item out of **Blocked** or **Done** without an explicitly approved policy. The only such policies are the native closing and reopening rules in §5, approved with that configuration: closing an issue or PR, or merging a PR, sets Done from any Status (closing is a person's or a merge's own act), and reopening an issue moves it from Done to Ready. `project-sync` has no exception.
- AI triage output never changes Status, fields or dependencies by itself.

## 1. Existing configuration

**Pending inventory.** The maintainer runs the read-only export in the #441 audit report, which lists fields with their options, views with their filters, and native workflows with their on/off state. Workflow settings (auto-add filter, target statuses, archive filter) come from screenshots, because the API does not expose them. This section will then record, verbatim:

| Item | Existing | Proposed change | Needs approval |
|---|---|---|---|
| Status options | *pending* | §2 | yes |
| Other custom fields | *pending* | none added except Execution (§3) | yes |
| Native workflows and their settings | *pending* | §5 | yes |
| Views and their filters | *pending* | §7 | yes |
| Item membership (which issues and PRs are on the board) | *pending* | one-time backfill (§5.1 step 3) | yes |

Nothing in sections 2–8 assumes the current options match the proposed ones. Each proposed change will be mapped from what the inventory shows.

## 2. Lifecycle Status (provisionally approved)

| Status | Meaning |
|---|---|
| Inbox | Added, not triaged. |
| Ready | Triaged and actionable; can start now. |
| In Progress | Being worked on, including draft PRs. |
| Review | Waiting for a human review. |
| Changes requested | A review asked for changes; waiting on the author. |
| Blocked | Cannot proceed. Set by a person; a native "Blocked by" dependency is shown separately (§9). |
| Done | The item itself is complete: issue closed, PR merged, or PR closed without merging. |

Migration from the existing options is proposed only after the inventory (§1).

## 3. Execution field (approved)

One new single-select field, **Execution**, with options **Human**, **Agent** and **Mixed**. It is set by hand at first. Execution says who does the work; it does not make an item ready. Agent Queue (§7) also requires a lifecycle Status and no unresolved prerequisite.

## 4. Classification comes from repository metadata

No Work type, Area or Priority Project fields. Views filter on the canonical repository metadata instead:

| Concept | Source |
|---|---|
| Priority | `priority:P1`, `priority:P2`, `priority:P3` labels |
| Area | `area:*` labels |
| Research | `research` label, applied by the Research / evaluation template |
| Kind | `bug`, `enhancement`, `roadmap`, `audit-finding`, `ux-finding` labels. An issue with none of them is an engineering task (that template sets no kind label). Issue Types are not used: they are an organization feature, and `Fooftilly` is a personal account. |
| Dependency updates | `dependencies` label (Dependabot's default) and `author:app/dependabot` |
| Parent / progress | native sub-issues, Parent issue and Sub-issue progress fields |
| Linked PRs, reviewers, milestone | native fields |

Research backfill: the only `[Research]` issues, #181, #234 and #246 (all closed as completed), received the `research` label on 2026-10-10 with the maintainer's approval. Their state and content were not changed. No other issue is relabelled.

## 5. Native Project workflows (Proposed)

Every setting is checked against its current configuration (§1) before it is changed.

| Workflow | Proposed setting | Why |
|---|---|---|
| Auto-add to project | On. Filter: all issues and PRs in `Fooftilly/PRKS`, including dependency PRs | Everything enters without manual adding. Dependency PRs are kept out of the ordinary views by their filters (§7), not by auto-add. |
| Item added to project | **When:** Issues only (Pull requests unchecked). **Set:** Status Inbox. Approved 2026-10-10 | Default entry state for issues. PR items get their first Status from `project-sync` instead (§10.1), so a delayed native run cannot reset a classified PR. |
| Item closed | Status: Done, for issues and PRs | The item itself is complete. A PR closed without merging is finished too. If a merged PR's closing keyword closes an issue, GitHub closes it and this rule moves it to Done; that is why slice PRs reference controller and roadmap issues with `Refs #N`, never a closing keyword. |
| Pull request merged | Status: Done | Applies to the PR item only. |
| Item reopened | **When:** Issues only, if the workflow offers the selector (checked against the screenshots). **Set:** Status Ready | Reopened work is actionable again. Whether an explicitly set state should be preserved is checked against the native workflow's behavior first. A reopened PR keeps Done: `project-sync` never moves an item out of Done, so a person resets a reopened PR's Status by hand. |
| Code changes requested | **Off.** `project-sync` owns this transition (§6) | The native rule does not check the current Status, so any changes-requested review, a bot's included, would move a PR out of a Blocked status a person set. `project-sync` applies it only from empty, In Progress or Review. |
| Code review approved | **Off** (provisional) | A bot approval would move Status, and approval is not authorization. |
| Auto-close issue | **Off** (provisional) | Moving a controller issue to Done must not close it. |
| Pull request linked to issue | **Off** (provisional) | One slice PR must not move its controller issue. |
| Auto-archive items | **Off for issues** (§8) | Issues stay discoverable. |

### 5.1 Applying these settings (only after the rollout is approved)

The settings live under Project #3 → ⋯ → **Workflows**. Each built-in workflow has its own page with a **When** selector for the item type, a **Set** value, and an on/off toggle; auto-add and auto-archive use a filter text box instead. Nothing here is applied until the inventory has been reviewed and the rollout approved.

1. **Item added to project:** under **When**, select Issues and clear Pull requests. Set Status to Inbox. Save, and turn it on.
2. **Auto-add to project:** select the `Fooftilly/PRKS` repository. Use a filter that matches both issues and PRs, for example `is:issue,pr`, with no label condition, so dependency PRs are added too. Turn it on. Auto-add only acts on items created or updated afterwards; it does not add existing ones.
3. **One-time membership backfill:** from the inventory's item list, compare Project #3's items with the repository's open issues and PRs (and closed research issues for Research History, §7). The maintainer adds the missing items by hand, or approves a one-off list. Added open issues get Status Inbox from "Item added"; added open PRs stay without Status until their next `project-sync` event or a person sets one. "Item closed" fires only on the close event, not on add, so the backfill sets **Done by hand on every closed item it adds** (including #181, #234 and #246), and the inventory check lists any closed item whose Status is not Done.
4. **Item closed**, **Pull request merged** and **Item reopened:** set the values in the table above. Where a **When** selector is shown, choose the item types listed there.
5. **Code review approved**, **Auto-close issue** and **Pull request linked to issue:** record their current settings from the screenshots, then turn them off now. Also record the current setting of **Code changes requested**, but leave it as it is until the `PROJECT_SYNC_MODE=apply` cutover, and turn it off in that same step, so the transition always has exactly one owner. Until then the native rule may still move a Blocked PR; that is the accepted interim state.
6. **Auto-archive items:** leave it off.

## 6. Event → Status mapping

| Event | Item | New Status | Owner | Allowed only from |
|---|---|---|---|---|
| Issue added | issue | Inbox | native | (new item) |
| PR added (by auto-add or `project-sync`) | PR | no change (empty until classified) | none | |
| Triaged as actionable | issue | Ready | person | Inbox |
| Work starts | issue | In Progress | person | Ready |
| Draft PR opened | PR | In Progress (Changes requested if a changes-requested review is unanswered) | `project-sync` | empty, Inbox |
| PR opened ready for review (approved 2026-10-10) | PR | Review (Changes requested if a changes-requested review is unanswered) | `project-sync` | empty, Inbox |
| PR marked ready for review | PR | Review (Changes requested if a changes-requested review is unanswered) | `project-sync` | empty, Inbox, Ready, In Progress |
| PR converted back to draft | PR | In Progress | `project-sync` | Review, Changes requested, and only if the latest conversion to draft is newer than the latest changes-requested review |
| Changes requested in a review | PR | Changes requested | `project-sync` | empty, In Progress, Review, and only while a changes-requested review is unanswered. Bot reviews count like any other review. |
| Change request dismissed, or withdrawn by the same reviewer's approval | PR | In Progress (draft) or Review | `project-sync` | Changes requested, only when no changes-requested review is unanswered and a change request was dismissed or withdrawn after that Status was set. A comment review, or an approval from someone who never requested changes, leaves Changes requested alone, so one set by hand stays. |
| Re-review explicitly requested | PR | Review | `project-sync` | Changes requested, and only when no changes-requested review is unanswered: every reviewer who requested changes has been requested again since. Requesting a different reviewer does not count. |
| New commits pushed | PR | no change | none | |
| CI result of any kind | either | no change | none | |
| PR merged | PR | Done | native | |
| PR closed without merging | PR | Done | native | |
| Issue closed | issue | Done | native | |
| Issue reopened | issue | Ready | native | Done |
| Linked PR merged | its issue | no change, unless the PR's closing keyword closes the issue (then "Issue closed" applies) | none | |

A changes-requested review is *unanswered* while it is its reviewer's latest approving or change-requesting review (a dismissed review does not count), and neither a review request of that same reviewer nor a conversion to draft is newer than it. A request at the same instant does not answer it. The rule is evaluated per reviewer, so the opened and ready rows also cover a late `opened` run that adds the item after a review.

`project-sync` never writes an issue item, never moves an item out of Blocked or Done, and leaves any item whose current Status is not in its "allowed from" list unchanged, logging that it did so.

## 7. Views as filtered projections (Proposed)

Filters use GitHub Projects filter syntax. Exact field and option names follow §1 once it is filled in.

| View | Layout | Filter |
|---|---|---|
| Active Work | table | `is:open -label:dependencies status:Ready,"In Progress",Review,"Changes requested",Blocked` |
| Agent Queue | table, grouped by Status | `is:open is:issue execution:Agent,Mixed status:Ready,"In Progress" -label:dependencies` |
| Research Queue | table | `label:research is:open -status:Done` (active research only) |
| Research History (new) | table, sorted by Closed date | `label:research is:closed` (completed research stays discoverable here) |
| Roadmap | roadmap | `label:roadmap`, with Sub-issue progress and milestone |
| Work Board | board by Status | `-label:dependencies` (Done items included and visible) |
| Timeline | roadmap by date fields | same items as Work Board |
| Maintenance (new) | table | `label:dependencies` |

**Dependency PRs:** they are added to Project #3 like any other PR and follow the same PR lifecycle (§6). Active Work, Agent Queue and Work Board exclude them with `-label:dependencies`, so they show up only in Maintenance. Automation never merges them.

**Research:** active and completed research are separated by two views rather than by archiving. Research Queue holds open research. Research History holds closed research, including #181, #234 and #246. Neither view archives anything, and completed research issues are never auto-archived (§8). A closed research issue appears in Research History only if it is an item on Project #3. Whether these three already are is checked in the inventory (§1); if not, adding them is a manual step for the maintainer.

Agent Queue separates ready work from work blocked by an unresolved prerequisite. A blocked-state filter qualifier in Project views is not yet verified. If it exists, Agent Queue adds it. Otherwise a read-only eligibility report lists Ready Agent/Mixed issues with and without open native blockers (§9), and the view shows both groups.

## 8. Archival

**Issues are never archived automatically.** Age, closure or Done alone never decides relevance. This covers roadmap and controller issues, completed issues still relevant to future work, issues that record architectural or implementation decisions or historical bugs and their fixes, and issues referenced by dependencies or roadmap work.

- Completed issues are hidden from Active Work and Agent Queue by the view filters in §7, not by archiving.
- A person may archive an issue manually when it is genuinely obsolete.

**Merged PRs (proposal for later evaluation, not enabled):** a native auto-archive filter scoped to merged PRs only, for example `is:pr is:merged updated:<@today-30d`. Whether the auto-archive filter can be restricted to PRs safely is checked before this is proposed for approval.

## 9. Native issue dependencies

Prerequisites are GitHub's native **Blocked by / Blocking** relationships, recorded only through the maintainer-dispatched writer described in `docs/agent-workflows/issue-dependencies.md`. Project #3 reads them as they are; nothing copies them into labels, comments or Status. A relationship never forces an item's Status backwards. Agent Queue and triage reports treat an issue with an open blocker as not ready (§7).

## 10. `project-sync` design (approved design; ships in dry-run)

A checked-in workflow, `.github/workflows/project-sync.yml`, covering only the seven PR rows marked `project-sync` in §6. They come from five events: `opened` covers both the draft and the ready row, and the relayed review event covers both a new change request and its dismissal or withdrawal.

- **Events:** `pull_request_target` (`opened`, `ready_for_review`, `converted_to_draft`, `review_requested`), and a relayed review event: a submitted changes-requested or approving review, or a dismissed review. A review has no `pull_request_target` action, so `.github/workflows/project-sync-review.yml` relays it: a `pull_request_review` workflow with no permissions, no secrets and no checkout, whose successful completion starts `project-sync` through `workflow_run`. A `workflow_run` run always uses the default branch's copy of `project-sync.yml` and takes the PR number from GitHub's record of the relay run, never from its output. The job checks out only the default branch and never runs pull-request code. The PR's number is the only input; the PR's draft and open state and its review history are re-read from the API, so a stale event changes nothing.
- **Merged or closed PRs:** an event for a PR that is already merged or closed when the run reads it changes nothing: no item is added and no Status is written. A run never writes In Progress, Review or Changes requested to a merged or closed PR. Its only write for one is Done, and only for a PR that was merged or closed after the run had read it open (during the item add or the Status write); then the run sets Done on an item that has no Status yet, or on the Status the run itself just wrote, and leaves any other Status alone.
- **Fork PRs (known limitation):** GitHub does not attach the PR number to a `workflow_run` started from a fork's review, so reviews and dismissals on fork PRs are not relayed and their Status is set by hand. The `pull_request_target` events still apply.
- **Scope:** PR items only. Field and option IDs are resolved at run time from names kept in one config file (`.github/project-sync.json`). If any name is missing, the run fails clearly.
- **Behavior:** applies the "allowed from" rules in §6, so a repeated run makes no change. Each run logs one audit record (PR, event, Status before and after, outcome) and a job summary.
- **Default:** dry-run. It writes only when the repository variable `PROJECT_SYNC_MODE` is exactly `apply`, which is set only after the credential, the configuration and the workflow have been reviewed and the maintainer authorizes it. Without `PROJECT_SYNC_TOKEN` a run reports `not-configured` and succeeds without calling the API, so PR checks stay green before rollout.
- **Dependabot PRs (known limitation):** GitHub gives `pull_request_target` runs triggered by Dependabot only Dependabot secrets. `PROJECT_SYNC_TOKEN` is deliberately **not** added as a Dependabot secret for now, so these runs report `not-configured` and leave dependency PRs without a `project-sync` Status. Native auto-add still puts them on the board, where the Maintenance view shows them, and the native merged and closed rules still apply. A safe rollout strategy for them is evaluated after the inventory.
- **Missing items (approved):** if auto-add has not yet created the PR's item, `project-sync` adds it with `addProjectV2ItemById`, using the PR's own node ID. It first looks for an existing item. GitHub documents that adding an item that already exists returns the existing item, so repeated or concurrent adds converge to one item. It only adds PRs whose repository is `Fooftilly/PRKS`, only to Project #3, and never adds or edits the linked issue.

### 10.1 Race between native auto-add, "Item added" and `project-sync`

The risky ordering, with "Item added → Inbox" applying to PRs:

1. A draft PR opens. `project-sync` finds no item, adds it, and sets In Progress.
2. The native auto-add then runs and finds the item already there (no new item).
3. The native "Item added" workflow, triggered by step 1's add and running later, sets the Status to Inbox.

The PR is now wrongly back in Inbox. GitHub does not document whether "Item added" leaves an already-set Status alone, or in what order native workflows run against API writes, so this combination **cannot be guaranteed safe**. The same reset can happen when the native add wins and `project-sync` classifies the PR before the native "Item added" run lands.

**Recommended division of responsibility:**

| Concern | Owner |
|---|---|
| Putting the PR on the board | native auto-add, with `project-sync` as an idempotent fallback |
| Inbox for new issues | native "Item added", **When: Issues only** |
| First Status of a PR (In Progress or Review) | `project-sync` only; a PR with no Status counts as unclassified |
| Merged or closed → Done | native |
| The seven `project-sync` PR rows in §6 (five events), Changes requested included | `project-sync` |

This division was approved on 2026-10-10. With "Item added" limited to issues through its **When** selector, nothing native ever writes Inbox to a PR, so no delay can reset one.

Remaining races and how `project-sync` handles them:

- **PR merged or closed while `project-sync` writes.** It re-reads the PR and the item's Status immediately before writing; a PR already merged or closed at that point is left alone (§10). The Projects API has no compare-and-set, so a short window remains. After writing, it re-reads the PR; if it was merged or closed in that window, it replaces its own write with Done, which is what the native rule would have set, and logs the correction.
- **PR merged or closed while its item is being added.** The run read the PR open, so it adds the item; the native Done rule may have fired before the item existed and had nothing to update. If the added item has no Status, it sets Done; a Status set meanwhile is left alone.
- **A delayed re-review request or draft conversion.** A `review_requested` run applies only when no changes-requested review is unanswered (§6), and a `converted_to_draft` run only when the latest conversion is newer than the latest changes-requested review. Both fail closed when their event is not recorded or the times are equal. If a changes-requested review lands during the write, Changes requested is restored. `opened` and `ready_for_review` never move an item out of Changes requested, so they need no ordering check.
- **A delayed review run.** It re-derives the state from the review history: it sets Changes requested only while a changes-requested review is unanswered, and otherwise moves only an item in Changes requested on to In Progress or Review, and only after a change request was dismissed or withdrawn later than that Status was set. So whichever of a review run, a dismissal run and a re-review run executes first, the PR ends in the state the review history implies.
- **Two `project-sync` runs for the same PR.** One concurrency group per PR, without cancellation and with `queue: max` (up to 100 pending runs, so a newer event never replaces a pending one), serializes them. Each re-reads before writing, so the later run sees the earlier result.
- **A person changes Status at the same time.** A Status outside the "allowed from" list is left alone, and a person's write after the `project-sync` write stands. The Projects API offers no conditional write, so in the short window between the `project-sync` read and its write, the later write wins. The audit record keeps the before and after values for a manual fix.

**Testing.** The decision logic is a pure function of PR state, item state and event. Unit tests run it against a fake project that replays each ordering above: native add before and after, a delayed native "Item added", a duplicate concurrent add, a merge during the write, a PR already merged or closed when the run reads it, a merge or close between the first read and the item add (with the native Done rule delivered before or after), delayed review-request, draft-conversion and review runs in both orders, a dismissed or withdrawn change request, a hand-set Changes requested with only comment reviews, several reviewers, requesting a different reviewer, and Blocked or Done set by hand. The live check is a dry-run on the next ordinary PR after the configuration is approved. It does not use test PRs or arbitrary items.

Implementation: `scripts/project_sync.py`, `.github/workflows/project-sync.yml`, `.github/workflows/project-sync-review.yml` and `.github/project-sync.json`, with tests in `tests/test_project_sync.py`.

## 11. Credentials and permissions

| Automation | Identity | Permissions |
|---|---|---|
| Issue dependency writer | `GITHUB_TOKEN` | `issues: read` (plan job), `issues: write` (apply job only) |
| `project-sync` review relay (`project-sync-review.yml`) | none | `permissions: {}`; no secrets, no checkout, no writes |
| `project-sync` | `PROJECT_SYNC_TOKEN` repository Actions secret: a classic personal access token with only the `project` scope (`GITHUB_TOKEN` cannot reach Projects) | Project read/write. Repository data it reads is public. |

- The token has an expiration date. To rotate it, create a new token with the same single scope, replace the secret, then revoke the old token.
- To revoke it, delete the token in the account's developer settings and delete the secret. `project-sync` then fails closed without writing.
- The token never appears in repository files, workflow logs, PR comments or documentation. Only the job that needs it receives it, as an environment variable.
- A classic `project` scope reaches every Project the account owns. That is inherent to classic tokens. The workflow only addresses Project #3 by number.

## 12. Recovery, rollback and troubleshooting

- **Stop automation:** disable the workflow in Actions, or switch `project-sync` back to dry-run. Native workflows are switched off in Project → Workflows.
- **Undo a wrong transition:** set the Status back by hand. The audit record gives the previous value, and `project-sync` will not override a Status outside its "allowed from" list.
- **A field or option was renamed:** runs fail with the missing name. Update `.github/project-sync.json` or rename it back.
- **Dependency writer refused or failed:** see the job summary and `docs/agent-workflows/issue-dependencies.md`.
- **A token expired or was revoked:** `project-sync` fails without writing. Rotate it as in §11.
