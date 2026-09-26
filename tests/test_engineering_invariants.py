"""Regression tests for scripts/check_invariants.py."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_invariants.py"
_SPEC = importlib.util.spec_from_file_location("prks_check_invariants", _SCRIPT)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
# Dataclass processing looks the class module up in sys.modules (3.12+).
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


class EngineeringInvariantTests(unittest.TestCase):
    def test_blocks_shutil_copy2_module_alias(self):
        findings = checker.check_source(
            "import shutil as s\ns.copy2('a', 'b')\n",
            "backend/example.py",
        )
        self.assertEqual([f.code for f in findings], ["INV-STORAGE-001"])

    def test_blocks_direct_import_alias(self):
        findings = checker.check_source(
            "from shutil import copyfile as cp\ncp('a', 'b')\n",
            "backend/example.py",
        )
        self.assertEqual([f.code for f in findings], ["INV-STORAGE-001"])

    def test_alias_reuse_across_functions_keeps_storage_violation(self):
        """Same alias may bind shutil in one function and os in another.

        A tree-wide final alias map would let the later binding overwrite the
        earlier one and misclassify (or drop) INV-STORAGE-001.
        """
        source_copy_first = (
            "def publish():\n"
            "    import shutil as s\n"
            "    s.copy2('a', 'b')\n"
            "\n"
            "def commit():\n"
            "    import os as s\n"
            "    s.replace('a', 'b')\n"
        )
        source_replace_first = (
            "def commit():\n"
            "    import os as s\n"
            "    s.replace('a', 'b')\n"
            "\n"
            "def publish():\n"
            "    import shutil as s\n"
            "    s.copy2('a', 'b')\n"
        )
        cases = (
            ("copy_then_replace", source_copy_first),
            ("replace_then_copy", source_replace_first),
        )
        for label, source in cases:
            with self.subTest(order=label):
                findings = checker.check_source(source, "backend/example.py")
                codes = sorted(f.code for f in findings)
                self.assertEqual(
                    codes,
                    ["INV-DURABILITY-001", "INV-STORAGE-001"],
                )

    def test_import_os_path_still_binds_os_for_replace(self):
        """``import os.path`` binds the name ``os``; dotted ``as`` must not."""
        findings = checker.check_source(
            "import os.path\nos.replace('a', 'b')\n",
            "backend/new_feature.py",
        )
        self.assertEqual([f.code for f in findings], ["INV-DURABILITY-001"])
        aliased = checker.check_source(
            "import os.path as p\nos.replace('a', 'b')\n",
            "backend/new_feature.py",
        )
        # ``os`` was never bound; ``os.replace`` is an unresolved Name path.
        self.assertEqual(aliased, [])

    def test_replace_is_allowed_only_at_approved_boundary(self):
        allowed = checker.check_source(
            "import os\nos.replace('a', 'b')\n",
            "backend/fs_durability.py",
        )
        derived = checker.check_source(
            "import os\nos.replace('a', 'b')\n",
            "backend/derived_cache_publish.py",
        )
        blocked = checker.check_source(
            "import os\nos.replace('a', 'b')\n",
            "backend/new_feature.py",
        )
        # HTTP adapter must not be a file-level escape hatch.
        blocked_server = checker.check_source(
            "import os\nos.replace('a', 'b')\n",
            "backend/server.py",
        )
        self.assertEqual(allowed, [])
        self.assertEqual(derived, [])
        self.assertEqual([f.code for f in blocked], ["INV-DURABILITY-001"])
        self.assertEqual([f.code for f in blocked_server], ["INV-DURABILITY-001"])

    def test_fsync_is_allowed_only_at_approved_boundary(self):
        allowed = checker.check_source(
            "from os import fsync\nfsync(1)\n",
            "backend/fs_durability.py",
        )
        blocked_feature = checker.check_source(
            "from os import fsync as sync\nsync(1)\n",
            "backend/new_feature.py",
        )
        # Managed-PDF replace must not be an fsync island — bare os.fsync there
        # is still INV-DURABILITY-002 (use fsync_open_file / fsync_directory).
        blocked_pdf = checker.check_source(
            "from os import fsync\nfsync(1)\n",
            "backend/services/work_pdf_replace.py",
        )
        self.assertEqual(allowed, [])
        self.assertEqual([f.code for f in blocked_feature], ["INV-DURABILITY-002"])
        self.assertEqual([f.code for f in blocked_pdf], ["INV-DURABILITY-002"])

    def test_current_backend_passes(self):
        findings = checker.check_repo(_ROOT)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))

    def test_repo_scan_reports_new_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "bad.py").write_text(
                "import shutil\nshutil.copy2('a', 'b')\n",
                encoding="utf-8",
            )
            findings = checker.check_repo(root)
            self.assertEqual([f.code for f in findings], ["INV-STORAGE-001"])

    def test_repo_scan_covers_prks_app_entry(self):
        """Startup/orchestration in prks_app.py must not bypass INV-* rules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "prks_app.py").write_text(
                "import shutil\nshutil.copy2('a', 'b')\n",
                encoding="utf-8",
            )
            findings = checker.check_repo(root)
            self.assertEqual([f.code for f in findings], ["INV-STORAGE-001"])
            self.assertEqual(findings[0].path, "prks_app.py")
            scanned = [
                p.relative_to(root).as_posix()
                for p in checker.iter_production_python(root)
            ]
            self.assertIn("prks_app.py", scanned)

    def _write_valid_pyright_tree(self, root: Path) -> None:
        (root / "backend" / "storage").mkdir(parents=True)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / "pyrightconfig.json").write_text(
            json.dumps(
                {
                    "include": ["prks_app.py", "backend"],
                    "typeCheckingMode": "off",
                    "reportUndefinedVariable": "error",
                    "reportUnboundVariable": "error",
                    "reportUnusedExcept": "error",
                }
            ),
            encoding="utf-8",
        )
        (root / "pyrightconfig.typed-slice.json").write_text(
            json.dumps(
                {
                    "include": ["backend/storage"],
                    "exclude": ["**/__pycache__"],
                    "typeCheckingMode": "basic",
                    "reportUndefinedVariable": "error",
                    "reportUnboundVariable": "error",
                    "reportUnusedExcept": "error",
                }
            ),
            encoding="utf-8",
        )
        (root / ".github" / "workflows" / "static-analysis.yml").write_text(
            (
                "jobs:\n"
                "  pyright:\n"
                "    steps:\n"
                "      - run: pyright --project pyrightconfig.json\n"
                "      - run: pyright --project pyrightconfig.typed-slice.json\n"
            ),
            encoding="utf-8",
        )

    def test_pyright_typed_slice_config_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            self.assertEqual(checker.check_pyright_configs(root), [])

    def test_pyright_typed_slice_rejects_off_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            typed = root / "pyrightconfig.typed-slice.json"
            typed.write_text(
                json.dumps(
                    {
                        "include": ["backend/storage"],
                        "typeCheckingMode": "off",
                        "reportUndefinedVariable": "error",
                        "reportUnboundVariable": "error",
                        "reportUnusedExcept": "error",
                    }
                ),
                encoding="utf-8",
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-002"])
            self.assertIn("typeCheckingMode", findings[0].message)

    def test_pyright_dataflow_requires_backend_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            (root / "pyrightconfig.json").write_text(
                json.dumps(
                    {
                        "include": ["prks_app.py"],
                        "typeCheckingMode": "off",
                        "reportUndefinedVariable": "error",
                        "reportUnboundVariable": "error",
                        "reportUnusedExcept": "error",
                    }
                ),
                encoding="utf-8",
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-001"])
            self.assertIn("backend", findings[0].message)

    def test_pyright_typed_slice_rejects_ignore_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            typed = json.loads((root / "pyrightconfig.typed-slice.json").read_text())
            typed["ignore"] = ["backend/storage"]
            (root / "pyrightconfig.typed-slice.json").write_text(
                json.dumps(typed), encoding="utf-8"
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-002"])
            self.assertIn("ignore", findings[0].message)

    def test_pyright_typed_slice_rejects_exclude_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            typed = json.loads((root / "pyrightconfig.typed-slice.json").read_text())
            typed["exclude"] = ["**/__pycache__", "backend/storage"]
            (root / "pyrightconfig.typed-slice.json").write_text(
                json.dumps(typed), encoding="utf-8"
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-002"])
            self.assertIn("exclude", findings[0].message)

    def test_pyright_typed_slice_rejects_missing_ci_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            (root / ".github" / "workflows" / "static-analysis.yml").write_text(
                "jobs:\n  pyright:\n    steps:\n      - run: pyright --project pyrightconfig.json\n",
                encoding="utf-8",
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-003"])

    def test_pyright_ci_rejects_comment_only_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            (root / ".github" / "workflows" / "static-analysis.yml").write_text(
                (
                    "jobs:\n"
                    "  pyright:\n"
                    "    steps:\n"
                    "      - run: pyright --project pyrightconfig.json\n"
                    "      # - run: pyright --project pyrightconfig.typed-slice.json\n"
                    "      - run: echo pyrightconfig.typed-slice.json\n"
                ),
                encoding="utf-8",
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-003"])
            self.assertIn("typed-slice", findings[0].message)

    def test_pyright_ci_rejects_commented_out_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            (root / ".github" / "workflows" / "static-analysis.yml").write_text(
                (
                    "jobs:\n"
                    "  pyright:\n"
                    "    steps:\n"
                    "      - run: pyright --project pyrightconfig.json\n"
                    "      # kept for docs: pyright --project pyrightconfig.typed-slice.json\n"
                ),
                encoding="utf-8",
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-003"])

    def test_current_repo_pyright_configs_pass(self):
        findings = checker.check_pyright_configs(_ROOT)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))


if __name__ == "__main__":
    unittest.main()
