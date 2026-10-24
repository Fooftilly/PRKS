"""The application settings on PostgreSQL: what parity cannot show (L-O-1 #633).

``tests/test_settings_parity.py`` proves the facade answers as SQLite does.
These tests cover what only PostgreSQL has: settings stored per Library,
never instance settings, the ``ADMIN_LIBRARY`` gate, atomic patches,
persistence across connections, and concurrent patches of the BibTeX
export profile (each race holds one transaction open until the other is
waiting on it).
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

from backend.db import Database, DatabaseConfig
from backend.db_manager import PRKS_BIBTEX_EXPORT_FIELD_IDS
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.request_context import RequestContext
from backend.domain.settings import app_settings
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from tests.pg.facade_context import provision_actor
from tests.pg.test_people_facade import PeopleFacadeTestCase

_ALL_ON = {field: True for field in PRKS_BIBTEX_EXPORT_FIELD_IDS}
_DEFAULTS = {"annotation_author": "", "bibtex_export_fields": _ALL_ON}


class SettingsTestCase(PeopleFacadeTestCase):
    def response(self) -> dict[str, Any]:
        return self.library.get_app_settings_response()

    def profile(self) -> dict[str, bool]:
        return self.response()["bibtex_export_fields"]

    def stored(self) -> dict[str, str]:
        rows = self.sql("SELECT key, value FROM prks.library_settings WHERE library_id = %s",
                        (self.actor.library_id,))
        return {row["key"]: row["value"] for row in rows}

    def member_context(self, role: str) -> RequestContext:
        """A member of this test's Library with ``role``, signed in."""
        from backend.domain.identity import sessions

        member = provision_actor(self.db, role=role)
        self.sql("INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, %s)",
                 (self.actor.library_id, member.user_id, role))
        new = self.db.command(lambda tx: sessions.create_session(
            tx, user_id=member.user_id, library_id=self.actor.library_id))
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return RequestContext.for_session(valid, epoch=1, device_id=member.context.device_id)

    def refused(self, message: str, data: Any) -> None:
        with self.assertRaises(ValueError) as caught:
            self.library.patch_app_settings(data)
        self.assertEqual(str(caught.exception), message)


class ReadAndPatchTests(SettingsTestCase):
    def test_a_new_library_answers_the_defaults(self) -> None:
        self.assertEqual(self.response(), _DEFAULTS)
        self.assertEqual(self.library.get_app_settings_map(), {})
        self.assertEqual(self.stored(), {})

    def test_the_annotation_author(self) -> None:
        self.library.patch_app_settings({"annotation_author": "  Dr. Ada  "})
        self.assertEqual(self.response()["annotation_author"], "Dr. Ada")
        self.library.patch_app_settings({"annotation_author": None})
        self.assertEqual(self.response()["annotation_author"], "")
        self.library.patch_app_settings({"annotation_author": 42})  # SQLite's str()
        self.assertEqual(self.response()["annotation_author"], "42")
        self.library.patch_app_settings({"annotation_author": "é" * 500})  # characters, not bytes
        self.assertEqual(self.response()["annotation_author"], "é" * 500)
        self.assertEqual(self.library.get_app_settings_map(), {"annotation_author": "é" * 500})

    def test_complete_and_partial_profile_patches_merge(self) -> None:
        self.library.patch_app_settings({"bibtex_export_fields": {"isbn": False, "location": False}})
        self.assertEqual(self.profile(), {**_ALL_ON, "isbn": False, "location": False})
        self.library.patch_app_settings({"bibtex_export_fields": {"doi": False}})
        self.assertEqual(self.profile(), {**_ALL_ON, "isbn": False, "location": False, "doi": False})
        self.library.patch_app_settings({"bibtex_export_fields": {"isbn": True}})
        self.assertEqual(self.profile(), {**_ALL_ON, "location": False, "doi": False})
        every_off = {field: False for field in PRKS_BIBTEX_EXPORT_FIELD_IDS}
        self.library.patch_app_settings({"bibtex_export_fields": every_off})
        self.assertEqual(self.profile(), every_off)
        self.library.patch_app_settings({"bibtex_export_fields": {}})
        self.assertEqual(self.profile(), every_off)
        # Stored as SQLite stores it: compact JSON, keys sorted.
        self.assertEqual(self.stored()["bibtex_export_fields"],
                         json.dumps(every_off, separators=(",", ":"), sort_keys=True))

    def test_a_stored_profile_sqlite_would_ignore_reads_as_its_defaults(self) -> None:
        for raw, expected in (("{bad", _ALL_ON), ("[]", _ALL_ON), ('{"isbn": "no", "x": false}', _ALL_ON),
                              ('{"doi": false}', {**_ALL_ON, "doi": False})):
            with self.subTest(raw=raw):
                self.sql("INSERT INTO prks.library_settings (library_id, key, value) VALUES (%s, %s, %s)"
                         " ON CONFLICT (library_id, key) DO UPDATE SET value = EXCLUDED.value",
                         (self.actor.library_id, "bibtex_export_fields", raw))
                self.assertEqual(self.profile(), expected)
        self.library.patch_app_settings({"bibtex_export_fields": {"isbn": False}})
        self.assertEqual(self.profile(), {**_ALL_ON, "doi": False, "isbn": False})

    def test_unknown_keys_are_ignored(self) -> None:
        self.library.patch_app_settings({"theme": "dark", "secret": "x"})
        self.assertEqual(self.stored(), {})
        self.library.patch_app_settings({"theme": "dark", "annotation_author": "Ada"})
        self.assertEqual(self.stored(), {"annotation_author": "Ada"})
        self.assertEqual(self.response(), {**_DEFAULTS, "annotation_author": "Ada"})

    def test_invalid_values_are_refused_with_sqlites_messages(self) -> None:
        for data, message in (
            ([], "invalid body"), ("x", "invalid body"), (None, "invalid body"),
            ({"annotation_author": "x" * 501}, "annotation_author too long"),
            ({"annotation_author": "a\x00b"}, "annotation_author cannot contain NUL"),
            ({"bibtex_export_fields": []}, "bibtex_export_fields must be an object"),
            ({"bibtex_export_fields": None}, "bibtex_export_fields must be an object"),
            ({"bibtex_export_fields": {"not_a_field": False}}, "unknown bibtex_export_fields key: not_a_field"),
            ({"bibtex_export_fields": {"isbn": "no"}}, "bibtex_export_fields.isbn must be boolean"),
            ({"bibtex_export_fields": {"isbn": 0}}, "bibtex_export_fields.isbn must be boolean"),
        ):
            with self.subTest(data=str(data)[:40]):
                self.refused(message, data)
        self.assertEqual(self.stored(), {})


class AtomicityTests(SettingsTestCase):
    def test_a_refused_key_leaves_the_others_unwritten(self) -> None:
        self.library.patch_app_settings({"annotation_author": "Kept", "bibtex_export_fields": {"doi": False}})
        before = self.response()
        for data in ({"annotation_author": "Lost", "bibtex_export_fields": {"doi": True, "nope": True}},
                     {"annotation_author": "Lost", "bibtex_export_fields": "all"},
                     {"annotation_author": "x" * 501, "bibtex_export_fields": {"doi": True}}):
            with self.subTest(data=str(data)[:50]), self.assertRaises(ValueError):
                self.library.patch_app_settings(data)
        self.assertEqual(self.response(), before)

    def test_a_failure_after_a_write_rolls_the_patch_back(self) -> None:
        with mock.patch.object(app_settings, "_merge_profile", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.library.patch_app_settings({"annotation_author": "Lost",
                                                 "bibtex_export_fields": {"doi": False}})
        self.assertEqual(self.stored(), {})
        self.library.patch_app_settings({"bibtex_export_fields": {"doi": False}})
        with mock.patch.object(app_settings, "_SET_SQL", "UPDATE prks.library_settings SET value = 'x'"
                               " WHERE library_id = %(library_id)s AND key = %(key)s AND 1/0 = 1"):
            with self.assertRaises(Exception):
                self.library.patch_app_settings({"bibtex_export_fields": {"isbn": False}})
        self.assertEqual(self.profile(), {**_ALL_ON, "doi": False})


class IsolationAndAccessTests(SettingsTestCase):
    def test_each_library_has_its_own_settings_and_instance_settings_stay_out(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.library.patch_app_settings({"annotation_author": "Theirs",
                                             "bibtex_export_fields": {"doi": False}})
        self.sql("INSERT INTO prks.instance_settings (key, value) VALUES ('smtp_password', 'hunter2')")
        self.assertEqual(self.response(), _DEFAULTS)
        self.assertEqual(self.library.get_app_settings_map(), {})
        self.library.patch_app_settings({"annotation_author": "Mine"})
        with facade_context.acting_as(other.context):
            self.assertEqual(self.response(), {"annotation_author": "Theirs",
                                               "bibtex_export_fields": {**_ALL_ON, "doi": False}})
            self.assertEqual(set(self.library.get_app_settings_map()), {"annotation_author", "bibtex_export_fields"})

    def test_readers_and_editors_read_but_only_an_owner_patches(self) -> None:
        self.library.patch_app_settings({"annotation_author": "Owner"})
        for role in ("reader", "editor"):
            with self.subTest(role=role), facade_context.acting_as(self.member_context(role)):
                self.assertEqual(self.response()["annotation_author"], "Owner")
                self.assertEqual(self.library.get_app_settings_map(), {"annotation_author": "Owner"})
                for data in ({"annotation_author": "Changed"}, {"bibtex_export_fields": {"doi": False}},
                             {"unknown": 1}, []):
                    with self.assertRaises(AccessRefused) as caught:
                        self.library.patch_app_settings(data)
                    self.assertEqual(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.stored(), {"annotation_author": "Owner"})

    def test_settings_persist_across_connections_and_restarts(self) -> None:
        self.library.patch_app_settings({"annotation_author": "Ada", "bibtex_export_fields": {"doi": False}})
        expected = self.response()
        restarted = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test",
                                            pool_min=0, pool_max=2))
        self.addCleanup(restarted.close)
        self.assertEqual(PostgresLibrary(restarted, storage=self.storage).get_app_settings_response(), expected)


class RaceTests(SettingsTestCase):
    def test_two_first_patches_of_different_fields_both_land(self) -> None:
        holder, contender = self.race(
            lambda scope: app_settings.patch(scope, {"bibtex_export_fields": {"isbn": False}}),
            lambda: self.library.patch_app_settings({"bibtex_export_fields": {"doi": False}}))
        self.assertEqual((holder, contender), (None, None))
        self.assertEqual(self.profile(), {**_ALL_ON, "isbn": False, "doi": False})

    def test_two_patches_of_a_stored_profile_both_land(self) -> None:
        self.library.patch_app_settings({"bibtex_export_fields": {"url": False}})
        holder, contender = self.race(
            lambda scope: app_settings.patch(scope, {"bibtex_export_fields": {"isbn": False, "url": True}}),
            lambda: self.library.patch_app_settings({"bibtex_export_fields": {"doi": False}}))
        self.assertEqual((holder, contender), (None, None))
        self.assertEqual(self.profile(), {**_ALL_ON, "isbn": False, "doi": False})

    def test_a_patch_of_one_field_waits_and_keeps_the_others_last_write(self) -> None:
        holder, contender = self.race(
            lambda scope: app_settings.patch(scope, {"bibtex_export_fields": {"isbn": False}}),
            lambda: self.library.patch_app_settings({"bibtex_export_fields": {"isbn": True, "doi": False}}))
        self.assertEqual((holder, contender), (None, None))
        self.assertEqual(self.profile(), {**_ALL_ON, "doi": False})  # the later write of isbn wins
