"""Architectural guidance must not drift back to the pre-local-first world.

`AGENTS.md` is read by people and by agents *before* they write code, so a stale
sentence there is worse than a stale comment: it actively instructs the next
contributor to rebuild something that was deliberately removed. Three of PRKS's
worst offline defects came from exactly that -- a surface was made durable, the
guidance still said "read-only offline", and the next change re-added a
connectivity guard in front of an operation that no longer needed one.

These are deliberately *phrase* assertions rather than a general style check.
They fail loudly when a specific obsolete claim returns, and they say what is
true instead. `docs/local-first-rollout-status.md` is the running score and is
what this test reads the truth from.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
STATUS = ROOT / "docs" / "local-first-rollout-status.md"

# Families that are durable today. Each entry is (operation, the domain word a
# reader would search AGENTS.md for).
DURABLE_FAMILIES = (
    "ADD_WORK_TAG",
    "REMOVE_WORK_TAG",
    "CREATE_TAG",
    "DELETE_TAG",
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
)


class AgentGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.agents = AGENTS.read_text()
        self.status = STATUS.read_text()

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
                self.assertNotIn(phrase, self.agents,
                                 "Playlists are local-first: see the Playlists "
                                 "section and docs/local-first-sync.md (3G)")

    def test_the_blanket_no_offline_list_is_gone(self):
        """"No offline Tag creation, Folder edits, Playlists, ..." was true in
        Phase 1 and is now wrong about four separate families at once."""
        self.assertNotIn("No offline Tag creation", self.agents)
        self.assertNotRegex(
            self.agents,
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
                self.assertIsNone(pattern.search(self.agents),
                                  "%s is durable; describe what it supports" % domain)

    def test_person_and_group_creation_are_not_described_as_guarded(self):
        """Both modals open offline: the id is minted on the device."""
        for phrase in (
            "`openModal('person-modal')` is guarded centrally",
            "`openModal('group-modal')` is guarded centrally",
            "`openModal('playlist-modal')` is guarded centrally",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.agents)

    def test_work_tags_are_not_the_only_mutation_that_crosses_the_cache(self):
        self.assertNotIn(
            "Existing Work Tags are the one\nmutation that crosses it",
            self.agents)
        self.assertNotIn(
            "Apart from existing Work Tags (see *Local-first Work Tags* below), "
            "there is no\noffline mutation outbox",
            self.agents)

    # ---- and the positive half: the truth has to be stated ----------------

    def test_agents_md_names_every_durable_family(self):
        """Not a vague "everything works offline": the exact operation names, so
        a reader can tell a durable surface from a server-bound one."""
        missing = [f for f in DURABLE_FAMILIES if f not in self.agents]
        self.assertEqual(missing, [],
                         "AGENTS.md must name each durable family explicitly")

    def test_agents_md_points_at_the_running_score(self):
        self.assertIn("docs/local-first-rollout-status.md", self.agents)

    def test_agents_md_still_names_what_is_server_bound(self):
        """The list must stay honest in both directions -- a reader has to be
        able to find what is NOT durable."""
        for phrase in ("research-note editing", "PDF annotations",
                       "multi-user sync", "server push"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.agents)

    def test_the_status_doc_and_agents_md_agree_on_the_durable_set(self):
        """The two documents are written by hand and drift apart silently."""
        for family in DURABLE_FAMILIES:
            with self.subTest(family=family):
                self.assertIn(family, self.status,
                              "the rollout status is the score and must list it")


if __name__ == "__main__":
    unittest.main()
