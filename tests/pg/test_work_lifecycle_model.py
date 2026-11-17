"""A model-based Work lifecycle sequence test on PostgreSQL (#1285; #391 §3-§5).

Generates short operation sequences across Works, Tags and Folders, sends
each through the sync protocol as a device would, and after every step
compares what PostgreSQL holds with a small reference model:

- live Works and Tags, Work-Tag links, each Work's Folder and title;
- the revision of every scope the model edits (work-tag, work-folder,
  work-field ``title``), predicted by the decision table every scalar and
  link family shares: a future base is ``FUTURE_REVISION``, a stale base that
  disagrees is ``REVISION_CONFLICT``, anything else is acknowledged and
  advances the revision only on a real change;
- refusals for a target that is gone: a deleted Work is ``ENTITY_NOT_FOUND``,
  a deleted Tag ``TAG_DELETED``;

and that hold whatever the sequence:

- no link names a missing Work or Tag, and no Work is filed in a missing Folder;
- no row of ``entity_revisions`` ever moves backwards or disappears, and a
  Work delete moves each of its existing scopes exactly one on (tombstones);
- a replayed operation (same ``op_id``) answers exactly as the first time and
  changes nothing.

The PostgreSQL job installs only the runtime requirements, so sequences are
drawn from seeded :class:`random.Random` (as ``test_tags_properties.py``
does), not Hypothesis: a failure names its seed and the steps so far, which
replay it exactly. Keep a counterexample as a named regression test.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.domain.organization import folders, links
from backend.domain.works import metadata
from tests.pg.test_tags_sync import TagSyncTestCase

SEEDS = range(1285_000, 1285_010)
STEPS = 14
TITLES = ("Alpha", "Beta")
BASES = ("current", "current", "stale", "future")


@dataclass
class Model:
    works: set[str]
    tags: set[str]
    folders: tuple[str, ...]
    # Every Work and Tag this sequence made, live or not: what it observes.
    universe: frozenset[str] = frozenset()
    links: set[tuple[str, str]] = field(default_factory=set)
    folder_of: dict[str, str] = field(default_factory=dict)
    title_of: dict[str, str] = field(default_factory=dict)
    # (scope type, scope id) -> revision, for the scopes the model edits.
    revisions: dict[tuple[str, str], int] = field(default_factory=dict)

    def rev(self, key: tuple[str, str]) -> int:
        return self.revisions.get(key, 0)


def link_key(work_id: str, tag_id: str) -> tuple[str, str]:
    return ("work-tag", links.WORK.scope_id(work_id, tag_id))


def folder_key(work_id: str) -> tuple[str, str]:
    return (folders.WORK_SCOPE_TYPE, work_id)


def title_key(work_id: str) -> tuple[str, str]:
    return (metadata.SCOPE_TYPE, metadata.scope_id(work_id, "title"))


class WorkLifecycleModelTests(TagSyncTestCase):
    maxDiff = None

    # -- what PostgreSQL holds -------------------------------------------------------

    def all_revisions(self) -> dict[tuple[str, str], int]:
        return {(row["scope_type"], row["scope_id"]): int(row["revision"])
                for row in self.sql("SELECT scope_type, scope_id, revision FROM prks.entity_revisions")}

    def observe(self, model: Model) -> dict[str, Any]:
        """The observable state in the model's terms."""
        mine = list(model.universe)
        works = {row["id"] for row in self.sql("SELECT id FROM prks.works WHERE id = ANY(%s)", (mine,))}
        tags = {row["id"] for row in self.sql("SELECT id FROM prks.tags WHERE id = ANY(%s)", (mine,))}
        linked = {(row["work_id"], row["tag_id"]) for row in self.sql(
            "SELECT work_id, tag_id FROM prks.work_tags WHERE work_id = ANY(%s) OR tag_id = ANY(%s)", (mine, mine))}
        filed = {row["work_id"]: row["folder_id"] for row in self.sql(
            "SELECT work_id, folder_id FROM prks.folder_files WHERE work_id = ANY(%s)", (mine,))}
        titles = {row["id"]: row["title"] for row in self.sql(
            "SELECT id, title FROM prks.works WHERE id = ANY(%s)", (mine,))}
        revisions = self.all_revisions()
        keys = [title_key(w) for w in model.works] + [folder_key(w) for w in model.works]
        keys += [link_key(w, t) for w in model.works for t in model.tags]
        return {
            "works": works, "tags": tags, "links": linked,
            "folder_of": {w: filed.get(w, "") for w in works}, "title_of": titles,
            "revisions": {key: revisions.get(key, 0) for key in keys},
        }

    def expected(self, model: Model) -> dict[str, Any]:
        keys = [title_key(w) for w in model.works] + [folder_key(w) for w in model.works]
        keys += [link_key(w, t) for w in model.works for t in model.tags]
        return {
            "works": set(model.works), "tags": set(model.tags), "links": set(model.links),
            "folder_of": dict(model.folder_of), "title_of": dict(model.title_of),
            "revisions": {key: model.rev(key) for key in keys},
        }

    def assert_integrity(self, where: Any) -> None:
        dangling_links = self.sql(
            "SELECT l.work_id, l.tag_id FROM prks.work_tags l"
            " LEFT JOIN prks.works w ON w.library_id = l.library_id AND w.id = l.work_id"
            " LEFT JOIN prks.tags t ON t.library_id = l.library_id AND t.id = l.tag_id"
            " WHERE w.id IS NULL OR t.id IS NULL")
        self.assertEqual(dangling_links, [], where)
        dangling_files = self.sql(
            "SELECT f.work_id FROM prks.folder_files f"
            " LEFT JOIN prks.works w ON w.library_id = f.library_id AND w.id = f.work_id"
            " LEFT JOIN prks.folders d ON d.library_id = f.library_id AND d.id = f.folder_id"
            " WHERE w.id IS NULL OR d.id IS NULL")
        self.assertEqual(dangling_files, [], where)

    # -- the decision table -------------------------------------------------------------

    def decide(self, model: Model, key: tuple[str, str], current: Any, desired: Any,
               base_kind: str) -> tuple[int, tuple[Any, ...]]:
        revision = model.rev(key)
        base = {"current": revision, "stale": max(0, revision - 1), "future": revision + 1}[base_kind]
        if base > revision:
            return base, (400, "FUTURE_REVISION")
        if base < revision and current != desired:
            return base, (409, "REVISION_CONFLICT")
        return base, (200, "ACKNOWLEDGED", current != desired)

    # -- one step -------------------------------------------------------------------------

    def step(self, rng: random.Random, model: Model, every_work: list[str], every_tag: list[str]
             ) -> tuple[str, Optional[dict[str, Any]], Optional[tuple[Any, ...]], Any]:
        """Pick and build one operation; returns (label, envelope, expected answer, apply-on-ack)."""
        kind = rng.choice(("link", "link", "folder", "title", "delete_work", "delete_tag"))
        work_id, tag_id = rng.choice(every_work), rng.choice(every_tag)
        base_kind = rng.choice(BASES)
        if kind == "link":
            desired = rng.random() < 0.6
            data_op = "ADD_WORK_TAG" if desired else "REMOVE_WORK_TAG"
            label = f"{data_op} {work_id} {tag_id} {base_kind}"
            if work_id not in model.works:
                return label, self.link(data_op, work_id, tag_id, 0), (404, "ENTITY_NOT_FOUND"), None
            if tag_id not in model.tags:
                return label, self.link(data_op, work_id, tag_id, 0), (None, "TAG_DELETED"), None
            key = link_key(work_id, tag_id)
            base, expected = self.decide(model, key, (work_id, tag_id) in model.links, desired, base_kind)

            def apply_link(result: dict[str, Any]) -> None:
                (model.links.add if desired else model.links.discard)((work_id, tag_id))
                model.revisions[key] = result["server_revision"]

            return label, self.link(data_op, work_id, tag_id, base), expected, apply_link
        if kind == "folder":
            desired_folder = rng.choice(model.folders + ("",))
            label = f"SET_WORK_FOLDER {work_id} {desired_folder!r} {base_kind}"
            if work_id not in model.works:
                return label, self.envelope("SET_WORK_FOLDER", work_id, {"folder_id": desired_folder}, 0), \
                    (404, "ENTITY_NOT_FOUND"), None
            key = folder_key(work_id)
            base, expected = self.decide(model, key, model.folder_of[work_id], desired_folder, base_kind)

            def apply_folder(result: dict[str, Any]) -> None:
                model.folder_of[work_id] = desired_folder
                model.revisions[key] = result["server_revision"]

            return label, self.envelope("SET_WORK_FOLDER", work_id, {"folder_id": desired_folder}, base), \
                expected, apply_folder
        if kind == "title":
            desired_title = rng.choice(TITLES)
            label = f"SET_WORK_METADATA_FIELD title {work_id} {desired_title!r} {base_kind}"
            if work_id not in model.works:
                return label, self.title_op(work_id, desired_title, 0), (404, "ENTITY_NOT_FOUND"), None
            key = title_key(work_id)
            base, expected = self.decide(model, key, model.title_of[work_id], desired_title, base_kind)

            def apply_title(result: dict[str, Any]) -> None:
                model.title_of[work_id] = desired_title
                model.revisions[key] = result["server_revision"]

            return label, self.title_op(work_id, desired_title, base), expected, apply_title
        if kind == "delete_work":
            label = f"DELETE_WORK {work_id}"
            alive = work_id in model.works

            def apply_delete_work(_result: dict[str, Any]) -> None:
                model.works.discard(work_id)
                model.links -= {pair for pair in model.links if pair[0] == work_id}
                model.folder_of.pop(work_id, None)
                model.title_of.pop(work_id, None)

            return label, self.envelope("DELETE_WORK", work_id, {}), (200, "ACKNOWLEDGED", alive), apply_delete_work
        label = f"DELETE_TAG {tag_id}"
        alive = tag_id in model.tags

        def apply_delete_tag(_result: dict[str, Any]) -> None:
            model.tags.discard(tag_id)
            model.links -= {pair for pair in model.links if pair[1] == tag_id}

        expected_tag: tuple[Any, ...] = (200, "ACKNOWLEDGED", True) if alive else (None, None)
        return label, self.envelope("DELETE_TAG", tag_id, {}), expected_tag, apply_delete_tag

    # -- the run --------------------------------------------------------------------------

    def setup_library(self) -> Model:
        every_work = [self.library.add_work("Seed " + uuid.uuid4().hex) for _ in range(2)]
        every_tag = [self.create_tag("Seed " + uuid.uuid4().hex) for _ in range(2)]
        every_folder = tuple(self.folder("Folder " + uuid.uuid4().hex) for _ in range(2))
        model = Model(works=set(every_work), tags=set(every_tag), folders=every_folder,
                      universe=frozenset(every_work + every_tag))
        held = self.observe(model)
        model.folder_of = {w: held["folder_of"][w] for w in every_work}
        model.title_of = {w: held["title_of"][w] for w in every_work}
        model.revisions = dict(held["revisions"])
        return model

    def test_generated_sequences_follow_the_model_and_keep_the_invariants(self) -> None:
        acknowledged_changes = refusals = replays = 0
        for seed in SEEDS:
            rng = random.Random(seed)
            model = self.setup_library()
            every_work, every_tag = sorted(model.works), sorted(model.tags)
            sent: list[tuple[dict[str, Any], tuple[int, dict[str, Any]]]] = []
            steps: list[str] = []
            seen = self.all_revisions()
            for _ in range(STEPS):
                if sent and rng.random() < 0.2:
                    data, first = rng.choice(sent)
                    steps.append("replay " + data["operation"])
                    where = (seed, steps)
                    before = self.observe(model)
                    self.assertEqual(self.send(data), first, where)
                    self.assertEqual(self.observe(model), before, where)
                    replays += 1
                else:
                    label, data, expected, apply = self.step(rng, model, every_work, every_tag)
                    steps.append(label)
                    where = (seed, steps)
                    assert data is not None and expected is not None
                    scopes_before = self.revisions_of(data["entity_id"]) if data["operation"] == "DELETE_WORK" else {}
                    status, result = self.send(data)
                    if expected[0] is not None:
                        self.assertEqual(status, expected[0], (where, result))
                    if expected[1] is not None:
                        self.assertEqual(result["code"], expected[1], (where, result))
                    if len(expected) == 3:
                        self.assertEqual(result["changed"], expected[2], (where, result))
                    if status == 200 and result.get("code") == "ACKNOWLEDGED":
                        if apply is not None:
                            apply(result)
                        acknowledged_changes += int(bool(result.get("changed")))
                        if data["operation"] == "DELETE_WORK" and result["changed"]:
                            # §5.2: every existing scope of the deleted Work moves one on, none vanishes,
                            # and its fixed scopes are tombstoned at 1 if they had no row.
                            self.assertEqual(self.revisions_of(data["entity_id"]), self.tombstoned(
                                data["entity_id"], {key: rev + 1 for key, rev in scopes_before.items()}), where)
                    else:
                        refusals += 1
                    sent.append((data, (status, result)))
                self.assertEqual(self.observe(model), self.expected(model), where)
                self.assert_integrity(where)
                now = self.all_revisions()
                moved_back = {key: (old, now.get(key)) for key, old in seen.items() if now.get(key, -1) < old}
                self.assertEqual(moved_back, {}, where)
                seen = now
        # The generator must exercise both sides of the decision table, not only refusals.
        self.assertGreater(acknowledged_changes, len(SEEDS) * 3)
        self.assertGreater(refusals, len(SEEDS))
        self.assertGreater(replays, len(SEEDS))

    def test_a_replay_after_its_target_is_deleted_answers_as_the_first_time(self) -> None:
        """A named regression of the model's replay rule across a Work delete."""
        model = self.setup_library()
        work_id, tag_id = sorted(model.works)[0], sorted(model.tags)[0]
        data = self.link("ADD_WORK_TAG", work_id, tag_id, model.rev(link_key(work_id, tag_id)))
        first = self.send(data)
        self.assertEqual((first[0], first[1]["changed"]), (200, True))
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], True)
        self.assertEqual(self.send(data), first)
        self.assertEqual(self.sql("SELECT 1 FROM prks.work_tags WHERE work_id = %s", (work_id,)), [])
        self.assert_integrity("after the replay")


