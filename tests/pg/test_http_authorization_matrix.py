"""The HTTP authorization matrix: every route family, every kind of caller (#1286).

``test_http_admission.py`` proves the admission rules with ``/api/tags``,
``/api/settings`` and the backups download standing for a family. PG-C3 #642
moved each family to its own ``backend/api/<family>.py`` module calling the
domain commands directly, so this matrix sends one real request per family
(and per route that streams a managed file) over HTTP as each caller:

- the owner, an editor and a reader of the served Library;
- the owner of another Library only (whose session is nobody here);
- a member whose membership was revoked after signing in;
- a signed-out browser (its session cookie was revoked).

Each row states what the request needs (read, edit, or Library
administration). A caller with the right gets anything but 401/403; a reader
asking to edit or an editor asking to administer gets 403 ``forbidden``; the
others get 401 ``not_authenticated``. A refusal never names the Library or
the seeded entities, and a refused request leaves every Library table as it
was (:meth:`AuthorizationMatrixTests.fingerprint`). The registry test fails
when a ``backend/api`` module has no row, so a new family joins the matrix.
"""

from __future__ import annotations

import itertools
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.domain.maintenance import tables
from tests import acting_context as facade_context
from tests.library_adapter import TestLibrary
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.test_search_text import pdf_bytes

_API_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend", "api")

READ, EDIT, ADMIN = "read", "edit", "admin"
_RANK = {READ: 0, EDIT: 1, ADMIN: 2}
_ROLE_RANK = {"reader": 0, "editor": 1, "owner": 2}


@dataclass(frozen=True)
class Row:
    """One request standing for ``family`` (its ``backend/api`` module, or a server route)."""

    family: str
    needs: str
    method: str
    path: str
    body: Any = None
    # A route-level answer the owner may get: the backups download is 404 without a ready token.
    allowed: tuple[int, ...] = ()


# ``{work}``, ``{tag}`` … are the ids seeded in setUp. Bodies are valid for the owner,
# so the owner's answer shows the route is real, not a 404 for an unknown path.
ROWS: tuple[Row, ...] = (
    Row("works", READ, "GET", "/api/works/{work}"),
    Row("works", EDIT, "PATCH", "/api/works/{work}", {"title": "Renamed by the matrix"}),
    Row("works", READ, "GET", "/api/pdfs/{pdf}"),  # streams a managed file
    Row("research_notes", EDIT, "PATCH", "/api/works/{work}", {"notes": "A note from the matrix"}),
    Row("annotations", READ, "GET", "/api/works/{work}/annotations"),
    Row("annotations", EDIT, "POST", "/api/works/{work}/annotations", {"annotations_json": "[]", "canonical_annotation_set_revision": 0}),
    Row("work_roles", EDIT, "POST", "/api/roles", {"person_id": "{person}", "work_id": "{work}", "role_type": "{role}"}),
    Row("tags", READ, "GET", "/api/tags"),
    Row("tags", EDIT, "POST", "/api/tags", {"name": "Matrix tag {unique}"}),
    Row("folders", EDIT, "POST", "/api/folders", {"title": "Matrix folder {unique}"}),
    Row("playlists", EDIT, "POST", "/api/playlists", {"title": "Matrix playlist {unique}"}),
    Row("persons", READ, "GET", "/api/persons"),
    Row("persons", EDIT, "POST", "/api/persons", {"first_name": "Grace", "last_name": "Hopper {unique}"}),
    Row("person_groups", EDIT, "POST", "/api/person-groups", {"name": "Matrix group {unique}"}),
    Row("concepts", EDIT, "POST", "/api/concepts", {"name": "Matrix concept {unique}"}),
    Row("positions", EDIT, "POST", "/api/positions", {"name": "Matrix position {unique}"}),
    Row("arguments", EDIT, "POST", "/api/arguments", {"name": "Matrix argument {unique}", "kind": "argument"}),
    Row("saved_views", EDIT, "POST", "/api/saved-views",
        {"name": "Matrix view {unique}", "search": {"mode": "all", "q": "matrix", "tag": "", "author": "", "publisher": ""}}),
    Row("publishers", READ, "GET", "/api/publishers"),
    Row("publishers", EDIT, "POST", "/api/publishers", {"name": "Matrix press {unique}"}),
    Row("processing", READ, "GET", "/api/processing-items"),
    Row("processing", EDIT, "POST", "/api/processing-items/import", {"item_ids": ["PI-MATRIX"]}),
    Row("search", READ, "GET", "/api/search?q=matrix"),
    Row("settings", READ, "GET", "/api/settings"),
    Row("settings", ADMIN, "PATCH", "/api/settings", {}),
    Row("maintenance", ADMIN, "GET", "/api/backups/download", allowed=(404,)),
    # The stage route takes the archive as the raw body; a JSON body is refused after admission.
    Row("maintenance", ADMIN, "POST", "/api/backups/stage", {}, allowed=(415,)),
    # An empty envelope is the protocol's own refusal, after admission.
    Row("session", EDIT, "POST", "/api/sync/operations", {"operations": []}, allowed=(400,)),
)
# Modules that are not a route family of their own.
_NOT_FAMILIES = frozenset({"__init__"})
# Families with no mutating route.
_READ_ONLY = frozenset({"search"})


class AuthorizationMatrixTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        previous = module.db
        self.addCleanup(setattr, module, "db", previous)
        storage = module._bound_storage
        assert storage is not None
        self.facade = TestLibrary(self.db, storage=storage)
        module.db = self.facade
        self.serve()
        owner_cookie = self.session_cookie()
        self.seed(storage, owner_cookie)
        # A role row's type differs per call, so a second permitted caller is not refused as a duplicate.
        self.roles = itertools.cycle(("Author", "Editor", "Translator", "Reviewer"))

    # -- the Library and its callers ------------------------------------------------------

    def acting(self, cookie: dict[str, str]) -> Any:
        valid = sessions.resolve_session(self.db, cookie["prks_session"])
        assert valid is not None
        return facade_context.acting_as(RequestContext.for_session(valid, epoch=self.library_epoch()))

    def seed(self, storage: Any, cookie: dict[str, str]) -> None:
        name = f"matrix-{uuid.uuid4().hex[:8]}.pdf"
        with open(os.path.join(storage.pdfs_dir, name), "wb") as handle:
            handle.write(pdf_bytes("Matrix Work"))
        with self.acting(cookie):
            self.ids = {
                "work": self.facade.add_work("Matrix Work", file_path=f"/api/pdfs/{name}"),
                "pdf": name,
                "tag": self.facade.add_tag("Seeded tag")["id"],
                "person": self.facade.add_person("Ada", "Lovelace"),
            }

    def callers(self) -> dict[str, Optional[dict[str, str]]]:
        """Cookie per caller kind; signed in now, so revocations happen after sign-in."""
        self.user("Editor", library=self.lib, role="editor")
        self.user("Reader", library=self.lib, role="reader")
        self.user("Revoked", library=self.lib, role="editor")
        other = self.library("Other")
        self.user("Stranger", library=other, role="owner")
        cookies: dict[str, Optional[dict[str, str]]] = {
            "owner": self.session_cookie(), "editor": self.session_cookie("Editor"),
            "reader": self.session_cookie("Reader"), "revoked": self.session_cookie("Revoked"),
        }
        signed_out = self.session_cookie("Editor")
        self.assertEqual(self.call("POST", "/api/logout", cookies=signed_out).status // 100, 2)
        cookies["signed_out"] = signed_out
        # The stranger's own Library session: a real session, for a Library this server does not serve.
        stranger = self.run_sql("SELECT id FROM prks.users WHERE login_name = 'Stranger'")[0]["id"]
        new = self.db.command(lambda tx: sessions.create_session(tx, user_id=stranger, library_id=other))
        cookies["other_library"] = {"prks_session": new.token.reveal()}
        cookies["anonymous"] = None
        self.run_sql("DELETE FROM prks.library_memberships WHERE library_id = %s AND user_id ="
                     " (SELECT id FROM prks.users WHERE login_name = 'Revoked')", (self.lib,))
        return cookies

    # -- what a request may change --------------------------------------------------------

    def fingerprint(self) -> dict[str, Any]:
        """A digest of every Library content table's rows for the served Library."""
        out = {}
        for table in tables.BACKUP_CONTENT_TABLES:
            schema, name = table.split(".")
            (row,) = self.run_sql(
                f'SELECT count(*) AS n, md5(coalesce(string_agg(t::text, \',\' ORDER BY t::text), \'\')) AS h'
                f' FROM "{schema}"."{name}" t WHERE t.library_id = %s', (self.lib,))
            out[table] = (row["n"], row["h"])
        return out

    def send(self, row: Row, cookie: Optional[dict[str, str]]) -> Response:
        def fill(value: Any) -> Any:
            if isinstance(value, str):
                return value.format(**self.ids, unique=uuid.uuid4().hex[:8], role=next(self.roles))
            if isinstance(value, dict):
                return {key: fill(item) for key, item in value.items()}
            if isinstance(value, list):
                return [fill(item) for item in value]
            return value

        epoch = self.library_epoch() if row.method not in ("GET", "HEAD") else None
        return self.call(row.method, fill(row.path), body=fill(row.body), cookies=cookie, epoch=epoch)

    # -- the matrix -------------------------------------------------------------------------

    def test_every_family_answers_each_caller_by_its_right_and_refusals_change_nothing(self) -> None:
        cookies = self.callers()
        secrets = [self.lib, *[value for key, value in self.ids.items() if key != "pdf"]]
        for row in ROWS:
            for caller in ("owner", "reader", "editor", "revoked", "signed_out", "other_library", "anonymous"):
                with self.subTest(family=row.family, method=row.method, path=row.path, caller=caller):
                    before = self.fingerprint()
                    res = self.send(row, cookies[caller])
                    rank = _ROLE_RANK.get(caller)
                    if rank is None:
                        expected: Optional[tuple[int, str]] = (401, "not_authenticated")
                    elif rank < _RANK[row.needs]:
                        expected = (403, "forbidden")
                    else:
                        expected = None
                    if expected is None:
                        if res.status in row.allowed:
                            # The route's own JSON answer, not the server's "endpoint not found" page.
                            self.assertTrue({"error", "code"} & set(res.body), res.raw[:300])
                            continue
                        if caller == "owner":  # the route is real and the row's request is valid
                            self.assertLess(res.status, 300, res.raw[:300])
                        self.assertNotIn(res.status, (401, 403, 404, 405), res.raw[:300])
                        self.assertLess(res.status, 500, res.raw[:300])
                        continue
                    self.assertEqual(res.status, expected[0], res.raw[:300])
                    self.assertEqual(res.body.get("error"), expected[1], res.raw[:300])
                    for secret in secrets:
                        self.assertNotIn(secret, res.text)
                    self.assertEqual(self.fingerprint(), before, "a refused request changed the Library")

    def test_every_api_module_has_a_row(self) -> None:
        modules = {name[:-3] for name in os.listdir(_API_DIR) if name.endswith(".py")} - _NOT_FAMILIES
        covered = {row.family for row in ROWS}
        self.assertEqual(modules - covered, set(), "add a matrix row for each new backend/api family")
        self.assertEqual(covered - modules, set(), "a row names a family that no longer exists")
        for family in covered - _READ_ONLY:
            needs = {row.needs for row in ROWS if row.family == family}
            with self.subTest(family=family):
                self.assertTrue(needs & {EDIT, ADMIN}, "each family needs at least one mutating row")
