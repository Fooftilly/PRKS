"""Unit/selfcheck coverage for ``scripts/e2e doctor`` / ``tests.e2e.doctor``."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import doctor
from tests.e2e.sharding import AGENT_MEMORY_PER_JOB_BYTES, BASELINE_TIMINGS_PATH, TIMINGS_PATH

REPO = Path(__file__).resolve().parents[1]


class FormatBytesTests(unittest.TestCase):
    def test_formats_are_stable(self):
        self.assertEqual(doctor._fmt_bytes(None), "unknown")
        self.assertEqual(doctor._fmt_bytes(512), "512 B")
        self.assertEqual(doctor._fmt_bytes(2048), "2 KiB")
        self.assertEqual(doctor._fmt_bytes(3 * 1024 * 1024 * 1024), "3 GiB")


class AssessmentClassificationTests(unittest.TestCase):
    def _facts(
        self,
        *,
        playwright_installed="1.63.0",
        playwright_pinned="1.63.0",
        chromium_available=True,
        cpu_effective=4,
        memory_limit_bytes=16 * AGENT_MEMORY_PER_JOB_BYTES,
        shm_avail=2 * 1024 * 1024 * 1024,
        temp_avail=8 * 1024 * 1024 * 1024,
        agent_default_workers=2,
    ):
        return {
            "browser": {
                "playwright_installed": playwright_installed,
                "playwright_pinned": playwright_pinned,
                "playwright_match": bool(
                    playwright_installed
                    and playwright_pinned
                    and playwright_installed == playwright_pinned
                ),
                "chromium_available": chromium_available,
                "browsers_dir": "/repo/.playwright-browsers",
            },
            "resources": {
                "cpu_effective": cpu_effective,
                "memory_limit_bytes": memory_limit_bytes,
                "shm": {
                    "present": shm_avail is not None,
                    "avail_bytes": shm_avail,
                },
                "temp": {
                    "present": temp_avail is not None,
                    "avail_bytes": temp_avail,
                },
            },
            "agent_default_workers": agent_default_workers,
        }

    def test_adequate_points_at_app_regression(self):
        result = doctor.classify_assessment(self._facts())
        self.assertEqual(result["kind"], "resources_look_adequate")
        self.assertIn("app/regression", result["hint"])

    def test_under_resourced_cpu(self):
        result = doctor.classify_assessment(
            self._facts(cpu_effective=1, agent_default_workers=1)
        )
        self.assertEqual(result["kind"], "under_resourced_container")
        self.assertTrue(any("effective CPU" in r for r in result["reasons"]))
        self.assertIn("resource pressure", result["hint"])

    def test_under_resourced_memory(self):
        result = doctor.classify_assessment(
            self._facts(
                memory_limit_bytes=AGENT_MEMORY_PER_JOB_BYTES // 2,
                agent_default_workers=1,
            )
        )
        self.assertEqual(result["kind"], "under_resourced_container")
        self.assertTrue(any("cgroup memory" in r for r in result["reasons"]))

    def test_browser_toolchain_gap(self):
        result = doctor.classify_assessment(
            self._facts(playwright_installed=None, chromium_available=False)
        )
        self.assertEqual(result["kind"], "browser_toolchain_gap")
        self.assertIn("environment gap", result["hint"])

    def test_combined_setup_and_resources(self):
        result = doctor.classify_assessment(
            self._facts(
                playwright_installed="1.0.0",
                playwright_pinned="1.63.0",
                chromium_available=False,
                cpu_effective=1,
                agent_default_workers=1,
            )
        )
        self.assertEqual(result["kind"], "setup_and_under_resourced")

    def test_unknown_resource_probes_are_not_adequate(self):
        # Present-but-unreadable shm + failed temp must not claim adequate.
        facts = self._facts(shm_avail=None, temp_avail=None)
        facts["resources"]["shm"]["present"] = True
        result = doctor.classify_assessment(facts)
        self.assertEqual(result["kind"], "resource_facts_incomplete")
        self.assertNotEqual(result["kind"], "resources_look_adequate")
        self.assertTrue(any("shm" in r for r in result["reasons"]))

    def test_absent_shm_does_not_force_incomplete(self):
        # Windows / non-Linux: no /dev/shm is inapplicable, not a probe failure.
        facts = self._facts(shm_avail=None)
        facts["resources"]["shm"] = {
            "present": False,
            "avail_bytes": None,
            "error": "not present / inapplicable",
        }
        result = doctor.classify_assessment(facts)
        self.assertEqual(result["kind"], "resources_look_adequate")


class DiskProbeTests(unittest.TestCase):
    def test_portable_without_statvfs(self):
        # Regression: Windows has no os.statvfs. On Linux shutil.disk_usage still
        # calls it; AttributeError must become a structured error, never a crash.
        with mock.patch.object(
            doctor.os,
            "statvfs",
            side_effect=AttributeError("module 'os' has no attribute 'statvfs'"),
            create=True,
        ):
            result = doctor._disk_usage_bytes(Path(tempfile.gettempdir()))
        self.assertTrue(result["present"])
        self.assertIsNone(result["avail_bytes"])
        self.assertIn("AttributeError", result["error"])

    def test_disk_usage_success_path(self):
        result = doctor._disk_usage_bytes(Path(tempfile.gettempdir()))
        self.assertTrue(result["present"])
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["avail_bytes"])
        self.assertGreater(result["avail_bytes"], 0)

    def test_absent_shm_path_is_inapplicable(self):
        missing = Path(tempfile.mkdtemp()) / "no-such-shm"
        result = doctor._disk_usage_bytes(missing)
        self.assertFalse(result["present"])
        self.assertIsNone(result["avail_bytes"])
        self.assertIn("inapplicable", result["error"])

    def test_collect_report_survives_missing_statvfs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tests" / "e2e").mkdir(parents=True)
            with mock.patch.object(
                doctor.os,
                "statvfs",
                side_effect=AttributeError("no statvfs"),
                create=True,
            ):
                with mock.patch.object(doctor, "_chromium_probe") as probe:
                    probe.return_value = {
                        "playwright_installed": None,
                        "playwright_pinned": "1.63.0",
                        "playwright_pin_error": None,
                        "playwright_match": False,
                        "chromium_revision": None,
                        "chromium_revision_error": None,
                        "chromium_available": False,
                        "chromium_path": None,
                        "chromium_version": None,
                        "browsers_dir": str(root / ".playwright-browsers"),
                    }
                    with mock.patch.object(doctor, "detect_cgroup_cpu_count", return_value=4):
                        with mock.patch.object(
                            doctor, "detect_cgroup_memory_limit_bytes", return_value=None
                        ):
                            with mock.patch.object(
                                doctor, "_cpu_affinity_count", return_value=4
                            ):
                                with mock.patch.object(
                                    doctor, "_cgroup_cpu_quota_count", return_value=None
                                ):
                                    facts = doctor.collect_report(
                                        repo=root, environ={}
                                    )
                                    text = doctor.format_report(facts)
        self.assertIn("PRKS E2E doctor", text)
        self.assertIn("disk_temp:", text)
        # Must not raise; temp may be error-structured when statvfs is gone.
        self.assertIn(facts["resources"]["temp"]["path"], text)


class CgroupCpuQuotaTests(unittest.TestCase):
    def test_quota_uses_tightest_finite_ancestor(self):
        import tests.e2e.sharding as sharding

        leaf = Path("/sys/fs/cgroup/pod/agent/workload")
        mid = Path("/sys/fs/cgroup/pod/agent")
        root = Path("/sys/fs/cgroup")
        values = {
            leaf / "cpu.max": "400000 100000",  # 4 CPUs at leaf
            mid / "cpu.max": "100000 100000",  # 1 CPU parent (tighter)
            root / "cpu.max": "max 100000",
        }

        def fake_dirs():
            yield leaf
            yield mid
            yield root

        def fake_read(paths):
            for path in paths:
                if path in values:
                    return values[path]
            return None

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_dirs):
            with mock.patch.object(sharding, "_read_first", fake_read):
                self.assertEqual(doctor._cgroup_cpu_quota_count(), 1)


class ChromiumProbeTests(unittest.TestCase):
    def test_available_requires_version_probe(self):
        browsers = Path("/repo/.playwright-browsers")
        fake_exe = browsers / "chromium-1" / "chrome-linux" / "chrome"
        with mock.patch.object(doctor, "installed_playwright_version", return_value="1.63.0"):
            with mock.patch.object(doctor, "pinned_playwright_version", return_value="1.63.0"):
                with mock.patch.object(
                    doctor, "playwright_chromium_revision", return_value="1"
                ):
                    with mock.patch.object(
                        doctor, "chromium_executable", return_value=fake_exe
                    ):
                        with mock.patch.object(
                            doctor, "_chrome_version_string", return_value=None
                        ):
                            probe = doctor._chromium_probe(browsers)
        self.assertFalse(probe["chromium_available"])
        self.assertEqual(probe["chromium_path"], str(fake_exe))

    def test_available_false_when_version_command_fails_with_stderr(self):
        browsers = Path("/repo/.playwright-browsers")
        fake_exe = browsers / "chromium-1" / "chrome-linux" / "chrome"
        failed = subprocess.CompletedProcess(
            args=[str(fake_exe), "--version"],
            returncode=127,
            stdout="",
            stderr="error while loading shared libraries: libnss3.so",
        )
        with mock.patch.object(doctor, "installed_playwright_version", return_value="1.63.0"):
            with mock.patch.object(doctor, "pinned_playwright_version", return_value="1.63.0"):
                with mock.patch.object(
                    doctor, "playwright_chromium_revision", return_value="1"
                ):
                    with mock.patch.object(
                        doctor, "chromium_executable", return_value=fake_exe
                    ):
                        with mock.patch.object(
                            doctor.subprocess, "run", return_value=failed
                        ):
                            probe = doctor._chromium_probe(browsers)
        self.assertIsNone(probe["chromium_version"])
        self.assertFalse(probe["chromium_available"])
        self.assertEqual(probe["chromium_path"], str(fake_exe))


class FormatReportTests(unittest.TestCase):
    def test_format_is_deterministic_and_pasteable(self):
        facts = {
            "python": {
                "version": "3.12.3",
                "implementation": "CPython",
                "executable": "/usr/bin/python3",
            },
            "browser": {
                "playwright_installed": "1.63.0",
                "playwright_pinned": "1.63.0",
                "playwright_pin_error": None,
                "playwright_match": True,
                "chromium_revision": "1200",
                "chromium_revision_error": None,
                "chromium_available": True,
                "chromium_path": "/repo/.playwright-browsers/chromium-1200/chrome-linux/chrome",
                "chromium_version": "Chromium 120.0.0",
                "browsers_dir": "/repo/.playwright-browsers",
                "browsers_dir_inherited": "/external/pw-cache",
            },
            "resources": {
                "cpu_affinity": 4,
                "cpu_cgroup_quota": 2,
                "cpu_effective": 2,
                "memory_limit_bytes": 4 * 1024 * 1024 * 1024,
                "shm": {
                    "path": str(Path(os.sep) / "dev" / "shm"),
                    "present": True,
                    "total_bytes": 64 * 1024 * 1024,
                    "avail_bytes": 64 * 1024 * 1024,
                    "error": None,
                },
                "temp": {
                    "path": tempfile.gettempdir(),
                    "present": True,
                    "total_bytes": 20 * 1024 * 1024 * 1024,
                    "avail_bytes": 10 * 1024 * 1024 * 1024,
                    "error": None,
                },
            },
            "agent_default_workers": 1,
            "timing_history_local": {
                "path": str(TIMINGS_PATH).replace("\\", "/"),
                "present": False,
                "entries": 0,
            },
            "timing_baseline_committed": {
                "path": str(BASELINE_TIMINGS_PATH).replace("\\", "/"),
                "present": True,
                "entries": 14,
            },
            "env": {key: None for key in doctor.E2E_ENV_KEYS},
            "assessment": {
                "kind": "under_resourced_container",
                "reasons": ["effective CPU is 2"],
                "hint": "resource pressure hint",
            },
        }
        text = doctor.format_report(facts)
        again = doctor.format_report(facts)
        self.assertEqual(text, again)
        self.assertTrue(text.startswith("PRKS E2E doctor (read-only)\n"))
        self.assertIn("python: 3.12.3 (CPython)", text)
        self.assertIn("playwright: installed=1.63.0 pinned=1.63.0 match=yes", text)
        self.assertIn("browsers_dir: /repo/.playwright-browsers", text)
        self.assertIn(
            "playwright_browsers_path_inherited: /external/pw-cache", text
        )
        self.assertIn("cpu: affinity_or_cpuset=4 cgroup_quota=2 effective=2", text)
        self.assertIn("memory_cgroup: 4 GiB", text)
        self.assertIn("agent_default_workers: 1", text)
        self.assertIn("timing_history_local: present=no", text)
        self.assertIn("timing_baseline_committed: present=yes", text)
        self.assertIn("assessment: under_resourced_container", text)
        self.assertIn("PRKS_E2E_JOBS=(unset)", text)
        # Env keys appear in sorted declaration order.
        idx_jobs = text.index("PRKS_E2E_JOBS=")
        idx_profile = text.index("PRKS_E2E_PROFILE=")
        self.assertLess(idx_jobs, idx_profile)


class CollectReportTests(unittest.TestCase):
    def test_collect_uses_injected_environ_and_repo_timings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline = root / BASELINE_TIMINGS_PATH
            baseline.parent.mkdir(parents=True)
            baseline.write_text('{"tests.e2e.test_app.C.test_x": 1.5}\n', encoding="utf-8")
            env = {
                "PRKS_E2E_JOBS": "2",
                "TMPDIR": str(root / "tmp"),
                "PLAYWRIGHT_BROWSERS_PATH": str(root / "external-cache"),
            }
            (root / "tmp").mkdir()
            with mock.patch.object(doctor, "_chromium_probe") as probe:
                probe.return_value = {
                    "playwright_installed": None,
                    "playwright_pinned": "1.63.0",
                    "playwright_pin_error": None,
                    "playwright_match": False,
                    "chromium_revision": None,
                    "chromium_revision_error": None,
                    "chromium_available": False,
                    "chromium_path": None,
                    "chromium_version": None,
                    "browsers_dir": str(root / ".playwright-browsers"),
                }
                with mock.patch.object(doctor, "detect_cgroup_cpu_count", return_value=1):
                    with mock.patch.object(
                        doctor, "detect_cgroup_memory_limit_bytes", return_value=512 * 1024 * 1024
                    ):
                        with mock.patch.object(doctor, "_cpu_affinity_count", return_value=1):
                            with mock.patch.object(
                                doctor, "_cgroup_cpu_quota_count", return_value=1
                            ):
                                facts = doctor.collect_report(repo=root, environ=env)
            probe.assert_called_once_with(root / ".playwright-browsers")
            self.assertEqual(
                facts["browser"]["browsers_dir_inherited"],
                str(root / "external-cache"),
            )
            self.assertEqual(facts["env"]["PRKS_E2E_JOBS"], "2")
            self.assertIsNone(facts["env"]["PRKS_E2E_PROFILE"])
            self.assertEqual(facts["timing_baseline_committed"]["present"], True)
            self.assertEqual(facts["timing_baseline_committed"]["entries"], 1)
            self.assertEqual(facts["timing_history_local"]["present"], False)
            self.assertEqual(facts["agent_default_workers"], 1)
            self.assertIn(
                facts["assessment"]["kind"],
                ("browser_toolchain_gap", "setup_and_under_resourced"),
            )


class ScriptsE2EDoctorSelfcheckTests(unittest.TestCase):
    def test_wrapper_lists_doctor_and_runs_read_only(self):
        wrapper = (REPO / "scripts" / "e2e").read_text(encoding="utf-8")
        self.assertIn("doctor", wrapper)
        self.assertIn("tests/e2e/doctor.py", wrapper)

        # Live smoke: must exit 0, print the banner, and not create browser cache.
        browsers = REPO / ".playwright-browsers"
        existed = browsers.exists()
        completed = subprocess.run(
            ["bash", str(REPO / "scripts" / "e2e"), "doctor"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHON": sys.executable, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PRKS E2E doctor (read-only)", completed.stdout)
        self.assertIn("assessment:", completed.stdout)
        self.assertIn("agent_default_workers:", completed.stdout)
        if not existed:
            self.assertFalse(
                browsers.exists(),
                "doctor must not install Chromium / create .playwright-browsers",
            )

    def test_module_main_is_read_only(self):
        completed = subprocess.run(
            [sys.executable, str(REPO / "tests" / "e2e" / "doctor.py")],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith("PRKS E2E doctor (read-only)"))


if __name__ == "__main__":
    unittest.main()
