"""The application settings: PostgreSQL answers as SQLite does (L-O-1 #633).

Each call runs on this leg's library and on a reference SQLite library
holding the same settings, and the results must be equal; a refusal must
carry the same message. ``/api/settings`` is compared too, through the
route's own steps in ``backend/server.py`` (status and body). Inputs avoid
the documented difference (``backend/pg_facade/organization.py``): no
patch pairs a valid ``annotation_author`` with a refused
``bibtex_export_fields``, which SQLite half-commits and PostgreSQL refuses
whole.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_BIBTEX_EXPORT_FIELD_IDS
from tests.backend_parity import parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase


def _settings_route(db: Any, method: str, data: Any = None) -> tuple[int, Any]:
    """``/api/settings`` as ``backend/server.py`` serves it (GET, and PATCH's body checks)."""
    if method == "GET":
        return 200, db.get_app_settings_response()
    if not isinstance(data, dict):
        return 400, {"error": "JSON object body required"}
    try:
        db.patch_app_settings(data)
    except ValueError as error:
        return 400, {"error": str(error)}
    return 200, db.get_app_settings_response()


@parametrize_backends
class SettingsFacadeParityTests(PeopleParityTestCase):
    def refused_same(self, data: Any) -> str:
        messages = []
        for side in self.sides():
            with self.assertRaises(ValueError) as caught:
                side.db.patch_app_settings(data)
            messages.append(str(caught.exception))
        self.assertEqual(messages[0], messages[1], data)
        return messages[0]

    def test_defaults_and_the_annotation_author(self) -> None:
        self.same("get_app_settings_response")
        self.same("get_app_settings_map")
        for value in ("  Dr. Ada ", "", None, 42, "é" * 500, "Λόγος"):
            with self.subTest(value=value):
                self.same("patch_app_settings", {"annotation_author": value})
                self.same("get_app_settings_response")
        self.same("get_app_settings_map")

    def test_complete_and_partial_profile_patches(self) -> None:
        patches = [{"isbn": False, "location": False}, {"doi": False}, {"isbn": True}, {},
                   {field: False for field in PRKS_BIBTEX_EXPORT_FIELD_IDS},
                   {field: True for field in PRKS_BIBTEX_EXPORT_FIELD_IDS[:5]}]
        for patch in patches:
            with self.subTest(patch=patch):
                self.same("patch_app_settings", {"bibtex_export_fields": patch})
                self.same("get_app_settings_response")
        self.same("patch_app_settings", {"annotation_author": "Ada", "bibtex_export_fields": {"url": False}})
        self.same("get_app_settings_response")
        self.same("get_app_settings_map")

    def test_unknown_keys_and_refusals(self) -> None:
        self.same("patch_app_settings", {"theme": "dark"})
        self.same("patch_app_settings", {"theme": "dark", "annotation_author": "Ada"})
        self.same("get_app_settings_map")
        for data in ([], "x", None, {"annotation_author": "x" * 501},
                     {"bibtex_export_fields": []}, {"bibtex_export_fields": None},
                     {"bibtex_export_fields": {"not_a_field": False}},
                     {"bibtex_export_fields": {"isbn": "no"}}, {"bibtex_export_fields": {"isbn": 1}},
                     {"bibtex_export_fields": {"isbn": False, "nope": True}}):
            with self.subTest(data=str(data)[:40]):
                self.refused_same(data)
        self.same("get_app_settings_response")
        self.same("get_app_settings_map")

    def test_the_settings_route_answers_alike(self) -> None:
        steps: list[tuple[str, Any]] = [
            ("GET", None),
            ("PATCH", {"annotation_author": "  Ada  "}),
            ("PATCH", {"bibtex_export_fields": {"doi": False}}),
            ("PATCH", {"bibtex_export_fields": {"isbn": False}}),
            ("PATCH", {}),
            ("PATCH", ["not", "object"]),
            ("PATCH", {"annotation_author": "x" * 501}),
            ("PATCH", {"bibtex_export_fields": {"doi": "yes"}}),
            ("PATCH", {"bibtex_export_fields": {"unknown": True}}),
            ("GET", None),
        ]
        statuses = []
        for method, data in steps:
            with self.subTest(method=method, data=str(data)[:40]):
                mine, theirs = (_settings_route(side.db, method, data) for side in self.sides())
                self.assertEqual(mine, theirs)
                statuses.append(mine[0])
        self.assertEqual(statuses, [200, 200, 200, 200, 200, 400, 400, 400, 400, 200])
