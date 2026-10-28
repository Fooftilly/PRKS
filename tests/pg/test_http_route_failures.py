"""An admitted route whose PostgreSQL call is refused answers as admission would (§5.4, §9.4).

Admission authorizes a request in its own transaction; each facade call in
the route authorizes again in its own (``library_command``/``library_query``).
What changes in between (a restore moving the Library epoch, a demotion, a
removed membership, a maintenance flag) refuses in the route, and that
refusal must reach the client with admission's status and code, not as a
500. A mapped database error keeps its status: a transient one is 503 with
``Retry-After``. Anything else stays a logged 500 with a request id.

The interfering change runs inside the facade method, after admission and
before the method's own transaction, exactly where a concurrent restore or
membership change would land.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from backend.api import session as session_api
from backend.db.errors import (
    ConstraintViolationError,
    RetryTransactionError,
    UnmappedDatabaseError,
)
from backend.domain.identity.authorization import AccessRefused, Refusal
from tests.pg.test_http_research import ResearchHttpTestCase


class RouteFailureTests(ResearchHttpTestCase):
    def interfere(self, method: str, before: Callable[[], None]) -> None:
        """Run ``before`` when the route calls the facade's ``method``, then the method itself."""
        original = getattr(self.facade, method)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            before()
            return original(*args, **kwargs)

        setattr(self.facade, method, wrapped)

    def raises(self, method: str, error: BaseException) -> None:
        def fail() -> None:
            raise error

        self.interfere(method, fail)

    def concept_names(self) -> list[str]:
        return [row["name"] for row in self.run_sql("SELECT name FROM prks.concepts ORDER BY name")]

    def test_a_restore_between_admission_and_the_route_is_library_epoch_changed(self) -> None:
        self.interfere("create_concept", lambda: self.run_sql(
            "UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.lib,)))
        res = self.send("POST", "/api/concepts", {"name": "Ethics"})
        self.assertEqual(res.status, 409, res.raw)
        self.assertEqual((res.body["error"], res.body["code"]), ("LIBRARY_EPOCH_CHANGED",) * 2)
        self.assertEqual(res.body["message"], Refusal.LIBRARY_EPOCH_CHANGED.message)
        self.assertEqual(res.header("PRKS-Library-Epoch"), [])  # admission's epoch is stale now
        self.assertEqual(self.concept_names(), [])

    def test_a_demotion_between_admission_and_the_route_is_forbidden(self) -> None:
        ethics = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics"}), 201)["id"]
        self.interfere("update_concept", lambda: self.run_sql(
            "UPDATE prks.library_memberships SET role = 'reader' WHERE library_id = %s", (self.lib,)))
        res = self.send("PATCH", f"/api/concepts/{ethics}", {"name": "Morals"})
        self.assertEqual((res.status, res.body["code"]), (403, "forbidden"), res.raw)
        self.assertEqual(self.concept_names(), ["Ethics"])

    def test_a_maintenance_flag_set_after_admission_is_maintenance(self) -> None:
        self.interfere("create_position", lambda: self.run_sql(
            "UPDATE prks.libraries SET maintenance = 'restore', maintenance_run = %s WHERE id = %s",
            (uuid.uuid4(), self.lib)))
        res = self.send("POST", "/api/positions", {"name": "Thesis"})
        self.assertEqual((res.status, res.body["code"]), (503, "MAINTENANCE"), res.raw)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.positions")[0]["n"], 0)

    def test_a_read_after_the_membership_is_removed_is_not_authenticated(self) -> None:
        self.interfere("list_concepts", lambda: self.run_sql(
            "DELETE FROM prks.library_memberships WHERE library_id = %s", (self.lib,)))
        res = self.get("/api/concepts")
        self.assertEqual((res.status, res.body["code"]), (401, "not_authenticated"), res.raw)
        self.assertNotIn(self.lib, res.text)

    def test_a_transient_database_error_is_503_with_retry_after(self) -> None:
        self.raises("list_arguments", RetryTransactionError(sqlstate="40001"))
        res = self.get("/api/arguments")
        self.assertEqual((res.status, res.body["code"]), (503, "RETRY"), res.raw)
        self.assertEqual(res.header("Retry-After"), ["1"])
        self.assertEqual(res.header("PRKS-Library-Epoch"), [str(self.library_epoch())])

    def test_an_unmapped_database_error_stays_a_logged_500(self) -> None:
        self.raises("list_positions", UnmappedDatabaseError(sqlstate="XX000"))
        res = self.get("/api/positions")
        self.assertEqual(res.status, 500, res.raw)
        self.assertEqual(res.body["error"], "internal_error")
        self.assertIn("request_id", res.body)
        self.assertIn("unhandled_api_error", self.logs.getvalue())

class RouteFailureMappingTests(ResearchHttpTestCase):
    """``route_failure`` itself: what is answered and what is left to the 500 path."""

    def reply(self, exc: BaseException) -> session_api.Reply:
        answer = session_api.route_failure(exc)
        if answer is None:
            self.fail(f"{type(exc).__name__} is not answered")
        return answer

    def test_the_mapping(self) -> None:
        refused = self.reply(AccessRefused(Refusal.FORBIDDEN))
        self.assertEqual((refused.status, refused.body["code"], refused.headers), (403, "forbidden", ()))
        constraint = self.reply(
            ConstraintViolationError("CONCEPT_EXISTS", "Taken.", http_status=409, constraint="concept_keys_pkey"))
        self.assertEqual((constraint.status, constraint.body),
                         (409, {"error": "CONCEPT_EXISTS", "code": "CONCEPT_EXISTS", "message": "Taken."}))
        retry = self.reply(RetryTransactionError(sqlstate="40P01"))
        self.assertEqual((retry.status, retry.headers), (503, (("Retry-After", "1"),)))
        self.assertIsNone(session_api.route_failure(UnmappedDatabaseError(sqlstate="XX000")))
        self.assertIsNone(session_api.route_failure(ValueError("x")))
