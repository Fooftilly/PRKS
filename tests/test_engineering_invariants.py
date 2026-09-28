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

    def test_import_resolution_honors_deferred_imports_and_shadowing(self):
        """The shared lexical model also serves the os/shutil invariants."""
        deferred = checker.check_source(
            "def commit():\n    os.replace('a', 'b')\n\nimport os\n",
            "backend/new_feature.py",
        )
        self.assertEqual([f.code for f in deferred], ["INV-DURABILITY-001"])
        shadowed = checker.check_source(
            "import shutil\ndef publish(shutil):\n    shutil.copy2('a', 'b')\n",
            "backend/new_feature.py",
        )
        self.assertEqual(shadowed, [])

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

    # --- INV-BACKUP-001: ZipFile.extractall() ------------------------------

    def _backup_codes(self, source: str, relpath: str = "backend/example.py") -> list[str]:
        return [f.code for f in checker.check_source(source, relpath)]

    def test_blocks_zipfile_extractall_binding_forms(self):
        """Every realistic ZipFile import/binding shape must fail the gate."""
        cases = {
            "module_with": (
                "import zipfile\n"
                "with zipfile.ZipFile(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "module_alias_with": (
                "import zipfile as z\n"
                "with z.ZipFile(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "from_import_with": (
                "from zipfile import ZipFile\n"
                "with ZipFile(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "class_alias_assign": (
                "from zipfile import ZipFile as Z\n"
                "archive = Z(path)\n"
                "archive.extractall(dest)\n"
            ),
            "module_assign": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "archive.extractall(dest)\n"
            ),
            "module_chained": (
                "import zipfile\n"
                "zipfile.ZipFile(path).extractall(dest)\n"
            ),
            "from_import_chained": (
                "from zipfile import ZipFile\n"
                "ZipFile(path).extractall(dest)\n"
            ),
            "annotated_assign": (
                "import zipfile\n"
                "archive: zipfile.ZipFile = zipfile.ZipFile(path)\n"
                "archive.extractall(dest)\n"
            ),
            "walrus": (
                "import zipfile\n"
                "if (archive := zipfile.ZipFile(path)):\n"
                "    archive.extractall(dest)\n"
            ),
            "multi_item_with": (
                "import zipfile\n"
                "with open(log) as fh, zipfile.ZipFile(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "async_with": (
                "import zipfile\n"
                "async def restore():\n"
                "    async with zipfile.ZipFile(path) as archive:\n"
                "        archive.extractall(dest)\n"
            ),
            "annotated_parameter": (
                "import zipfile\n"
                "def restore(archive: zipfile.ZipFile, dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "optional_parameter": (
                "from __future__ import annotations\n"
                "from zipfile import ZipFile\n"
                "def restore(archive: ZipFile | None, dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "string_annotation_parameter": (
                "import zipfile\n"
                "def restore(archive: 'zipfile.ZipFile', dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "unbound_method_via_module": (
                "import zipfile\n"
                "zipfile.ZipFile.extractall(archive, dest)\n"
            ),
            "unbound_method_via_class_alias": (
                "from zipfile import ZipFile as Z\n"
                "Z.extractall(archive, dest)\n"
            ),
            "method_reference_escape": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "extract = archive.extractall\n"
                "extract(dest)\n"
            ),
            "getattr_literal": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "getattr(archive, 'extractall')(dest)\n"
            ),
            "pyzipfile_subclass": (
                "import zipfile\n"
                "with zipfile.PyZipFile(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "import_inside_function": (
                "def restore(path, dest):\n"
                "    import zipfile as zf_mod\n"
                "    with zf_mod.ZipFile(path) as archive:\n"
                "        archive.extractall(dest)\n"
            ),
            "closure_reads_enclosing_binding": (
                "import zipfile\n"
                "def restore(path, dest):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    def run():\n"
                "        archive.extractall(dest)\n"
                "    run()\n"
            ),
            # Function bodies run after the module finished importing.
            "deferred_module_import": (
                "def restore(path, dest):\n"
                "    zipfile.ZipFile(path).extractall(dest)\n"
                "\n"
                "import zipfile\n"
            ),
            # Class attributes are not visible to method bodies by bare name.
            "class_attribute_does_not_hide_module_archive": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "class Restore:\n"
                "    archive = None\n"
                "    def run(self, dest):\n"
                "        archive.extractall(dest)\n"
            ),
            "wildcard_import": (
                "from zipfile import *\n"
                "archive = ZipFile(path)\n"
                "archive.extractall(dest)\n"
            ),
            "tuple_unpacking": (
                "import zipfile\n"
                "archive, label = zipfile.ZipFile(path), 'backup'\n"
                "archive.extractall(dest)\n"
            ),
            "loop_over_constructors": (
                "import zipfile\n"
                "for archive in (zipfile.ZipFile(a), zipfile.ZipFile(b)):\n"
                "    archive.extractall(dest)\n"
            ),
            "comprehension_over_constructors": (
                "import zipfile\n"
                "[a.extractall(dest) for a in (zipfile.ZipFile(path),)]\n"
            ),
            # Only a definite rebinding clears the archive classification.
            "conditional_rebinding_keeps_archive": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "if staged:\n"
                "    archive = load_bundle()\n"
                "archive.extractall(dest)\n"
            ),
            "loop_carried_binding": (
                "import zipfile\n"
                "def restore(paths, dest):\n"
                "    for path in paths:\n"
                "        if ready:\n"
                "            archive.extractall(dest)\n"
                "        archive = zipfile.ZipFile(path)\n"
            ),
            "class_alias_by_assignment": (
                "import zipfile\n"
                "Z = zipfile.ZipFile\n"
                "Z(path).extractall(dest)\n"
            ),
            "getattr_on_class": (
                "from zipfile import ZipFile\n"
                "getattr(ZipFile, 'extractall')(archive, dest)\n"
            ),
            # Instance / object attribute ownership shapes.
            "self_attribute_same_method": (
                "import zipfile\n"
                "class Restore:\n"
                "    def run(self, path, dest):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_assigned_in_init": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_method_before_init": (
                "import zipfile\n"
                "class Restore:\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
            ),
            "self_attribute_with_target": (
                "import zipfile\n"
                "class Restore:\n"
                "    def open(self, path):\n"
                "        with zipfile.ZipFile(path) as self.archive:\n"
                "            pass\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_annotated": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive: zipfile.ZipFile = open_archive(path)\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
            "class_attribute_annotation": (
                "import zipfile\n"
                "class Restore:\n"
                "    archive: zipfile.ZipFile\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
            "classmethod_cls_attribute": (
                "import zipfile\n"
                "class Restore:\n"
                "    archive = zipfile.ZipFile('backup.zip')\n"
                "    @classmethod\n"
                "    def run(cls, dest):\n"
                "        cls.archive.extractall(dest)\n"
            ),
            "self_attribute_via_local_alias": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        archive = self.archive\n"
                "        archive.extractall(dest)\n"
            ),
            "self_attribute_in_method_closure": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        def go():\n"
                "            self.archive.extractall(dest)\n"
                "        go()\n"
            ),
            "self_attribute_from_same_module_base": (
                "import zipfile\n"
                "class Base:\n"
                "    def __init__(self, path):\n"
                "        self._zf = zipfile.ZipFile(path)\n"
                "class Restore(Base):\n"
                "    def run(self, dest):\n"
                "        self._zf.extractall(dest)\n"
            ),
            "object_attribute_in_function": (
                "import zipfile\n"
                "def restore(holder, path, dest):\n"
                "    holder.archive = zipfile.ZipFile(path)\n"
                "    holder.archive.extractall(dest)\n"
            ),
            # Control flow: later loop iterations, handlers and match arms.
            "for_loop_carried_over_prior_binding": (
                "import zipfile\n"
                "prev = None\n"
                "for path in paths:\n"
                "    if prev is not None:\n"
                "        prev.extractall(dest)\n"
                "    prev = zipfile.ZipFile(path)\n"
            ),
            "while_loop_carried_over_prior_binding": (
                "import zipfile\n"
                "prev = None\n"
                "while more():\n"
                "    if prev is not None:\n"
                "        prev.extractall(dest)\n"
                "    prev = zipfile.ZipFile(next_path())\n"
            ),
            "try_handler_sees_body_binding": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "try:\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    validate()\n"
                "except OSError:\n"
                "    archive.extractall(dest)\n"
            ),
            "match_case_binding": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "match kind:\n"
                "    case 'zip':\n"
                "        archive = zipfile.ZipFile(path)\n"
                "archive.extractall(dest)\n"
            ),
            # Scope declarations and escaping walrus targets.
            "global_declared_module_later_rebound": (
                "import zipfile\n"
                "def restore(path, dest):\n"
                "    global zipfile\n"
                "    zipfile.ZipFile(path).extractall(dest)\n"
                "    zipfile = None\n"
            ),
            "global_archive_assigned_in_other_function": (
                "import zipfile\n"
                "archive = None\n"
                "def open_archive(path):\n"
                "    global archive\n"
                "    archive = zipfile.ZipFile(path)\n"
                "def restore(dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "nonlocal_archive_assigned_in_closure": (
                "import zipfile\n"
                "def restore(path, dest):\n"
                "    archive = None\n"
                "    def open_archive():\n"
                "        nonlocal archive\n"
                "        archive = zipfile.ZipFile(path)\n"
                "    open_archive()\n"
                "    def run():\n"
                "        archive.extractall(dest)\n"
                "    run()\n"
            ),
            "comprehension_walrus_escapes": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "[(archive := zipfile.ZipFile(p)) for p in paths]\n"
                "archive.extractall(dest)\n"
            ),
            # Conditional / boolean value selection.
            "conditional_expression_value": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path) if use_zip else load_bundle(path)\n"
                "archive.extractall(dest)\n"
            ),
            "boolean_fallback_value": (
                "import zipfile\n"
                "archive = cached or zipfile.ZipFile(path)\n"
                "archive.extractall(dest)\n"
            ),
            "self_attribute_used_before_local_rebinding": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
                "        self.archive = None\n"
            ),
            # Same-module subclasses inherit ZipFile.extractall.
            "subclass_constructor": (
                "import zipfile\n"
                "class SafeZip(zipfile.ZipFile):\n"
                "    pass\n"
                "SafeZip(path).extractall(dest)\n"
            ),
            "subclass_self_call": (
                "import zipfile\n"
                "class SafeZip(zipfile.ZipFile):\n"
                "    def unpack(self, dest):\n"
                "        self.extractall(dest)\n"
            ),
            "subclass_super_call": (
                "import zipfile\n"
                "class SafeZip(zipfile.ZipFile):\n"
                "    def extractall(self, dest):\n"
                "        return super().extractall(dest)\n"
            ),
            "indirect_subclass_with": (
                "from zipfile import ZipFile\n"
                "class Base(ZipFile):\n"
                "    pass\n"
                "class Restore(Base):\n"
                "    pass\n"
                "with Restore(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            "subclass_unbound_method": (
                "import zipfile\n"
                "class SafeZip(zipfile.ZipFile):\n"
                "    pass\n"
                "SafeZip.extractall(archive, dest)\n"
            ),
            # A handler can start after any statement of the try body.
            "try_handler_sees_mid_body_binding": (
                "import zipfile\n"
                "archive = None\n"
                "try:\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    validate(archive)\n"
                "    archive = None\n"
                "except ValueError:\n"
                "    archive.extractall(dest)\n"
            ),
            # Match captures of the whole subject.
            "match_capture_pattern": (
                "import zipfile\n"
                "match zipfile.ZipFile(path):\n"
                "    case archive:\n"
                "        archive.extractall(dest)\n"
            ),
            "match_as_pattern": (
                "import zipfile\n"
                "match zipfile.ZipFile(path):\n"
                "    case zipfile.ZipFile() as archive:\n"
                "        archive.extractall(dest)\n"
            ),
            # The while test runs before the body and before leaving the loop.
            "while_test_walrus_in_body": (
                "import zipfile\n"
                "archive = None\n"
                "while (archive := zipfile.ZipFile(next_path())):\n"
                "    archive.extractall(dest)\n"
            ),
            "while_test_walrus_after_loop": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "while (archive := zipfile.ZipFile(next_path())):\n"
                "    pass\n"
                "archive.extractall(dest)\n"
            ),
            # A nested global/nonlocal rebinding reaches the owner's own uses.
            "global_rebinding_seen_by_owner_scope": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "def open_archive(path):\n"
                "    global archive\n"
                "    archive = zipfile.ZipFile(path)\n"
                "open_archive(path)\n"
                "archive.extractall(dest)\n"
            ),
            "nonlocal_rebinding_seen_by_owner_scope": (
                "import zipfile\n"
                "def restore(path, dest):\n"
                "    archive = load_bundle()\n"
                "    def open_archive():\n"
                "        nonlocal archive\n"
                "        archive = zipfile.ZipFile(path)\n"
                "    open_archive()\n"
                "    archive.extractall(dest)\n"
            ),
            "annotated_class_alias": (
                "import zipfile\n"
                "Z: type[zipfile.ZipFile] = zipfile.ZipFile\n"
                "Z(path).extractall(dest)\n"
            ),
            "walrus_receiver": (
                "import zipfile\n"
                "(archive := zipfile.ZipFile(path)).extractall(dest)\n"
            ),
            # A partial attribute rebinding keeps the class record in play on
            # the paths that did not rebind it.
            "self_attribute_rebound_on_one_branch": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest, use_bundle):\n"
                "        if use_bundle:\n"
                "            self.archive = load_bundle()\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_rebound_in_else_only": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest, keep):\n"
                "        if keep:\n"
                "            pass\n"
                "        else:\n"
                "            self.archive = load_bundle()\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_rebound_in_try": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        try:\n"
                "            self.archive = load_bundle()\n"
                "        except OSError:\n"
                "            pass\n"
                "        self.archive.extractall(dest)\n"
            ),
            "self_attribute_rebound_later_in_loop": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest, items):\n"
                "        for _ in items:\n"
                "            self.archive.extractall(dest)\n"
                "            self.archive = load_bundle()\n"
            ),
            # A branch that falls through still reaches the join.
            "non_terminating_else_reaches_join": (
                "import zipfile\n"
                "def restore(path, dest, custom):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    if custom:\n"
                "        archive = load_bundle()\n"
                "    else:\n"
                "        log()\n"
                "    archive.extractall(dest)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(form=label):
                findings = checker.check_source(source, "backend/example.py")
                self.assertEqual([f.code for f in findings], ["INV-BACKUP-001"])
                message = findings[0].message
                self.assertIn("ZipFile.extractall()", message)
                self.assertIn("forbidden", message)
                self.assertIn("backup_restore", message)

    def test_zipfile_extractall_has_no_production_allowlist(self):
        """Even the backup/restore module itself may not call extractall()."""
        source = (
            "import zipfile\n"
            "with zipfile.ZipFile(path) as archive:\n"
            "    archive.extractall(dest)\n"
        )
        for relpath in (
            "backend/backup_restore.py",
            "backend/fs_durability.py",
            "backend/server.py",
            "prks_app.py",
        ):
            with self.subTest(path=relpath):
                self.assertEqual(self._backup_codes(source, relpath), ["INV-BACKUP-001"])

    def test_zipfile_extractall_does_not_classify_unrelated_receivers(self):
        """High-signal rule: only receivers proven to come from zipfile."""
        cases = {
            # An application object that happens to expose extractall().
            "unrelated_object": (
                "class Bundle:\n"
                "    def extractall(self, dest):\n"
                "        pass\n"
                "Bundle().extractall(dest)\n"
            ),
            # A ZipFile-named class that is not zipfile's.
            "shadowing_local_class": (
                "class ZipFile:\n"
                "    def extractall(self, dest):\n"
                "        pass\n"
                "ZipFile(path).extractall(dest)\n"
            ),
            # tarfile is a different API and is not part of this invariant.
            "tarfile_archive": (
                "import tarfile\n"
                "with tarfile.open(path) as archive:\n"
                "    archive.extractall(dest)\n"
            ),
            # Validated per-member reads are the approved pattern.
            "validated_member_reads": (
                "import zipfile\n"
                "with zipfile.ZipFile(path) as archive:\n"
                "    for info in archive.infolist():\n"
                "        data = archive.read(info)\n"
                "        archive.extract(info, dest)\n"
            ),
            # zipfile imported but extractall() receiver is unrelated.
            "zipfile_imported_unrelated_receiver": (
                "import zipfile\n"
                "archive = load_bundle(path)\n"
                "archive.extractall(dest)\n"
            ),
            "unrelated_getattr": (
                "import zipfile\n"
                "getattr(bundle, 'extractall')(dest)\n"
            ),
            # A definite rebinding replaces the archive classification.
            "definite_rebinding": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "archive.close()\n"
                "archive = load_bundle()\n"
                "archive.extractall(dest)\n"
            ),
            "reassigned_class_alias": (
                "from zipfile import ZipFile as Z\n"
                "Z = Bundle\n"
                "archive = Z(path)\n"
                "archive.extractall(dest)\n"
            ),
            # A rebinding replaces the archive on its own branch, and a join
            # where every path rebinds drops it.
            "branch_local_rebinding": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "if use_bundle:\n"
                "    archive = load_bundle()\n"
                "    archive.extractall(dest)\n"
            ),
            "every_branch_rebinds": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "if use_bundle:\n"
                "    archive = load_bundle()\n"
                "else:\n"
                "    archive = load_other()\n"
                "archive.extractall(dest)\n"
            ),
            # A definite rebinding of an attribute on the current path wins
            # over the class-level record.
            "self_attribute_definite_rebinding": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest):\n"
                "        self.archive = load_bundle()\n"
                "        self.archive.extractall(dest)\n"
            ),
            # A capture of part of the subject, or of an unrelated subject,
            # is an ordinary local.
            "match_subpart_capture": (
                "import zipfile\n"
                "match zipfile.ZipFile(path):\n"
                "    case Wrapper(inner=part):\n"
                "        part.extractall(dest)\n"
            ),
            "match_capture_shadows_archive": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "match load_bundle():\n"
                "    case archive:\n"
                "        archive.extractall(dest)\n"
            ),
            # return/raise paths never reach the code after the branch.
            "archive_path_returns_before_use": (
                "import zipfile\n"
                "def restore(path, dest, custom):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    if custom:\n"
                "        archive = load_bundle()\n"
                "    else:\n"
                "        return\n"
                "    archive.extractall(dest)\n"
            ),
            "archive_path_raises_before_use": (
                "import zipfile\n"
                "def restore(path, dest, ok):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    if not ok:\n"
                "        raise ValueError(path)\n"
                "    else:\n"
                "        archive = load_bundle()\n"
                "    archive.extractall(dest)\n"
            ),
            "self_attribute_rebound_on_every_branch": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    def run(self, dest, use_bundle):\n"
                "        if use_bundle:\n"
                "            self.archive = load_bundle()\n"
                "        else:\n"
                "            self.archive = load_other()\n"
                "        self.archive.extractall(dest)\n"
            ),
            # Only zipfile subclasses make ``self`` an archive.
            "unrelated_class_self_call": (
                "import zipfile\n"
                "class Bundle:\n"
                "    def unpack(self, dest):\n"
                "        self.extractall(dest)\n"
            ),
            # Archive attributes are per class, per attribute name.
            "same_attribute_on_unrelated_class": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "class Bundle:\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
            "different_attribute_same_class": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "        self.bundle = load_bundle()\n"
                "    def run(self, dest):\n"
                "        self.bundle.extractall(dest)\n"
            ),
            "staticmethod_first_parameter_is_not_self": (
                "import zipfile\n"
                "class Restore:\n"
                "    def __init__(self, path):\n"
                "        self.archive = zipfile.ZipFile(path)\n"
                "    @staticmethod\n"
                "    def run(self, dest):\n"
                "        self.archive.extractall(dest)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(form=label):
                self.assertEqual(self._backup_codes(source), [])

    def test_zipfile_binding_does_not_leak_across_functions(self):
        """A ZipFile binding in one function must not taint a sibling scope."""
        cases = {
            "sibling_parameter": (
                "import zipfile\n"
                "def open_archive(path):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    return archive.namelist()\n"
                "\n"
                "def unpack(archive, dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "sibling_local": (
                "import zipfile\n"
                "def open_archive(path):\n"
                "    with zipfile.ZipFile(path) as archive:\n"
                "        return archive.namelist()\n"
                "\n"
                "def unpack(dest):\n"
                "    archive = load_bundle()\n"
                "    archive.extractall(dest)\n"
            ),
            "function_import_does_not_leak": (
                "def open_archive(path):\n"
                "    from zipfile import ZipFile\n"
                "    return ZipFile(path).namelist()\n"
                "\n"
                "class ZipFile:\n"
                "    pass\n"
                "\n"
                "def unpack(path, dest):\n"
                "    ZipFile(path).extractall(dest)\n"
            ),
            "module_binding_shadowed_by_parameter": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "def unpack(archive, dest):\n"
                "    archive.extractall(dest)\n"
            ),
            "module_binding_shadowed_by_local": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "def unpack(dest):\n"
                "    archive = load_bundle()\n"
                "    archive.extractall(dest)\n"
            ),
            "class_import_shadowed_by_parameter": (
                "from zipfile import ZipFile\n"
                "def unpack(ZipFile, path, dest):\n"
                "    ZipFile(path).extractall(dest)\n"
            ),
            "module_import_shadowed_by_parameter": (
                "import zipfile\n"
                "def unpack(zipfile, path, dest):\n"
                "    zipfile.ZipFile(path).extractall(dest)\n"
            ),
            "module_binding_shadowed_by_comprehension": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "[archive.extractall(dest) for archive in bundles]\n"
            ),
            "module_binding_shadowed_by_lambda": (
                "import zipfile\n"
                "archive = zipfile.ZipFile(path)\n"
                "unpack = lambda archive: archive.extractall(dest)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(form=label):
                self.assertEqual(self._backup_codes(source), [])

        # The same source still fails when the extraction sits in the scope
        # that owns the ZipFile binding.
        in_scope = (
            "import zipfile\n"
            "def open_archive(path, dest):\n"
            "    archive = zipfile.ZipFile(path)\n"
            "    archive.extractall(dest)\n"
            "\n"
            "def unpack(archive, dest):\n"
            "    archive.extractall(dest)\n"
        )
        findings = checker.check_source(in_scope, "backend/example.py")
        self.assertEqual([(f.code, f.line) for f in findings], [("INV-BACKUP-001", 4)])

    def test_repo_scan_reports_zipfile_extractall_in_backend_and_prks_app(self):
        source = (
            "from zipfile import ZipFile as Z\n"
            "archive = Z('backup.zip')\n"
            "archive.extractall('out')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend" / "services").mkdir(parents=True)
            (root / "backend" / "services" / "restore_helper.py").write_text(
                source, encoding="utf-8"
            )
            (root / "prks_app.py").write_text(source, encoding="utf-8")
            findings = checker.check_repo(root)
            self.assertEqual(
                sorted((f.code, f.path, f.line) for f in findings),
                [
                    ("INV-BACKUP-001", "backend/services/restore_helper.py", 3),
                    ("INV-BACKUP-001", "prks_app.py", 3),
                ],
            )

    def test_repo_scan_ignores_zipfile_extractall_in_tests_and_scripts(self):
        """Test fixtures and tooling may unpack archives; they are not production."""
        source = (
            "import zipfile\n"
            "with zipfile.ZipFile('fixture.zip') as archive:\n"
            "    archive.extractall('tmp')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "tests" / "support").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "tests" / "support" / "archive_fixture.py").write_text(
                source, encoding="utf-8"
            )
            (root / "scripts" / "unpack_fixture.py").write_text(source, encoding="utf-8")
            self.assertEqual(checker.check_repo(root), [])
            scanned = [
                p.relative_to(root).as_posix()
                for p in checker.iter_production_python(root)
            ]
            self.assertEqual(scanned, [])

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

    def test_pyright_typed_slice_rejects_parent_globs(self):
        """Globs that can match backend/storage or anything under it must fail."""
        cases = (
            "backend/**/storage/**",
            "backend/**/storage",
            "**/storage/**/*",
            "**/backend/**/storage/**",
            "backend/**/*",
            "**",
            "backend/**",
            "**/backend/**",
            "backend/*",
            "**/storage/**",
            "backend/storage/services/**",
            "backend/storage/services/*",
            "backend/storage/config.*",
            "./backend/storage",
            "backend/./storage",
            "./backend/**",
            "backend/../backend/storage",
            "backend/./storage/**",
            "../backend/storage",
            "**/..",
        )
        for pattern in cases:
            with self.subTest(pattern=pattern):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self._write_valid_pyright_tree(root)
                    typed = json.loads((root / "pyrightconfig.typed-slice.json").read_text())
                    typed["exclude"] = ["**/__pycache__", pattern]
                    (root / "pyrightconfig.typed-slice.json").write_text(
                        json.dumps(typed), encoding="utf-8"
                    )
                    findings = checker.check_pyright_configs(root)
                    self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-002"])
                    self.assertIn(pattern, findings[0].message)

    def test_pyright_dataflow_rejects_ignore_of_backend(self):
        """Data-flow ignore/exclude must not silence the backend include root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            dataflow = json.loads((root / "pyrightconfig.json").read_text())
            dataflow["ignore"] = ["backend"]
            (root / "pyrightconfig.json").write_text(
                json.dumps(dataflow), encoding="utf-8"
            )
            findings = checker.check_pyright_configs(root)
            self.assertEqual([f.code for f in findings], ["INV-PYRIGHT-001"])
            self.assertIn("ignore", findings[0].message)
            self.assertIn("backend", findings[0].message)

    def test_pyright_typed_slice_allows_pycache_excludes(self):
        """Genuine cache-only excludes must not trip INV-PYRIGHT-002."""
        for pattern in ("**/__pycache__", "**/__pycache__/**", "__pycache__"):
            with self.subTest(pattern=pattern):
                self.assertFalse(checker._path_covers_typed_slice(pattern))
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self._write_valid_pyright_tree(root)
                    typed = json.loads((root / "pyrightconfig.typed-slice.json").read_text())
                    typed["exclude"] = [pattern]
                    (root / "pyrightconfig.typed-slice.json").write_text(
                        json.dumps(typed), encoding="utf-8"
                    )
                    findings = checker.check_pyright_configs(root)
                    self.assertEqual(findings, [])

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

    def test_pyright_ci_rejects_echo_of_pyright_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid_pyright_tree(root)
            (root / ".github" / "workflows" / "static-analysis.yml").write_text(
                (
                    "jobs:\n"
                    "  pyright:\n"
                    "    steps:\n"
                    "      - run: pyright --project pyrightconfig.json\n"
                    '      - run: echo "pyright --project pyrightconfig.typed-slice.json"\n'
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