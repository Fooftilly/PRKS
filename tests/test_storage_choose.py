"""Choosing the Library folder before setup claims it (storage-architecture §8.1, #720).

Under PostgreSQL "choose" only records a folder for the next start, and only
while the current folder is unclaimed; "open another library" is refused;
every refusal writes nothing; and no process serves a folder the committed
bootstrap file does not name (the two-process race is a refusal test).
"""

from __future__ import annotations

import dataclasses
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import choose as choose_mod  # noqa: E402
from backend.storage import resolver, root_binding, root_marker  # noqa: E402
from backend.storage.bootstrap_config import BootstrapConfigStore, expect_absent, read_bootstrap_config  # noqa: E402
from backend.storage.choose import choose_root  # noqa: E402
from backend.storage.config import StorageConfig  # noqa: E402
from backend.storage.errors import StorageRootError  # noqa: E402
from backend.storage.root_binding import acquire_root_lease, open_storage_root  # noqa: E402


def _marker(root: str) -> root_marker.RootMarker:
    marker = root_marker.read_marker(root)
    assert marker is not None, root
    return marker


MARKER = root_marker.MARKER_FILENAME


def _snapshot(path: str) -> dict:
    """Every file under ``path`` with its bytes: proof that nothing was written."""
    found = {}
    for top, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(top, name)
            with open(full, "rb") as handle:
                found[os.path.relpath(full, path)] = handle.read()
    return found


def _choose_in_child(target: str, environ: dict, results) -> None:
    try:
        choose_root(target, environ=environ)
        results.put(("chose", target))
    except StorageRootError as exc:
        results.put(("refused", exc.reason))


class ChooseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-choose-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.config_file = os.path.join(self.tmp, "config", "config.json")
        self.default_root = os.path.join(self.tmp, "default-library")
        self.environ = {"HOME": self.tmp, "PRKS_CONFIG_FILE": self.config_file}
        # Start from a selection inside the test's tmp, so neither this
        # process nor a spawned child ever resolves the checkout's data/.
        BootstrapConfigStore(self.config_file).compare_and_set(expect=expect_absent(), local_root=self.default_root)
        self._held = []

    def tearDown(self) -> None:
        for held in self._held:
            held.release()
        self._tmp.cleanup()

    def path(self, *parts: str) -> str:
        return os.path.join(self.tmp, *parts)

    def selected(self):
        config = read_bootstrap_config(self.config_file)
        return config.local_root if config is not None else None

    def make_root(self, root: str, *, claimed: bool = False) -> str:
        bound = open_storage_root(StorageConfig.from_env(cli_root=root, environ={"HOME": self.tmp}), register=False)
        bound.release()
        if claimed:
            marker_file = os.path.join(root, MARKER)
            with open(marker_file, encoding="utf-8") as handle:
                doc = json.load(handle)
            doc.update(instance_id="IN-" + uuid.uuid4().hex.upper(), start_generation=3, start_token="a" * 32)
            with open(marker_file, "w", encoding="utf-8") as handle:
                json.dump(doc, handle)
            self.assertIsNotNone(_marker(root).instance_id)
        return root

    def assert_refused(self, reason: str, target: str, **kwargs) -> None:
        before_config = _snapshot(os.path.dirname(self.config_file)) if os.path.isdir(os.path.dirname(self.config_file)) else {}
        before_target = _snapshot(target) if os.path.isdir(target) else None
        with self.assertRaises(StorageRootError) as caught:
            choose_root(target, environ=kwargs.pop("environ", self.environ), **kwargs)
        self.assertEqual(caught.exception.reason, reason, caught.exception.message)
        after_config = _snapshot(os.path.dirname(self.config_file)) if os.path.isdir(os.path.dirname(self.config_file)) else {}
        # The config lock file may be created; the selection never changes.
        after_config.pop("config.json.lock", None)
        before_config.pop("config.json.lock", None)
        self.assertEqual(after_config, before_config)
        if before_target is not None:
            self.assertEqual(_snapshot(target), before_target)


class ChooseTests(ChooseTestCase):
    def test_a_new_folder_becomes_a_root_and_the_selection(self) -> None:
        chosen = choose_root(self.path("new"), environ=self.environ)
        self.assertTrue(chosen.created)
        self.assertEqual(self.selected(), self.path("new"))
        marker = _marker(self.path("new"))
        self.assertEqual(marker.storage_root_id, chosen.storage_root_id)
        self.assertIsNone(marker.instance_id)

    def test_an_existing_unpaired_folder_keeps_its_storage_root_id(self) -> None:
        root = self.make_root(self.path("existing"))
        before = _marker(root).storage_root_id
        chosen = choose_root(root, environ=self.environ)
        self.assertFalse(chosen.created)
        self.assertEqual(chosen.storage_root_id, before)
        self.assertEqual(_marker(root).storage_root_id, before)

    def test_the_selection_can_change_again_before_setup(self) -> None:
        choose_root(self.path("first"), environ=self.environ)
        choose_root(self.path("second"), environ=self.environ)
        self.assertEqual(self.selected(), self.path("second"))

    def test_testing_mode_never_chooses(self) -> None:
        self.assert_refused("choose_testing", self.path("t"), environ={**self.environ, "PRKS_TESTING": "1"})

    def test_a_folder_the_environment_sets_stays_read_only(self) -> None:
        self.assert_refused("choose_set_by_host", self.path("x"), environ={**self.environ, "PRKS_STORAGE": self.path("env")})

    def test_a_claimed_current_folder_refuses_every_choice(self) -> None:
        self.make_root(self.default_root, claimed=True)
        self.assert_refused("choose_root_claimed", self.path("other"))

    def test_open_another_library_is_refused_and_writes_nothing(self) -> None:
        other = self.make_root(self.path("someone-elses"), claimed=True)
        self.assert_refused("open_another_library_refused", other)

    def test_the_current_folder_is_not_a_choice(self) -> None:
        choose_root(self.path("lib"), environ=self.environ)
        self.assert_refused("choose_same_root", self.path("lib"))

    def test_a_busy_target_is_library_in_use(self) -> None:
        target = self.make_root(self.path("busy"))
        self._held.append(acquire_root_lease(target))
        self.assert_refused("root_in_use", target)

    def test_a_running_prks_on_the_current_folder_refuses(self) -> None:
        self.make_root(self.default_root)
        self._held.append(acquire_root_lease(self.default_root))
        self.assert_refused("root_in_use", self.path("elsewhere"))

    def test_a_recorded_storage_move_refuses(self) -> None:
        with open(self.config_file, "w", encoding="utf-8") as handle:
            json.dump({"format": 1, "storage": {"backend": "local", "local_root": self.default_root,
                                                 "relocation": {"id": "r1", "phase": "copying"}}}, handle)
        self.assert_refused("choose_move_unresolved", self.path("x"))

    def test_rejected_folders_are_refused_with_nothing_written(self) -> None:
        # A link inside the folder (§7.3).
        linked = self.path("linked")
        os.mkdir(linked)
        os.symlink(self.tmp, os.path.join(linked, "pdfs"))
        with self.assertRaises(StorageRootError):
            choose_root(linked, environ=self.environ)
        self.assertEqual(self.selected(), self.default_root)
        self.assertFalse(os.path.exists(os.path.join(linked, MARKER)))
        # A folder holding someone's files (foreign).
        foreign = self.path("foreign")
        os.mkdir(foreign)
        with open(os.path.join(foreign, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("not a library")
        self.assert_refused("root_foreign", foreign)
        # The bootstrap file's own directory (V11).
        self.assert_refused("root_contains_config", os.path.dirname(os.path.dirname(self.config_file)))


class SelectionRaceTests(ChooseTestCase):
    def test_a_start_that_read_the_old_selection_refuses_after_a_choice(self) -> None:
        choose_root(self.path("old"), environ=self.environ)
        # A start reads the bootstrap file and is about to lease ...
        starting = StorageConfig.from_env(environ=self.environ)
        self.assertEqual(starting.root, self.path("old"))
        # ... when a choice commits another folder.
        choose_root(self.path("new"), environ=self.environ)
        with self.assertRaises(StorageRootError) as caught:
            open_storage_root(starting, config_file_path=self.config_file, register=False)
        self.assertEqual(caught.exception.reason, "storage_selection_changed")
        # The committed selection starts normally.
        bound = open_storage_root(StorageConfig.from_env(environ=self.environ), config_file_path=self.config_file,
                                  register=False)
        self.assertEqual(bound.root, self.path("new"))
        bound.release()

    def test_a_default_start_refuses_after_a_first_choice(self) -> None:
        os.remove(self.config_file)  # no selection yet: the default applies
        start = StorageConfig.from_env(cli_root=self.default_root, environ=self.environ)
        for source in (resolver.SOURCE_DEVELOPMENT_DEFAULT, resolver.SOURCE_PLATFORM_DEFAULT):
            with self.subTest(source=source):
                starting = dataclasses.replace(start, root_source=source)
                # Unchanged while nothing is recorded.
                open_storage_root(starting, config_file_path=self.config_file, register=False).release()
                BootstrapConfigStore(self.config_file).compare_and_set(
                    expect=expect_absent(), local_root=self.path("first-choice"))
                with self.assertRaises(StorageRootError) as caught:
                    open_storage_root(starting, config_file_path=self.config_file, register=False)
                self.assertEqual(caught.exception.reason, "storage_selection_changed")
                os.remove(self.config_file)

    def test_a_choice_that_waited_sees_the_folder_another_choice_committed_and_setup_claimed(self) -> None:
        real = choose_mod.refusal_before_target
        calls = []

        def interleave(environ, current_root):
            if not calls:
                calls.append(current_root)
                # While this choice is still outside the config lock, another
                # commits B and setup claims it.
                choose_root(self.path("b"), environ=self.environ)
                self.make_root(self.path("b"), claimed=True)
            return real(environ, current_root)

        with patch.object(choose_mod, "refusal_before_target", side_effect=interleave):
            with self.assertRaises(StorageRootError) as caught:
                choose_root(self.path("c"), environ=self.environ)
        self.assertEqual(calls, [self.default_root])
        self.assertEqual(caught.exception.reason, "choose_root_claimed")
        self.assertEqual(self.selected(), self.path("b"))

    def test_setup_page_process_refuses_once_another_process_chose(self) -> None:
        choose_root(self.path("b"), environ=self.environ)
        self.assert_refused("storage_selection_changed", self.path("c"), held_current_root=self.default_root)

    def test_two_processes_choosing_different_folders_leave_one_committed_selection(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        results = ctx.Queue()
        targets = [self.path("a"), self.path("b")]
        procs = [ctx.Process(target=_choose_in_child, args=(t, self.environ, results)) for t in targets]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(60)
            self.assertEqual(proc.exitcode, 0)
        outcomes = [results.get(timeout=5) for _ in procs]
        chose = [target for kind, target in outcomes if kind == "chose"]
        self.assertTrue(chose, outcomes)
        committed = self.selected()
        self.assertIn(committed, targets)
        # Whoever won, a start for any other folder refuses: no process serves
        # a folder the committed selection does not name.
        for target in targets:
            config = StorageConfig.from_env(environ=self.environ)
            stale = StorageConfig.from_env(cli_root=target, environ={"HOME": self.tmp})
            stale = type(stale)(**{**stale.__dict__, "root_source": "config_file"})
            if target == committed:
                bound = open_storage_root(config, config_file_path=self.config_file, register=False)
                self.assertEqual(bound.root, committed)
                bound.release()
            else:
                with self.assertRaises(StorageRootError) as caught:
                    open_storage_root(stale, config_file_path=self.config_file, register=False)
                self.assertEqual(caught.exception.reason, "storage_selection_changed")


class StorageCommandTests(ChooseTestCase):
    def run_command(self, *argv: str, environ=None) -> tuple[int, str, str]:
        import io

        import prks_app

        out, err = io.StringIO(), io.StringIO()
        code = prks_app.run_storage(list(argv), environ=environ or self.environ, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_storage_choose_records_the_folder(self) -> None:
        code, out, err = self.run_command("choose", self.path("cli-chosen"))
        self.assertEqual((code, err), (0, ""))
        self.assertIn(self.path("cli-chosen"), out)
        self.assertIn("Start PRKS (or restart it)", out)
        self.assertEqual(self.selected(), self.path("cli-chosen"))

    def test_storage_choose_says_why_it_refused(self) -> None:
        code, out, err = self.run_command("choose", self.path("x"), environ={**self.environ, "PRKS_STORAGE": self.path("e")})
        self.assertEqual((code, out), (2, ""))
        self.assertIn("PRKS_STORAGE selects the Library folder", err)
        self.assertEqual(self.selected(), self.default_root)


if __name__ == "__main__":
    unittest.main()
