# PRKS Discussions: proposal and architecture-question workflow

PRKS uses GitHub **Discussions for deliberation**, Issues / GitHub Project #3 for approved work, and repository documentation for accepted technical decisions. A discussion is not an implementation ticket.

## One-time GitHub setup

Discussions is already enabled for `Fooftilly/PRKS`. A maintainer should open [PRKS Discussions](https://github.com/Fooftilly/PRKS/discussions), use the edit/pencil icon beside **Categories**, and create or verify:

| Category | Format | Purpose | Matching form filename |
| --- | --- | --- | --- |
| Design Proposals | Open-ended discussion | Compare alternatives for proposed changes | `design-proposals.yml` |
| Architecture Questions | Question and answer | Resolve one bounded technical question | `architecture-questions.yml` |
| Announcements | Announcement | Publish important **already approved** directions and release/development notes | no custom form needed |

The YAML file names must match the **actual category slugs**. Check the slugs in the GitHub interface; rename a file if GitHub uses a different slug. Avoid duplicate categories if equivalent default categories already serve this purpose.

The structured forms are defined under `.github/DISCUSSION_TEMPLATE/`. GitHub displays them after they have been merged into the default branch and matching categories exist.

## Lifecycle and source of truth

1. **Open a Discussion** for a design question, trade-off, or proposal. Link existing roadmap issues, PRs, and relevant source documents. Use synthetic examples and omit personal research material.
2. **Investigate alternatives.** Agents may research, prototype in isolated tests when explicitly commissioned, and propose evidence. Do not treat an agent response, popularity, or a marked Q&A answer as maintainer approval.
3. **Record an explicit decision** from the maintainer, including chosen option, rejected alternatives, constraints, and follow-ups. Mark a Q&A answer as helpful when appropriate, but the marked answer does not replace explicit implementation approval.
4. **Make the decision durable.** Update the relevant existing roadmap issue and canonical design/architecture documentation (or a focused ADR when warranted), with a backlink to the discussion. Prefer one authoritative document over duplicating contracts across several places.
5. **Schedule work.** Link or create a bounded Engineering / implementation issue only once implementation is authorized. Use GitHub native sub-issues for slices, and native `Blocked by` dependencies for true prerequisites. Link a draft PR to its issue. Never auto-merge.
6. **Close the loop.** The implementing PR should reference the approved issue and the discussion/decision record. Future agents should check those sources instead of reopening settled alternatives.

## What belongs where

- **Discussion:** "Should the server use a job queue for ingest?" / "What is the canonical annotation identity?" — uncertainty or comparison is the deliverable.
- **Research/Evaluation issue:** A maintainer has approved a bounded investigation with evidence and a decision as its deliverable. Reference the discussion when useful.
- **Implementation issue:** Decision accepted; scope and acceptance criteria are actionable.
- **Bug / UX / audit finding:** Reproducible defect or unapproved finding, according to existing issue templates and `AGENTS.md`. Do not move known bugs into Discussions just because the fix is complex.
- **Roadmap issue:** Long-term product direction, priorities, and parent/sub-issue progress.
- **Canonical docs:** Long-lived approved contracts; follow `AGENTS.md` and scoped instructions.

## Agent and daily-triage rules

- Read accepted decisions and relevant issue/PR state before proposing work. A discussion thread by itself is not permission to implement, change roadmap priorities, or launch agents.
- Check for duplicate discussions, active research, assigned issues, and overlapping PRs before proposing an investigation; a read-only search is **not** a reservation.
- **Serialize investigation dispatch:** only the maintainer or a single designated coordinator may start research agents for a shared topic. Other triagers submit proposals, not independent assignments. Before starting work, the coordinator rechecks current ownership/status and records the bounded scope, responsible agent, and active investigation link in its existing tracking issue or Project #3; agents must verify that record before proceeding. If another investigation already owns the scope, contribute evidence there or request explicit coordination instead of launching a duplicate.
- **Future automated dispatch must acquire an exclusive claim before launching an agent.** Use an atomic/serialized reservation keyed by canonical issue or investigation scope (for example, a single GitHub Actions coordinator with concurrency control and an authoritative claim), then revalidate it on retry and release it on completion or cancellation. Project labels/comments alone, or two agents independently checking for duplicates, are not atomic locks. Until a reliable dispatcher exists, do not claim unattended, conflict-free automatic assignment.
- Treat comments as opinions or hypotheses unless backed by current code/tests or approved by the maintainer.
- For issue dependencies, distinguish **mandatory prerequisites** from "related", "overlapping code", and "prefer to do first". Never fabricate a native GitHub dependency. The triage task may suggest exact pairs while its connected GitHub tools lack a native dependency-write action.
- Preserve PRKS governance: agents may open draft PRs, but only the maintainer expressly authorizes merging.
- Do not post sensitive security findings in public Discussions; follow `SECURITY.md` and GitHub private vulnerability reporting.

## Maintaining the forms

These forms intentionally collect alternatives, code/roadmap references, and invariants without demanding large copied context. Once live, gather feedback on their usefulness, remove unnecessary questions, and avoid creating discussion threads for every tiny PR or implementation detail.
