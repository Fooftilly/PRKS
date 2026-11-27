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
administration). A caller with the right gets a success (or the row's
documented route-level refusal, which comes after admission), and a row
with ``verify`` also checks that the request did its work (the Research Note
is saved, the Processing item is imported); a reader
asking to edit or an editor asking to administer gets 403 ``forbidden``; the
others get 401 ``not_authenticated``. A refusal never names the Library or
the seeded entities, and a refused request leaves every Library table as it
was (:meth:`AuthorizationMatrixTests.fingerprint`). The registry test fails
when a ``backend/api`` module has no row, so a new family joins the matrix.
"""

from __future__ import annotations

import io
import itertools
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.domain.maintenance import tables
from tests import acting_context as facade_context
from backend.library import PostgresLibrary
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.test_http_processing import ProcessingHttpTestCase
from tests.pg.test_search_text import pdf_bytes
from backend.domain.organization import tag_commands
from tests.acting_context import current_context
from tests import people_fixtures
from backend.domain.people import person_commands
from backend.domain.works import work_commands
from backend.storage.objects import LocalFilesystemStorage
from tests.narrowing import require
from tests import works_fixtures

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
    # A check that a permitted request really did its work, not only answered 2xx:
    # a method of the test case named ``verify_<name>``, given the sent body and the answer.
    verify: Optional[str] = None


# A minimal independent CSL style the import accepts; it has no braces, so ``str.format`` leaves it as is.
_CSL = ('<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0">'
        '<info><title>Matrix style</title></info>'
        '<citation><layout><text variable="title"/></layout></citation>'
        '<bibliography><layout><text variable="title"/></layout></bibliography></style>')

# ``{work}``, ``{tag}`` … are the ids seeded in setUp. Bodies are valid for the owner,
# so the owner's answer shows the route is real, not a 404 for an unknown path.
ROWS: tuple[Row, ...] = (
    Row("works", READ, "GET", "/api/works/{work}"),
    Row("works", EDIT, "PATCH", "/api/works/{work}", {"title": "Renamed by the matrix"}),
    Row("pdfs", READ, "GET", "/api/assets/{asset}/content"),  # streams a managed file
    Row("thumbnails", READ, "GET", "/api/works/{work}/thumbnail", allowed=(404,)),  # no PDF page to render
    Row("research_graph", READ, "GET", "/api/research-graph"),
    Row("research_notes", EDIT, "PATCH", "/api/works/{work}", {"text_content": "A note from the matrix {unique}"},
        verify="note_saved"),
    Row("annotations", READ, "GET", "/api/works/{work}/annotations"),
    Row("annotations", EDIT, "POST", "/api/works/{work}/annotations", {"annotations_json": "[]", "canonical_annotation_set_revision": 0}),
    Row("work_roles", EDIT, "POST", "/api/roles", {"person_id": "{person}", "work_id": "{work}", "role_type": "{role}"}),
    Row("tags", READ, "GET", "/api/tags"),
    Row("tags", EDIT, "POST", "/api/tags", {"name": "Matrix tag {unique}"}),
    Row("folders", EDIT, "POST", "/api/folders", {"title": "Matrix folder {unique}"}),
    Row("playlists", EDIT, "POST", "/api/playlists", {"title": "Matrix playlist {unique}"}),
    Row("persons", READ, "GET", "/api/persons"),
    Row("persons", READ, "GET", "/api/persons/{person}/profile-image"),  # streams the cached portrait
    Row("persons", EDIT, "POST", "/api/persons", {"first_name": "Grace", "last_name": "Hopper {unique}"}),
    # A URL the Library names nowhere: the owner's answer is 404, and nothing is fetched.
    Row("remote_images", READ, "GET", "/api/remote-images?url=https%3A%2F%2Fexample.invalid%2Fx.png",
        allowed=(404,)),
    Row("person_groups", EDIT, "POST", "/api/person-groups", {"name": "Matrix group {unique}"}),
    Row("concepts", EDIT, "POST", "/api/concepts", {"name": "Matrix concept {unique}"}),
    Row("positions", EDIT, "POST", "/api/positions", {"name": "Matrix position {unique}"}),
    Row("arguments", EDIT, "POST", "/api/arguments", {"name": "Matrix argument {unique}", "kind": "argument"}),
    Row("saved_views", EDIT, "POST", "/api/saved-views",
        {"name": "Matrix view {unique}", "search": {"mode": "all", "q": "matrix", "tag": "", "author": "", "publisher": ""}}),
    Row("publishers", READ, "GET", "/api/publishers"),
    Row("publishers", EDIT, "POST", "/api/publishers", {"name": "Matrix press {unique}"}),
    Row("processing", READ, "GET", "/api/processing-items"),
    Row("processing", READ, "GET", "/api/processing-items/{item}/pdf"),  # streams the uploaded object
    # ``{ready}`` is a ready item uploaded just before each request, so every permitted caller imports one.
    Row("processing", EDIT, "POST", "/api/processing-items/import", {"item_ids": ["{ready}"]}, verify="imported"),
    Row("search", READ, "GET", "/api/search?q=matrix"),
    Row("settings", READ, "GET", "/api/settings"),
    Row("citation_styles", READ, "GET", "/api/citation-styles"),
    Row("citation_styles", ADMIN, "POST", "/api/citation-styles", {"csl": _CSL}),
    Row("settings", ADMIN, "PATCH", "/api/settings", {}),
    Row("maintenance", ADMIN, "GET", "/api/backups/download", allowed=(404,)),
    # The stage route takes the archive as the raw body; a JSON body is refused after admission.
    Row("maintenance", ADMIN, "POST", "/api/backups/stage", {}, allowed=(415,)),
    # How this PRKS is deployed, read-only (#612): the owner only.
    Row("deployment", ADMIN, "GET", "/api/deployment-settings"),
    # Where the Library's files are (#720): carries host paths, the owner only.
    Row("storage", ADMIN, "GET", "/api/storage-status"),
    # An empty envelope is the protocol's own refusal, after admission.
    Row("sync", EDIT, "POST", "/api/sync/operations", {"operations": []}, allowed=(400,)),
    Row("diagnostics", READ, "GET", "/api/diagnostics/performance"),
    Row("diagnostics", ADMIN, "POST", "/api/diagnostics/performance/reset", {}),
    Row("diagnostics", ADMIN, "GET", "/api/docs"),
)
# Modules that are not a route family of their own. ``setup`` serves only a
# first run's setup phase, before any session or Library exists (#612); its
# token, origin and rate-limit rules are in tests/pg/test_web_setup.py.
# ``session`` is admission itself, ``identity`` only declares the contracts of the paths
# admission answers, ``common`` holds shared helpers, and
# ``health`` serves the unauthenticated probes (tests/test_health_probes.py).
_NOT_FAMILIES = frozenset({"__init__", "setup", "session", "common", "health", "identity"})
# Families with no mutating route. A saved remote image is written only by a
# read (``GET /api/remote-images`` fills it, as the portrait route does).
_READ_ONLY = frozenset({"search", "remote_images", "deployment", "pdfs", "thumbnails", "research_graph"})


class AuthorizationMatrixTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.app
        previous = module.library
        self.addCleanup(setattr, module, "library", previous)
        storage = module.storage
        assert storage is not None
        self.facade = PostgresLibrary(self.db, storage=storage)
        module.library = self.facade
        self.serve()
        owner_cookie = self.session_cookie()
        self.seed(storage, owner_cookie)
        # A role row's type differs per request, so a second permitted caller is not refused as a duplicate.
        self.roles = itertools.cycle(("Author", "Editor", "Translator", "Reviewer"))

    # The Processing upload as a browser sends it, to seed one ready item.
    raw_upload = ProcessingHttpTestCase.raw_upload

    # -- the Library and its callers ------------------------------------------------------

    def acting(self, cookie: dict[str, str]) -> Any:
        valid = sessions.resolve_session(self.db, cookie["prks_session"])
        assert valid is not None
        return facade_context.acting_as(RequestContext.for_session(valid, epoch=self.library_epoch()))

    def seed(self, storage: Any, cookie: dict[str, str]) -> None:
        name = f"matrix-{uuid.uuid4().hex[:8]}.pdf"
        with open(os.path.join(storage.pdfs_dir, name), "wb") as handle:
            handle.write(pdf_bytes("Matrix Work"))
        from PIL import Image

        from backend.media.person_image import PortraitImage

        portrait = io.BytesIO()
        Image.new("RGB", (4, 4), (200, 30, 30)).save(portrait, format="JPEG")
        with self.acting(cookie):
            self.ids = {
                "work": works_fixtures.add_work(self.facade.database, current_context(), self.facade.storage, "Matrix Work", file_path=f"/api/pdfs/{name}"),
                "tag": tag_commands.create(self.facade.database, current_context(), "Seeded tag")["id"],
                "person": people_fixtures.add_person(self.facade.database, current_context(), "Ada", "Lovelace", image_url="https://portrait.invalid/ada.jpg"),
            }
            self.ids["asset"] = require(work_commands.detail(self.facade.database, current_context(),
                                                             self.ids["work"]))["primary_asset_id"]
            cached = person_commands.cache_portrait(self.facade.database, current_context(), LocalFilesystemStorage.for_config(require(self.facade.storage)), 
                self.ids["person"], lambda _url: PortraitImage(portrait.getvalue(), "jpeg"))
            assert cached is not None, "the portrait was not cached"
        self.cookies = cookie
        uploaded = self.raw_upload(pdf_bytes("Matrix upload"), filename="matrix-upload.pdf")
        self.assertEqual(uploaded.status, 201, uploaded.raw[:300])
        self.ids["item"] = uploaded.body["item"]["id"]

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

    def prepare(self, row: Row) -> None:
        """Seed what this request consumes, before the refusal check's fingerprint is taken."""
        if "{ready}" in repr(row.body):
            uploaded = self.raw_upload(pdf_bytes(f"Matrix ready {uuid.uuid4().hex[:8]}"), filename="matrix-ready.pdf")
            self.assertEqual(uploaded.status, 201, uploaded.raw[:300])
            self.ids["ready"] = uploaded.body["item"]["id"]

    def verify_note_saved(self, body: dict[str, Any], res: Response) -> None:
        rows = self.run_sql("SELECT body FROM prks.work_notes WHERE library_id = %s AND work_id = %s"
                            " AND kind = 'research'", (self.lib, self.ids["work"]))
        self.assertEqual([row["body"] for row in rows], [body["text_content"]], "the Research Note was not saved")

    def verify_imported(self, body: dict[str, Any], res: Response) -> None:
        (result,) = res.body["results"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["item_id"], body["item_ids"][0])
        (row,) = self.run_sql("SELECT status FROM prks.processing_items WHERE library_id = %s AND id = %s",
                              (self.lib, body["item_ids"][0]))
        self.assertEqual(row["status"], "imported")

    def send(self, row: Row, cookie: Optional[dict[str, str]]) -> tuple[Response, Any]:
        role = next(self.roles)  # one role per request, so each caller asks for a different one

        def fill(value: Any) -> Any:
            if isinstance(value, str):
                return value.format(**self.ids, unique=uuid.uuid4().hex[:8], role=role)
            if isinstance(value, dict):
                return {key: fill(item) for key, item in value.items()}
            if isinstance(value, list):
                return [fill(item) for item in value]
            return value

        epoch = self.library_epoch() if row.method not in ("GET", "HEAD") else None
        body = fill(row.body)
        return self.call(row.method, fill(row.path), body=body, cookies=cookie, epoch=epoch), body

    # -- the matrix -------------------------------------------------------------------------

    def test_every_family_answers_each_caller_by_its_right_and_refusals_change_nothing(self) -> None:
        cookies = self.callers()
        for row in ROWS:
            for caller in ("owner", "reader", "editor", "revoked", "signed_out", "other_library", "anonymous"):
                with self.subTest(family=row.family, method=row.method, path=row.path, caller=caller):
                    self.prepare(row)
                    before = self.fingerprint()
                    res, body = self.send(row, cookies[caller])
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
                        # The route is real and the row's request is valid for every caller with the right.
                        self.assertLess(res.status, 300, res.raw[:300])
                        if row.verify is not None:
                            getattr(self, f"verify_{row.verify}")(body, res)
                        continue
                    self.assertEqual(res.status, expected[0], res.raw[:300])
                    self.assertEqual(res.body.get("error"), expected[1], res.raw[:300])
                    # Built after prepare(), so a fixture seeded for this request is checked too.
                    secrets = [self.lib, *[value for key, value in self.ids.items() if key != "pdf"]]
                    for secret in secrets:
                        self.assertNotIn(secret, res.text)
                    self.assertEqual(self.fingerprint(), before, "a refused request changed the Library")

    def test_every_api_module_has_a_row(self) -> None:
        modules = {name[:-3] for name in os.listdir(_API_DIR) if name.endswith(".py")} - _NOT_FAMILIES
        covered = {row.family for row in ROWS}
        self.assertEqual(modules - covered, set(), "add a matrix row for each new backend/api family")
        self.assertEqual(covered - modules, set(), "a row names a family that no longer exists")
        for row in ROWS:
            if row.verify is not None:
                self.assertTrue(callable(getattr(self, f"verify_{row.verify}", None)), row)
        for family in covered - _READ_ONLY:
            needs = {row.needs for row in ROWS if row.family == family}
            with self.subTest(family=family):
                self.assertTrue(needs & {EDIT, ADMIN}, "each family needs at least one mutating row")
