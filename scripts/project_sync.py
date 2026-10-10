#!/usr/bin/env python3
"""Keep a pull request's Project #3 Status in step with its review lifecycle.

Runs from ``.github/workflows/project-sync.yml`` on four ``pull_request_target``
actions, plus ``review_changed`` relayed through ``workflow_run`` from a
submitted or dismissed review, and covers only the PR rows owned by
``project-sync`` in ``docs/agent-workflows/project-3.md`` section 6:

* ``opened``             -> In Progress (draft) or Review, from empty or Inbox
* ``ready_for_review``   -> Review, from empty, Inbox, Ready or In Progress
* ``converted_to_draft`` -> In Progress, from Review or Changes requested, when
  the conversion is newer than the current Status
* ``review_requested``   -> Review, from Changes requested, when no review is
  unanswered and a change request was first answered after that Status was set
* ``review_changed``     -> Changes requested, from empty, In Progress or Review,
  while a review is unanswered; otherwise In Progress (draft) or Review, from
  Changes requested, on the same release rule as ``review_requested``

Review history is read one way throughout (see ``answers``). Each
changes-requested review is answered once, by the first later event among: a
request of the same reviewer, an approval by the same reviewer, the dismissal
of that review, or a conversion to draft. A review with no answer is
*unanswered*. Only a first answer newer than the current Status releases
Changes requested, so a repeat request or approval, a dismissal of a review
already answered, a request of another reviewer, a comment review, or a
delayed run never clears a Changes requested set later by hand.
``converted_to_draft`` applies only when the conversion is newer than the
latest changes-requested review. If a review is unanswered when
``opened`` or ``ready_for_review`` runs, the run sets Changes requested
instead of In Progress or Review.

Any other current Status, Blocked and Done included, is left alone. The event
only says which rule to consider: the PR's draft and open state are re-read
from the API, so a stale or repeated event changes nothing. If the PR has no
Project #3 item yet, one is added with ``addProjectV2ItemById``, which returns
the existing item when native auto-add got there first. Only the PR itself is
ever added or written; a linked issue is never touched.

Dry-run is the default. Writes happen only when ``PROJECT_SYNC_MODE`` is
exactly ``apply``. Without ``PROJECT_SYNC_TOKEN`` the run reports that it is
not configured and succeeds without calling the API. The script never merges,
approves, or reads CI results. Standard library only.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

GRAPHQL_URL = "https://api.github.com/graphql"
CONFIG_PATH = Path(__file__).resolve().parents[1] / ".github" / "project-sync.json"
STATUS_KEYS = ("inbox", "ready", "in_progress", "review", "changes_requested", "blocked", "done")
OPEN = "OPEN"

# action -> (target when the PR is a draft, target when it is not, allowed-from)
# A target of None means the event does not apply to the PR's current state.
# None in allowed-from is an item with no Status yet.
RULES: dict[str, tuple[Optional[str], Optional[str], frozenset[Optional[str]]]] = {
    "opened": ("in_progress", "review", frozenset({None, "inbox"})),
    "ready_for_review": (None, "review", frozenset({None, "inbox", "ready", "in_progress"})),
    "converted_to_draft": ("in_progress", None, frozenset({"review", "changes_requested"})),
    "review_requested": (None, "review", frozenset({"changes_requested"})),
    # While a review is unanswered; see decide() for the release direction.
    "review_changed": (
        "changes_requested",
        "changes_requested",
        frozenset({None, "in_progress", "review"}),
    ),
}

_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class Refused(Exception):
    """The event is outside what project-sync may act on."""


class ConfigError(Exception):
    """Project #3 or the config file does not match the expected names."""


class ApiError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Config:
    owner: str
    project_number: int
    repository: str
    status_field: str
    statuses: Mapping[str, str]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        raw = json.loads(path.read_text(encoding="utf-8"))
        statuses = raw.get("statuses", {})
        missing = [key for key in STATUS_KEYS if not isinstance(statuses.get(key), str)]
        if missing:
            raise ConfigError(f"{path.name} has no status name for: {', '.join(missing)}")
        if not _REPO_RE.match(str(raw.get("repository", ""))):
            raise ConfigError(f"{path.name} has an invalid repository")
        return cls(
            owner=str(raw["owner"]),
            project_number=int(raw["project_number"]),
            repository=str(raw["repository"]),
            status_field=str(raw["status_field"]),
            statuses={key: statuses[key] for key in STATUS_KEYS},
        )

    def key_of(self, name: Optional[str]) -> Optional[str]:
        """Map a Status option name to its key; unknown names never match a rule."""
        if name is None:
            return None
        for key, value in self.statuses.items():
            if value == name:
                return key
        return f"unknown:{name}"


@dataclass(frozen=True)
class Project:
    id: str
    field_id: str
    options: Mapping[str, str]  # option name -> option id


@dataclass(frozen=True)
class Item:
    id: str
    status: Optional[str]  # option name
    archived: bool = False
    # ISO 8601 time the Status was last set, or None when unknown.
    status_updated_at: Optional[str] = None


@dataclass(frozen=True)
class PullRequest:
    id: str
    state: str
    is_draft: bool
    item: Optional[Item]
    # ISO 8601 times of the conversions to draft.
    draft_conversions: tuple[str, ...] = ()
    # (reviewer, time) of each review request, and of each reviewer whose
    # latest approving or change-requesting review requested changes
    # (dismissed reviews do not count).
    review_requests: tuple[tuple[str, str], ...] = ()
    changes_requested: tuple[tuple[str, str], ...] = ()
    # The review history: (reviewer, time) of every changes-requested review
    # (dismissed ones included) and of every approval, and (reviewer, time of
    # the dismissed review, time of the dismissal) of each dismissed
    # changes-requested review.
    change_reviews: tuple[tuple[str, str], ...] = ()
    approvals: tuple[tuple[str, str], ...] = ()
    dismissals: tuple[tuple[str, Optional[str], str], ...] = ()

    @property
    def last_converted_to_draft_at(self) -> Optional[str]:
        return _latest(self.draft_conversions)

    @property
    def last_review_requested_at(self) -> Optional[str]:
        return _latest(at for _, at in self.review_requests)

    @property
    def last_changes_requested_at(self) -> Optional[str]:
        return _latest(at for _, at in self.changes_requested)


@dataclass(frozen=True)
class Event:
    action: str
    number: int
    repository: str


class ProjectApi(Protocol):
    def load_project(self, config: Config) -> Project: ...

    def load_pr(self, config: Config, number: int, project_id: str) -> PullRequest: ...

    def add_item(self, project_id: str, content_id: str) -> None: ...

    def set_status(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None: ...


@dataclass
class Audit:
    pr: int
    action: str
    mode: str
    outcome: str = ""
    reason: str = ""
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    item_added: bool = False
    steps: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "action": self.action,
            "mode": self.mode,
            "outcome": self.outcome,
            "reason": self.reason,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "item_added": self.item_added,
            "steps": self.steps,
        }

    def summary_markdown(self) -> str:
        before = self.status_before or "(none)"
        after = self.status_after or "(none)"
        return (
            f"### Project sync: PR #{self.pr} `{self.action}` ({self.mode})\n\n"
            f"- Outcome: **{self.outcome}**\n"
            f"- Status: {before} → {after}\n"
            f"- Reason: {self.reason}\n"
        )


def _time(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _latest(values: Any) -> Optional[str]:
    times = [value for value in values if value]
    return max(times, key=lambda value: _time(value) or datetime.min) if times else None


def _after(later: Optional[str], earlier: str) -> bool:
    later_time, earlier_time = _time(later), _time(earlier)
    return later_time is not None and earlier_time is not None and later_time > earlier_time


# How review history is read: each changes-requested review is answered once,
# by the first of these events after it: a request of the same reviewer, an
# approval by the same reviewer, the dismissal of that review, or a conversion
# to draft. That first answer is the only event that can release Changes
# requested, and only when it is newer than the current Status. Later events
# (a repeat request or approval, or a dismissal of a review already answered)
# answer nothing new, so they never replay the release. Equal times answer
# nothing.


@dataclass(frozen=True)
class Answer:
    reviewer: str
    requested_at: str
    answered_at: str
    by: str


def _first_answer(pr: PullRequest, reviewer: str, at: str) -> Optional[tuple[str, str]]:
    """The (time, kind) of the first event that answers a change request."""
    candidates = [(when, "re-request") for who, when in pr.review_requests if who == reviewer and _after(when, at)]
    candidates += [(when, "approval") for who, when in pr.approvals if who == reviewer and _after(when, at)]
    candidates += [(when, "dismissal") for who, review, when in pr.dismissals if who == reviewer and review == at]
    candidates += [(when, "conversion to draft") for when in pr.draft_conversions if _after(when, at)]
    return min(candidates, key=lambda answer: _time(answer[0]) or datetime.min) if candidates else None


def answers(pr: PullRequest) -> list[Answer]:
    """The first answer of every change request in the history that has one."""
    requests = dict.fromkeys(
        [*pr.change_reviews, *pr.changes_requested, *((who, review) for who, review, _ in pr.dismissals if review)]
    )
    found = []
    for reviewer, at in requests:
        first = _first_answer(pr, reviewer, at)
        if first is not None:
            found.append(Answer(reviewer, at, *first))
    return found


def unanswered_changes(pr: PullRequest) -> list[str]:
    """Reviewers whose latest changes-requested review has no answer yet."""
    return sorted(reviewer for reviewer, at in pr.changes_requested if _first_answer(pr, reviewer, at) is None)


def releases(pr: PullRequest) -> list[Answer]:
    """First answers newer than the current Status: the only events that may
    release Changes requested. With no recorded Status time, every answer
    counts."""
    since = pr.item.status_updated_at if pr.item else None
    return [answer for answer in answers(pr) if since is None or _after(answer.answered_at, since)]


def _describe(found: list[Answer]) -> str:
    return "; ".join(
        sorted({f"{answer.reviewer}'s change request was answered by {_ARTICLES[answer.by]} {answer.by}" for answer in found})
    )


_ARTICLES = {"re-request": "a", "approval": "an", "dismissal": "a", "conversion to draft": "a"}


# Actions whose run must not hide a changes-requested review.
ORDERED_ACTIONS = frozenset({"review_requested", "converted_to_draft"})


def event_is_current(action: str, pr: PullRequest) -> tuple[bool, str]:
    """Whether the review history still supports the event.

    ``review_requested`` applies only when every changes-requested review is
    answered, so requesting another reviewer or a stale run never hides one.
    ``converted_to_draft`` applies only when the latest conversion is newer
    than the latest changes-requested review. Each fails closed when the event
    it relies on is not recorded. ``opened``, ``ready_for_review`` and
    ``review_changed`` are not gated here (see ``decide``).
    """
    if action == "review_requested":
        if pr.last_review_requested_at is None:
            return False, "no review request is recorded on the PR"
        pending = unanswered_changes(pr)
        if pending:
            return False, f"changes requested by {', '.join(pending)} are not answered by a newer re-request"
        return True, ""
    if action == "converted_to_draft":
        converted = pr.last_converted_to_draft_at
        if converted is None:
            return False, "no conversion to draft is recorded on the PR"
        changes = pr.last_changes_requested_at
        if changes is not None and not _after(converted, changes):
            return False, "stale event: changes were requested after the latest conversion to draft"
        return True, ""
    return True, ""


def decide(config: Config, action: str, pr: PullRequest) -> tuple[Optional[str], str]:
    """Return the Status key to set, or None with the reason nothing applies."""
    if action == "review_changed":
        return _decide_review(config, pr)
    draft_target, ready_target, allowed = RULES[action]
    target = draft_target if pr.is_draft else ready_target
    if target is None:
        state = "a draft" if pr.is_draft else "ready for review"
        return None, f"stale event: the PR is now {state}"
    current_event, why = event_is_current(action, pr)
    if not current_event:
        return None, why
    outstanding = action in ("opened", "ready_for_review") and bool(unanswered_changes(pr))
    if outstanding:
        # A changes-requested review that no re-review request or conversion
        # to draft has answered still stands, for example when this run is
        # late and adds the item after the review.
        target = "changes_requested"
    current = config.key_of(pr.item.status if pr.item else None)
    if current == target:
        return target, "already set"
    if current not in allowed:
        return None, f"current Status {pr.item.status if pr.item else None!r} is not one `{action}` may change"
    if action == "converted_to_draft" and pr.item is not None and pr.item.status_updated_at:
        # A delayed run must not undo a Status set after the conversion.
        if not _after(pr.last_converted_to_draft_at, pr.item.status_updated_at):
            return None, "stale event: the current Status was set after the latest conversion to draft"
    if action == "review_requested":
        # Only the first answer of a change request, newer than the Status,
        # releases it (see ``answers``). A request of anyone else, a repeat
        # request, or one older than the Status leaves it alone.
        released = releases(pr)
        if not released:
            return None, "no change request was first answered after the current Status was set"
        return target, f"{_describe(released)}, so `{action}` moves the PR to {config.statuses[target]}"
    if outstanding:
        return target, f"a changes-requested review is still outstanding, so `{action}` sets {config.statuses[target]}"
    return target, f"`{action}` moves the PR to {config.statuses[target]}"


def _decide_review(config: Config, pr: PullRequest) -> tuple[Optional[str], str]:
    """A review was submitted or dismissed: re-derive the state from the history.

    While a changes-requested review is unanswered the PR moves to Changes
    requested. Once none is, an item in Changes requested moves on, to In
    Progress or Review, only when a change request was first answered after
    its Status was set (see ``answers``). A Changes requested set by hand
    stays through comment reviews, unrelated approvals and repeat events.
    """
    current = config.key_of(_status_of(pr))
    pending = unanswered_changes(pr)
    if pending:
        target = "changes_requested"
        allowed = RULES["review_changed"][2]
        reason = f"changes requested by {', '.join(pending)} are unanswered"
    else:
        target = "in_progress" if pr.is_draft else "review"
        allowed = frozenset({"changes_requested"})
        reason = "no changes-requested review is unanswered"
        if current != "changes_requested":
            return None, reason
        released = releases(pr)
        if not released:
            return None, f"{reason}, but no change request was first answered after Changes requested was set"
        reason = _describe(released)
    if current == target:
        return target, "already set"
    if current not in allowed:
        return None, f"current Status {_status_of(pr)!r} is not one `review_changed` may change"
    return target, f"{reason}, so the PR moves to {config.statuses[target]}"


def _skip_reason(pr: PullRequest) -> Optional[str]:
    if pr.state != OPEN:
        return f"the PR is {pr.state.lower()}"
    if pr.item is not None and pr.item.archived:
        return "the PR's item is archived"
    return None


def _status_of(pr: PullRequest) -> Optional[str]:
    return pr.item.status if pr.item else None


def validate(event: Event, config: Config) -> None:
    if event.repository != config.repository:
        raise Refused(f"only pull requests in {config.repository} are synced")
    if event.action not in RULES:
        raise Refused(f"action {event.action!r} is not handled")
    if event.number < 1:
        raise Refused("invalid pull request number")


def run(event: Event, config: Config, api: ProjectApi, apply: bool) -> Audit:
    audit = Audit(pr=event.number, action=event.action, mode="apply" if apply else "dry-run")
    validate(event, config)
    project = api.load_project(config)
    missing = [name for name in config.statuses.values() if name not in project.options]
    if missing:
        raise ConfigError(f"Project Status has no option named: {', '.join(missing)}")

    pr = api.load_pr(config, event.number, project.id)
    audit.status_before = audit.status_after = _status_of(pr)
    skip = _skip_reason(pr)
    if skip:
        return _finish(audit, "skipped", skip)
    target, reason = decide(config, event.action, pr)
    if target is None:
        return _finish(audit, "skipped", reason)

    if pr.item is None:
        if not apply:
            return _finish(audit, "dry-run", f"would add the PR to the project; {reason}", config.statuses[target])
        api.add_item(project.id, pr.id)
        audit.item_added = True
        audit.steps.append("added item")
        pr = api.load_pr(config, event.number, project.id)
        if pr.item is None:
            return _finish(audit, "failed", "the added item is not visible")
        audit.status_after = _status_of(pr)
        if pr.state != OPEN and pr.item.status is None:
            # Closed or merged before the item existed, so the native Done
            # rule had nothing to update. Only an item with no Status is set.
            done = config.statuses["done"]
            api.set_status(project.id, pr.item.id, project.field_id, project.options[done])
            audit.steps.append(f"set {done}")
            return _finish(audit, "corrected", f"the PR was {pr.state.lower()} before its item was added", done)
        skip = _skip_reason(pr)
        if skip:
            return _finish(audit, "skipped", skip)
        target, reason = decide(config, event.action, pr)
        if target is None:
            return _finish(audit, "skipped", reason)

    assert pr.item is not None
    target_name = config.statuses[target]
    if pr.item.status == target_name:
        return _finish(audit, "unchanged", "already set", target_name)
    if not apply:
        return _finish(audit, "dry-run", f"would set {target_name}; {reason}", target_name)

    api.set_status(project.id, pr.item.id, project.field_id, project.options[target_name])
    audit.steps.append(f"set {target_name}")
    return _verify(audit, config, api, project, event, target_name, reason)


def _verify(
    audit: Audit, config: Config, api: ProjectApi, project: Project, event: Event, target_name: str, reason: str
) -> Audit:
    """Re-read after writing; repair a state change that landed during the write.

    A merge or close sets Done. For ``review_requested`` and
    ``converted_to_draft``, a changes-requested review submitted during the
    write restores Changes requested, and a merge or close during that
    corrective write still ends in Done. For any write of Changes requested
    (``review_changed``, or ``opened`` and ``ready_for_review`` retargeted to
    it), a withdrawal during the write undoes it: the
    withdrawal's own run would find it older than the Status and leave it.
    Any other newer review event after a ``review_changed`` write needs no
    repair here: its own run is queued behind this one and applies next.
    """
    after = api.load_pr(config, event.number, project.id)
    status = _status_of(after)
    if status == target_name and after.item is not None:
        repair: Optional[tuple[str, str]] = None
        if after.state != OPEN:
            repair = ("done", f"the PR was {after.state.lower()} during the write")
        elif event.action in ORDERED_ACTIONS and not event_is_current(event.action, after)[0]:
            repair = ("changes_requested", "changes were requested during the write")
        elif target_name == config.statuses["changes_requested"]:
            # review_changed, or opened / ready_for_review retargeted to it.
            if not unanswered_changes(after):
                # Withdrawn between this run's read and its write: that
                # withdrawal is older than the Status just written, so its
                # own run would leave it, and only this run can undo it.
                repair = (
                    "in_progress" if after.is_draft else "review",
                    "the change request was dismissed or withdrawn during the write",
                )
        if repair:
            name = config.statuses[repair[0]]
            api.set_status(project.id, after.item.id, project.field_id, project.options[name])
            audit.steps.append(f"set {name}")
            if repair[0] != "done":
                # The PR may have closed during the corrective write too, and
                # later runs skip a closed PR, so check once more.
                final = api.load_pr(config, event.number, project.id)
                if final.state != OPEN and final.item is not None and _status_of(final) == name:
                    done = config.statuses["done"]
                    api.set_status(project.id, final.item.id, project.field_id, project.options[done])
                    audit.steps.append(f"set {done}")
                    return _finish(audit, "corrected", f"the PR was {final.state.lower()} during the write", done)
            return _finish(audit, "corrected", repair[1], name)
    if status != target_name:
        return _finish(audit, "superseded", f"another writer set {status!r} after this run", status)
    return _finish(audit, "updated", reason, target_name)


def _finish(audit: Audit, outcome: str, reason: str, after: Optional[str] = None) -> Audit:
    audit.outcome = outcome
    audit.reason = reason
    if after is not None:
        audit.status_after = after
    return audit


# --- GitHub GraphQL ---------------------------------------------------------

PROJECT_QUERY = """
query($owner: String!, $number: Int!, $field: String!) {
  user(login: $owner) {
    projectV2(number: $number) {
      id
      field(name: $field) {
        __typename
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
    }
  }
}
"""

PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $field: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      state
      isDraft
      reviewRequests: timelineItems(last: 100, itemTypes: [REVIEW_REQUESTED_EVENT]) {
        nodes {
          ... on ReviewRequestedEvent {
            createdAt
            requestedReviewer {
              __typename
              ... on User { login }
              ... on Bot { login }
              ... on Mannequin { login }
              ... on Team { slug }
            }
          }
        }
      }
      convertedToDraft: timelineItems(last: 100, itemTypes: [CONVERT_TO_DRAFT_EVENT]) {
        nodes { ... on ConvertToDraftEvent { createdAt } }
      }
      latestOpinionatedReviews(first: 100) {
        nodes { state submittedAt author { login } }
      }
      changeReviews: reviews(last: 100, states: [CHANGES_REQUESTED]) {
        nodes { submittedAt author { login } }
      }
      approvals: reviews(last: 100, states: [APPROVED]) {
        nodes { submittedAt author { login } }
      }
      dismissals: timelineItems(last: 100, itemTypes: [REVIEW_DISMISSED_EVENT]) {
        nodes { ... on ReviewDismissedEvent { createdAt previousReviewState review { submittedAt author { login } } } }
      }
      projectItems(first: 50, includeArchived: true) {
        pageInfo { hasNextPage }
        nodes {
          id
          isArchived
          project { id }
          fieldValueByName(name: $field) {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt }
          }
        }
      }
    }
  }
}
"""

ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

SET_STATUS = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}
"""

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def http_transport(token: str, url: str = GRAPHQL_URL) -> Transport:
    if not url.startswith("https://"):
        raise ApiError("the GraphQL URL must use https")

    def send(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "prks-project-sync",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - https only
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ApiError(f"GraphQL HTTP {error.code}") from None
        except (OSError, http.client.HTTPException, ValueError) as error:
            # URLError, read timeouts, connection resets, truncated reads and
            # bodies that are not JSON.
            raise ApiError(f"GraphQL request failed: {type(error).__name__}") from None
        if not isinstance(payload, dict):
            raise ApiError("GraphQL response is not an object")
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", "")) for e in payload["errors"] if isinstance(e, dict))
            raise ApiError(f"GraphQL error: {messages or 'unknown'}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ApiError("GraphQL response has no data")
        return data

    return send


class GraphQLApi:
    def __init__(self, send: Transport) -> None:
        self._send = send

    def load_project(self, config: Config) -> Project:
        data = self._send(
            PROJECT_QUERY,
            {"owner": config.owner, "number": config.project_number, "field": config.status_field},
        )
        project = (data.get("user") or {}).get("projectV2")
        if not project:
            raise ConfigError(f"project {config.owner} #{config.project_number} is not visible to the token")
        status = project.get("field") or {}
        if status.get("__typename") != "ProjectV2SingleSelectField":
            raise ConfigError(f"project has no single-select field named {config.status_field!r}")
        options = {opt["name"]: opt["id"] for opt in status.get("options", [])}
        return Project(id=project["id"], field_id=status["id"], options=options)

    def load_pr(self, config: Config, number: int, project_id: str) -> PullRequest:
        owner, name = config.repository.split("/", 1)
        data = self._send(
            PR_QUERY, {"owner": owner, "name": name, "number": number, "field": config.status_field}
        )
        pr = (data.get("repository") or {}).get("pullRequest")
        if not pr:
            raise ApiError(f"pull request #{number} not found in {config.repository}")
        items = [
            node
            for node in (pr.get("projectItems") or {}).get("nodes", [])
            if node and (node.get("project") or {}).get("id") == project_id
        ]
        if not items and ((pr.get("projectItems") or {}).get("pageInfo") or {}).get("hasNextPage"):
            # The item may be on a later page; adding it again would not show
            # it on this page either, so fail closed instead of guessing.
            raise ApiError(f"pull request #{number} is in more than 50 projects; its item could not be located")
        if len(items) > 1:
            raise ApiError(f"pull request #{number} has {len(items)} items in the project")
        item = None
        if items:
            value = items[0].get("fieldValueByName") or {}
            item = Item(
                id=items[0]["id"],
                status=value.get("name") if value.get("__typename") == "ProjectV2ItemFieldSingleSelectValue" else None,
                archived=bool(items[0].get("isArchived")),
                status_updated_at=value.get("updatedAt") if value.get("__typename") == "ProjectV2ItemFieldSingleSelectValue" else None,
            )
        drafts = (pr.get("convertedToDraft") or {}).get("nodes") or []
        requests: list[tuple[str, str]] = []
        for node in (pr.get("reviewRequests") or {}).get("nodes") or []:
            reviewer = (node or {}).get("requestedReviewer") or {}
            who = reviewer.get("login") or (f"team:{reviewer['slug']}" if reviewer.get("slug") else "")
            if who and node.get("createdAt"):
                requests.append((who.lower(), node["createdAt"]))
        changes = [
            (((node.get("author") or {}).get("login") or "ghost").lower(), node["submittedAt"])
            for node in (pr.get("latestOpinionatedReviews") or {}).get("nodes") or []
            if node and node.get("state") == "CHANGES_REQUESTED" and node.get("submittedAt")
        ]
        dismissals = [
            (
                (((node.get("review") or {}).get("author") or {}).get("login") or "ghost").lower(),
                (node.get("review") or {}).get("submittedAt"),
                node["createdAt"],
            )
            for node in (pr.get("dismissals") or {}).get("nodes") or []
            if node and node.get("previousReviewState") == "CHANGES_REQUESTED" and node.get("createdAt")
        ]

        def reviews_of(key: str) -> list[tuple[str, str]]:
            return [
                (((node.get("author") or {}).get("login") or "ghost").lower(), node["submittedAt"])
                for node in (pr.get(key) or {}).get("nodes") or []
                if node and node.get("submittedAt")
            ]

        return PullRequest(
            id=pr["id"],
            state=str(pr["state"]),
            is_draft=bool(pr["isDraft"]),
            item=item,
            draft_conversions=tuple(node["createdAt"] for node in drafts if node and node.get("createdAt")),
            review_requests=tuple(requests),
            changes_requested=tuple(changes),
            change_reviews=tuple(reviews_of("changeReviews")),
            approvals=tuple(reviews_of("approvals")),
            dismissals=tuple(dismissals),
        )

    def add_item(self, project_id: str, content_id: str) -> None:
        self._send(ADD_ITEM, {"project": project_id, "content": content_id})

    def set_status(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        self._send(SET_STATUS, {"project": project_id, "item": item_id, "field": field_id, "option": option_id})


# --- entry point ------------------------------------------------------------


def _parse_number(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value or ""):
        raise Refused("invalid pull request number")
    return int(value)


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    api_factory: Optional[Callable[[str], ProjectApi]] = None,
    config_path: Path = CONFIG_PATH,
) -> int:
    del argv  # inputs come from the environment only
    env = os.environ if env is None else env
    apply = env.get("PROJECT_SYNC_MODE", "") == "apply"
    action = env.get("EVENT_ACTION", "")
    audit = Audit(pr=0, action=action, mode="apply" if apply else "dry-run")
    status = 0
    try:
        event = Event(action=action, number=_parse_number(env.get("PR_NUMBER", "")), repository=env.get("EVENT_REPOSITORY", ""))
        audit.pr = event.number
        config = Config.load(config_path)
        validate(event, config)
        token = env.get("PROJECT_SYNC_TOKEN", "")
        if not token:
            _finish(audit, "not-configured", "PROJECT_SYNC_TOKEN is not set; nothing was read or written")
        else:
            factory = api_factory or (lambda value: GraphQLApi(http_transport(value)))
            audit = run(event, config, factory(token), apply)
            if audit.outcome == "failed":
                status = 2
    except Refused as error:
        _finish(audit, "refused", str(error))
        status = 1
    except ConfigError as error:
        _finish(audit, "misconfigured", str(error))
        status = 1
    except ApiError as error:
        _finish(audit, "failed", error.detail)
        status = 2
    print(json.dumps(audit.record(), sort_keys=True))
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(audit.summary_markdown())
    return status


if __name__ == "__main__":
    sys.exit(main())
