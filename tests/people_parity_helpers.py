"""Shared fixture of the People parity tests (L-P-1 #632).

As in ``tests/works_parity_helpers.py``: a test does the same calls on its
leg's library object and on a reference SQLite ``PRKSDatabase`` built in the
same test, then compares the results after replacing what differs by
construction. Generated ids (Persons, Person Groups, publishers, publisher
aliases and Works) become labels the test chose, and timestamps and ETags
become their formats. On the ``postgres`` leg the left side is the
PostgreSQL facade.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from typing import Any

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.backend_parity import BackendParityTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_ID = re.compile(r"\b(PG|P|R|M|W|MF|AS)-[0-9A-F]{32}\b")
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{3})?")
_ETAG = re.compile(r'W/"prks-([a-z-]+)-(\d+)-([0-9a-f]+)"')


class PeopleSide:
    """One library object plus the labels of the ids it generated."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.labels: dict[str, str] = {}

    def label(self, entity_id: str, label: str) -> str:
        self.labels[entity_id] = label
        return entity_id

    def person(self, label: str, **fields: Any) -> str:
        fields.setdefault("first_name", "")
        fields.setdefault("last_name", label)
        return self.label(self.db.add_person(**fields), f"<P {label}>")

    def group(self, label: str, **fields: Any) -> str:
        fields.setdefault("name", label)
        return self.label(self.db.add_person_group_with_parent_options(**fields), f"<PG {label}>")

    def group_named(self, name: str) -> str:
        """The id of an existing group (one created as a parent), labelled by its name."""
        for row in self.db.get_all_person_groups():
            if row["name"] == name:
                return self.label(row["id"], f"<PG {name}>")
        raise AssertionError(f"no group named {name!r}")

    def publisher(self, label: str, name: str) -> dict[str, Any]:
        out = self.db.add_publisher(name)
        if not out["existed"]:
            self.label(out["id"], f"<R {label}>")
        return out

    def work(self, label: str, **fields: Any) -> str:
        return self.label(self.db.add_work(**fields), f"<W {label}>")

    def normal(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.normal(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [self.normal(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.normal(item) for item in value)
        if isinstance(value, str):
            text = _ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
            text = _MOMENT.sub(lambda match: "<ms>" if match.group(1) else "<s>", text)
            return _ETAG.sub(lambda match: f"<etag {match.group(1)} {match.group(2)}>", text)
        return value


def _unordered(state: Any) -> Any:
    if not isinstance(state, dict):
        return state
    return {key: sorted(value, key=str) if key in ("members", "groups") else value
            for key, value in state.items()}


class PeopleParityTestCase(BackendParityTestCase):
    """Two sides per test: ``self.leg`` (this leg's library) and ``self.ref`` (SQLite)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="prks-people-parity-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        leg_storage = StorageConfig.for_testing(os.path.join(self.tmp, "leg"))
        ref_storage = StorageConfig.for_testing(os.path.join(self.tmp, "ref"))
        self.leg = PeopleSide(self.open_library(leg_storage))
        self.ref = PeopleSide(PRKSDatabase(storage=ref_storage, schema_path=_SCHEMA_PATH))

    def sides(self) -> tuple[PeopleSide, PeopleSide]:
        return self.leg, self.ref

    def both(self, call: str, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return (getattr(self.leg.db, call)(*args, **kwargs),
                getattr(self.ref.db, call)(*args, **kwargs))

    def same(self, call: str, *args: Any, **kwargs: Any) -> Any:
        mine, theirs = self.both(call, *args, **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def same_for(self, call: str, ids: tuple[Any, Any], *args: Any, **kwargs: Any) -> Any:
        """``call(ids[side], *args)`` on each side; asserts equal results."""
        mine = getattr(self.leg.db, call)(ids[0], *args, **kwargs)
        theirs = getattr(self.ref.db, call)(ids[1], *args, **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def same_state_for(self, call: str, ids: tuple[Any, Any]) -> Any:
        """A revision state: its pair lists are ordered by generated ids, so compare them as sets."""
        mine = getattr(self.leg.db, call)(ids[0])
        theirs = getattr(self.ref.db, call)(ids[1])
        self.assertEqual(_unordered(self.leg.normal(mine)), _unordered(self.ref.normal(theirs)), call)
        return mine

    def refused_alike(self, call: str, ids: tuple[Any, ...], *args: Any, **kwargs: Any) -> str:
        """``call`` raises ``ValueError`` with one message on both sides; returns it."""
        messages = []
        for side, first in zip(self.sides(), ids or (None, None)):
            positional = (first, *args) if ids else args
            with self.assertRaises(ValueError) as caught:
                getattr(side.db, call)(*positional, **kwargs)
            messages.append(str(caught.exception))
        self.assertEqual(messages[0], messages[1], call)
        return messages[0]

    def person_both(self, label: str, **fields: Any) -> tuple[str, str]:
        return self.leg.person(label, **fields), self.ref.person(label, **fields)

    def group_both(self, label: str, **fields: Any) -> tuple[str, str]:
        return self.leg.group(label, **fields), self.ref.group(label, **fields)
