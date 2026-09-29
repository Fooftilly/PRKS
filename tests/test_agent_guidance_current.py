"""Keep agent guidance honest about both current behavior and future architecture.

`AGENTS.md` and scoped agent files are read before contributors write code, so
stale guidance can recreate removed behavior or harden transitional architecture
into a permanent constraint. Current offline/durable-operation behavior remains
a correctness contract until deliberately migrated, while #310/#311 define a
future-capable target that must not be inferred away by SQLite/local-path/
single-owner implementation details.

The detailed Offline/PWA contract lives in `docs/agent-rules/offline-pwa.md`,
routed from the scoped frontend guidance. Obsolete-phrase and durable-family
assertions cover the files agents read for that domain. Separate assertions pin
the current-vs-target distinction and ensure Vue source has scoped guidance.

These are deliberately *phrase* assertions rather than a general style check.
They fail loudly when a specific obsolete claim returns, and they say what is
true instead. `docs/local-first-rollout-status.md` remains the running score
for what is implemented today.
"""
import pathlib
import re
import unittest

from backend.db_migrations import LATEST_SCHEMA_VERSION

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
FRONTEND_APP_AGENTS = ROOT / "frontend-app" / "AGENTS.md"
OFFLINE_PWA = ROOT / "docs" / "agent-rules" / "offline-pwa.md"
STATUS = ROOT / "docs" / "local-first-rollout-status.md"
README = ROOT / "README.md"

# Families that are durable today. Each entry is (operation, the domain word a
# reader would search the Offline/PWA contract for).
DURABLE_FAMILIES = (
    "ADD_WORK_TAG",
    "REMOVE_WORK_TAG",
    "CREATE_TAG",
    "DELETE_TAG",
    "MERGE_TAG",
    "CREATE_WORK",
    "DELETE_WORK",
    "MARK_WORK_OPENED",
    "SET_WORK_METADATA_FIELD",
    "SET_WORK_SOURCE",
    "ADD_WORK_PERSON_ROLE",
    "REMOVE_WORK_PERSON_ROLE",
    "SET_WORK_PERSON_ROLE_CREDIT",
    "CREATE_PERSON",
    "SET_PERSON_METADATA_FIELD",
    "DELETE_PERSON",
    "CREATE_PERSON_GROUP",
    "SET_PERSON_GROUP_FIELD",
    "ADD_PERSON_GROUP_MEMBER",
    "REMOVE_PERSON_GROUP_MEMBER",
    "DELETE_PERSON_GROUP",
    "CREATE_FOLDER",
    "SET_FOLDER_FIELD",
    "DELETE_FOLDER",
    "SET_WORK_FOLDER",
    "ADD_FOLDER_TAG",
    "REMOVE_FOLDER_TAG",
    "CREATE_PLAYLIST",
    "SET_PLAYLIST_FIELD",
    "REORDER_PLAYLIST_ITEMS",
    "DELETE_PLAYLIST",
    "SET_WORK_PLAYLIST",
    "CREATE_CONCEPT",
    "SET_CONCEPT_FIELD",
    "SET_CONCEPT_IDENTITY",
    "SET_CONCEPT_PARENTS",
    "DELETE_CONCEPT",
    "CREATE_POSITION",
    "SET_POSITION_FIELD",
    "DELETE_POSITION",
    "CREATE_ARGUMENT",
    "SET_ARGUMENT_FIELD",
    "SET_ARGUMENT_SOURCES",
    "SET_ARGUMENT_TARGETS",
    "DELETE_ARGUMENT",
    "SET_WORK_RESEARCH_NOTE",
    "SET_WORK_PRIVATE_NOTE",
)


class AgentGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.agents = AGENTS.read_text()
        self.offline_pwa = OFFLINE_PWA.read_text()
        self.frontend_agents = (ROOT / "frontend" / "AGENTS.md").read_text(
            encoding="utf-8")
        self.frontend_app_agents = FRONTEND_APP_AGENTS.read_text(encoding="utf-8")
        # Combined corpus agents read for current offline/local-first guidance.
        self.guidance = (
            self.agents + "\n" + self.frontend_agents + "\n" + self.offline_pwa
        )
        self.status = STATUS.read_text()

    # ---- current implementation vs accepted target architecture ------------

    def test_root_distinguishes_current_runtime_from_target_architecture(self):
        for phrase in ("#310", "#311", "PostgreSQL", "current implementation"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.agents)

    def test_vue_source_has_scoped_agent_guidance(self):
        self.assertIn("frontend-app/AGENTS.md", self.agents)
        self.assertIn("DESIGN.md", self.frontend_app_agents)
        self.assertIn("frontend/AGENTS.md", self.frontend_app_agents)
        self.assertIn("#310", self.frontend_app_agents)
        self.assertIn("#311", self.frontend_app_agents)

    def test_test_router_names_vue_and_runtime_frontend_scopes(self):
        tests_agents = (ROOT / "tests" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("frontend-app/AGENTS.md", tests_agents)
        self.assertIn("frontend/AGENTS.md", tests_agents)

    def test_offline_contract_is_not_declared_permanent_target_architecture(self):
        self.assertIn("contract for the offline/sync implementation that exists today",
                      self.offline_pwa)
        self.assertIn("#310", self.offline_pwa)
        self.assertIn("not treat it as the permanent target", self.offline_pwa)

    # ---- obsolete phrases that must never come back ------------------------

    def test_playlists_are_not_described_as_read_only_offline(self):
        """The exact claim that sent one agent to re-disable every Playlist
        control after they had been made durable."""
        for phrase in (
            "Playlists are **read-only** offline",
            "Playlists are read-only offline",
            "Playlist routes are **read-only** offline",
            "Playlist routes are read-only offline",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.guidance,
                                 "Playlists are local-first: see the Playlists "
                                 "section and docs/local-first-sync.md (3G)")

    def test_the_blanket_no_offline_list_is_gone(self):
        """"No offline Tag creation, Folder edits, Playlists, ..." was true in
        Phase 1 and is now wrong about four separate families at once."""
        self.assertNotIn("No offline Tag creation", self.guidance)
        self.assertNotRegex(
            self.guidance,
            r"No offline[^.\n]*\b(Tag creation|Folder edits|Playlists)\b",
            "those families are durable; say what is actually still missing")

    def test_no_domain_that_is_durable_is_still_called_read_only(self):
        """A per-domain sweep, so a family made durable later cannot leave its
        own section behind."""
        durable_domains = ("Playlists", "People", "Person Groups", "Folders",
                           "Folders/Home", "Concepts", "Concept routes",
                           "Positions", "Position routes")
        for domain in durable_domains:
            with self.subTest(domain=domain):
                pattern = re.compile(
                    r"^%s[^\n]*\*\*read-only\*\* offline" % re.escape(domain),
                    re.MULTILINE)
                self.assertIsNone(pattern.search(self.guidance),
                                  "%s is durable; describe what it supports" % domain)

    def test_person_and_group_creation_are_not_described_as_guarded(self):
        """Both modals open offline: the id is minted on the device."""
        for phrase in (
            "`openModal('person-modal')` is guarded centrally",
            "`openModal('group-modal')` is guarded centrally",
            "`openModal('playlist-modal')` is guarded centrally",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.guidance)

    def test_work_tags_are_not_the_only_mutation_that_crosses_the_cache(self):
        self.assertNotIn(
            "Existing Work Tags are the one\nmutation that crosses it",
            self.guidance)
        self.assertNotIn(
            "Apart from existing Work Tags (see *Local-first Work Tags* below), "
            "there is no\noffline mutation outbox",
            self.guidance)

    # ---- and the positive half: the truth has to be stated ----------------

    def test_offline_pwa_contract_names_every_durable_family(self):
        """Not a vague "everything works offline": the exact operation names, so
        a reader can tell a durable surface from a server-bound one. Lives in
        the scoped Offline/PWA contract, not the root routing stub."""
        missing = [f for f in DURABLE_FAMILIES if f not in self.offline_pwa]
        self.assertEqual(missing, [],
                         "docs/agent-rules/offline-pwa.md must name each "
                         "durable family explicitly")

    def test_agents_md_routes_to_offline_pwa_contract(self):
        """Root routes frontend work to the scoped policy, which then routes
        offline/PWA work to the detailed domain contract."""
        self.assertIn("frontend/AGENTS.md", self.agents)
        frontend_agents = (ROOT / "frontend" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Offline / PWA", frontend_agents)
        self.assertIn("docs/agent-rules/offline-pwa.md", frontend_agents)

    def test_backend_agents_routes_cross_domain_contracts(self):
        """Backend-only workers must still be told to load shared domain
        contracts that live outside backend/AGENTS.md (sync/offline, research,
        Saved Views)."""
        backend = (ROOT / "backend" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Cross-domain contracts", backend)
        self.assertIn("docs/agent-context/sync-map.md", backend)
        self.assertIn("docs/agent-rules/offline-pwa.md", backend)
        self.assertIn("completely", backend)
        self.assertIn("frontend/AGENTS.md", backend)
        for phrase in (
            "Research network",
            "Research Graph",
            "Saved Views",
            "Offline / PWA",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, backend)

    def test_agents_md_points_at_the_running_score(self):
        self.assertIn("docs/local-first-rollout-status.md", self.agents)

    def test_offline_pwa_still_names_what_is_server_bound(self):
        """The list must stay honest in both directions -- a reader has to be
        able to find what is NOT durable."""
        for phrase in ("PDF annotations",
                       "multi-user sync", "server push"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.offline_pwa)

    def test_the_status_doc_and_offline_contract_agree_on_the_durable_set(self):
        """The two documents are written by hand and drift apart silently."""
        for family in DURABLE_FAMILIES:
            with self.subTest(family=family):
                self.assertIn(family, self.status,
                              "the rollout status is the score and must list it")

    def test_readme_current_schema_matches_latest(self):
        """User-facing README current-version line tracks LATEST_SCHEMA_VERSION.

        Historical notes such as "Schema v13 removed…" are deliberately left
        alone; only the explicit current-version statement is compared.
        """
        readme = README.read_text()
        match = re.search(
            r"(?m)^Current schema version:\s*\*\*(\d+)\*\*\.\s*$",
            readme,
        )
        self.assertIsNotNone(
            match,
            "README must state `Current schema version: **N**.` exactly once")
        self.assertEqual(
            int(match.group(1)),
            LATEST_SCHEMA_VERSION,
            "README current schema drifted from backend.db_migrations.LATEST_SCHEMA_VERSION")


if __name__ == "__main__":
    unittest.main()
