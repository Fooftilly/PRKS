"""Spell out a domain answer in a test: readable labels for what a run makes up.

A test names the entities it creates (``labels.name(tag_id, "<T Ethics>")``).
:meth:`Labels.show` then rewrites an answer so it can be written out
literally: each named id becomes its label, an id the test did not name
becomes ``<unknown id>``, a timestamp becomes ``<s>`` (seconds) or ``<ms>``
(milliseconds), and a weak ETag becomes ``<etag kind count>``.

:func:`assert_answer` compares such an answer with the expected literal. A
dict in the expected value lists the keys the test checks: the answer must
hold each of them with the expected value, and may hold more, so a new field
on a row does not break every test that spells the row out.
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from typing import Any

_ID = re.compile(r"\b(?:[A-Z]{1,3})-[0-9A-F]{32}\b")
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d{3,6})?(?:Z|[+-]\d\d:?\d\d)?")
_ETAG = re.compile(r'W/"prks-([a-z-]+)-(\d+)-([0-9a-f]+)"')


class Labels:
    """The labels a test gave the ids its run generated."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}

    def name(self, entity_id: str, label: str) -> str:
        """Label ``entity_id``; returns it, so ``x = labels.name(create(...), "<T x>")`` reads."""
        self.labels[entity_id] = label
        return entity_id

    def show(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self.show(key): self.show(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.show(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.show(item) for item in value)
        if isinstance(value, str):
            text = _ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
            text = _MOMENT.sub(lambda match: "<ms>" if match.group(1) else "<s>", text)
            return _ETAG.sub(lambda match: f"<etag {match.group(1)} {match.group(2)}>", text)
        return value


def sort_keys(value: Any, keys: frozenset[str] | set[str]) -> Any:
    """``value`` with the lists under ``keys`` sorted, at any depth: id lists come in id order, which a run makes up."""
    if isinstance(value, dict):
        return {key: sorted(sort_keys(item, keys), key=repr) if key in keys and isinstance(item, list)
                else sort_keys(item, keys) for key, item in value.items()}
    if isinstance(value, list):
        return [sort_keys(item, keys) for item in value]
    if isinstance(value, tuple):
        return tuple(sort_keys(item, keys) for item in value)
    return value


def _mismatch(actual: Any, expected: Any, path: str) -> str | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: expected a dict, got {actual!r}"
        for key, item in expected.items():
            if key not in actual:
                return f"{path}: missing key {key!r}"
            found = _mismatch(actual[key], item, f"{path}[{key!r}]")
            if found:
                return found
        return None
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, type(expected)) or len(actual) != len(expected):
            return f"{path}: expected {expected!r}, got {actual!r}"
        for index, (got, want) in enumerate(zip(actual, expected)):
            found = _mismatch(got, want, f"{path}[{index}]")
            if found:
                return found
        return None
    if actual != expected or type(actual) is not type(expected):
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def assert_answer(test: unittest.TestCase, actual: Any, expected: Any, msg: str = "") -> None:
    """``actual`` holds ``expected``: equal, except that dicts may carry keys ``expected`` leaves out."""
    found = _mismatch(actual, expected, "answer")
    if found:
        test.fail(f"{msg}: {found}" if msg else found)


class AnswerAssertions(unittest.TestCase):
    """Mixin: ``self.labels`` and the assertions that read answers through it."""

    # Answer keys holding id lists: their order follows the generated ids.
    id_lists: frozenset[str] = frozenset({
        "affected_work_ids", "affected_folder_ids", "affected_tag_options_work_ids",
        "affected_tag_options_folder_ids"})

    def setUp(self) -> None:
        super().setUp()
        self.labels = Labels()

    def answer(self, value: Any, expected: Any, msg: str = "") -> None:
        assert_answer(self, sort_keys(self.labels.show(value), self.id_lists), expected, msg)

    def assert_refused(self, call: Callable[[], Any], message: str, error: type[Exception] = ValueError) -> None:
        with self.assertRaises(error) as caught:
            call()
        self.assertEqual(str(caught.exception), message)
