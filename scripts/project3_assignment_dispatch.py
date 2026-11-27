#!/usr/bin/env python3
"""Owner-authenticated, comment-free Project #3 assignment workflow dispatcher.

An explicit workflow_dispatch on protected master executes the existing reviewed
Project V2 triage guards. A complete expected-before snapshot makes repeated or
stale commands fail closed. No issue-comment command/receipt is written.

An Implementation Order command (``"kind": "implementation-order"``) changes
only that NUMBER field. Its apply also needs PROJECT3_ORDER_WRITE_MODE=apply
and PROJECT3_ORDER_APPROVED_SHA256 set to the digest that its check printed.
Each approved digest is applied at most once: a commit status claims it.

A plan command (``"kind": "implementation-order-plan"``) carries one batch
and the manifest of all checked batch digests. The maintainer approves the
manifest digest once; its batches apply in order and stop at a failure.

A full-plan command (``"kind": "implementation-order-full-plan"``) carries
every batch. Its check prints the manifest; its apply runs every batch of the
approved manifest in one job, with the same per-batch guards and claims.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Collection, Iterator, Mapping, Protocol
from contextlib import contextmanager

import project3_migrate as pm
import project3_triage as triage

REPOSITORY = "Fooftilly/PRKS"
OWNER_ID = 46610174
MAX_BYTES = 12000
# workflow_dispatch inputs share one 65,535-character payload limit.
DISPATCH_MAX_BYTES = 60000
FULL_MAX_BYTES = 131072
MAX_ITEMS = 10
FIELDS = frozenset(("Agent", "Status", "Execution"))
ORDER_KIND = "implementation-order"
PLAN_KIND = "implementation-order-plan"
FULL_KIND = "implementation-order-full-plan"
ORDER_KINDS = frozenset((ORDER_KIND, PLAN_KIND, FULL_KIND))
MANIFEST_SCHEMA = "prks-project3-order-manifest/v1"
# 30 full batches keep a plan command under MAX_BYTES with short reasons.
MAX_BATCHES = 30
ORDER_ROW_KEYS = frozenset(("number", "order", "expected_before", "reason",
                            "roadmap_checked", "overlap_checked"))
# A master commit that is never rewritten; it holds one claim per approval.
ORDER_CLAIM_SHA = "126d25fb6f50ef9bd00cfad52065461eca993c7e"
ORDER_CLAIM_PREFIX = "prks/project3-order-approval/"
PLAN_CLAIM_PREFIX = "prks/project3-order-plan/"
REVOKE_PREFIX = "prks/project3-order-revoke/"
CLAIM_STATES = frozenset(("pending", "success", "failure"))
HEX64 = re.compile(r"[0-9a-f]{64}")
# About 10,000 applies at two statuses each, and far below the Actions
# token's hourly REST limit of 1,000 requests.
ORDER_CLAIM_PAGES = 200
# REST requests of the Actions token: two per issue snapshot (issue and
# blockers), and claim, revocation and rate reads around each batch.
CHECK_COST_PER_ROW = 2
APPLY_COST_PER_ROW = 8
BATCH_OVERHEAD = 8
# The workflow job stops at 360 minutes; stop between batches well before.
FULL_PLAN_SECONDS = 5 * 3600


class DispatchError(Exception):
    """Rejected dispatch; Project #3 has not been authorized by this command."""


def authenticate(event: Any, env: Mapping[str, str]) -> str:
    """Require the immutable maintainer ID and the trusted default-branch run."""
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/master"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"):
        raise DispatchError("not a first-attempt dispatch on protected master")
    if (not isinstance(event, dict)
            or (event.get("repository") or {}).get("full_name") != REPOSITORY
            or (event.get("sender") or {}).get("id") != OWNER_ID
            or (event.get("sender") or {}).get("login") != "Fooftilly"):
        raise DispatchError("dispatch sender or repository not authorized")
    if event.get("ref") not in (None, "refs/heads/master"):
        raise DispatchError("dispatch event ref is not master")
    inputs = event.get("inputs")
    command = inputs.get("command") if isinstance(inputs, dict) else None
    if not isinstance(command, str) or len(command.encode("utf-8")) > DISPATCH_MAX_BYTES:
        raise DispatchError("missing or oversized command input")
    return command


def _load(command: str) -> Any:
    try:
        return json.loads(command)
    except (ValueError, TypeError):
        raise DispatchError("invalid command JSON") from None


def command_kind(command: str) -> tuple[str, str]:
    """Return (kind, operation) after full validation of any command type."""
    payload = _load(command)
    size = len(command.encode("utf-8"))
    if isinstance(payload, dict) and payload.get("kind") == FULL_KIND:
        if size > FULL_MAX_BYTES:
            raise DispatchError("oversized full-plan command")
        return FULL_KIND, parse_full(command)[0]
    if size > MAX_BYTES:
        raise DispatchError("oversized command")
    if isinstance(payload, dict) and payload.get("kind") == PLAN_KIND:
        return PLAN_KIND, parse_plan(command)[0]
    if isinstance(payload, dict) and "kind" in payload:
        operation, _ = parse_order(command)
        return ORDER_KIND, operation
    operation, _, _, _ = parse(command)
    return "assignment", operation


def parse(command: str) -> tuple[str, list[dict], list[dict], list[bool]]:
    payload = _load(command)
    if (not isinstance(payload, dict)
            or set(payload) != {"version", "operation", "items"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["operation"] not in ("check", "apply")
            or not isinstance(payload["items"], list)
            or not 1 <= len(payload["items"]) <= MAX_ITEMS):
        raise DispatchError("invalid command schema or batch size")
    rows, expected, reassign = [], [], []
    for original in payload["items"]:
        if not isinstance(original, dict):
            raise DispatchError("invalid command row")
        if set(original) - {"number", "set", "reason", "roadmap_checked",
                            "overlap_checked", "expected_before", "allow_reassignment"}:
            raise DispatchError("unexpected command row fields")
        snapshot = original.get("expected_before")
        if (not isinstance(snapshot, dict) or set(snapshot) != FIELDS
                or any(value is not None and not isinstance(value, str)
                       for value in snapshot.values())):
            raise DispatchError("expected_before must include all three Project fields")
        allow = original.get("allow_reassignment", False)
        if type(allow) is not bool:
            raise DispatchError("allow_reassignment must be boolean")
        row = {k: v for k, v in original.items()
               if k not in ("expected_before", "allow_reassignment")}
        if row.get("number") == 982:
            raise DispatchError("legacy command inbox is not an assignment target")
        try:
            triage.validate_row(row, plan=False)
        except triage.TriageError as error:
            raise DispatchError(str(error)) from None
        rows.append(row)
        expected.append(snapshot)
        reassign.append(allow)
    if len({row["number"] for row in rows}) != len(rows):
        raise DispatchError("duplicate issue number")
    return payload["operation"], rows, expected, reassign


def parse_order(command: str) -> tuple[str, list[dict]]:
    payload = _load(command)
    if (not isinstance(payload, dict)
            or set(payload) != {"version", "kind", "operation", "items"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["kind"] != ORDER_KIND
            or payload["operation"] not in ("check", "apply")
            or not isinstance(payload["items"], list)
            or not 1 <= len(payload["items"]) <= MAX_ITEMS):
        raise DispatchError("invalid order command schema or batch size")
    return payload["operation"], _order_rows(payload["items"])


def _order_rows(items: list) -> list[dict]:
    rows = []
    for original in items:
        if not isinstance(original, dict) or set(original) != ORDER_ROW_KEYS:
            raise DispatchError("order rows need exactly: " + ", ".join(sorted(ORDER_ROW_KEYS)))
        if original["number"] == 982:
            raise DispatchError("legacy command inbox is not an order target")
        row = {k: v for k, v in original.items() if k != "expected_before"}
        try:
            triage.validate_order_row(row, plan=False)
            triage.order_value(original["expected_before"],
                               f"#{original['number']}: expected_before", nullable=True)
        except triage.TriageError as error:
            raise DispatchError(str(error)) from None
        rows.append(original)
    try:
        triage._check_order_batch(rows)
    except triage.TriageError as error:
        raise DispatchError(str(error)) from None
    return rows


def _manifest(value: Any) -> dict:
    if (not isinstance(value, dict) or set(value) != {"schema", "field", "batches"}
            or value["schema"] != MANIFEST_SCHEMA
            or not isinstance(value["field"], dict) or set(value["field"]) != {"name", "id"}
            or value["field"]["name"] != triage.ORDER_FIELD
            or not isinstance(value["field"]["id"], str) or not value["field"]["id"]
            or not isinstance(value["batches"], list)
            or not 1 <= len(value["batches"]) <= MAX_BATCHES):
        raise DispatchError(f"invalid order manifest schema, field or size (1–{MAX_BATCHES} batches)")
    digests = set()
    for entry in value["batches"]:
        if (not isinstance(entry, dict) or set(entry) != {"digest", "items"}
                or not isinstance(entry["digest"], str) or not HEX64.fullmatch(entry["digest"])
                or not isinstance(entry["items"], list)
                or not 1 <= len(entry["items"]) <= MAX_ITEMS):
            raise DispatchError("manifest batches need a digest and 1–10 [number, order] pairs")
        if entry["digest"] in digests:
            raise DispatchError("manifest lists one batch digest twice")
        digests.add(entry["digest"])
        for pair in entry["items"]:
            if (not isinstance(pair, list) or len(pair) != 2
                    or type(pair[0]) is not int or pair[0] < 1):
                raise DispatchError("manifest items must be [number, order] pairs")
            try:
                triage.order_value(pair[1], f"#{pair[0]}: manifest order", nullable=False)
            except triage.TriageError as error:
                raise DispatchError(str(error)) from None
    _unique_pairs([entry["items"] for entry in value["batches"]])
    return value


def _unique_pairs(batches: list[list[list[int]]]) -> None:
    numbers, ranks = set(), set()
    for pairs in batches:
        for number, rank in pairs:
            if number in numbers:
                raise DispatchError(f"#{number}: issue appears in more than one manifest row")
            if rank in ranks:
                raise DispatchError(f"rank {rank} appears in more than one manifest row")
            numbers.add(number)
            ranks.add(rank)


def _pairs(rows: list[dict]) -> list[list[int]]:
    return [[row["number"], row["order"]] for row in rows]


def parse_full(command: str) -> tuple[str, list[list[dict]], dict | None]:
    """Validate every batch of a full plan; apply also carries the checked manifest."""
    payload = _load(command)
    keys = {"version", "kind", "operation", "batches"}
    if (not isinstance(payload, dict) or payload.get("operation") not in ("check", "apply")
            or set(payload) != (keys | {"manifest"} if payload["operation"] == "apply" else keys)
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["kind"] != FULL_KIND
            or not isinstance(payload["batches"], list)
            or not 1 <= len(payload["batches"]) <= MAX_BATCHES
            or any(not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS
                   for rows in payload["batches"])):
        raise DispatchError(f"invalid full-plan command schema or size (1–{MAX_BATCHES} "
                            f"batches of 1–{MAX_ITEMS} rows; apply needs the checked manifest)")
    batches = [_order_rows(rows) for rows in payload["batches"]]
    _unique_pairs([_pairs(rows) for rows in batches])
    manifest = None
    if payload["operation"] == "apply":
        manifest = _manifest(payload["manifest"])
        if [entry["items"] for entry in manifest["batches"]] != [_pairs(rows) for rows in batches]:
            raise DispatchError("full-plan batches differ from the manifest")
    return payload["operation"], batches, manifest


def parse_plan(command: str) -> tuple[str, dict, int, list[dict]]:
    payload = _load(command)
    if (not isinstance(payload, dict)
            or set(payload) != {"version", "kind", "operation", "manifest", "batch", "items"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["kind"] != PLAN_KIND
            or payload["operation"] not in ("check", "apply")
            or type(payload["batch"]) is not int
            or not isinstance(payload["items"], list)
            or not 1 <= len(payload["items"]) <= MAX_ITEMS):
        raise DispatchError("invalid order plan command schema or batch size")
    manifest = _manifest(payload["manifest"])
    index = payload["batch"]
    if not 1 <= index <= len(manifest["batches"]):
        raise DispatchError("batch is not a position in the manifest")
    rows = _order_rows(payload["items"])
    if [[row["number"], row["order"]] for row in rows] != manifest["batches"][index - 1]["items"]:
        raise DispatchError(f"batch {index}: rows differ from its manifest entry")
    return payload["operation"], manifest, index, rows


def manifest_digest(manifest: dict) -> str:
    """The schema key keeps this digest disjoint from batch digests."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def batch_claim(digest: str) -> str:
    return ORDER_CLAIM_PREFIX + digest


def plan_claim(manifest: str, index: int) -> str:
    return f"{PLAN_CLAIM_PREFIX}{manifest}/{index}"


def revoke_claim(manifest: str) -> str:
    return REVOKE_PREFIX + manifest


def order_digest(rows: list[dict], field: dict, live: list[dict]) -> str:
    """Identical for the check and apply of one batch while its field, items and issues are unchanged."""
    canonical = json.dumps({"kind": ORDER_KIND, "items": rows, "field": field, "live": live},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def order_apply_allowed(digest: str, env: Mapping[str, str]) -> bool:
    if env.get("PROJECT3_ORDER_WRITE_MODE") != "apply":
        return False
    if env.get("PROJECT3_ORDER_APPROVED_SHA256", "") != digest:
        raise DispatchError("order apply refused: digest is not the maintainer-approved "
                            "PROJECT3_ORDER_APPROVED_SHA256")
    return True


class OrderClaims(Protocol):
    def states(self, contexts: Collection[str]) -> dict[str, set[str]]: ...
    def mark(self, context: str, state: str) -> None: ...
    def recent(self, context: str) -> bool: ...


def _valid_claim(context: str) -> bool:
    if context.startswith(ORDER_CLAIM_PREFIX):
        return bool(HEX64.fullmatch(context[len(ORDER_CLAIM_PREFIX):]))
    manifest, _, index = context.removeprefix(PLAN_CLAIM_PREFIX).partition("/")
    return (context.startswith(PLAN_CLAIM_PREFIX) and bool(HEX64.fullmatch(manifest))
            and index.isdecimal() and 1 <= int(index) <= MAX_BATCHES)


class GitHubOrderClaims:
    """Append-only commit statuses on ORDER_CLAIM_SHA, one context per claim.

    Both writers share the project3-assignment-mutations concurrency group,
    so the read-then-claim sequence cannot race another writer.
    """

    def __init__(self, token: str, run_id: str) -> None:
        if not token or not run_id.isdecimal():
            raise DispatchError("order approval claim needs the Actions token and run ID")
        self._token = token
        self._run_id = run_id
        self._opener = pm._https_only_opener()
        self._base = f"https://api.github.com/repos/{REPOSITORY}"

    def _send(self, url: str, payload: bytes | None = None) -> Any:
        request = urllib.request.Request(url, data=payload, method="POST" if payload else "GET", headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "prks-project3-order-claim",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with self._opener.open(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise DispatchError("order approval claim request failed: "
                                + type(error).__name__) from None

    def states(self, contexts: Collection[str]) -> dict[str, set[str]]:
        """Every state ever written for each context, from one full history scan."""
        wanted = set(contexts)
        found: dict[str, set[str]] = {}
        for page in range(1, ORDER_CLAIM_PAGES + 1):
            history = self._send(f"{self._base}/commits/{ORDER_CLAIM_SHA}/statuses"
                                 f"?per_page=100&page={page}")
            if not isinstance(history, list) or len(history) > 100:
                raise DispatchError("malformed order approval claim history")
            if any(not isinstance(item, dict) for item in history):
                raise DispatchError("malformed order approval claim")
            for item in history:
                if item.get("context") in wanted:
                    found.setdefault(item["context"], set()).add(str(item.get("state")))
            if len(history) < 100:
                return found
        raise DispatchError("order approval claim history too large to prove non-replay")

    def recent(self, context: str) -> bool:
        """Read only the newest 100 statuses; enough between two batches of one run."""
        history = self._send(f"{self._base}/commits/{ORDER_CLAIM_SHA}/statuses?per_page=100&page=1")
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise DispatchError("malformed order approval claim history")
        return any(item.get("context") == context for item in history)

    def mark(self, context: str, state: str) -> None:
        if state not in CLAIM_STATES or not _valid_claim(context):
            raise DispatchError("invalid order approval claim")
        result = self._send(f"{self._base}/statuses/{ORDER_CLAIM_SHA}", json.dumps({
            "state": state, "context": context,
            "description": f"Implementation Order approval {state} (run {self._run_id})",
            "target_url": f"https://github.com/{REPOSITORY}/actions/runs/{self._run_id}",
        }).encode("utf-8"))
        if not isinstance(result, dict) or (result.get("context"), result.get("state")) != (context, state):
            raise DispatchError("order approval claim write was not verified")


def mutations_allowed(command: str, env: Mapping[str, str]) -> bool:
    kind, operation = command_kind(command)
    if operation != "apply":
        return False
    if kind in ORDER_KINDS:
        return env.get("PROJECT3_ORDER_WRITE_MODE") == "apply"
    return env.get("PROJECT3_ASSIGN_DISPATCH_MODE") == "apply"


def prepare_order(rows: list[dict], project: pm.Project,
                  read: triage.Callable[[str], Any], *, preflight: bool = True) -> dict:
    proposals = [{k: v for k, v in row.items() if k != "expected_before"} for row in rows]
    plan = triage.propose_order(proposals, project, read)
    for planned, row in zip(plan["items"], rows, strict=True):
        if planned["expected_before"] != row["expected_before"]:
            raise DispatchError(f"#{row['number']}: expected_before differs from live Project")
        planned["approved"] = True
    if preflight:
        triage.preflight_order(plan, project, read)
    return plan


def _checked_batch(rows: list[dict], project: pm.Project,
                   read: triage.Callable[[str], Any], *, preflight: bool = True) -> tuple[dict, str]:
    plan = prepare_order(rows, project, read, preflight=preflight)
    return plan, order_digest(rows, plan["field"],
                              [{"item": row["expected_item"], "issue": row["expected_issue"]}
                               for row in plan["items"]])


def _claimed_apply(plan: dict, project: pm.Project, read: triage.Callable[[str], Any],
                   claims: OrderClaims, contexts: list[str]) -> int:
    for context in contexts:
        claims.mark(context, "pending")
    try:
        changed = triage.apply_order(plan, project, read)
    except BaseException:
        for context in contexts:
            try:
                claims.mark(context, "failure")
            except DispatchError:
                print("order approval claim completion failed; pending claim is durable",
                      file=sys.stderr)
        raise
    for context in contexts:
        claims.mark(context, "success")
    return changed


def execute_order(command: str, *, project: pm.Project,
                  read: triage.Callable[[str], Any],
                  env: Mapping[str, str],
                  claims: OrderClaims | None) -> tuple[str, int, int]:
    operation, rows = parse_order(command)
    plan, digest = _checked_batch(rows, project, read)
    if operation == "check":
        return (f"CHECK ONLY — no Project fields changed; field {plan['field']['id']}; "
                f"order digest {digest}"), 0, len(rows)
    if not order_apply_allowed(digest, env):
        return f"DRY RUN — order write mode is not enabled; order digest {digest}", 0, len(rows)
    if claims is None:
        raise DispatchError("order apply refused: no approval claim store")
    if claims.states([batch_claim(digest)]):
        raise DispatchError("order apply refused: this approval was already used; no replay")
    changed = _claimed_apply(plan, project, read, claims, [batch_claim(digest)])
    return f"APPLIED — {triage.ORDER_FIELD} verified; order digest {digest}", changed, len(rows)


def _plan_batch(rows: list[dict], manifest: dict, index: int, project: pm.Project,
                read: triage.Callable[[str], Any], *, preflight: bool = True) -> tuple[dict, str]:
    plan, digest = _checked_batch(rows, project, read, preflight=preflight)
    if plan["field"] != manifest["field"]:
        raise DispatchError(f"{triage.ORDER_FIELD} field identity differs from the manifest")
    if digest != manifest["batches"][index - 1]["digest"]:
        raise DispatchError(f"batch {index}: live evidence differs from its checked order digest; "
                            "check the remaining work again")
    return plan, digest


def _fresh_items(plan: dict, project: pm.Project) -> None:
    """Re-read Project items without repeating the issue reads of this check."""
    for row in plan["items"]:
        triage.order_guard(row, project.refresh(row["expected_item"]), row["expected_issue"])


def _plan_sequence(claims: OrderClaims, manifest: dict, approval: str, index: int) -> list[str]:
    steps = [plan_claim(approval, n) for n in range(1, len(manifest["batches"]) + 1)]
    # A used digest in a later batch would strand the plan after this batch writes.
    remaining = {n: batch_claim(entry["digest"])
                 for n, entry in enumerate(manifest["batches"], start=1) if n >= index}
    seen = claims.states([*remaining.values(), *steps, revoke_claim(approval)])
    if seen.get(revoke_claim(approval)):
        raise DispatchError("order plan refused: the maintainer revoked this manifest")
    for n, context in remaining.items():
        if seen.get(context):
            raise DispatchError(f"batch {n}: order digest was already used; no replay")
    for n, context in enumerate(steps, start=1):
        states = seen.get(context, set())
        if n < index and ("success" not in states or "failure" in states):
            raise DispatchError(f"batch {n} has not succeeded under this manifest; batches "
                                "run in order and stop after a failure")
        if n >= index and states:
            raise DispatchError(f"batch {n} already started under this manifest; no replay")
    return steps


def execute_plan(command: str, *, project: pm.Project,
                 read: triage.Callable[[str], Any],
                 env: Mapping[str, str],
                 claims: OrderClaims | None) -> tuple[str, int, int]:
    operation, manifest, index, rows = parse_plan(command)
    total = len(manifest["batches"])
    approval = manifest_digest(manifest)
    where = f"batch {index}/{total}; manifest digest {approval}"
    if operation == "check":
        _, digest = _plan_batch(rows, manifest, index, project, read)
        return f"CHECK ONLY — no Project fields changed; order digest {digest}; {where}", 0, len(rows)
    if env.get("PROJECT3_ORDER_WRITE_MODE") != "apply":
        _plan_batch(rows, manifest, index, project, read)
        return f"DRY RUN — order write mode is not enabled; {where}", 0, len(rows)
    order_apply_allowed(approval, env)
    if claims is None:
        raise DispatchError("order apply refused: no approval claim store")
    # The step is claimed before live preflight, so any refusal ends this manifest.
    held = [_plan_sequence(claims, manifest, approval, index)[index - 1]]
    claims.mark(held[0], "pending")
    try:
        plan, digest = _plan_batch(rows, manifest, index, project, read)
        claims.mark(batch_claim(digest), "pending")
        held.insert(0, batch_claim(digest))
        changed = triage.apply_order(plan, project, read)
    except BaseException:
        _fail(claims, held)
        later = ("no later batches" if index == total
                 else f"{_later(index, total)} stays blocked" if index + 1 == total
                 else f"{_later(index, total)} stay blocked")
        print(f"order plan {where}: FAILED / POSSIBLY PARTIAL; {later} under this manifest",
              file=sys.stderr)
        raise
    for context in held:
        claims.mark(context, "success")
    return (f"APPLIED — {triage.ORDER_FIELD} verified; {total - index} batches pending; {where}",
            changed, len(rows))


def _later(index: int, total: int) -> str:
    return f"batch {total}" if index + 1 == total else f"batches {index + 1}–{total}"


def _fail(claims: OrderClaims, held: list[str]) -> None:
    for context in held:
        try:
            claims.mark(context, "failure")
        except DispatchError:
            print("order approval claim completion failed; pending claim is durable",
                  file=sys.stderr)


class RateBudget(Protocol):
    def require(self, cost: int) -> None: ...
    def wait(self, cost: int) -> None: ...


class GitHubRateBudget:
    """Paces Actions-token REST reads; GET /rate_limit does not count against the limit."""

    def __init__(self, token: str, *, seconds: int = FULL_PLAN_SECONDS,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not token:
            raise DispatchError("rate budget needs the Actions token")
        self._token = token
        self._clock = clock
        self._sleep = sleep
        self._deadline = clock() + seconds
        self._opener = pm._https_only_opener()

    def _core(self) -> tuple[int, int, float]:
        request = urllib.request.Request("https://api.github.com/rate_limit", headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "prks-project3-order-budget",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with self._opener.open(request, timeout=30) as response:
                core = json.loads(response.read().decode("utf-8"))["resources"]["core"]
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as error:
            raise DispatchError("rate limit read failed: " + type(error).__name__) from None
        if any(type(core.get(key)) is not int for key in ("limit", "remaining", "reset")):
            raise DispatchError("malformed rate limit response")
        return core["limit"], core["remaining"], float(core["reset"])

    def require(self, cost: int) -> None:
        limit, remaining, reset = self._core()
        windows = max(0, int((self._deadline - reset) // 3600) + 1)
        if remaining + windows * limit < cost:
            raise DispatchError(f"plan needs about {cost} Actions-token requests; about "
                                f"{remaining + windows * limit} fit before the job deadline")

    def _before_deadline(self) -> None:
        if self._clock() >= self._deadline:
            raise DispatchError("the full-plan deadline passed; no new batch starts")

    def wait(self, cost: int) -> None:
        _, remaining, reset = self._core()
        self._before_deadline()
        if remaining >= cost:
            return
        if reset + 5 >= self._deadline:
            raise DispatchError("Actions-token requests would run out before the job deadline")
        print(f"waiting {int(reset + 5 - self._clock())} s for the REST rate limit to reset",
              flush=True)
        self._sleep(max(0.0, reset + 5 - self._clock()))
        if self._core()[1] < cost:
            raise DispatchError("Actions-token requests still exhausted after the reset")
        self._before_deadline()


@contextmanager
def _cancellable() -> Iterator[None]:
    """A cancelled run gets SIGINT, then SIGTERM; both stop at the current call."""
    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def publish(lines: list[str]) -> None:
    """Print to the run log and append to the step summary; never print reasons."""
    for line in lines:
        print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n\n")


def _rows_table(index: int, rows: list[dict]) -> list[str]:
    return [f"| {index} | #{row['number']} | {row['expected_before']} | {row['order']} |"
            for row in rows]


def execute_full(command: str, *, project: pm.Project,
                 read: triage.Callable[[str], Any],
                 env: Mapping[str, str],
                 claims: OrderClaims | None,
                 budget: RateBudget | None) -> tuple[str, int, int]:
    operation, batches, given = parse_full(command)
    issues = sum(len(rows) for rows in batches)
    if budget is None:
        raise DispatchError("full plan refused: no rate budget")
    if given is None:
        budget.require(issues * CHECK_COST_PER_ROW + len(batches) * BATCH_OVERHEAD)
        entries, field = [], None
        for rows in batches:
            budget.wait(len(rows) * CHECK_COST_PER_ROW + BATCH_OVERHEAD)
            plan, digest = _checked_batch(rows, project, read, preflight=False)
            _fresh_items(plan, project)
            if field not in (None, plan["field"]):
                raise DispatchError(f"{triage.ORDER_FIELD} field identity changed during the check")
            field = plan["field"]
            entries.append({"digest": digest, "items": _pairs(rows)})
        manifest = {"schema": MANIFEST_SCHEMA, "field": field, "batches": entries}
        approval = manifest_digest(manifest)
        publish(["### Implementation Order full plan", "",
                 "| Batch | Issue | Before | Order |", "| --- | --- | --- | --- |",
                 *(line for n, rows in enumerate(batches, start=1) for line in _rows_table(n, rows)),
                 "", "ORDER MANIFEST " + json.dumps(manifest, sort_keys=True, separators=(",", ":"))])
        return (f"CHECK ONLY — no Project fields changed; {len(batches)} batches; "
                f"manifest digest {approval}"), 0, issues
    approval = manifest_digest(given)
    total = len(batches)
    if env.get("PROJECT3_ORDER_WRITE_MODE") != "apply":
        for n, rows in enumerate(batches, start=1):
            budget.wait(len(rows) * CHECK_COST_PER_ROW + BATCH_OVERHEAD)
            _fresh_items(_plan_batch(rows, given, n, project, read, preflight=False)[0], project)
        return f"DRY RUN — order write mode is not enabled; manifest digest {approval}", 0, issues
    order_apply_allowed(approval, env)
    if claims is None:
        raise DispatchError("order apply refused: no approval claim store")
    budget.require(ORDER_CLAIM_PAGES + issues * (CHECK_COST_PER_ROW + APPLY_COST_PER_ROW)
                   + total * BATCH_OVERHEAD)
    budget.wait(ORDER_CLAIM_PAGES + 1)
    steps = _plan_sequence(claims, given, approval, 1)
    held: list[str] = []
    following: list[str] = []
    done, changed, index, wrote = 0, 0, 1, False
    with _cancellable():
        try:
            # The first step is claimed before the whole-plan preflight, so any refusal ends the manifest.
            held = [steps[0]]
            claims.mark(steps[0], "pending")
            for n, rows in enumerate(batches, start=1):
                budget.wait(len(rows) * CHECK_COST_PER_ROW + BATCH_OVERHEAD)
                _fresh_items(_plan_batch(rows, given, n, project, read, preflight=False)[0], project)
            for index, rows in enumerate(batches, start=1):
                budget.wait(len(rows) * APPLY_COST_PER_ROW + BATCH_OVERHEAD)
                if claims.recent(revoke_claim(approval)):
                    raise DispatchError("the maintainer revoked this manifest during the run")
                plan, digest = _plan_batch(rows, given, index, project, read, preflight=False)
                claims.mark(batch_claim(digest), "pending")
                held.insert(0, batch_claim(digest))
                wrote = True
                changed += triage.apply_order(plan, project, read)
                # The next step is claimed while this batch's quota is reserved, so a stop
                # during the next budget wait cannot leave the manifest resumable.
                following = steps[index:index + 1]
                for context in following:
                    claims.mark(context, "pending")
                for context in held:
                    claims.mark(context, "success")
                held, following, wrote, done = following, [], False, index
                publish([f"batch {index}/{total} verified: "
                         + ", ".join(f"#{row['number']}={row['order']}" for row in rows)])
        except BaseException:
            _fail(claims, held + following)
            if done == index:
                state = f"stopped after verified batch {done}"
            else:
                state = f"batch {index}: " + ("FAILED / POSSIBLY PARTIAL" if wrote
                                              else "REFUSED before any write in this batch")
            rest = f"; {_later(index, total)} not applied" if index < total else ""
            publish([f"order full plan; manifest digest {approval}: {done} of {total} batches "
                     f"verified; {state}{rest}"])
            raise
    return (f"APPLIED — {triage.ORDER_FIELD} verified; {total} batches; "
            f"manifest digest {approval}"), changed, issues


def run(command: str, *, project: pm.Project, read: triage.Callable[[str], Any],
        env: Mapping[str, str], claims: OrderClaims | None = None,
        budget: RateBudget | None = None) -> tuple[str, int, int]:
    kind = command_kind(command)[0]
    if kind == FULL_KIND:
        return execute_full(command, project=project, read=read, env=env, claims=claims,
                            budget=budget)
    if kind == PLAN_KIND:
        return execute_plan(command, project=project, read=read, env=env, claims=claims)
    if kind == ORDER_KIND:
        return execute_order(command, project=project, read=read, env=env, claims=claims)
    return execute(command, project=project, read=read,
                   mode=env.get("PROJECT3_ASSIGN_DISPATCH_MODE", ""))


def prepare(rows: list[dict], expected: list[dict], reassign: list[bool],
            project: pm.Project, read: triage.Callable[[str], Any]) -> dict:
    for row, before, allowed in zip(rows, expected, reassign, strict=True):
        current = triage.project_live(project, row["number"])
        values = {field: current["values"].get(field) for field in FIELDS}
        if values != before:
            raise DispatchError(f"#{row['number']}: expected_before differs from live Project")
        old = values["Agent"]
        new = row["set"].get("Agent", old)
        if old not in (None, "Unassigned", new) and not allowed:
            raise DispatchError(f"#{row['number']}: changing Agent requires allow_reassignment=true")
    plan = triage.propose(rows, project, read)
    for row in plan["items"]:
        row["approved"] = True
    triage.preflight(plan, project, read)
    return plan


def execute(command: str, *, project: pm.Project,
            read: triage.Callable[[str], Any], mode: str) -> tuple[str, int, int]:
    operation, rows, expected, reassign = parse(command)
    plan = prepare(rows, expected, reassign, project, read)
    if operation == "check":
        return "CHECK ONLY — no Project fields changed", 0, len(rows)
    if mode != "apply":
        return "DRY RUN — dispatch write mode is not enabled", 0, len(rows)
    changed = triage.apply(plan, project, read)
    return "APPLIED — Project fields verified", changed, len(rows)


def report(outcome: str, changed: int, issues: int, *, failure: bool = False) -> None:
    # Workflow-run logs and step summary are the audit record, not issue comments.
    # Never echo the event command, user-supplied reason, or either token.
    line = f"{outcome}; issues={issues}; field_updates={changed}"
    print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("## Project #3 assignment dispatch\n\n")
            output.write("**FAILED / POSSIBLY PARTIAL** — inspect live fields before a new "
                         "dispatch.\n" if failure else line + "\n")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and not failure:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")


def main() -> int:
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise DispatchError("missing workflow event")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        command = authenticate(event, os.environ)
        allow = mutations_allowed(command, os.environ)
        project_token = os.environ.get("PROJECT_SYNC_TOKEN")
        if not project_token:
            raise DispatchError("protected Project token missing")
        actions_token = os.environ.get("GITHUB_TOKEN")
        if not actions_token:
            raise DispatchError("scoped Actions token missing")
        project = pm.Project(pm.GraphQL(pm.http_transport(project_token),
                                        allow_mutations=allow))
        read = triage.rest_client(actions_token)
        kind = command_kind(command)[0]
        claims = (GitHubOrderClaims(actions_token, os.environ.get("GITHUB_RUN_ID", ""))
                  if allow and kind in ORDER_KINDS else None)
        budget = GitHubRateBudget(actions_token) if kind == FULL_KIND else None
        outcome, changed, issues = run(command, project=project, read=read, env=os.environ,
                                       claims=claims, budget=budget)
        report(outcome, changed, issues)
        return 0
    except (DispatchError, triage.TriageError, pm.MigrationError, OSError,
            ValueError, TypeError, KeyError, KeyboardInterrupt) as error:
        print(f"assignment dispatch refused or failed: {type(error).__name__}: {error}",
              file=sys.stderr)
        report("FAILED / POSSIBLY PARTIAL", 0, 0, failure=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
