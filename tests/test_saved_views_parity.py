"""Saved Views: PostgreSQL answers as SQLite does (L-O-1 #633).

Each call runs on this leg's library and on a reference SQLite library
holding the same Saved Views, and the results must be equal after generated
ids and timestamps become labels; a refusal must carry the same message and
HTTP status. The HTTP controller is compared too, status and body. Inputs
avoid the documented difference (``backend/pg_facade/organization.py``):
names differ beyond ASCII case.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.api import saved_views as saved_views_api
from backend.db_manager import PRKS_SAVED_VIEW_MAX, SavedViewError
from tests.backend_parity import parametrize_backends
from tests.test_tags_parity import TagParityTestCase, TagSide

_SV = re.compile(r"\bSV-[0-9A-F]{32}\b")
_MISSING = "SV-" + "0" * 32
_ALL = {"mode": "all", "q": "culture", "tag": "", "author": "", "publisher": ""}
_ADVANCED = {"mode": "advanced", "q": "", "tag": "", "author": "Adorno", "publisher": "Verso"}
_TAG = {"mode": "tag", "q": "", "tag": "Ethics", "author": "", "publisher": ""}


class ViewSide(TagSide):
    """A side whose labels cover Saved Views."""

    def view(self, label: str, search: dict[str, str], name: str | None = None) -> str:
        return self.label(self.db.create_saved_view(name or label, search)["id"], f"<SV {label}>")

    def normal(self, value: Any) -> Any:
        if isinstance(value, str):
            value = _SV.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
        return super().normal(value)


class _Handler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Any]] = []

    def send_json(self, status: int, body: Any, etag: Any = None, precondition_checked: bool = False) -> None:
        self.calls.append((status, body))


class SavedViewParityTestCase(TagParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.leg, self.ref = ViewSide(self.leg.db), ViewSide(self.ref.db)

    def view_both(self, label: str, search: dict[str, str], name: str | None = None) -> tuple[str, str]:
        return self.leg.view(label, search, name), self.ref.view(label, search, name)

    def refused_same(self, call: str, *args: Any, ids: tuple[Any, Any] | None = None, **kwargs: Any) -> str:
        """``call`` refused on both sides with one message and one HTTP status."""
        outcomes = []
        for index, side in enumerate(self.sides()):
            head = () if ids is None else (ids[index],)
            with self.assertRaises(SavedViewError) as caught:
                getattr(side.db, call)(*head, *args, **kwargs)
            outcomes.append((str(caught.exception), caught.exception.http_status))
        self.assertEqual(outcomes[0], outcomes[1], call)
        return outcomes[0][0]


@parametrize_backends
class SavedViewFacadeParityTests(SavedViewParityTestCase):
    def test_create_read_update_delete_and_list(self) -> None:
        industry = self.view_both("Industry", {**_ALL, "q": "  culture "}, name="  Industry ")
        authors = self.view_both("Authors", _ADVANCED)
        tagged = self.view_both("Tagged", {**_TAG, "author": "Adorno"})
        for pair in (industry, authors, tagged):
            self.same_for("get_saved_view", pair)
        self.same_for("get_saved_view", (f" {industry[0]} ", f" {industry[1]} "))
        self.same("get_saved_view", _MISSING)
        self.same("get_saved_view", "")
        self.same("get_saved_views")
        self.same_for("update_saved_view", industry, name="Industry renamed")
        self.same_for("update_saved_view", authors, search={**_ADVANCED, "q": "dialectic", "author": ""})
        self.same_for("update_saved_view", tagged, name="TAGGED", search=_ALL)
        self.same_for("update_saved_view", tagged, name="Tagged")  # its own name in another case
        self.same("get_saved_views")
        self.same_for("delete_saved_view", authors)
        self.same_for("get_saved_view", authors)
        self.same("get_saved_views")

    def test_every_definition_shape(self) -> None:
        shapes = [_ALL, {**_ADVANCED, "author": ""}, {**_ADVANCED, "publisher": ""},
                  {**_ADVANCED, "q": "x", "author": "", "publisher": ""}, _TAG,
                  {**_TAG, "author": "A", "publisher": "P"}, {**_TAG, "mode": " tag ", "q": "  "}]
        for index, search in enumerate(shapes):
            with self.subTest(search=search):
                pair = self.view_both(f"Shape {index}", search)
                self.same_for("get_saved_view", pair)
        self.same("get_saved_views")

    def test_refusals_carry_sqlites_message_and_status(self) -> None:
        kept = self.view_both("Kept", _ALL)
        other = self.view_both("Other", _TAG)
        invalid: list[tuple[Any, Any]] = [
            (None, _ALL), ("  ", _ALL), ("a\x00b", _ALL), ("x" * 81, _ALL), ("Ok", None),
            ("Ok", {"mode": "all"}), ("Ok", {**_ALL, "mode": "fuzzy"}), ("Ok", {**_ALL, "q": 1}),
            ("Ok", {**_ALL, "q": "a\x7fb"}), ("Ok", {**_ALL, "q": "x" * 501}), ("Ok", {**_ALL, "author": "A"}),
            ("Ok", {**_ADVANCED, "tag": "T"}), ("Ok", {**_ADVANCED, "author": "", "publisher": ""}),
            ("Ok", {**_TAG, "q": "x"}),
        ]
        for name, search in invalid:
            with self.subTest(name=str(name)[:10], search=search):
                self.refused_same("create_saved_view", name, search)
                if name is not None and search is not None:
                    self.refused_same("update_saved_view", ids=kept, name=name if name != "Ok" else None,
                                      search=search if name == "Ok" else None)
        self.refused_same("create_saved_view", "KEPT", _ALL)  # 409
        self.refused_same("update_saved_view", ids=other, name="kept")  # 409
        self.refused_same("update_saved_view", ids=other, name="kept", search=_ALL)  # 409, nothing written
        self.refused_same("update_saved_view", ids=kept)  # 400 nothing to update
        for view_id in ("", "  ", _MISSING, "SV-missing"):
            with self.subTest(view_id=view_id):
                self.refused_same("update_saved_view", view_id, name="X")
                self.refused_same("delete_saved_view", view_id)
        self.refused_same("update_saved_view", "")
        self.refused_same("update_saved_view", _MISSING)
        self.same("get_saved_views")

    def test_the_limit(self) -> None:
        for index in range(PRKS_SAVED_VIEW_MAX):
            self.view_both(f"View {index:03d}", _ALL)
        self.refused_same("create_saved_view", "One more", _ALL)
        self.refused_same("create_saved_view", "view 000", _ALL)  # the limit before the name
        self.same("get_saved_views")

    def test_the_list_order(self) -> None:
        for name in ("beta", "Alpha", "alpha two", "Zeta", "_under", "Ärger", "ångström", "10", "9", "a-b"):
            self.view_both(name, _ALL)
        self.same("get_saved_views")

    def test_the_http_controller_answers_alike(self) -> None:
        handlers = (_Handler(), _Handler())
        created: list[str] = []

        def run(method: str, path: str, *body: Any) -> int:
            for index, (side, handler) in enumerate(zip(self.sides(), handlers)):
                target = path.replace("<id>", created[index]) if created else path
                self.assertTrue(getattr(saved_views_api, f"handle_{method}")(handler, side.db, target, *body))
            mine, theirs = handlers[0].calls[-1], handlers[1].calls[-1]
            self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), (method, path))
            return mine[0]

        self.assertEqual(run("post", "/api/saved-views", {"name": "Industry", "search": _ADVANCED}), 201)
        for side, handler in zip(self.sides(), handlers):
            created.append(side.label(handler.calls[-1][1]["id"], "<SV Industry>"))
        statuses = [
            run("get", "/api/saved-views/<id>"),
            run("patch", "/api/saved-views/<id>", {"name": "Renamed"}),
            run("patch", "/api/saved-views/<id>", {"search": _TAG}),
            run("patch", "/api/saved-views/<id>", {"name": None, "search": None}),
            run("patch", "/api/saved-views/<id>", {}),
            run("get", "/api/saved-views"),
            run("post", "/api/saved-views", {"name": "renamed", "search": _ALL}),
            run("post", "/api/saved-views", {"name": "", "search": _ALL}),
            run("post", "/api/saved-views", ["not", "object"]),
            run("patch", f"/api/saved-views/{_MISSING}", {"name": "Nope"}),
            run("get", f"/api/saved-views/{_MISSING}"),
            run("delete", f"/api/saved-views/{_MISSING}"),
            run("delete", "/api/saved-views/<id>"),
            run("get", "/api/saved-views/<id>"),
            run("get", "/api/saved-views"),
        ]
        self.assertEqual(statuses[-4:], [404, 200, 404, 200])
        self.assertIn(409, statuses)
