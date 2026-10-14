"""Constraints of the PG-2 core tables (#624, §6.2, §6.6, §12.1)."""

from __future__ import annotations

import uuid

import psycopg

from tests.pg.harness import PostgresTestCase, fetch_one

ROOT_ID = "SR-" + "a" * 32
LIB = "LIB-" + "1" * 32
USER = "U-" + "2" * 32


class CoreSchemaTests(PostgresTestCase):
    def setUp(self) -> None:
        self.conn = self.connect()
        self.addCleanup(self.conn.rollback)

    def refused(self, error: type[psycopg.Error], statement: str, params: tuple[object, ...] = ()) -> None:
        with self.assertRaises(error):
            with self.conn.transaction():
                self.conn.execute(statement.encode(), params)

    def instance(self, instance_id: str) -> None:
        self.conn.execute(
            "INSERT INTO prks.instance (id, storage_root_id, setup_state, testing) "
            "VALUES (%s, %s, 'bootstrapping', true)",
            (instance_id, ROOT_ID),
        )

    def library_and_owner(self) -> None:
        self.conn.execute("INSERT INTO prks.libraries (id, name) VALUES (%s, 'L')", (LIB,))
        self.conn.execute(
            "INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, 'owner', 'Owner')",
            (USER,),
        )
        self.conn.execute(
            "INSERT INTO prks.library_memberships VALUES (%s, %s, 'owner')", (LIB, USER)
        )

    def test_instance_is_a_singleton_with_start_pairing_columns(self) -> None:
        self.instance("IN-" + "A" * 32)
        row = fetch_one(
            self.conn.execute(
                "SELECT start_generation, start_token, prev_start_token FROM prks.instance"
            )
        )
        self.assertEqual(row, (0, None, None))
        self.conn.execute(
            "UPDATE prks.instance SET start_generation = 1, start_token = 't1', prev_start_token = NULL"
        )
        self.refused(
            psycopg.errors.UniqueViolation,
            "INSERT INTO prks.instance (id, storage_root_id, setup_state, testing) "
            "VALUES (%s, %s, 'ready', true)",
            ("IN-" + "B" * 32, ROOT_ID),
        )
        self.refused(psycopg.errors.CheckViolation, "UPDATE prks.instance SET start_generation = -1")
        self.refused(psycopg.errors.CheckViolation, "UPDATE prks.instance SET setup_state = 'serving'")

    def test_id_formats_are_checked(self) -> None:
        for statement, params in (
            ("INSERT INTO prks.instance (id, storage_root_id, setup_state, testing) "
             "VALUES (%s, %s, 'ready', true)", ("IN-" + "a" * 32, ROOT_ID)),
            ("INSERT INTO prks.instance (id, storage_root_id, setup_state, testing) "
             "VALUES (%s, %s, 'ready', true)", ("IN-" + "A" * 32, "SR-short")),
            ("INSERT INTO prks.libraries (id, name) VALUES (%s, 'L')", ("LIB-" + "1" * 8,)),
            ("INSERT INTO prks.users (id, login_name, display_name) VALUES (%s, 'x', 'x')", ("W-" + "1" * 32,)),
        ):
            with self.subTest(params=params):
                self.refused(psycopg.errors.CheckViolation, statement, params)

    def test_session_needs_and_follows_its_membership(self) -> None:
        self.library_and_owner()
        insert = (
            "INSERT INTO prks.sessions (token_sha256, user_id, library_id, expires_at) "
            "VALUES (%s, %s, %s, now() + interval '1 day')"
        )
        self.refused(psycopg.errors.ForeignKeyViolation, insert, (b"x" * 32, "U-" + "9" * 32, LIB))
        self.conn.execute(insert.encode(), (b"t" * 32, USER, LIB))
        session_id = fetch_one(self.conn.execute("SELECT id FROM prks.sessions"))[0]
        self.assertEqual(session_id.version, 7)  # uuidv7()
        self.conn.execute("DELETE FROM prks.library_memberships")
        self.assertEqual(fetch_one(self.conn.execute("SELECT count(*) FROM prks.sessions"))[0], 0)

    def test_job_epoch_follows_library_and_queued_dedupe_is_unique(self) -> None:
        self.library_and_owner()
        self.refused(
            psycopg.errors.CheckViolation,
            "INSERT INTO prks.jobs (library_id, kind) VALUES (%s, 'search.rebuild')",
            (LIB,),
        )
        self.refused(
            psycopg.errors.CheckViolation,
            "INSERT INTO prks.jobs (library_epoch, kind) VALUES (1, 'jobs.prune')",
        )
        insert = (
            "INSERT INTO prks.jobs (library_id, library_epoch, kind, dedupe_key, state) "
            "VALUES (%s, 1, 'search.rebuild', 'all', %s)"
        )
        self.conn.execute(insert.encode(), (LIB, "queued"))
        self.conn.execute(insert.encode(), (LIB, "running"))  # a running twin is allowed
        self.refused(psycopg.errors.UniqueViolation, insert, (LIB, "queued"))
        self.refused(psycopg.errors.CheckViolation, insert, (LIB, "paused"))

    def test_one_running_job_per_singleton_key(self) -> None:
        insert = "INSERT INTO prks.jobs (kind, singleton_key, state) VALUES ('objects.sweep', 'sweep', %s)"
        self.conn.execute(insert.encode(), ("running",))
        self.conn.execute(insert.encode(), ("queued",))
        self.refused(psycopg.errors.UniqueViolation, insert, ("running",))

    def test_sync_ledger_references_device_user_and_library(self) -> None:
        self.library_and_owner()
        device = uuid.uuid4()
        self.conn.execute("INSERT INTO prks.devices (id, user_id) VALUES (%s, %s)", (device, USER))
        insert = (
            "INSERT INTO prks.sync_operations (op_id, library_id, device_id, user_id, operation_type, "
            "entity_type, entity_id, request_sha256, status, http_status, result) "
            "VALUES (%s, %s, %s, %s, 'CREATE_TAG', 'tag', 'T-1', %s, 'applied', 200, '{}')"
        )
        self.conn.execute(insert.encode(), (uuid.uuid4(), LIB, device, USER, b"h" * 32))
        self.refused(
            psycopg.errors.ForeignKeyViolation, insert, (uuid.uuid4(), LIB, uuid.uuid4(), USER, b"h" * 32)
        )
        self.conn.execute(
            "INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision) "
            "VALUES (%s, 'tag', '[\"T-1\"]', 0)",
            (LIB,),
        )
        self.refused(
            psycopg.errors.UniqueViolation,
            "INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision) "
            "VALUES (%s, 'tag', '[\"T-1\"]', 1)",
            (LIB,),
        )
        self.refused(
            psycopg.errors.CheckViolation,
            "UPDATE prks.entity_revisions SET revision = -1",
        )
