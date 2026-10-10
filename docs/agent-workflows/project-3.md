# Project #3 contract

This is the checked-in record of how GitHub Project #3 ("PRKS Roadmap", owned by the `Fooftilly` account) is configured and automated, and of the rules automation must keep (#441). Project-side settings live in GitHub's UI, not in this repository, so this document is the reference for reasoning about them together with the checked-in workflows.

> **Status of this document.** Section 1 records the configuration verified read-only on 2026-10-10. Lifecycle Status (§2), Roadmap Stage and Execution (§3) are approved designs; everything marked **Proposed** still needs the maintainer's approval of the exact configuration. Nothing in §5, §7 or §13 is applied by this document, and no automation may change Project #3 fields, options, items, workflows or views until the maintainer approves the specific step.

## Governance (always applies)

- Automation never merges a pull request, never enables auto-merge, and never reads green CI or a bot approval as merge authorization.
- Status is lifecycle only. It never encodes CI health, priority, category or who executes the work.
- Pushing commits after a "changes requested" review does not imply approval or move the item to Review.
- Merging a PR completes the PR item only. Automation never completes, closes or moves its parent, controller or roadmap issue, and PRs reference those issues with `Refs #N`, never a closing keyword (which would make GitHub close them).
- Controller and roadmap issues stay active until their own acceptance criteria are met, and are closed by a person.
- Where automation cannot know intent, a manually set Status wins. Automation never moves an item out of **Blocked** or **Done** without an explicitly approved policy. The only such policies are the native closing and reopening rules in §5, approved with that configuration: closing an issue or PR, or merging a PR, sets Done from any Status (closing is a person's or a merge's own act), and reopening an issue moves it from Done to Ready. `project-sync` has no exception.
- AI triage output never changes Status, fields or dependencies by itself.

## 1. Existing configuration (verified 2026-10-10, read-only)

Sources: the maintainer's baseline export (44 items, `hasNextPage: false`, taken before the 2026-10-10 manual changes) and a live read-only GraphQL snapshot. Workflow filters, target values and When selectors are not exposed by the API; those rows are as reported by the maintainer.

**Items.** 44, all issues from `Fooftilly/PRKS`, none archived, no PRs or draft issues. **35 have Work Type = Epic** (the Roadmap view). The others: #472 (Feature, sub-issue of #314) and eight findings/sub-issues (#35, #75, #81, #82, #85, #87, #91, #102). An earlier draft of the inventory said "26 epics"; that was a miscount of the `[Roadmap]` titles, not a pagination gap. A recount of all 44 baseline nodes and of the live items gives 35.

**Custom fields (all preserved).** Status; Horizon {Now, Next, Later, Long-term, Exploratory}; Work Type {Epic, Feature, Research, Bug, Infrastructure, Documentation}; Priority {P0, P1, P2, P3}; Commitment {Committed, Research-first, Exploratory}; Agent {Unassigned, Cursor Cloud, Claude Code, Codex, Manual}; Effort {XS, S, M, L, XL}; Roadmap Order (number); Start date, Target date (date). No iteration field. No item has Agent, Effort, dates or assignees set.

**Status options.**

| Baseline | Live |
|---|---|
| Idea, Research / Design, **Planned** (`cff9bdb9`), Ready for Agent, In Progress, Review, Ready to Merge, Done, Parked | Idea, Research / Design, **Ready** (`cff9bdb9`, the renamed Planned), Ready for Agent, In Progress, Review, Ready to Merge, Done, Parked, Inbox, Changes requested, Blocked, Backlog |

Renaming Planned to Ready kept the option ID, so the 23 epics that were Planned now read Ready, and the live option cannot tell them apart from items that are really Ready. The maintainer's pre-rename baseline export is the only record of which they were. It is kept outside the repository, and the reviewed migration plan (§13) carries those 23 assignments, which become Roadmap Stage = Planned with Status Backlog.

**Native workflows.** Baseline (API): Auto-add sub-issues **on**, Auto-close issue on, Item added to project **off**, Item closed on, Item reopened on, Pull request linked to issue on, Pull request merged on. "Auto-add to project", "Auto-archive items", "Code changes requested" and "Code review approved" were not returned by the API. As reported by the maintainer since then (not verifiable via the API): Auto-close issue **off**, Pull request linked to issue **off**, Item reopened = Issues only → Ready.

**Views (baseline = live).**

| # | View | Layout | Filter | Group / columns by | Sort |
|---|---|---|---|---|---|
| 1 | Roadmap | table | `work-type:Epic` | Horizon | Roadmap Order ↑ |
| 7 | Active Work | table | `status:"In Progress",Review,"Ready to Merge"` | Status | – |
| 8 | Work Board | board | `-status:Idea,Parked,Done -work-type:Epic` | columns: Status | – |
| 9 | Agent Queue | table | `status:"Ready for Agent"` | – | Priority ↑, Effort ↑ |
| 10 | Research Queue | table | `status:"Research / Design"` | – | – |
| 11 | Timeline | roadmap | none | – | – |

| Item | Existing | Proposed change | Needs approval |
|---|---|---|---|
| Status options | as above | add nothing more; legacy options kept until no item or view uses them (§13) | yes |
| Other custom fields | as above, all kept | add Roadmap Stage and Execution (§3) | yes (field creation) |
| Native workflows | as above | §5 | yes |
| Views | as above | §7 | yes |
| Membership | 44 items | backfill (§13) | yes |

## 2. Lifecycle Status (approved)

| Status | Meaning |
|---|---|
| Inbox | Added, not triaged. |
| Backlog | Triaged and accepted or planned, but not actionable yet (approved 2026-10-10). |
| Ready | Triaged and actionable; can start now. |
| In Progress | Being worked on, including draft PRs. |
| Review | Waiting for a human review. |
| Changes requested | A review asked for changes; waiting on the author. |
| Blocked | Cannot proceed. Set by a person; a native "Blocked by" dependency is shown separately (§9). |
| Done | The item itself is complete: issue closed, PR merged, or PR closed without merging. |

Legacy planning options (Idea, Research / Design, Ready for Agent, Ready to Merge, Parked) stay on the field until §13 has moved every item and no view references them. Their meaning moves to Roadmap Stage (§3) and Execution, never by an automatic lossy remap. Planned→Ready, Parked→Blocked and Ready to Merge→Review would each lose information: planned is not actionable, parked is a choice and not an impediment, and approval is not a lifecycle state (the native review decision shows it, and a merge is always a person's act).

`project-sync` treats any Status it has no key for, Backlog and the legacy options included, as `unknown:<name>`. Such a value matches no "allowed from" list, so a PR in Backlog is never moved by `project-sync` (covered by `tests/test_project3_migrate.py`). No change to `project-sync` was needed.

## 3. Roadmap Stage and Execution fields (approved; not created yet)

**Roadmap Stage**, single select {Idea, Research / Design, Planned, Parked}: the planning stage of roadmap items, previously carried by Status. It is separate from Commitment (how firmly PRKS commits: Committed, Research-first, Exploratory) and Horizon (when). These are three different questions. Today Stage and Commitment mostly line up (#472 is Idea + Research-first), but they evolve independently, and Parked has no other home.

**Execution**, single select {Human, Agent, Mixed}: who does the work, as a category. The existing **Agent** field (the specific executor: Cursor Cloud, Claude Code, Codex, Manual) stays. Execution is set by hand and never inferred: no current item has any Agent value, assignee or agent label, so the migration leaves it empty. Execution does not make an item ready. Agent Queue (§7) also requires a lifecycle Status, and a person checks for open prerequisites (§9).

## 4. Classification comes from repository metadata

No **new** classification fields. The existing Work Type and Priority Project fields are kept: they carry the roadmap planning data (the Roadmap view keys on `work-type:Epic`; Priority is set on epics) and are not copied onto other items. For all other items, views filter on the canonical repository metadata. Note there is no `priority:P0` label.

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
| Auto-add to project | On. Repository `Fooftilly/PRKS`, filter `is:issue,pr` (all issues and PRs, including dependency PRs). GitHub Free allows one auto-add workflow, which is enough. Supported auto-add qualifiers: `is` (open, closed, merged, draft, issue, pr), `label`, `reason`, `assignee`, `no` | Everything enters without manual adding. Dependency PRs are kept out of the ordinary views by their filters (§7), not by auto-add. |
| Item added to project | **When:** Issues only (Pull requests unchecked). **Set:** Status Inbox. Approved 2026-10-10 | Default entry state for issues. PR items get their first Status from `project-sync` instead (§10.1), so a delayed native run cannot reset a classified PR. |
| Item closed | Status: Done, for issues and PRs | The item itself is complete. A PR closed without merging is finished too. If a merged PR's closing keyword closes an issue, GitHub closes it and this rule moves it to Done; that is why slice PRs reference controller and roadmap issues with `Refs #N`, never a closing keyword. |
| Pull request merged | Status: Done | Applies to the PR item only. |
| Item reopened | **When:** Issues only, if the workflow offers the selector (checked against the screenshots). **Set:** Status Ready | Reopened work is actionable again. Whether an explicitly set state should be preserved is checked against the native workflow's behavior first. A reopened PR keeps Done: `project-sync` never moves an item out of Done, so a person resets a reopened PR's Status by hand. |
| Code changes requested | **Off.** `project-sync` owns this transition (§6) | The native rule does not check the current Status, so any changes-requested review, a bot's included, would move a PR out of a Blocked status a person set. `project-sync` applies it only from empty, In Progress or Review. |
| Code review approved | **Off** (provisional) | A bot approval would move Status, and approval is not authorization. |
| Auto-close issue | **Off** (provisional) | Moving a controller issue to Done must not close it. |
| Pull request linked to issue | **Off** (provisional) | One slice PR must not move its controller issue. |
| Auto-archive items | **Off** (§8; merged-PR archiving is only a later proposal) | Issues stay discoverable. |

### 5.1 Applying these settings (only after the rollout is approved)

The settings live under Project #3 → ⋯ → **Workflows**. Each built-in workflow has its own page with a **When** selector for the item type, a **Set** value, and an on/off toggle; auto-add and auto-archive use a filter text box instead. Nothing here is applied until the inventory has been reviewed and the rollout approved.

1. **Item added to project:** under **When**, select Issues and clear Pull requests. Set Status to Inbox. Save, and turn it on.
2. **Auto-add to project:** select the `Fooftilly/PRKS` repository. Use a filter that matches both issues and PRs, for example `is:issue,pr`, with no label condition, so dependency PRs are added too. Turn it on. Auto-add only acts on items created or updated afterwards; it does not add existing ones.
3. **One-time membership backfill:** generate a backfill plan with the read-only `plan-backfill` command of `scripts/project3_migrate.py` (§13), which compares Project #3's live items with the repository's open issues and PRs and its closed research issues for Research History (§7). Review that plan, then run the `backfill` stage with it, passing the plan's sha256 so only the reviewed file is executed. Added open issues get Status Inbox; added open PRs stay without Status until their next `project-sync` event or a person sets one. "Item closed" fires only on the close event, not on add, so the backfill sets **Done explicitly on every closed item it adds**, and its settle check keeps a late "Item added" Inbox from replacing that Done. A closed issue left off the board that is reopened later is added by auto-add on that update, after "Item reopened" had no item to move, so it arrives in Inbox and is triaged like a new issue. That is intended: an issue nobody tracked on the board gets a fresh triage instead of going straight to Ready.
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
| PR converted back to draft | PR | In Progress | `project-sync` | Review, Changes requested, and only if the latest conversion to draft is newer than both the latest changes-requested review and the current Status (so a delayed run never undoes a Status set after it) |
| Changes requested in a review | PR | Changes requested | `project-sync` | empty, In Progress, Review, and only while a changes-requested review is unanswered. Bot reviews count like any other review. |
| Change request dismissed, or withdrawn by the same reviewer's approval | PR | In Progress (draft) or Review | `project-sync` | Changes requested, only when no changes-requested review is unanswered and a change request was first answered (see below) after that Status was set. |
| Re-review explicitly requested | PR | Review | `project-sync` | Changes requested, on the same rule as the row above. |
| New commits pushed | PR | no change | none | |
| CI result of any kind | either | no change | none | |
| PR merged | PR | Done | native | |
| PR closed without merging | PR | Done | native | |
| Issue closed | issue | Done | native | |
| Issue reopened | issue | Ready | native | Done |
| Linked PR merged | its issue | no change, unless the PR's closing keyword closes the issue (then "Issue closed" applies) | none | |

**Reading review history.** Every changes-requested review is *answered* once, by the first later event among: a review request of the same reviewer, an approval by the same reviewer, the dismissal of that review, or a conversion to draft. An event at the same instant does not answer it. A review with no answer yet is *unanswered*. The rule is evaluated per reviewer, so requesting or approving one reviewer never answers another's review, and the opened and ready rows also cover a late `opened` run that adds the item after a review.

Only a review's *first* answer can release Changes requested, and only when it is newer than the current Status (the `updatedAt` of the item's Status value, so edits to other fields do not count). Later events answer nothing new: a repeat request or approval, or the dismissal of a review that a request already answered, never replays a release. So a Changes requested set by hand stays through those events, through a comment review, through a request or approval of someone who never requested changes, and through a delayed run for an older event. A new changes-requested review starts over and is released by its own first answer.

`project-sync` never writes an issue item, never moves an item out of Blocked or Done, and leaves any item whose current Status is not in its "allowed from" list unchanged, logging that it did so.

## 7. Views as filtered projections (Proposed)

Only documented Projects filter qualifiers are used (`is:`, `label:`, `-label:`, field names such as `status:`, `work-type:`, `execution:`, `roadmap-stage:`, `has:`/`no:`, `updated:`, `reason:`, `parent-issue:`). `execution:` and `roadmap-stage:` work once those fields exist. **There is no blocked-by filter qualifier**, so views cannot hide items with an open native prerequisite (§9).

| View | Layout | Filter | Group / columns | Sort | Columns |
|---|---|---|---|---|---|
| Roadmap (#1, preserved) | table | `work-type:Epic` | Horizon | Roadmap Order ↑ | Title, Status, Roadmap Stage, Sub-issues progress, Priority, Commitment, Roadmap Order |
| Active Work (#7) | table | `is:open -label:dependencies -work-type:Epic status:"In Progress",Review,"Changes requested",Blocked` | Status | Priority ↑ | Title, Status, Assignees, Execution, Agent, Linked pull requests, Parent issue, Labels |
| Work Board (#8) | board | `-label:dependencies -work-type:Epic -status:Inbox,Backlog,Idea,Parked` | columns: Status (hide Inbox and Backlog columns) | – | Title, Status, Linked pull requests, Parent issue, Execution, Labels |
| Triage (new, optional) | table | `is:open status:Inbox -label:dependencies` | – | Updated ↓ | Title, Labels, Parent issue, Created |
| Agent Queue (#9) | table | `is:open is:issue -label:dependencies execution:Agent,Mixed status:Ready,"In Progress"` | Status | Priority ↑, Effort ↑ | Title, Status, Execution, Agent, Effort, Parent issue, Labels, Linked pull requests |
| Research Queue (#10) | table | `is:open label:research -status:Done` | – | Updated ↓ | Title, Status, Assignees, Parent issue, Labels |
| Roadmap Research (new, optional) | table | `is:open work-type:Epic roadmap-stage:"Research / Design"` | – | Roadmap Order ↑ | Title, Status, Horizon, Commitment, Sub-issues progress |
| Research History (new) | table | `label:research is:closed` | – | Closed ↓ | Title, Closed, Labels, Linked pull requests |
| Timeline (#11) | roadmap (Start date → Target date) | `-label:dependencies work-type:Epic` | Horizon | Roadmap Order ↑ | Title, Status, Roadmap Stage, Sub-issues progress |
| Maintenance (new) | table | `label:dependencies` | Status | Updated ↓ | Title, Status, Repository, Labels, Reviewers, Linked pull requests |

- **Work Board** keeps Done visible. If the Done column grows too long, add a date bound in the UI and check that the preview shows the expected items, because GitHub documents the `updated:` comparison operators ambiguously. Inbox (hundreds of backfilled findings) and Backlog stay out of it; Inbox has its own Triage view. In a board view, a hidden column is a view setting, not a filter.
- **Research:** Research Queue holds actual research tasks (the `research` label). Roadmap epics that are still being researched or designed are in Roadmap Research or the Roadmap view, not mixed into Research Queue. Research History holds closed research, including #181, #234 and #246 once they are added (§13).
- **Dependency PRs** (`dependencies` label: Dependabot, plus the human-authored #335) appear only in Maintenance. Maintenance does not filter on Status, because Dependabot PRs never get a `project-sync` Status (§10).
- **Agent Queue** cannot exclude items with an open Blocked-by prerequisite, because no filter qualifier exists. Check the issue's Relationships panel before starting work.
- **Timeline** stays sparse until Start date and Target date are filled in (no item has them today).

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
- **Fork PRs (known limitation, kept 2026-10-10):** reviews on fork PRs are not relayed. A fork PR's `pull_request_review` run gets no PR number in `workflow_run`, and the job condition requires the head repository to be this one. A relay that took the number from fork-controlled text, such as a `run-name`, would add a privileged trust path that this design does not take. If fork contributions become common, a separately reviewed and strictly validated relay can be added. The table lists every PR transition for a fork PR.

  | Transition (§6) | Owner | Fork PR |
  |---|---|---|
  | PR added to the board | native auto-add | Supported. Auto-add matches PRs in `Fooftilly/PRKS` whatever their head repository. |
  | Draft PR opened, PR opened ready for review, PR marked ready for review, PR converted back to draft, re-review explicitly requested | `project-sync` via `pull_request_target` | Supported. `pull_request_target` runs in the base repository's context for fork PRs, so the run has the token, and it reads only the PR number from the event. The opened and ready rows still set Changes requested when the API shows an unanswered review. |
  | Changes requested in a review | `project-sync` via the relay | **Not supported by `project-sync`.** Until the apply cutover, the native "Code changes requested" rule stays as it is (§5.1 step 5). That rule is a Projects workflow run by GitHub, not an Actions workflow, so the fork limitation does not obviously apply to it. However, GitHub's documentation for the built-in workflows neither describes this rule nor says whether it covers fork PRs, so this is **unverified**. After the cutover the native rule is off, and a person sets Changes requested on a fork PR. |
  | Change request dismissed, or withdrawn by an approval | `project-sync` via the relay | **Not supported.** No native rule covers it. A person moves the PR out of Changes requested, or a re-request of the reviewer moves it to Review through `pull_request_target`. |
  | PR merged, PR closed without merging | native | Supported. |

  Before claiming the native rule covers fork PRs, check it on the first fork PR that gets a changes-requested review while the rule is still on, and record the result here.
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
- **A delayed re-review request or draft conversion.** A `review_requested` run applies only when no changes-requested review is unanswered and a change request was first answered after the current Status was set (§6), and a `converted_to_draft` run only when the latest conversion is newer than the latest changes-requested review. Both fail closed when their event is not recorded or the times are equal. If a changes-requested review lands during the write, Changes requested is restored. `opened` and `ready_for_review` never move an item out of Changes requested, so they need no ordering check.
- **A delayed review run.** It re-derives the state from the review history: it sets Changes requested only while a changes-requested review is unanswered, and otherwise moves only an item in Changes requested on to In Progress or Review, and only when a change request was first answered after that Status was set (§6). If a withdrawal lands between a run's read and its write of Changes requested (a review run, or an `opened` or `ready_for_review` run that sets Changes requested), the run's re-read after writing finds nothing unanswered and moves the item on itself, because the withdrawal's own run would find the withdrawal older than the new Status. So whichever of a review run, a dismissal run and a re-review run executes first, the PR ends in the state the review history implies.
- **Two `project-sync` runs for the same PR.** One concurrency group per PR, without cancellation and with `queue: max` (up to 100 pending runs, so a newer event never replaces a pending one), serializes them. Each re-reads before writing, so the later run sees the earlier result.
- **A person changes Status at the same time.** A Status outside the "allowed from" list is left alone, and a person's write after the `project-sync` write stands. The Projects API offers no conditional write, so in the short window between the `project-sync` read and its write, the later write wins. The audit record keeps the before and after values for a manual fix.

**Testing.** The decision logic is a pure function of PR state, item state and event. Unit tests run it against a fake project that replays each ordering above: native add before and after, a delayed native "Item added", a duplicate concurrent add, a merge during the write, a PR already merged or closed when the run reads it, a merge or close between the first read and the item add (with the native Done rule delivered before or after), delayed review-request, draft-conversion and review runs in both orders, a dismissed or withdrawn change request, a hand-set Changes requested with only comment reviews, a dismissal or approval between a run's read and its Changes requested write, several reviewers, requesting a different reviewer, a repeat re-request, approval or dismissal after the change request was already answered, and Blocked or Done set by hand. The live check is a dry-run on the next ordinary PR after the configuration is approved. It does not use test PRs or arbitrary items.

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

## 13. One-time migration and backfill (Proposed; dry-run only until approved)

Tool: `scripts/project3_migrate.py`, tested with synthetic fixtures in `tests/test_project3_migrate.py`. GitHub is the source of truth for issues, PRs and Project membership, so **no inventory, plan or report is checked in**. Plans are local JSON files that the maintainer reviews and keeps outside the repository; the tool refuses any plan, report or checkpoint path inside the repository.

**Two inputs:**
- **Backfill plan:** generated read-only from live data by `plan-backfill`. It compares the current PRKS issues and PRs with the current Project #3 membership. Open issues get Status Inbox; open PRs get no Status (Maintenance view; `project-sync` owns PR Status); closed issues labelled `research` get Done, because "Item closed" does not fire on add. Counts, labels, states and the list of closed research issues are read at run time, never hardcoded.
- **Migrate-existing plan:** the maintainer's reviewed decisions for items already on the board, written by hand or from the baseline export. Historic and judgement calls live here, because live data cannot reproduce them, for example the 23 pre-rename Planned epics.

Both plans use the same schema:

```json
{
 "scope": {"owner": "Fooftilly", "repository": "Fooftilly/PRKS", "project_number": 3, "project_id": "PVT_kwHOAsc2_s4BkAo3"},
 "stage": "migrate-existing",
 "items": [
  {"number": 38, "type": "Issue", "github_state": "OPEN",
   "expected_before": {"Status": "Ready", "Roadmap Stage": null},
   "set": {"Status": "Backlog", "Roadmap Stage": "Planned"},
   "approved": true, "reason": "Planned in the baseline export"}
 ]
}
```

`expected_before` is the value the reviewer saw (`null` = empty). `set` may name only Status and Roadmap Stage. An item with `approved: false` is held (`approved` must be JSON `true` or `false`; a string such as `"false"` is refused), and a `set` of `{}` writes nothing. A backfill plan uses `"stage": "backfill"` and `state` in place of `github_state`. Every other row, and every backfill row (each one can add its item, even with an empty `set`), is checked before anything is read or written: it must give `github_state` (or `state`) as OPEN, CLOSED or MERGED, matched exactly (a PR reviewed as CLOSED that was merged since is drift), and an explicit `expected_before` entry for every field in `set`. Values in `set` must be option names (strings), and `expected_before` values strings or `null`. A missing entry is refused rather than read as empty, so a hand-written row cannot switch off the drift checks.

**Guarantees enforced by the tool:**
- Dry-run unless `--apply` is given. A dry-run, and `plan-backfill`, only send queries.
- `--apply` runs exactly the reviewed file. It requires `--plan-sha256`, which must match the plan's hash as printed by the dry-run and `plan-backfill`, and it never regenerates a plan. `plan-backfill` never overwrites an existing plan file.
- The only mutations it can send are `addProjectV2ItemById` and `updateProjectV2ItemFieldValue`. Nothing is ever deleted, cleared, archived, closed, reopened, approved or merged. It never reads or writes repository settings, secrets or `project-sync` variables.
- Scope is checked against owner `Fooftilly`, project 3, node `PVT_kwHOAsc2_s4BkAo3` and content from `Fooftilly/PRKS`.
- Field and option IDs are resolved by name. `--apply` refuses to run until Status has the Backlog option and Roadmap Stage and Execution exist as single-select fields with every option in §3.
- Only listed fields are written (Status, Roadmap Stage; never Execution), and only when the current value equals the plan's expected-before value. Any other value, or a changed issue state, is reported as drift and skipped.
- The item is re-read immediately before each field write, not only in the snapshot taken when the run starts. A Status, Roadmap Stage or issue state changed while the run is in progress is reported as drift and never overwritten.
- In the backfill, Inbox also counts as an expected Status. The native "Item added" workflow sets Inbox on any item that auto-add or the tool adds, so whether it runs before or after the add, the item still gets its planned Status.
- After the writes, the tool waits `--settle-seconds` (default 30; a negative or non-finite value is refused before anything runs) and re-reads every item it added that has not verified yet, including one a rerun leaves unchanged after an earlier run stopped before verifying, and any backfill item already on the project at its target (for example auto-added and closed before the run). If a late "Item added" Inbox replaced the Status it wrote (for example Done on a closed research issue), it writes the Status again once. That repair is guarded like every other write: if the item was removed or archived, or its issue or PR changed state during the wait, nothing is written. The item is re-read after the repair as well, and a change in that moment is reported the same way. That case, and any other value, is reported as `verify-failed` and left alone.
- Items on the board that a migrate-existing plan does not list are reported as `not-in-plan` and never touched.
- PRs are never set to Inbox.
- Plans, reports and checkpoints must be under the home or temp directory and outside this repository. By default reports and the checkpoint go to `prks-project3-migration` in the system temp directory. The plan, the checkpoint and the two report files must be different paths, compared after following symlinks, and none may sit inside another; a report file that is a symlink must point inside the same roots; a run where one would overwrite another is refused. The report and checkpoint directories are created before anything else runs, so a path that cannot hold them fails first.
- A checkpoint records the items the tool added and completed. It is saved through a freshly created temp file and an atomic rename, so a leftover `.tmp` path or symlink is never written through. In the backfill, an item is recorded as added before its add is sent, and so is an item native auto-add put there first, so a lost add response or an early auto-add still gets the settle check and the late-Inbox repair. An item the tool added counts as completed only after it verifies; if verification fails or stops, a rerun plans it again from the live state with every guard. A rerun skips completed items, so a value a person changed afterwards is never rewritten. The only exception is a late native Inbox on an item the tool added, which a rerun repairs. A checkpoint is bound to the sha256 of the plan that wrote it: a checkpoint from the other stage, from a different plan file, or one that does not name its plan is refused, in dry-run and `--apply` alike. To run a revised plan, remove the old checkpoint or pass a new `--checkpoint`; the run then starts from the live state.
- A failed call (including a truncated or non-JSON response), or a checkpoint that cannot be saved after a write, stops the run. The report and checkpoint are still written, the remaining items are marked `not-run`, and the command exits 1. A rerun continues from the live state.
- A write cannot be atomic with the check before it, so each item is re-read right after every write as well. If its issue or PR changed state (for example it closed and "Item closed" set Done) or it was archived or removed in that moment, the item is reported as `drift`, which fails the apply, and is not marked completed. A rerun leaves it alone, so a person checks it by hand. The read after the last write must also still show every planned field at its target; a field a person changed in the meantime is reported the same way (a late native Inbox on an added item is left to the settle check).
- An `--apply` that skipped any item as `drift` did not apply the whole reviewed plan, so its report has `ok: false` and the command exits 1, even though the other items were written. `held`, `unchanged`, `checkpointed` and `not-in-plan` are intended and do not fail a run. A dry-run reports drift without failing.

**Reviewed decisions for the existing items (2026-10-10):**

| Group | Status → | Roadmap Stage → |
|---|---|---|
| The 23 epics that were Planned before the rename (from the baseline export) | Backlog | Planned |
| Research / Design epics | Backlog | Research / Design |
| Idea items | Inbox | Idea |
| Items with no Status, except #35 | Inbox | – |
| #35 (`accepted`, not necessarily actionable) | Backlog | – |
| #52 (open native blockers #45 and #46) | Backlog, not Blocked (§9) | Research / Design |
| #179 | keep In Progress | Planned |
| #39 | **held**: nothing is written | – |
| Items already Done | keep Done | – |

#39 was closed by `cursor[bot]` on 2026-10-01 with no closing commit or comment and all 9 scope boxes unchecked, and the `Fooftilly` account reopened it on 2026-10-10. It stays held, and no current Status is assumed, because the native "Item reopened" rule may have changed it. The maintainer chooses its Status and Roadmap Stage before approving it.

**Commands** (run by the maintainer only after approval, with a classic token that has the `project` scope, exported as `PROJECT3_MIGRATION_TOKEN`; never committed or printed). Keep the plan files somewhere outside the repository, such as `~/prks-project3/`:

```bash
# Existing items: review the plan, dry-run it, note the printed sha256, then apply that exact file.
python scripts/project3_migrate.py migrate-existing --plan ~/prks-project3/migrate-existing.json
python scripts/project3_migrate.py migrate-existing --plan ~/prks-project3/migrate-existing.json --apply --plan-sha256 <sha256>

# Backfill: generate from live data, review, dry-run, then apply that exact file.
python scripts/project3_migrate.py plan-backfill --out ~/prks-project3/backfill.json
python scripts/project3_migrate.py backfill --plan ~/prks-project3/backfill.json
python scripts/project3_migrate.py backfill --plan ~/prks-project3/backfill.json --apply --plan-sha256 <sha256>
```

Order:
1. Create the fields.
2. Run `migrate-existing`, dry-run then apply.
3. Configure the native workflows (§5.1). The backfill works whether "Item added" is enabled before or after it, because of the Inbox rule and the settle check above.
4. Generate the backfill plan, review it, then dry-run and apply it.
5. Change the views (§7).
6. Retire legacy options only after a separate approval.
