"""The E2E coverage map's pure parts: line attribution, merging and diff selection."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import coverage_map as cm


def _fn(start, end, count):
    return {"functionName": "", "ranges": [{"startOffset": start, "endOffset": end, "count": count}]}


class ExecutedLinesTests(unittest.TestCase):
    # Line starts of a five-line script: offsets 0, 10, 20, 30, 40.
    STARTS = [0, 10, 20, 30, 40]

    def test_a_nested_function_that_never_ran_is_unexecuted_inside_one_that_ran(self):
        # The outer function's own code reaches into lines 2 and 4 (offsets
        # 10-11 and 35-39); line 3 is wholly inside the uncalled function.
        functions = [_fn(0, 50, 1), _fn(12, 35, 0)]
        self.assertEqual(cm.executed_lines(functions, self.STARTS), {1, 2, 4, 5})

    def test_a_nested_function_that_ran_marks_its_lines(self):
        functions = [_fn(0, 50, 1), _fn(12, 35, 0), _fn(21, 28, 1)]
        self.assertEqual(cm.executed_lines(functions, self.STARTS), {1, 2, 3, 4, 5})

    def test_an_end_offset_at_a_line_start_stays_on_the_line_before(self):
        self.assertEqual(cm.executed_lines([_fn(0, 20, 1)], self.STARTS), {1, 2})

    def test_nothing_ran(self):
        self.assertEqual(cm.executed_lines([_fn(0, 50, 0)], self.STARTS), set())

    def test_an_uncalled_inline_callback_keeps_the_statement_that_ran(self):
        text = "go();\nctx.on(x) || function () {};\ndone();\n"
        starts = [0, 6, 36]
        inner = text.index("function")
        functions = [_fn(0, len(text), 1), _fn(inner, inner + len("function () {}"), 0)]
        self.assertEqual(cm.executed_lines(functions, starts, text), {1, 2, 3})

    def test_indentation_before_an_uncalled_function_does_not_count(self):
        text = "run();\n  function never() {\n    x();\n  }\nend();\n"
        starts = [0, 7, 28, 37, 41]
        inner = text.index("function")
        functions = [_fn(0, len(text), 1), _fn(inner, text.index("}") + 1, 0)]
        self.assertEqual(cm.executed_lines(functions, starts, text), {1, 5})

    def test_source_offsets_count_utf16_units(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(cm, "REPO", Path(folder)), mock.patch.dict(cm._SOURCE_LINES, clear=True):
            (Path(folder) / "a.js").write_text("x('\U0001F600');\ny();\n", encoding="utf-8")
            text, starts = cm._source("a.js")
        # The emoji is two UTF-16 units, so line 2 starts at offset 9, not 8.
        self.assertEqual(starts, [0, 9, 14])
        self.assertEqual(text[9:13], "y();")


class RepoPathTests(unittest.TestCase):
    def test_only_local_classic_scripts_map_to_files(self):
        self.assertEqual(cm._repo_path_for("http://127.0.0.1:8123/js/app.js"), "frontend/js/app.js")
        self.assertEqual(
            cm._repo_path_for("http://127.0.0.1:8123/js/components/research-graph.js"),
            "frontend/js/components/research-graph.js",
        )
        for url in (
            "https://example.test/js/app.js",
            "http://127.0.0.1:8123/vendor/lucide/lucide.min.js",
            "http://127.0.0.1:8123/vue/prks-vue.js",
            "http://127.0.0.1:8123/js/../sw.js",
            "http://127.0.0.1:8123/js/missing-file.js",
            "data:text/javascript,1",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(cm._repo_path_for(url))


class BuildMapTests(unittest.TestCase):
    def _records(self, folder, rows):
        path = Path(folder) / "records-1.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return [path]

    def test_runs_group_consecutive_lines_with_one_test_set(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = self._records(folder, [
                {"test": "t.a", "kind": "py", "files": {"backend/x.py": [1, 2, 3, 7]}},
                {"test": "t.b", "kind": "py", "files": {"backend/x.py": [1, 2, 7]}},
                {"test": "t.b", "kind": "js", "files": {"frontend/js/y.js": [4]}},
            ])
            built = cm.build_map(paths, commit="abc")
        self.assertEqual(built["tests"], ["t.a", "t.b"])
        both = built["sets"].index([0, 1])
        only_a = built["sets"].index([0])
        self.assertEqual(built["files"]["backend/x.py"], [[1, 2, both], [3, 3, only_a], [7, 7, both]])
        self.assertEqual(cm.tests_for_lines(built, "backend/x.py", [3]), {"t.a"})
        self.assertEqual(cm.tests_for_lines(built, "backend/x.py", [5, 6]), set())
        self.assertEqual(cm.tests_for_lines(built, "frontend/js/y.js", [4]), {"t.b"})
        self.assertEqual(cm.tests_for_lines(built, "backend/none.py", [1]), set())

    def test_the_cli_writes_a_gzipped_map(self):
        with tempfile.TemporaryDirectory() as folder:
            self._records(folder, [{"test": "t.a", "kind": "py", "files": {"backend/x.py": [1]}}])
            out = Path(folder) / "map.json.gz"
            self.assertEqual(cm.main(["build", folder, "--commit", "abc", "--output", str(out)]), 0)
            with gzip.open(out, "rt", encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["commit"], "abc")

    def test_the_cli_refuses_an_empty_record_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(cm.main(["build", folder, "--commit", "abc", "--output", f"{folder}/m.gz"]), 1)

    def test_records_skip_empty_files_and_missing_test_ids(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {cm.ENV: folder}):
            cm._write("", "py", {"backend/x.py": [1]})
            cm._write("t.a", "py", {"backend/x.py": []})
            self.assertEqual(list(Path(folder).glob("*.jsonl")), [])
            cm._write("t.a", "py", {"backend/x.py": [2, 1, 2]})
            (written,) = Path(folder).glob("records-*.jsonl")
            self.assertEqual(json.loads(written.read_text())["files"], {"backend/x.py": [1, 2]})


def _git(repo, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class SelectForDiffTests(unittest.TestCase):
    IDS = ["tests.e2e.test_a.T.test_one", "tests.e2e.test_b.T.test_two"]

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.repo = Path(self._folder.name)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.test")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "backend").mkdir()
        (self.repo / "backend" / "x.py").write_text("".join(f"line{n}\n" for n in range(1, 21)))
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        self.map = {"commit": self.base, "tests": self.IDS, "sets": [[0], [1]],
                    "files": {"backend/x.py": [[1, 5, 0], [10, 20, 1]]}}

    def _select(self, **overrides):
        maps = [dict(self.map, **overrides)]
        with mock.patch("tests.e2e.policy.select_smoke", return_value=[]):
            return cm.select_for_diff(maps, repo=self.repo, base=self.base, head="HEAD", all_ids=self.IDS)

    def _commit(self):
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "change")

    def test_a_changed_line_selects_the_tests_that_ran_it(self):
        path = self.repo / "backend" / "x.py"
        path.write_text(path.read_text().replace("line3\n", "changed\n"))
        self._commit()
        result = self._select()
        self.assertEqual(result["mode"], "subset")
        self.assertEqual(result["selected"], [self.IDS[0]])

    def test_a_production_file_renamed_into_docs_is_still_a_production_change(self):
        (self.repo / "docs").mkdir()
        _git(self.repo, "mv", "backend/x.py", "docs/x-notes.md")
        self._commit()
        result = self._select()
        self.assertNotEqual(result["mode"], "skip")
        self.assertEqual(result["files"]["backend/x.py"]["rule"], "coverage-map")
        self.assertEqual(result["selected"], self.IDS)

    def test_an_unmeasured_stylesheet_runs_the_full_suite(self):
        (self.repo / "frontend" / "css").mkdir(parents=True)
        (self.repo / "frontend" / "css" / "style.css").write_text("a{}\n")
        self._commit()
        result = self._select()
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["files"]["frontend/css/style.css"]["why"], "frontend-full")

    def test_a_map_commit_that_is_not_a_sha_is_refused(self):
        for commit in ("--output=/tmp/x", "main", ""):
            with self.subTest(commit=commit), self.assertRaisesRegex(ValueError, "not a full SHA"):
                self._select(commit=commit)

    def test_an_option_as_a_revision_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not a revision"):
            cm.select_for_diff([self.map], repo=self.repo, base="--output=/tmp/x", head="HEAD", all_ids=self.IDS)


class DiffLineTests(unittest.TestCase):
    DIFF = (
        "diff --git a/backend/x.py b/backend/x.py\n"
        "@@ -3 +3 @@ def f():\n"
        "@@ -10,2 +10,0 @@\n"
        "@@ -20,0 +19,3 @@\n"
    )

    def test_hunks_and_touched_old_lines(self):
        hunks = cm.parse_hunks(self.DIFF)
        self.assertEqual(hunks, [(3, 1, 3, 1), (10, 2, 10, 0), (20, 0, 19, 3)])
        # A modification, a deletion, and an insertion's two neighbours.
        self.assertEqual(cm.touched_old_lines(hunks), {3, 10, 11, 20, 21})

    def test_translate_shifts_lines_after_a_hunk(self):
        # The later version inserted 2 lines after earlier line 5.
        drift = [(5, 0, 6, 2)]
        self.assertEqual(cm.translate_lines({3}, drift), {3})
        self.assertEqual(cm.translate_lines({10}, drift), {8})

    def test_translate_maps_an_edited_line_to_the_code_it_replaced(self):
        # Earlier lines 4-6 became later lines 4-5.
        drift = [(4, 3, 4, 2)]
        self.assertEqual(cm.translate_lines({5}, drift), {4, 5, 6})
        self.assertEqual(cm.translate_lines({7}, drift), {8})

    def test_translate_shifts_lines_after_a_deletion(self):
        # Earlier lines 10-11 were deleted (after later line 9).
        self.assertEqual(cm.translate_lines({12}, [(10, 2, 9, 0)]), {14})

    def test_translate_maps_added_lines_to_their_neighbours(self):
        drift = [(5, 0, 6, 2)]
        self.assertEqual(cm.translate_lines({7}, drift), {5, 6})

    def test_mapped_kinds(self):
        for rel in ("backend/api/tags.py", "prks_app.py", "frontend/js/app.js"):
            self.assertTrue(cm.is_mapped_kind(rel), rel)
        for rel in ("frontend/css/style.css", "frontend/vue/prks-vue.js", "frontend-app/src/App.vue",
                    "backend/db/schema/snapshot.json", "tests/test_x.py"):
            self.assertFalse(cm.is_mapped_kind(rel), rel)


if __name__ == "__main__":
    unittest.main()
