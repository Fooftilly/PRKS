"""Shared fixtures for the login, limiter and session tests (L-ID-1 #626, PR 3/5)."""

from __future__ import annotations

import io
import logging
import threading
import unittest
import uuid
from typing import Any, Optional

from argon2 import PasswordHasher, extract_parameters

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import login as login_module
from backend.domain.identity import passwords
from backend.domain.identity.login_limiter import ClientAddress
from tests.pg.harness import PostgresHarness, get_harness

PASSWORD = "a long enough passphrase"
CLIENT = ClientAddress.resolve("192.0.2.10")


class CountingHasher(PasswordHasher):
    """A fast Argon2id hasher that records every verification and its parameters."""

    def __init__(self, time_cost: int = 1) -> None:
        super().__init__(time_cost=time_cost, memory_cost=8, parallelism=1)
        self._lock = threading.Lock()
        self.verified: list[tuple[int, int, int]] = []
        self.on_verify: Optional[Any] = None

    @property
    def verifies(self) -> int:
        with self._lock:
            return len(self.verified)

    def verify(self, hash: str | bytes, password: str | bytes) -> bool:  # noqa: A002 - base name
        params = extract_parameters(hash if isinstance(hash, str) else hash.decode("ascii"))
        with self._lock:
            self.verified.append((params.time_cost, params.memory_cost, params.parallelism))
        result = super().verify(hash, password)
        if self.on_verify is not None:
            self.on_verify()
        return result


def params_of(hasher: PasswordHasher) -> tuple[int, int, int]:
    return (hasher.time_cost, hasher.memory_cost, hasher.parallelism)


class IdentityTestCase(unittest.TestCase):
    """A fresh migrated database per test, plus helpers to make accounts."""

    harness: PostgresHarness

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.harness = get_harness()

    def setUp(self) -> None:
        super().setUp()
        self.name = self.harness.create_database("login")
        self.addCleanup(self.harness.drop_database, self.name)
        self.db = self.open_db()
        self.hasher = CountingHasher()
        # Warm the dummy hash so counted verifications are the login's own.
        login_module.dummy_hash(self.hasher)
        self.logs = io.StringIO()
        handler = logging.StreamHandler(self.logs)
        handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        previous = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        self.addCleanup(root_logger.removeHandler, handler)
        self.addCleanup(root_logger.setLevel, previous)

    def open_db(self, pool_max: int = 4) -> Database:
        db = Database(
            DatabaseConfig(
                conninfo=self.harness.conninfo(self.name), role="test", pool_min=0, pool_max=pool_max
            )
        )
        self.addCleanup(db.close)
        return db

    def run_sql(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        def run(tx: Any) -> list[dict[str, Any]]:
            cursor = tx.execute(query, params)
            return cursor.fetchall() if cursor.description is not None else []

        return self.db.command(run)

    def library(self, name: str = "Library") -> str:
        library_id = "LIB-" + uuid.uuid4().hex.upper()
        self.run_sql("INSERT INTO prks.libraries (id, name) VALUES (%s, %s)", (library_id, name))
        return library_id

    def user(
        self,
        login_name: str = "Owner",
        *,
        password: Optional[str] = PASSWORD,
        password_hash: Optional[str] = None,
        library: Optional[str] = "new",
        role: str = "owner",
        disabled: bool = False,
        hasher: Optional[PasswordHasher] = None,
    ) -> str:
        user_id = "U-" + uuid.uuid4().hex.upper()
        if password_hash is None and password is not None:
            password_hash = passwords.hash_password(password, hasher=hasher or self.hasher)
        self.run_sql(
            "INSERT INTO prks.users (id, login_name, display_name, password_hash, disabled_at) "
            "VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)",
            (user_id, login_name, login_name, password_hash, disabled),
        )
        if library is not None:
            library_id = self.library() if library == "new" else library
            self.join(user_id, library_id, role)
        return user_id

    def join(self, user_id: str, library_id: str, role: str = "editor") -> None:
        self.run_sql(
            "INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, %s)",
            (library_id, user_id, role),
        )

    def attempts(self) -> dict[str, int]:
        return {
            row["key"]: row["attempts"]
            for row in self.run_sql("SELECT key, attempts FROM prks.login_attempts")
        }

    def login(self, login_name: object = "Owner", password: object = PASSWORD, **kwargs: Any
              ) -> login_module.LoginResult:
        kwargs.setdefault("client", CLIENT)
        kwargs.setdefault("hasher", self.hasher)
        return login_module.login(self.db, login_name, password, **kwargs)

    def everything_stored(self) -> str:
        """Every row of every identity table, as text."""
        parts = []
        for table in ("users", "sessions", "login_attempts", "login_device_tokens", "library_memberships"):
            for row in self.run_sql(f"SELECT row_to_json(t)::text AS j FROM prks.{table} t"):  # noqa: S608 - fixed names
                parts.append(row["j"])
        return "\n".join(parts)
