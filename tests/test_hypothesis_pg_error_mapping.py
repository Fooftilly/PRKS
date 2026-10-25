"""Bounded model/property checks for PostgreSQL error safety (#391, #651).

No PostgreSQL server is required: deliberately synthetic psycopg errors
exercise the production mapper. The real-server tests in
tests/pg/test_db_errors.py remain the integration authority.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import psycopg
from hypothesis import given, settings, strategies as st

from backend.db.errors import (
    CHECK_VIOLATION,
    DEADLOCK_DETECTED,
    FOREIGN_KEY_VIOLATION,
    LOCK_NOT_AVAILABLE,
    QUERY_CANCELED,
    READ_ONLY_SQL_TRANSACTION,
    SERIALIZATION_FAILURE,
    UNIQUE_VIOLATION,
    ConstraintRegistry,
    ConstraintViolationError,
    DatabaseError,
    RetryTransactionError,
    UnmappedDatabaseError,
    check_constraint_field,
    is_retryable,
    map_database_error,
    sqlstate_of,
)

EXAMPLES = 36
PRIVATE_MESSAGE = "confidential-work-title-and-secret-connection-details"
SYMBOLS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Include every documented special state, not just extremely rare random hits.
STATES = st.one_of(
    st.sampled_from((
        SERIALIZATION_FAILURE, DEADLOCK_DETECTED, LOCK_NOT_AVAILABLE,
        QUERY_CANCELED, UNIQUE_VIOLATION, FOREIGN_KEY_VIOLATION,
        CHECK_VIOLATION, READ_ONLY_SQL_TRANSACTION,
        "08000", "08006", "53300", "57P01", "57P02", "57P03", "25P03",
    )),
    st.text(alphabet=SYMBOLS, min_size=5, max_size=5),
)


class SyntheticPgError(psycopg.Error):
    """Supply controlled SQLSTATE and diagnostics without executing any SQL."""

    def __init__(self, state: str | None, constraint: str | None = None,
                 table: str | None = None) -> None:
        self._synthetic_sqlstate = state
        self._synthetic_diag = SimpleNamespace(constraint_name=constraint, table_name=table)
        super().__init__(PRIVATE_MESSAGE)

    @property
    def sqlstate(self) -> str | None:
        return self._synthetic_sqlstate

    @property
    def diag(self):
        return self._synthetic_diag


class PgErrorMappingProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, database=None, deadline=None)
    @given(state=STATES)
    def test_retry_is_an_exact_two_state_allowlist(self, state: str):
        err = SyntheticPgError(state)
        self.assertEqual(sqlstate_of(err), state)
        self.assertEqual(
            is_retryable(err),
            state in (SERIALIZATION_FAILURE, DEADLOCK_DETECTED),
        )
        mapped = map_database_error(err, ConstraintRegistry())
        self.assertIsInstance(mapped, DatabaseError)
        self.assertNotIn(PRIVATE_MESSAGE, str(mapped))
        self.assertNotIn(PRIVATE_MESSAGE, mapped.message)
        self.assertGreaterEqual(mapped.http_status, 400)
        if state in (SERIALIZATION_FAILURE, DEADLOCK_DETECTED):
            self.assertIsInstance(mapped, RetryTransactionError)
            self.assertEqual((mapped.code, mapped.http_status), ("RETRY", 503))
        else:
            self.assertNotIsInstance(mapped, RetryTransactionError)

    @settings(max_examples=EXAMPLES, database=None, deadline=None)
    @given(state=STATES)
    def test_only_known_transient_states_are_503(self, state: str):
        mapped = map_database_error(SyntheticPgError(state), ConstraintRegistry())
        unavailable = (
            state.startswith("08")
            or state in {"53300", "57P01", "57P02", "57P03", "25P03"}
        )
        transient = (
            state in {
                SERIALIZATION_FAILURE, DEADLOCK_DETECTED,
                LOCK_NOT_AVAILABLE, QUERY_CANCELED,
            }
            or unavailable
        )
        self.assertEqual(mapped.http_status, 503 if transient else (
            400 if state == CHECK_VIOLATION else 500
        ))
        if mapped.http_status == 503:
            self.assertIsNotNone(mapped.retry_after)
        else:
            self.assertIsNone(mapped.retry_after)

    @settings(max_examples=EXAMPLES, database=None, deadline=None)
    @given(
        suffix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
                       min_size=1, max_size=18),
        state=st.sampled_from((UNIQUE_VIOLATION, FOREIGN_KEY_VIOLATION)),
    )
    def test_constraint_resolution_is_exact_and_unregistered_is_500(
        self, suffix: str, state: str,
    ):
        registered = "prks_test_" + suffix + "_key"
        registry = ConstraintRegistry()
        registry.register(registered, "EXACT_CONSTRAINT", http_status=409)
        for constraint, matched in (
            (registered, True),
            (registered + "_suffix", False),
            ("prefix_" + registered, False),
            (None, False),
        ):
            mapped = map_database_error(SyntheticPgError(state, constraint), registry)
            self.assertNotIn(PRIVATE_MESSAGE, str(mapped))
            self.assertEqual(mapped.constraint, constraint)
            self.assertEqual(mapped.sqlstate, state)
            if matched:
                self.assertIsInstance(mapped, ConstraintViolationError)
                self.assertEqual((mapped.code, mapped.http_status), ("EXACT_CONSTRAINT", 409))
            else:
                self.assertIsInstance(mapped, UnmappedDatabaseError)
                self.assertEqual((mapped.code, mapped.http_status), ("DATABASE_ERROR", 500))

    @settings(max_examples=EXAMPLES, database=None, deadline=None)
    @given(
        table=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                      min_size=1, max_size=12),
        field=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
                      min_size=1, max_size=18),
    )
    def test_check_constraint_field_only_accepts_exact_table_and_suffix(
        self, table: str, field: str,
    ):
        constraint = f"{table}_{field}_check"
        self.assertEqual(check_constraint_field(constraint, table), field)
        self.assertIsNone(check_constraint_field(constraint, "other_" + table))
        self.assertIsNone(check_constraint_field(constraint + "_extra", table))
        mapped = map_database_error(
            SyntheticPgError(CHECK_VIOLATION, constraint, table),
            ConstraintRegistry(),
        )
        self.assertEqual((mapped.code, mapped.http_status, mapped.field),
                         ("INVALID_VALUE", 400, field))
        self.assertNotIn(PRIVATE_MESSAGE, str(mapped))

    @settings(max_examples=EXAMPLES, database=None, deadline=None)
    @given(state=st.one_of(st.none(), STATES))
    def test_missing_or_unknown_state_never_leaks_original_error(self, state):
        mapped = map_database_error(SyntheticPgError(state), ConstraintRegistry())
        self.assertNotIn(PRIVATE_MESSAGE, str(mapped))
        self.assertEqual(mapped.sqlstate, state if mapped.code in (
            "RETRY", "DATABASE_UNAVAILABLE", "DATABASE_ERROR", "INVALID_VALUE",
            "READ_ONLY_TRANSACTION", "EXACT_CONSTRAINT",
        ) else mapped.sqlstate)
        self.assertGreaterEqual(mapped.http_status, 400)


if __name__ == "__main__":
    unittest.main()
