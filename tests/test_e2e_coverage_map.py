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


class RunnerStatusTests(unittest.TestCase):
    """tests/e2e/run.py's own run_serial() writes what check-run decides on."""

    CASES = "tests.e2e.runner_selfcheck_cases"
    REPORT_ENV = ("PRKS_JUNIT_REPORT_DIR", "PRKS_E2E_BENCHMARK_RESULT", "PRKS_E2E_PROFILE",
                  "PRKS_E2E_HEARTBEAT_FILE", "PRKS_E2E_CANCEL_SENTINEL")

    def test_reports_of_the_calling_run_are_left_alone(self):
        import os

        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(os.environ, {"PRKS_JUNIT_REPORT_DIR": raw}):
            self._run("FailingCases.test_fails")
            self.assertEqual(list(Path(raw).iterdir()), [])
            self.assertEqual(os.environ["PRKS_JUNIT_REPORT_DIR"], raw)

    def _run(self, *ids):
        import contextlib
        import io
        import os

        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"  # importing the runner sets it; keep the unit suite clean
        try:
            from tests.e2e import run as runner

            with tempfile.TemporaryDirectory() as raw, mock.patch.dict(os.environ, {cm.ENV: raw}), \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                # The self-check's deliberate failure and skip must not reach a
                # real run's reports (mock.patch.dict restores these on exit).
                for name in self.REPORT_ENV:
                    os.environ.pop(name, None)
                passed, _, _, _ = runner.run_serial(["%s.%s" % (self.CASES, i) for i in ids], False)
                return passed, json.loads((Path(raw) / cm.RUN_STATUS).read_text(encoding="utf-8")), \
                    cm.check_run(Path(raw))
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_a_failure_alone_is_tolerated(self):
        passed, status, (ok, _why) = self._run("FailingCases.test_fails", "PassingCases.test_first")
        self.assertFalse(passed)
        self.assertEqual((status["selected"], status["ran"], status["failures"]), (2, 2, 1))
        self.assertTrue(ok)

    def test_a_failure_with_a_skip_fails_the_shard(self):
        passed, status, (ok, why) = self._run("FailingCases.test_fails", "SkippingCases.test_skips")
        self.assertFalse(passed)
        self.assertEqual(status["failures"], 1)
        self.assertIn("SkippingCases.test_skips", status["skip_error"])
        self.assertFalse(ok)
        self.assertIn("skipped", why)


class CheckRunTests(unittest.TestCase):
    """A failed recording shard still counts only when it ran to its end."""

    def _status(self, folder, **fields):
        status = {"selected": 3, "ran": 3, "failures": 1, "errors": 0, "count_error": None, "skip_error": None}
        status.update(fields)
        with mock.patch.dict("os.environ", {cm.ENV: folder}):
            cm.write_run_status(**status)
        return cm.check_run(Path(folder))

    def test_only_test_failures_in_a_complete_shard_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, why = self._status(folder)
        self.assertTrue(ok, why)
        self.assertIn("all 3 tests ran", why)

    def test_a_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, why = self._status(folder, ran=2, failures=0, count_error="selected 3 tests but 2 ran")
        self.assertFalse(ok)
        self.assertIn("2 ran", why)

    def test_an_unexpected_skip_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, why = self._status(folder, failures=0, skip_error="unexpected skip: t.a")
        self.assertFalse(ok)
        self.assertIn("unexpected skip", why)

    def test_fewer_tests_than_selected_fails_even_without_a_count_error(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, _why = self._status(folder, ran=2)
        self.assertFalse(ok)

    def test_a_failure_with_no_failed_test_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, why = self._status(folder, failures=0, errors=0)
        self.assertFalse(ok)
        self.assertIn("without a test failure", why)

    def test_no_status_means_the_shard_did_not_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, why = cm.check_run(Path(folder))
            self.assertEqual(cm.main(["check-run", folder]), 1)
        self.assertFalse(ok)
        self.assertIn("did not reach its end", why)

    def test_the_workflow_reads_the_status_instead_of_grepping_the_log(self):
        text = (Path(cm.REPO) / ".github" / "workflows" / "e2e-coverage-map.yml").read_text(encoding="utf-8")
        step = text.split("      - name: Record the shard's coverage", 1)[1].split("      - name:", 1)[0]
        self.assertIn("python -m tests.e2e.coverage_map check-run", step)
        self.assertIn("set +e", step)
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("| tee", step)


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

    def _vue_repo(self):
        (self.repo / "frontend-app" / "src" / "components").mkdir(parents=True)
        (self.repo / VUE).write_text(CARD)
        (self.repo / "frontend" / "vue").mkdir(parents=True)
        (self.repo / "frontend" / "vue" / "prks-vue.js").write_text("bundle1\n")
        (self.repo / "frontend" / "sw.js").write_text("a\n    const DEPENDENCY_REVISION = 'aaa111';\nb\n")
        self._commit()
        self.base = _git(self.repo, "rev-parse", "HEAD")
        return {"commit": self.base, "files": {VUE: [[2, 2, 0], [6, 6, 1], [9, 9, 1]]},
                "code_lines": {VUE: [[2, 2], [6, 6], [9, 9]]}}

    def _rebuild(self, revision="bbb222"):
        (self.repo / "frontend" / "vue" / "prks-vue.js").write_text("bundle2\n")
        sw = self.repo / "frontend" / "sw.js"
        sw.write_text(sw.read_text().replace("aaa111", revision))

    def test_a_vue_change_with_its_rebuilt_bundle_selects_from_the_map(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD.replace("function go() { return 1 }", "function go() { return 2 }"))
        self._rebuild()
        self._commit()
        result = self._select(**overrides)
        self.assertEqual(result["mode"], "subset")
        self.assertEqual(result["selected"], [self.IDS[1]])
        self.assertEqual(result["files"]["frontend/vue/prks-vue.js"], {"rule": "derived"})
        self.assertEqual(result["files"]["frontend/sw.js"], {"rule": "derived"})

    def test_a_vue_line_without_code_selects_through_its_neighbours(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD.replace("{ size: 'md' }", "{ size: 'lg' }"))
        self._commit()
        result = self._select(**overrides)
        self.assertEqual(result["selected"], self.IDS)

    def test_a_vue_style_change_runs_the_full_suite(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD.replace("color: red", "color: blue"))
        self._commit()
        result = self._select(**overrides)
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["files"][VUE]["why"], "vue-style")

    def test_a_new_style_block_runs_the_full_suite(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD + "<style>\n.x { margin: 0 }\n</style>\n")
        self._commit()
        self.assertEqual(self._select(**overrides)["files"][VUE]["why"], "vue-style")

    def test_a_moved_or_deleted_revision_line_is_not_derived(self):
        overrides = self._vue_repo()
        sw = self.repo / "frontend" / "sw.js"
        sw.write_text("a\nb\n    const DEPENDENCY_REVISION = 'aaa111';\n")
        self._commit()
        self.assertEqual(self._select(**overrides)["files"]["frontend/sw.js"]["rule"], "full")
        sw.write_text("a\nb\n")
        self._commit()
        self.assertEqual(self._select(**overrides)["files"]["frontend/sw.js"]["rule"], "full")

    def test_an_insertion_just_above_a_style_block_is_not_a_style_change(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD.replace("</template>\n", "</template>\n<!-- note -->\n"))
        self._commit()
        self.assertEqual(self._select(**overrides)["files"][VUE]["rule"], "coverage-map")

    def test_commenting_out_a_style_block_runs_the_full_suite(self):
        overrides = self._vue_repo()
        (self.repo / VUE).write_text(CARD.replace("<style scoped>\n", "<!--\n<style scoped>\n")
                                     .replace("</style>\n", "</style>\n-->\n"))
        self._commit()
        self.assertEqual(self._select(**overrides)["files"][VUE]["why"], "vue-style")

    def test_any_other_sw_change_is_not_derived(self):
        overrides = self._vue_repo()
        sw = self.repo / "frontend" / "sw.js"
        sw.write_text(sw.read_text().replace("b\n", "c\n"))
        self._commit()
        result = self._select(**overrides)
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["files"]["frontend/sw.js"]["rule"], "full")

    def test_a_vue_source_the_map_never_saw_runs_the_full_suite(self):
        overrides = self._vue_repo()
        (self.repo / "frontend-app" / "src" / "types.ts").write_text("export type X = 1\n")
        self._commit()
        result = self._select(**overrides)
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["files"]["frontend-app/src/types.ts"]["why"], "frontend-full")

    def test_a_bundle_change_alone_is_a_skip(self):
        overrides = self._vue_repo()
        self._rebuild()
        self._commit()
        self.assertEqual(self._select(**overrides)["mode"], "skip")

    def test_a_map_commit_that_is_not_a_sha_is_refused(self):
        for commit in ("--output=/tmp/x", "main", ""):
            with self.subTest(commit=commit), self.assertRaisesRegex(ValueError, "not a full SHA"):
                self._select(commit=commit)

    def test_an_option_as_a_revision_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not a revision"):
            cm.select_for_diff([self.map], repo=self.repo, base="--output=/tmp/x", head="HEAD", all_ids=self.IDS)


VUE = "frontend-app/src/components/Card.vue"
CARD = "".join([
    "<script setup lang=\"ts\">\n",        # 1
    "const props = withDefaults(\n",       # 2  (code)
    "  defineProps<{ size?: string }>(),\n",  # 3
    "  { size: 'md' },\n",                 # 4  no mapping of its own
    ")\n",                                  # 5
    "function go() { return 1 }\n",         # 6  (code)
    "</script>\n",                          # 7
    "<template>\n",                         # 8
    "  <button @click=\"go\">x</button>\n",   # 9  (code)
    "</template>\n",                        # 10
    "<style scoped>\n",                     # 11
    ".card { color: red }\n",               # 12
    "</style>\n",                           # 13
])


class SourceMapTests(unittest.TestCase):
    def test_decodes_v3_mappings(self):
        # Line 0: col 0 -> source 0 line 0; col 2 -> source 0 line 1. Line 1: col 0 -> source 1 line 1.
        self.assertEqual(cm.decode_mappings("AAAA,EACA;ACAA"), [(0, 0, 0, 0), (0, 2, 0, 1), (1, 0, 1, 1)])
        # Negative deltas, and a one-field segment: generated code with no source.
        self.assertEqual(cm.decode_mappings("AAEA,C,EADA"), [(0, 0, 0, 2), (0, 1, -1, -1), (0, 3, 0, 1)])

    def _bundle(self, repo: Path) -> cm.BundleMap:
        out = repo / "out"
        out.mkdir()
        source_map = {
            "version": 3,
            "sources": ["../frontend-app/src/components/Card.vue", "../frontend-app/node_modules/vue/x.js",
                        "../frontend-app/src/components/Card.test.ts", "\u0000plugin-vue:export-helper"],
            # Generated line 0: col 0 -> Card line 2, col 10 -> vue (dropped), col 20 -> Card line 6,
            # col 30 -> test file (dropped), col 40 -> Card line 9.
            "mappings": "AACA,UCAA,UDIA,UEAA,UFGA",
        }
        return cm.BundleMap(source_map, out, [0], repo=repo)

    def test_only_shipped_vue_sources_are_kept(self):
        with tempfile.TemporaryDirectory() as raw:
            bundle = self._bundle(Path(raw))
        self.assertEqual(bundle.code_lines, {VUE: {2, 6, 9}})

    def test_executed_pieces_map_to_their_source_lines(self):
        with tempfile.TemporaryDirectory() as raw:
            bundle = self._bundle(Path(raw))
        # 0-15 ran (Card 2 and the dropped vue line), 15-35 did not, 35-45 ran (the mapping at 30 is in effect).
        self.assertEqual(bundle.lines([(0, 15, 1), (15, 35, 0), (35, 45, 1)]), {VUE: {2, 9}})

    def test_unmapped_code_and_a_new_line_credit_no_source(self):
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "out"
            out.mkdir()
            # Line 0: col 0 -> Card line 2, col 5 unmapped, col 10 -> Card line 3 (the line ends mapped).
            # Line 1 (offset 20) has no mapping at all, so nothing carries over the newline.
            source_map = {"sources": ["../frontend-app/src/components/Card.vue"], "mappings": "AACA,K,KACA;"}
            bundle = cm.BundleMap(source_map, out, [0, 20], repo=Path(raw))
        self.assertEqual(bundle.lines([(6, 9, 1)]), {})
        self.assertEqual(bundle.lines([(21, 25, 1)]), {})
        self.assertEqual(bundle.lines([(0, 4, 1)]), {VUE: {2}})
        self.assertEqual(bundle.lines([(11, 15, 1)]), {VUE: {3}})

    def test_the_map_lists_code_lines_when_built_with_a_bundle(self):
        with tempfile.TemporaryDirectory() as raw:
            bundle = self._bundle(Path(raw))
            records = Path(raw) / "records-1.jsonl"
            records.write_text(json.dumps({"test": "t1", "kind": "js", "files": {VUE: [3]}}) + "\n")
            built = cm.build_map([records], commit="a" * 40, bundle=bundle)
        self.assertEqual(built["code_lines"], {VUE: [[2, 2], [6, 6], [9, 9]]})
        self.assertNotIn("code_lines", cm.build_map([], commit="a" * 40))

    def test_a_line_without_code_stands_for_its_code_neighbours(self):
        self.assertEqual(cm.with_code_neighbours({4}, [[2, 2], [6, 6], [9, 9]]), {2, 6})
        self.assertEqual(cm.with_code_neighbours({6, 12}, [[2, 2], [6, 6], [9, 9]]), {6, 9})
        self.assertEqual(cm.with_code_neighbours({1}, []), {1})

    def test_live_style_blocks_are_found(self):
        self.assertEqual(cm.active_styles(CARD), ["<style scoped>\n.card { color: red }\n</style>"])
        self.assertEqual(cm.active_styles("<style>a{}</style>\n<template/>\n"), ["<style>a{}</style>"])
        self.assertEqual(cm.active_styles("<!--\n<style>a{}</style>\n-->\n"), [])
        self.assertEqual(cm.active_styles("<!-- open\n<style>a{}</style>\n"), [])
        self.assertEqual(cm.active_styles("<script>const s = '<style>a{}</style>'</script>\n"), [])

    def test_only_the_bundle_url_is_the_bundle(self):
        self.assertTrue(cm._is_vue_bundle("http://127.0.0.1:8123/vue/prks-vue.js?v=1"))
        self.assertFalse(cm._is_vue_bundle("http://example.test/vue/prks-vue.js"))
        self.assertFalse(cm._is_vue_bundle("http://127.0.0.1:8123/js/app.js"))


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
        for rel in ("backend/api/tags.py", "prks_app.py", "frontend/js/app.js", "frontend-app/src/App.vue",
                    "frontend-app/src/domain/x.ts"):
            self.assertTrue(cm.is_mapped_kind(rel), rel)
        for rel in ("frontend/css/style.css", "frontend/vue/prks-vue.js", "frontend-app/src/App.test.ts",
                    "frontend-app/src/Card.stories.ts", "frontend-app/vite.config.ts",
                    "backend/db/schema/snapshot.json", "tests/test_x.py"):
            self.assertFalse(cm.is_mapped_kind(rel), rel)


class RecordingWorkflowTests(unittest.TestCase):
    def test_the_vue_source_map_is_hidden_and_only_of_the_committed_bundle(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/e2e-coverage-map.yml").read_text(encoding="utf-8")
        self.assertIn("--sourcemap hidden", text)
        self.assertIn('cmp -s "$out/prks-vue.js" frontend/vue/prks-vue.js', text)
        self.assertIn(f"{cm.VUE_SOURCE_MAP_ENV}=$out/prks-vue.js.map", text)
        # The map is built before the tests record, so every shard reads it.
        self.assertLess(text.index("--sourcemap hidden"), text.index("Record the shard's coverage"))


if __name__ == "__main__":
    unittest.main()
