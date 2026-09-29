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
            "match_capture_in_closure": (
                "import zipfile\n"
                "match zipfile.ZipFile(path):\n"
                "    case archive:\n"
                "        def inner():\n"
                "            archive.extractall(dest)\n"
                "        inner()\n"
            ),
            "match_capture_loop_carried": (
                "import zipfile\n"
                "archive = load_bundle()\n"
                "for path in paths:\n"
                "    archive.extractall(dest)\n"
                "    match zipfile.ZipFile(path):\n"
                "        case archive:\n"
                "            pass\n"
            ),
            # Same-module helpers that return a zipfile archive.
            "helper_returned_archive": (
                "import zipfile\n"
                "def open_backup(path):\n"
                "    return zipfile.ZipFile(path)\n"
                "archive = open_backup(path)\n"
                "archive.extractall(dest)\n"
            ),
            "helper_defined_after_use": (
                "import zipfile\n"
                "def restore(path, dest):\n"
                "    open_backup(path).extractall(dest)\n"
                "def open_backup(path):\n"
                "    return zipfile.ZipFile(path)\n"
            ),
            "helper_returns_validated_local": (
                "import zipfile\n"
                "def open_backup(path):\n"
                "    archive = zipfile.ZipFile(path)\n"
                "    validate(archive)\n"
                "    return archive\n"
                "open_backup(path).extractall(dest)\n"
            ),
            "helper_return_annotation": (
                "import zipfile\n"
                "def open_backup(path) -> zipfile.ZipFile:\n"
                "    return make_archive(path)\n"
                "open_backup(path).extractall(dest)\n"
            ),
            "helper_chain": (
                "import zipfile\n"
                "def outer(path):\n"
                "    return middle(path)\n"
                "def middle(path):\n"
                "    return inner(path)\n"
                "def inner(path):\n"
                "    return zipfile.ZipFile(path)\n"
                "outer(path).extractall(dest)\n"
            ),
            "helper_on_some_path": (
                "import zipfile\n"
                "def open_backup(path, is_zip):\n"
                "    if is_zip:\n"
                "        return zipfile.ZipFile(path)\n"
                "    return load_bundle(path)\n"
                "open_backup(path, is_zip).extractall(dest)\n"
            ),
            "method_helper": (
                "import zipfile\n"
                "class Restore:\n"
                "    def run(self, dest):\n"
                "        self._open().extractall(dest)\n"
                "    def _open(self):\n"
                "        return zipfile.ZipFile(self.path)\n"
            ),
            "base_class_method_helper": (
                "import zipfile\n"
                "class Base:\n"
                "    def _open(self):\n"
                "        return zipfile.ZipFile(self.path)\n"
                "class Restore(Base):\n"
                "    def run(self, dest):\n"
                "        with self._open() as archive:\n"
                "            archive.extractall(dest)\n"
            ),
            "super_method_helper": (
                "import zipfile\n"
                "class Base:\n"
                "    def _open(self):\n"
                "        return zipfile.ZipFile(self.path)\n"
                "class Restore(Base):\n"
                "    def run(self, dest):\n"
                "        super()._open().extractall(dest)\n"
            ),
            "async_helper": (
                "import zipfile\n"
                "async def open_backup(path):\n"
                "    return zipfile.ZipFile(path)\n"
                "async def restore(path, dest):\n"
                "    archive = await open_backup(path)\n"
                "    archive.extractall(dest)\n"
            ),
            # A parameter default runs whenever the caller omits it.
            "function_parameter_default": (
                "import zipfile\n"
                "def restore(dest, archive=zipfile.ZipFile('backup.zip')):\n"
                "    archive.extractall(dest)\n"
            ),
            "keyword_only_parameter_default": (
                "import zipfile\n"
                "def restore(dest, *, archive=zipfile.ZipFile('backup.zip')):\n"
                "    archive.extractall(dest)\n"
            ),
            "lambda_parameter_default": (
                "import zipfile\n"
                "restore = lambda archive=zipfile.ZipFile(path): archive.extractall(dest)\n"
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
            "parameter_default_none": (
                "import zipfile\n"
                "def restore(dest, bundle=None):\n"
                "    bundle.extractall(dest)\n"
            ),
            # Helpers are classified by what they return.
            "helper_returns_unrelated_value": (
                "import zipfile\n"
                "def open_bundle(path):\n"
                "    return load_bundle(path)\n"
                "open_bundle(path).extractall(dest)\n"
            ),
            "helper_reads_archive_returns_other": (
                "import zipfile\n"
                "def load(path):\n"
                "    with zipfile.ZipFile(path) as archive:\n"
                "        return Bundle(archive.read('manifest.json'))\n"
                "load(path).extractall(dest)\n"
            ),
            "super_method_returns_unrelated_value": (
                "import zipfile\n"
                "class Base:\n"
                "    def _open(self):\n"
                "        return load_bundle(self.path)\n"
                "class Restore(Base):\n"
                "    def _open(self):\n"
                "        return zipfile.ZipFile(self.path)\n"
                "    def run(self, dest):\n"
                "        super()._open().extractall(dest)\n"
            ),
            "helper_name_shadowed_by_parameter": (
                "import zipfile\n"
                "def open_backup(path):\n"
                "    return zipfile.ZipFile(path)\n"
                "def restore(open_backup, path, dest):\n"
                "    open_backup(path).extractall(dest)\n"
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



def _codes(source: str, relpath: str = "backend/new_feature.py") -> list[str]:
    return [f.code for f in checker.check_source(source, relpath)]


class PathReplaceDurabilityTests(unittest.TestCase):
    """INV-DURABILITY-001 also covers pathlib.Path.replace (#187)."""

    def test_blocks_path_replace_binding_forms(self):
        cases = {
            "direct_constructor": (
                "from pathlib import Path\n"
                "Path(src).replace(dst)\n"
            ),
            "imported_alias": (
                "from pathlib import Path as P\n"
                "P(src).replace(dst)\n"
            ),
            "module": (
                "import pathlib\n"
                "pathlib.Path(src).replace(dst)\n"
            ),
            "module_alias_assigned_instance": (
                "import pathlib as pl\n"
                "p = pl.Path(src)\n"
                "p.replace(dst)\n"
            ),
            "class_alias_and_local_alias": (
                "from pathlib import Path\n"
                "PathCls = Path\n"
                "q = PathCls(src)\n"
                "r = q\n"
                "r.replace(dst)\n"
            ),
            "posix_path": (
                "from pathlib import PosixPath\n"
                "PosixPath(src).replace(dst)\n"
            ),
            "div_join": (
                "from pathlib import Path\n"
                "(Path(base) / 'x.pdf').replace(dst)\n"
            ),
            "path_method_chain": (
                "from pathlib import Path\n"
                "tmp = Path(src).resolve().with_suffix('.tmp')\n"
                "tmp.replace(dst)\n"
            ),
            "parent_attribute": (
                "from pathlib import Path\n"
                "Path(src).parent.replace(dst)\n"
            ),
            "cwd_factory": (
                "from pathlib import Path\n"
                "Path.cwd().replace(dst)\n"
            ),
            "annotated_parameter": (
                "from pathlib import Path\n"
                "def publish(tmp: Path, dst):\n"
                "    tmp.replace(dst)\n"
            ),
            "annotated_optional_string": (
                "import pathlib\n"
                "def publish(tmp: 'pathlib.Path | None', dst):\n"
                "    tmp.replace(dst)\n"
            ),
            "annotated_local": (
                "from pathlib import Path\n"
                "tmp: Path = make()\n"
                "tmp.replace(dst)\n"
            ),
            "unbound_class_call": (
                "from pathlib import Path\n"
                "Path.replace(Path(src), dst)\n"
            ),
            "method_reference": (
                "from pathlib import Path\n"
                "publish = Path(src).replace\n"
                "publish(dst)\n"
            ),
            "walrus": (
                "from pathlib import Path\n"
                "if (tmp := Path(src)).exists():\n"
                "    tmp.replace(dst)\n"
            ),
            "may_alias_branch": (
                "from pathlib import Path\n"
                "tmp = Path(src) if flag else str(src)\n"
                "tmp.replace(dst)\n"
            ),
            "deferred_import": (
                "def publish():\n"
                "    Path(src).replace(dst)\n"
                "from pathlib import Path\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source), ["INV-DURABILITY-001"])

    def test_annotation_applies_to_imported_values(self):
        """An imported *value* annotated as Path / ZipFile is an instance."""
        path_value = (
            "from pathlib import Path\n"
            "from backend.settings import TMP\n"
            "tmp: Path = TMP\n"
            "tmp.replace(dst)\n"
        )
        self.assertEqual(_codes(path_value), ["INV-DURABILITY-001"])
        zip_value = (
            "from zipfile import ZipFile\n"
            "from backend.archives import current\n"
            "zf: ZipFile = current\n"
            "zf.extractall(dest)\n"
        )
        self.assertEqual(_codes(zip_value), ["INV-BACKUP-001"])
        class_alias = (
            "from pathlib import Path\n"
            "P: type[Path] = Path\n"
            "P.cwd()\n"
            "'x'.replace('a', 'b')\n"
        )
        self.assertEqual(_codes(class_alias), [])

    def test_path_replace_message_names_boundary(self):
        [finding] = checker.check_source(
            "from pathlib import Path\nPath(src).replace(dst)\n", "backend/new_feature.py"
        )
        self.assertIn("pathlib.Path.replace()", finding.message)
        self.assertIn("backend.fs_durability", finding.message)

    def test_path_replace_uses_the_os_replace_boundary(self):
        source = "from pathlib import Path\nPath(src).replace(dst)\n"
        self.assertEqual(_codes(source, "backend/fs_durability.py"), [])
        self.assertEqual(_codes(source, "backend/server.py"), ["INV-DURABILITY-001"])

    def test_unrelated_replace_calls_pass(self):
        cases = {
            "unknown_object": "record.replace(title='x')\n",
            "string_literal": "'a-b'.replace('-', '_')\n",
            "string_name": "name = 'a-b'\nname.replace('-', '_')\n",
            "str_of_path": (
                "from pathlib import Path\n"
                "text = str(Path(src))\n"
                "text.replace('/', '_')\n"
            ),
            "path_name_attribute": (
                "from pathlib import Path\n"
                "Path(src).name.replace('.pdf', '')\n"
            ),
            "datetime_replace": (
                "import datetime\n"
                "datetime.datetime.now().replace(microsecond=0)\n"
            ),
            "shadowed_by_parameter": (
                "from pathlib import Path\n"
                "def f(Path):\n"
                "    Path(src).replace(dst)\n"
            ),
            "shadowed_by_other_import": (
                "from pathlib import Path\n"
                "from mylib import Path\n"
                "Path(src).replace(dst)\n"
            ),
            "shadowed_by_assignment": (
                "from pathlib import Path\n"
                "Path = Template\n"
                "Path(src).replace(dst)\n"
            ),
            "rebound_to_string": (
                "from pathlib import Path\n"
                "p = Path(src)\n"
                "p = p.name\n"
                "p.replace('a', 'b')\n"
            ),
            "rebound_to_literal": (
                "from pathlib import Path\n"
                "p = Path(src)\n"
                "p = 'text'\n"
                "p.replace('t', 'x')\n"
            ),
            "binding_does_not_leak_across_functions": (
                "from pathlib import Path\n"
                "def a():\n"
                "    p = Path(src)\n"
                "def b(p):\n"
                "    p.replace('a', 'b')\n"
            ),
            "pure_path_name": (
                "import pathlib\n"
                "pathlib.PurePath(src).name.replace('a', 'b')\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source), [])

    def test_os_replace_still_reported(self):
        self.assertEqual(
            _codes("import os as o\no.replace('a', 'b')\n"), ["INV-DURABILITY-001"]
        )


class ManagedPdfRemovalTests(unittest.TestCase):
    """INV-STORAGE-002: raw managed-PDF deletes bypass survivor-aware cleanup."""

    def test_blocks_raw_managed_pdf_removals(self):
        cases = {
            "os_remove_pure_path_joinpath": (
                "import os\n"
                "from pathlib import PurePath\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove(PurePath(pdfs_dir).joinpath(name))\n"
            ),
            "remove_stored_on_attribute": (
                "import os\n"
                "class Cleaner:\n"
                "    def drop(self, pdfs_dir, name):\n"
                "        self.rm = os.remove\n"
                "        self.rm(os.path.join(pdfs_dir, name))\n"
            ),
            "saved_path_rename_onto_managed_destination": (
                "from pathlib import Path\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    move = Path(scratch).rename\n"
                "    move(Path(pdfs_dir) / name)\n"
            ),
            "os_rename_spread_then_keyword_destination": (
                "import os\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    os.rename(*[scratch], dst=os.path.join(pdfs_dir, name))\n"
            ),
            "shutil_move_spread_then_keyword_destination": (
                "import os, shutil\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    shutil.move(*[scratch], dst=os.path.join(pdfs_dir, name))\n"
            ),
            "os_renames_spread_then_keyword_destination": (
                "import os\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    os.renames(*[scratch], new=os.path.join(pdfs_dir, name))\n"
            ),
            "os_renames_keyword_managed_destination": (
                "import os\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    os.renames(old=scratch, new=os.path.join(pdfs_dir, name))\n"
            ),
            "os_renames_keyword_managed_source": (
                "import os\n"
                "def archive(pdfs_dir, name, elsewhere):\n"
                "    os.renames(old=os.path.join(pdfs_dir, name), new=elsewhere)\n"
            ),
            "os_rename_overwrites_managed_destination": (
                "import os\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    os.rename(scratch, os.path.join(pdfs_dir, name))\n"
            ),
            "shutil_move_keyword_managed_destination": (
                "import os, shutil\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    shutil.move(src=scratch, dst=os.path.join(pdfs_dir, name))\n"
            ),
            "path_rename_onto_managed_destination": (
                "from pathlib import Path\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    Path(scratch).rename(Path(pdfs_dir) / name)\n"
            ),
            "unbound_path_rename_onto_managed_destination": (
                "from pathlib import Path\n"
                "def publish(pdfs_dir, scratch, name):\n"
                "    Path.rename(Path(scratch), Path(pdfs_dir) / name)\n"
            ),
            "os_remove_pure_path_join": (
                "import os\n"
                "from pathlib import PurePath\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove(PurePath(pdfs_dir) / name)\n"
            ),
            "os_remove_normcase_join": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove(os.path.normcase(os.path.join(pdfs_dir, name)))\n"
            ),
            "scandir_context_manager_walrus": (
                "import os\n"
                "def wipe(pdfs_dir):\n"
                "    with (cm := os.scandir(pdfs_dir)) as entries:\n"
                "        for entry in entries:\n"
                "            os.remove(entry.path)\n"
            ),
            "scandir_context_manager_assigned": (
                "import os\n"
                "def wipe(pdfs_dir):\n"
                "    cm = os.scandir(pdfs_dir)\n"
                "    with cm as entries:\n"
                "        for entry in entries:\n"
                "            os.remove(entry.path)\n"
            ),
            "scandir_context_manager_conditional": (
                "import os\n"
                "def wipe(pdfs_dir, other, flag):\n"
                "    with (os.scandir(pdfs_dir) if flag else os.scandir(other)) as entries:\n"
                "        for entry in entries:\n"
                "            os.remove(entry.path)\n"
            ),
            "scandir_context_manager_entries": (
                "import os\n"
                "def wipe(pdfs_dir):\n"
                "    with os.scandir(pdfs_dir) as entries:\n"
                "        for entry in entries:\n"
                "            os.remove(entry.path)\n"
            ),
            "os_rename_out_of_managed_dir": (
                "import os\n"
                "def archive(pdfs_dir, name, dest):\n"
                "    os.rename(os.path.join(pdfs_dir, name), dest)\n"
            ),
            "shutil_move_keyword_src": (
                "import os, shutil\n"
                "def archive(pdfs_dir, name, dest):\n"
                "    shutil.move(src=os.path.join(pdfs_dir, name), dst=dest)\n"
            ),
            "path_rename_managed": (
                "from pathlib import Path\n"
                "def archive(pdfs_dir, name, dest):\n"
                "    (Path(pdfs_dir) / name).rename(dest)\n"
            ),
            "unbound_path_rename": (
                "from pathlib import Path\n"
                "def archive(pdfs_dir, name, dest):\n"
                "    Path.rename(Path(pdfs_dir) / name, dest)\n"
            ),
            "shutil_rmtree_managed_dir": (
                "import shutil\n"
                "def wipe(pdfs_dir):\n"
                "    shutil.rmtree(pdfs_dir)\n"
            ),
            "shutil_rmtree_alias_keyword": (
                "from shutil import rmtree as nuke\n"
                "def wipe(db):\n"
                "    nuke(path=db.storage.pdfs_dir, ignore_errors=True)\n"
            ),
            "os_remove_join": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove(os.path.join(pdfs_dir, name))\n"
            ),
            "os_remove_fsencoded_join": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove(os.fsencode(os.path.join(pdfs_dir, name)))\n"
            ),
            "os_unlink_contained_helper": (
                "import os\n"
                "from backend.db_manager import safe_pdf_path_under_dir\n"
                "def drop(db, name):\n"
                "    path = safe_pdf_path_under_dir(db.storage.pdfs_dir, name)\n"
                "    if path:\n"
                "        os.unlink(path)\n"
            ),
            "os_module_alias": (
                "import os as operating_system\n"
                "def drop(self, name):\n"
                "    path = operating_system.path.join(self.storage.pdfs_dir, name)\n"
                "    operating_system.remove(path)\n"
            ),
            "from_import_alias": (
                "import os\n"
                "from os import remove as rm\n"
                "def drop(pdfs_dir, name):\n"
                "    rm(os.path.join(pdfs_dir, name))\n"
            ),
            "local_function_alias": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    delete = os.unlink\n"
                "    delete(f'{pdfs_dir}/{name}')\n"
            ),
            "helper_module_alias": (
                "import os\n"
                "import backend.db_manager as dbm\n"
                "def drop(storage, name):\n"
                "    os.remove(dbm.safe_pdf_path_under_dir(storage.pdfs_dir, name))\n"
            ),
            "helper_import_alias": (
                "import os\n"
                "from backend.db_manager import safe_pdf_path_under_dir as contain\n"
                "def drop(root, name):\n"
                "    os.remove(contain(root, name))\n"
            ),
            "path_unlink_constructor": (
                "from pathlib import Path\n"
                "def drop(pdfs_dir, name):\n"
                "    Path(pdfs_dir, name).unlink(missing_ok=True)\n"
            ),
            "path_unlink_div_alias": (
                "import pathlib as pl\n"
                "def drop(db, name):\n"
                "    target = pl.Path(db.storage.pdfs_dir) / name\n"
                "    victim = target\n"
                "    victim.unlink()\n"
            ),
            "path_unlink_bound_method": (
                "from pathlib import Path\n"
                "def drop(pdfs_dir, name):\n"
                "    unlink = (Path(pdfs_dir) / name).unlink\n"
                "    unlink()\n"
            ),
            "path_glob_loop": (
                "from pathlib import Path\n"
                "def purge(pdfs_dir):\n"
                "    for path in Path(pdfs_dir).glob('*.pdf'):\n"
                "        path.unlink()\n"
            ),
            "path_iterdir_comprehension": (
                "from pathlib import Path\n"
                "def purge(db):\n"
                "    for path in [p for p in Path(db.storage.pdfs_dir).iterdir()]:\n"
                "        path.unlink()\n"
            ),
            "path_as_posix_string": (
                "import os\n"
                "from pathlib import Path\n"
                "def drop(pdfs_dir, name):\n"
                "    path = (Path(pdfs_dir) / name).as_posix()\n"
                "    os.remove(path)\n"
            ),
            "path_walk_root": (
                "from pathlib import Path\n"
                "def purge(pdfs_dir):\n"
                "    for root, _, files in Path(pdfs_dir).walk():\n"
                "        (root / files[0]).unlink()\n"
            ),
            "path_unlink_unbound_alias": (
                "from pathlib import Path\n"
                "def drop(pdfs_dir, name):\n"
                "    delete = Path.unlink\n"
                "    delete(Path(pdfs_dir) / name)\n"
            ),
            "os_scandir_entry_path": (
                "import os\n"
                "def purge(pdfs_dir):\n"
                "    for entry in os.scandir(pdfs_dir):\n"
                "        os.remove(entry.path)\n"
            ),
            "os_walk_root": (
                "import os\n"
                "def purge(pdfs_dir):\n"
                "    for root, _, files in os.walk(pdfs_dir):\n"
                "        os.remove(os.path.join(root, files[0]))\n"
            ),
            "relative_import_helper": (
                "import os\n"
                "from .db_manager import safe_pdf_path_under_dir as contain\n"
                "def drop(root, name):\n"
                "    os.remove(contain(root, name))\n"
            ),
            "os_fwalk_root": (
                "import os\n"
                "def purge(pdfs_dir):\n"
                "    for root, _, files, _ in os.fwalk(pdfs_dir):\n"
                "        os.remove(os.path.join(root, files[0]))\n"
            ),
            "path_unlink_unbound": (
                "from pathlib import Path\n"
                "def drop(pdfs_dir, name):\n"
                "    Path.unlink(Path(pdfs_dir) / name)\n"
            ),
            "http_route_helper": (
                "import os\n"
                "def drop(path):\n"
                "    pdf_path = _safe_pdf_path_for_route(path)\n"
                "    os.remove(pdf_path)\n"
            ),
            "str_format_join": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    os.remove('{}/{}'.format(pdfs_dir, name))\n"
            ),
            "server_style_realpath": (
                "import os\n"
                "def drop(pdfs_dir, name):\n"
                "    base = os.path.realpath(pdfs_dir)\n"
                "    full = os.path.normpath(os.path.join(base, os.path.basename(name)))\n"
                "    os.remove(full)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source), ["INV-STORAGE-002"])

    def test_removal_message_names_capability(self):
        [finding] = checker.check_source(
            "import os\ndef drop(pdfs_dir, n):\n    os.remove(os.path.join(pdfs_dir, n))\n",
            "backend/new_feature.py",
        )
        self.assertIn("os.remove()", finding.message)
        self.assertIn("survivor-aware", finding.message)
        self.assertIn("_remove_managed_pdf", finding.message)
        self.assertIn("discard_unowned_managed_pdf", finding.message)

    def test_os_replace_of_managed_source_is_a_removal(self):
        """Replacing *from* a managed name drops it like os.rename, even in a
        file whose os.replace durability boundary is allowlisted."""
        source = (
            "import os\n"
            "def move_out(pdfs_dir, name, dest):\n"
            "    os.replace(os.path.join(pdfs_dir, name), dest)\n"
        )
        self.assertEqual(
            sorted(_codes(source)), ["INV-DURABILITY-001", "INV-STORAGE-002"]
        )
        self.assertEqual(
            _codes(source, "backend/services/work_pdf_replace.py"), ["INV-STORAGE-002"]
        )

    def test_unrelated_file_cleanup_passes(self):
        cases = {
            "pure_path_other_dir": (
                "import os\n"
                "from pathlib import PurePath\n"
                "def prune(thumbs_dir, name):\n"
                "    os.remove(PurePath(thumbs_dir) / name)\n"
            ),
            "scandir_context_manager_other_dir": (
                "import os\n"
                "def prune(thumbs_dir):\n"
                "    with os.scandir(thumbs_dir) as entries:\n"
                "        for entry in entries:\n"
                "            os.remove(entry.path)\n"
            ),
            "rename_scratch_file": (
                "import os, tempfile\n"
                "def rotate(log_dir):\n"
                "    os.rename(os.path.join(log_dir, 'a.log'), os.path.join(log_dir, 'a.1'))\n"
            ),
            "rmtree_scratch_dir": (
                "import shutil, tempfile\n"
                "def cleanup():\n"
                "    scratch = tempfile.mkdtemp()\n"
                "    shutil.rmtree(scratch)\n"
            ),
            "temp_file_in_managed_dir": (
                "import os, tempfile\n"
                "def publish(pdfs_dir):\n"
                "    fd, tmp = tempfile.mkstemp(dir=os.path.realpath(pdfs_dir))\n"
                "    os.remove(tmp)\n"
            ),
            "thumbnail_cache": (
                "import os\n"
                "def prune(thumbs_dir, fname):\n"
                "    os.remove(os.path.join(thumbs_dir, fname))\n"
            ),
            "person_image_cache_path": (
                "from pathlib import Path\n"
                "def prune(people_dir, fname):\n"
                "    Path(people_dir, fname).unlink()\n"
            ),
            "index_files": (
                "import os\n"
                "def discard(db_path):\n"
                "    for suffix in ('', '-wal'):\n"
                "        os.remove(db_path + suffix)\n"
            ),
            "processing_inbox_source": (
                "import os\n"
                "def finish(source_abs):\n"
                "    os.remove(source_abs)\n"
            ),
            "backup_archive": (
                "import os\n"
                "def cleanup(archive_path):\n"
                "    os.unlink(archive_path)\n"
            ),
            "rebound_away_from_managed": (
                "import os\n"
                "def f(pdfs_dir, cache_dir, n):\n"
                "    path = os.path.join(pdfs_dir, n)\n"
                "    path = os.path.join(cache_dir, n)\n"
                "    os.remove(path)\n"
            ),
            "shadowed_os": (
                "def f(os, pdfs_dir, n):\n"
                "    os.remove(pdfs_dir + n)\n"
            ),
            "shadowed_helper": (
                "import os\n"
                "def f(safe_pdf_path_under_dir, cache, n):\n"
                "    os.remove(safe_pdf_path_under_dir(cache, n))\n"
            ),
            "cache_dir_glob": (
                "from pathlib import Path\n"
                "def purge(thumbs_dir):\n"
                "    for path in Path(thumbs_dir).glob('*.webp'):\n"
                "        path.unlink()\n"
            ),
            "cache_scandir": (
                "import os\n"
                "def purge(thumbs_dir):\n"
                "    for entry in os.scandir(thumbs_dir):\n"
                "        os.remove(entry.path)\n"
            ),
            "unrelated_unlink_method": (
                "def f(pdfs_dir, link):\n"
                "    link.unlink(pdfs_dir)\n"
            ),
            "exists_check_only": (
                "import os\n"
                "def f(pdfs_dir, n):\n"
                "    return os.path.exists(os.path.join(pdfs_dir, n))\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source), [])

    def _real(self, relpath: str) -> str:
        return (_ROOT / relpath).read_text(encoding="utf-8")

    def test_approved_survivor_aware_cleanup_passes(self):
        """The real _remove_managed_pdf / retry pass hold the capability."""
        deletion = "backend/work_deletion.py"
        self.assertEqual(_codes(self._real(deletion), deletion), [])
        replace = "backend/services/work_pdf_replace.py"
        self.assertEqual(_codes(self._real(replace), replace), [])

    def test_capability_is_function_level_not_file_level(self):
        rogue = (
            "\n\ndef _fast_delete(db, filename):\n"
            "    os.remove(safe_pdf_path_under_dir(db.storage.pdfs_dir, filename))\n"
        )
        deletion = "backend/work_deletion.py"
        self.assertEqual(_codes(self._real(deletion) + rogue, deletion), ["INV-STORAGE-002"])
        nested = (
            "import os\n"
            "def _remove_managed_pdf(db, filename, pdfs_dir):\n"
            "    def later():\n"
            "        os.remove(os.path.join(pdfs_dir, filename))\n"
            "    return later\n"
        )
        self.assertEqual(_codes(nested, deletion), ["INV-STORAGE-002"])
        # The approved name in another module is not the capability.
        moved = (
            "import os\n"
            "def _remove_managed_pdf(db, filename, pdfs_dir):\n"
            "    os.remove(os.path.join(pdfs_dir, filename))\n"
        )
        self.assertEqual(_codes(moved, "backend/server.py"), ["INV-STORAGE-002"])
        self.assertEqual(_codes(moved, deletion), [])
        # Deferred bodies run outside the capability's lock and re-check.
        deferred = {
            "lambda": (
                "import os\n"
                "def _remove_managed_pdf(db, filename, pdfs_dir):\n"
                "    return lambda: os.remove(os.path.join(pdfs_dir, filename))\n"
            ),
            "generator": (
                "import os\n"
                "def _remove_managed_pdf(db, names, pdfs_dir):\n"
                "    return (os.remove(os.path.join(pdfs_dir, n)) for n in names)\n"
            ),
        }
        for label, source in deferred.items():
            with self.subTest(deferred=label):
                self.assertEqual(_codes(source, deletion), ["INV-STORAGE-002"])

    def test_raw_unlink_helper_is_reserved_for_minted_rollback(self):
        call_sites = {
            "server_attribute": (
                "from backend.services import work_pdf_replace\n"
                "def handler(pdfs_dir, name):\n"
                "    work_pdf_replace.unlink_managed_pdf_best_effort(pdfs_dir, name)\n"
            ),
            "import_alias": (
                "from backend.services.work_pdf_replace import (\n"
                "    unlink_managed_pdf_best_effort as unlink,\n"
                ")\n"
                "def handler(pdfs_dir, name):\n"
                "    unlink(pdfs_dir, name)\n"
            ),
        }
        for label, source in call_sites.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, "backend/server.py"), ["INV-STORAGE-002"])
        rogue = (
            "\n\ndef _drop_shared(pdfs_dir, name):\n"
            "    unlink_managed_pdf_best_effort(pdfs_dir, name)\n"
        )
        replace = "backend/services/work_pdf_replace.py"
        self.assertEqual(_codes(self._real(replace) + rogue, replace), ["INV-STORAGE-002"])

    def test_survivor_aware_entry_points_are_callable_anywhere(self):
        source = (
            "from backend.work_deletion import cleanup_released_managed_pdfs, _remove_managed_pdf\n"
            "from backend.services import work_pdf_replace\n"
            "def f(db, pdfs_dir, names, stored):\n"
            "    cleanup_released_managed_pdfs(db, names)\n"
            "    _remove_managed_pdf(db, stored, pdfs_dir)\n"
            "    work_pdf_replace.discard_unowned_managed_pdf(pdfs_dir, stored, db=db)\n"
        )
        self.assertEqual(_codes(source, "backend/server.py"), [])


class ManagedPdfAdoptionTests(unittest.TestCase):
    """INV-STORAGE-003: claiming existing managed bytes needs the adoption guard."""

    def test_blocks_unguarded_adoption(self):
        cases = {
            "request_file_path_create": (
                "def create(db, data):\n"
                "    db.add_work(title='t', file_path=data.get('file_path'))\n"
            ),
            "request_basename_fstring": (
                "def create(db, data):\n"
                "    name = data['name']\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{name}')\n"
            ),
            "positional_file_path": (
                "def create(db, fp):\n"
                "    db.add_work('t', 'Not Started', '', '', '', fp)\n"
            ),
            "literal_managed_path": (
                "def create(db):\n"
                "    db.add_work(title='t', file_path='/api/pdfs/shared.pdf')\n"
            ),
            "patch_fields_dict": (
                "def patch(db, w_id, fp):\n"
                "    db.update_work_metadata(w_id, {'file_path': fp, 'title': 'x'})\n"
            ),
            "patch_opaque_body": (
                "def patch(db, w_id, body):\n"
                "    db.update_work_metadata(w_id, body)\n"
            ),
            "patch_spread_body": (
                "def patch(db, w_id, body):\n"
                "    db.update_work_metadata(w_id, {'title': 'x', **body})\n"
            ),
            "retarget_helper_alias": (
                "from backend.services.work_pdf_replace import (\n"
                "    retarget_work_managed_file_path as retarget,\n"
                ")\n"
                "def adopt(db, w_id, name):\n"
                "    retarget(db, w_id, '/api/pdfs/' + name)\n"
            ),
            "raw_sql_update": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "raw_sql_through_variable": (
                "def adopt(db, w_id, fp):\n"
                "    query = '''\n"
                "        UPDATE works\n"
                "        SET title = ?, file_path = ?, updated_at = CURRENT_TIMESTAMP\n"
                "        WHERE id = ?\n"
                "    '''\n"
                "    db.execute_query(query, ('t', fp, w_id))\n"
            ),
            "raw_sql_insert": (
                "def create(conn, w_id, fp):\n"
                "    conn.execute(\n"
                "        'INSERT INTO works (id, title, file_path) VALUES (?, ?, ?)',\n"
                "        (w_id, 't', fp),\n"
                "    )\n"
            ),
            "minted_only_on_one_path": (
                "from backend.services.work_pdf_replace import store_new_managed_pdf_bytes\n"
                "def create(db, data, pdfs_dir):\n"
                "    fp = data.get('file_path')\n"
                "    if data.get('file_b64'):\n"
                "        stored = store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', b'')\n"
                "        fp = f'/api/pdfs/{stored}'\n"
                "    db.add_work(title='t', file_path=fp)\n"
            ),
            "wrong_context_manager": (
                "import contextlib\n"
                "def create(db, fp, lock):\n"
                "    with lock, contextlib.nullcontext():\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
            "guard_does_not_cover_deferred_def": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        def later():\n"
                "            db.add_work(title='t', file_path=fp)\n"
                "    return later\n"
            ),
            "guard_ends_with_block": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        pass\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{name}')\n"
            ),
            "guard_for_other_path": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp, other_fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        db.add_work(title='t', file_path=other_fp)\n"
            ),
            "guard_constructed_not_entered": (
                "from contextlib import nullcontext\n"
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with nullcontext(work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp)):\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
            "conditional_with_non_noop_arm": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp, lock, flag):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) if flag else lock:\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
            "generator_escapes_guard": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        return (db.add_work(title=i, file_path=fp) for i in items)\n"
            ),
            "kwargs_body": (
                "def create(db, body):\n"
                "    db.add_work(**body)\n"
            ),
            "kwargs_after_title": (
                "def create(db, extra):\n"
                "    db.add_work('t', **extra)\n"
            ),
            "kwargs_literal": (
                "def create(db, fp):\n"
                "    db.add_work(title='t', **{'file_path': fp})\n"
            ),
            "star_args": (
                "def create(db, args):\n"
                "    db.add_work(*args)\n"
            ),
            "spread_overrides_cleared_path": (
                "def patch(db, w_id, body):\n"
                "    db.update_work_metadata(w_id, {'file_path': '', **body})\n"
            ),
            "raw_sql_fstring": (
                "def adopt(conn, w_id, fp, col):\n"
                "    conn.execute(f'UPDATE works SET file_path = ? WHERE {col} = ?', (fp, w_id))\n"
            ),
            "raw_sql_concatenated": (
                "def adopt(conn, w_id, fp, where_sql):\n"
                "    conn.execute('UPDATE works SET file_path = ? ' + where_sql, (fp, w_id))\n"
            ),
            "raw_sql_percent_and_strip": (
                "def adopt(conn, w_id, fp, key):\n"
                "    query = ('UPDATE works SET file_path = ? WHERE %s = ?' % key).strip()\n"
                "    conn.execute(query, (fp, w_id))\n"
            ),
            "raw_sql_quoted_identifiers": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE \"works\" SET \"file_path\" = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "raw_sql_bracket_and_schema": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('INSERT INTO main.[works] ([id], `file_path`) VALUES (?, ?)', (w_id, fp))\n"
            ),
            "guarded_input_with_suffix": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=fp + '.other')\n"
            ),
            "guarded_input_fstring_suffix": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=f'{fp}.other')\n"
            ),
            "guarded_name_with_suffix": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as n:\n"
                "        db.add_work(title='t', file_path=f'/api/pdfs/{n}.bak')\n"
            ),
            "minted_name_with_prefix": (
                "from backend.services.work_pdf_replace import store_new_managed_pdf_bytes\n"
                "def create(db, pdfs_dir, body):\n"
                "    stored = store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/old_{stored}')\n"
            ),
            "guarded_path_rewrapped": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=f'/api/pdfs/{fp}')\n"
            ),
            "raw_sql_keyword_query": (
                "def adopt(db, w_id, fp):\n"
                "    db.execute_query(query='UPDATE works SET file_path = ? WHERE id = ?', params=(fp, w_id))\n"
            ),
            "raw_sql_replace_into": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('REPLACE INTO works (id, file_path) VALUES (?, ?)', (w_id, fp))\n"
            ),
            "bound_method_alias": (
                "def create(db, fp):\n"
                "    save = db.add_work\n"
                "    save(title='t', file_path=fp)\n"
            ),
            "bound_sql_alias": (
                "def adopt(conn, w_id, fp):\n"
                "    run = conn.execute\n"
                "    run('UPDATE works SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "raw_sql_upsert_do_update": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute(\n"
                "        'INSERT INTO works (id) VALUES (?) '\n"
                "        'ON CONFLICT(id) DO UPDATE SET file_path = ?', (w_id, fp))\n"
            ),
            "guarded_dict_mutated_via_alias": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir):\n"
                "    fields = body\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        fields['file_path'] = other\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_updated_via_alias": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, extra, pdfs_dir):\n"
                "    fields = body\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        fields.update(extra)\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "raw_sql_update_or_replace": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE OR REPLACE works SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "guarded_dict_union_via_alias": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir):\n"
                "    fields = body\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        fields |= {'file_path': other}\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "raw_sql_update_table_alias": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works AS w SET file_path = ? WHERE w.id = ?', (fp, w_id))\n"
            ),
            "guarded_dict_annotated_store": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body['file_path']: str = other\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "raw_sql_update_indexed_by": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works INDEXED BY idx SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "raw_sql_update_not_indexed": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works NOT INDEXED SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "unbound_update_metadata": (
                "from backend.db_manager import PRKSDatabase\n"
                "def patch(db, body):\n"
                "    PRKSDatabase.update_work_metadata(db, 'id', body)\n"
            ),
            "unbound_update_metadata_alias": (
                "from backend.db_manager import PRKSDatabase\n"
                "def patch(db, body):\n"
                "    upd = PRKSDatabase.update_work_metadata\n"
                "    upd(db, 'id', body)\n"
            ),
            "unbound_add_work_positional": (
                "from backend.db_manager import PRKSDatabase\n"
                "def create(db, fp):\n"
                "    PRKSDatabase.add_work(db, 't', 'Not Started', '', '', '', fp)\n"
            ),
            "guarded_dict_passed_to_helper": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        apply_defaults(body)\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_updated_in_comprehension": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        _ = [body.update(x) for x in items]\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_escapes_in_comprehension": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        _ = {apply_defaults(body) for _ in items}\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_updated_in_consumed_generator": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        list(body.update(x) for x in items)\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_dirtied_before_break": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        for x in items:\n"
                "            if x:\n"
                "                body.update(x)\n"
                "                break\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_dirtied_before_continue": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        for x in items:\n"
                "            db.update_work_metadata(w_id, body)\n"
                "            if x:\n"
                "                body.update(x)\n"
                "                continue\n"
            ),
            "guarded_dict_dirtied_for_next_iteration": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        for x in items:\n"
                "            db.update_work_metadata(w_id, body)\n"
                "            body.update(x)\n"
            ),
            "guarded_dict_dirtied_before_while_break": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        while items:\n"
                "            if items.pop():\n"
                "                apply_defaults(body)\n"
                "                break\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_dirtied_before_raise": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        try:\n"
                "            body.update(other)\n"
                "            validate(other)\n"
                "            body['file_path'] = body.get('file_path')\n"
                "        except ValueError:\n"
                "            db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_tuple_unpack_store": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body['file_path'], _ = other, 1\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_starred_unpack_store": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body['file_path'], *_ = other\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_loop_target_store": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items, other):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        for body['file_path'] in items:\n"
                "            pass\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "partial_bound_under_guard_invoked_after": (
                "import functools\n"
                "from backend.services import work_pdf_replace\n"
                "def adopt(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        save = functools.partial(db.add_work, file_path=fp)\n"
                "    save(title='t')\n"
            ),
            "partial_bound_guarded_basename_positional": (
                "import functools\n"
                "from backend.services import work_pdf_replace\n"
                "def adopt(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        save = functools.partial(db.add_work, 't', 's', '', '', '', f'/api/pdfs/{name}')\n"
                "    save()\n"
            ),
            "inline_nested_partial_bound_under_guard": (
                "import functools\n"
                "from backend.services import work_pdf_replace\n"
                "def adopt(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        save = functools.partial(functools.partial(db.add_work, 't'), file_path=fp)\n"
                "    save()\n"
            ),
            "inline_nested_partial_unguarded": (
                "import functools\n"
                "def adopt(db, fp):\n"
                "    functools.partial(functools.partial(db.add_work, 't'), file_path=fp)()\n"
            ),
            "inline_nested_partial_outer_keyword_wins": (
                "import functools\n"
                "def adopt(db, fp):\n"
                "    functools.partial(\n"
                "        functools.partial(db.add_work, 't', file_path=''), file_path=fp)()\n"
            ),
            "raw_sql_subquery_where_before_file_path": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET title = (SELECT title FROM works WHERE id = ?), '\n"
                "                 'file_path = ? WHERE id = ?', (w_id, fp, w_id))\n"
            ),
            "raw_sql_upsert_subquery_where": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('INSERT INTO works (id) VALUES (?) ON CONFLICT(id) DO UPDATE SET '\n"
                "                 'title = (SELECT t FROM x WHERE y), file_path = excluded.file_path', (w_id,))\n"
            ),
            "guarded_dict_alias_escapes_to_helper": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        alias = body\n"
                "        mutate(alias)\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guard_method_on_unrelated_object": (
                "def adopt(db, pdfs_dir, fp, fake):\n"
                "    with fake.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
            "minting_method_on_unrelated_object": (
                "def create(db, fake, data):\n"
                "    name = fake.store_new_managed_pdf_bytes(data)\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{name}')\n"
            ),
            "lookalike_minting_helper_defined_elsewhere": (
                "def store_new_managed_pdf_bytes(pdfs_dir, name, data):\n"
                "    return name\n"
                "def create(db, pdfs_dir, name):\n"
                "    stored = store_new_managed_pdf_bytes(pdfs_dir, name, b'')\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "minting_helper_from_lookalike_module": (
                "from fake.work_pdf_replace import store_new_managed_pdf_bytes\n"
                "def create(db, pdfs_dir, name):\n"
                "    stored = store_new_managed_pdf_bytes(pdfs_dir, name, b'')\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "guard_from_lookalike_module": (
                "import fake.work_pdf_replace\n"
                "def adopt(db, pdfs_dir, fp):\n"
                "    with fake.work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
            "relative_import_resolving_elsewhere": (
                "from .work_pdf_replace import store_new_managed_pdf_bytes\n"
                "def create(db, pdfs_dir, name):\n"
                "    stored = store_new_managed_pdf_bytes(pdfs_dir, name, b'')\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "guarded_dict_may_be_rebound": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir, cond):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        if cond:\n"
                "            body = other\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_escapes_via_conditional_or_dict": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, other, c):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        mutate({'fields': body if c else other})\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_escapes_in_list_comprehension_element": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        mutate([body for _ in items])\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_escapes_in_starred_generator": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        mutate(*(body for _ in [1]))\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_escapes_in_dict_comprehension_value": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        mutate({k: body for k in items})\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "sink_stored_on_attribute": (
                "class Creator:\n"
                "    def create(self, db, fp):\n"
                "        self.save = db.add_work\n"
                "        self.save(title='t', file_path=fp)\n"
            ),
            "raw_sql_double_quoted_returning_literal": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET title=\"RETURNING\", file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "raw_sql_double_quoted_order_literal": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET title=\"ORDER\", file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "raw_sql_double_quoted_limit_literal": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET title=\"LIMIT\", file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "guarded_dict_attribute_alias_escapes": (
                "from backend.services import work_pdf_replace\n"
                "class Patcher:\n"
                "    def patch(self, db, w_id, body, pdfs_dir):\n"
                "        with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "            self.fields = body\n"
                "            mutate(self.fields)\n"
                "            db.update_work_metadata(w_id, body)\n"
            ),
            "wrapped_joined_partial_offsets": (
                "import functools\n"
                "def create(db, fp, cond, text):\n"
                "    if cond:\n"
                "        p = functools.partial(db.add_work, 't')\n"
                "    else:\n"
                "        p = functools.partial(db.add_work, 't', 's', 'a')\n"
                "    q = functools.partial(p, text)\n"
                "    q('date', fp)\n"
            ),
            "guarded_dict_escapes_inside_starred_tuple": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        mutate(*(body,))\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "partial_offsets_joined_across_branches": (
                "import functools\n"
                "def create(db, fp, cond):\n"
                "    if cond:\n"
                "        save = functools.partial(db.add_work, 't')\n"
                "    else:\n"
                "        save = functools.partial(db.add_work, 't', 's', 'a')\n"
                "    save('text', 'date', fp)\n"
            ),
            "raw_sql_row_value_set": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET (file_path, status) = (?, ?) WHERE id = ?', (fp, 's', w_id))\n"
            ),
            "raw_sql_upsert_row_value": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('INSERT INTO works (id) VALUES (?) ON CONFLICT(id) '\n"
                "                 'DO UPDATE SET (file_path, title) = (?, ?)', (w_id, fp, 't'))\n"
            ),
            "functools_partial_sink": (
                "import functools\n"
                "def create(db, fp):\n"
                "    save = functools.partial(db.add_work, title='t', file_path=fp)\n"
                "    save()\n"
            ),
            "partial_alias_invoked_with_file_path": (
                "from functools import partial\n"
                "def create(db, fp):\n"
                "    save = partial(db.add_work, title='t')\n"
                "    save(file_path=fp)\n"
            ),
            "guarded_dict_explicit_ior": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body.__ior__({'file_path': other})\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "raw_sql_block_comment": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE /* reason */ works SET file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "raw_sql_line_comment": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works -- note\\n SET file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "unbound_connection_execute": (
                "import sqlite3\n"
                "def adopt(conn, w_id, fp):\n"
                "    sqlite3.Connection.execute(conn, 'UPDATE works SET file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "later_manager_mutates_guarded_dict": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')), mutate(body):\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "raw_sql_where_inside_literal": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute(\"UPDATE works SET title='WHERE', file_path=? WHERE id=?\", (fp, w_id))\n"
            ),
            "raw_sql_executescript": (
                "def adopt(conn):\n"
                "    conn.executescript(\"UPDATE works SET file_path='/api/pdfs/shared.pdf' WHERE id='1'\")\n"
            ),
            "raw_sql_columnless_insert": (
                "def create(conn, w_id, fp):\n"
                "    conn.execute('INSERT INTO works VALUES (?, ?, ?)', (w_id, 't', fp))\n"
            ),
            "raw_sql_insert_select": (
                "def copy(conn):\n"
                "    conn.execute('INSERT INTO works SELECT * FROM staged_works')\n"
            ),
            "partial_prebound_positional": (
                "from functools import partial\n"
                "def create(db, fp):\n"
                "    save = partial(db.add_work, 't')\n"
                "    save('Not Started', '', '', '', fp)\n"
            ),
            "raw_sql_double_quoted_literal": (
                "def adopt(conn, w_id, fp):\n"
                "    conn.execute('UPDATE works SET title=\"WHERE\", file_path=? WHERE id=?', (fp, w_id))\n"
            ),
            "nested_partial_offsets": (
                "from functools import partial\n"
                "def create(db, fp, status, abstract):\n"
                "    p = partial(db.add_work, 't')\n"
                "    q = partial(p, status, abstract)\n"
                "    q('text', 'date', fp)\n"
            ),
            "raw_sql_under_guard": (
                "from backend.services import work_pdf_replace\n"
                "def adopt(conn, pdfs_dir, w_id, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        conn.execute('UPDATE works SET file_path = ? WHERE id = ?', (fp, w_id))\n"
            ),
            "guarded_dict_rebound": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body = other\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_entry_replaced": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other_fp, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body['file_path'] = other_fp\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_updated": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, extra, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body.update(extra)\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_dynamic_key": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, key, val, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        body[key] = val\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "shadowed_guard_name": (
                "def create(db, pdfs_dir, fp, managed_pdf_adoption_guard):\n"
                "    with managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        db.add_work(title='t', file_path=fp)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, "backend/server.py"), ["INV-STORAGE-003"])

    def test_adoption_message_names_guard(self):
        [finding] = checker.check_source(
            "def f(db, fp):\n    db.add_work(title='t', file_path=fp)\n", "backend/server.py"
        )
        self.assertIn("managed_pdf_adoption_guard", finding.message)
        self.assertIn("store_new_managed_pdf_bytes", finding.message)

    _CREATE_SITE = (
        "from contextlib import nullcontext\n"
        "from backend.services import work_pdf_replace\n"
        "class PRKSHandler:\n"
        "    def handle_api_post(self, db, data, pdfs_dir):\n"
        "        stored_name = None\n"
        "        file_path = data.get('file_path', '')\n"
        "        if data.get('file_b64'):\n"
        "            stored_name = work_pdf_replace.store_new_managed_pdf_bytes(\n"
        "                pdfs_dir, data['file_name'], b'')\n"
        "            file_path = f'/api/pdfs/{stored_name}'\n"
        "        with (\n"
        "            work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, file_path)\n"
        "            if {cond}\n"
        "            else nullcontext()\n"
        "        ) as adopted_name:\n"
        "            if adopted_name:\n"
        "                file_path = f'/api/pdfs/{adopted_name}'\n"
        "            return db.add_work(title='t', file_path=file_path)\n"
    )
    _PATCH_SITE = (
        "from contextlib import nullcontext\n"
        "from backend.services import work_pdf_replace\n"
        "class PRKSHandler:\n"
        "    def handle_api_patch(self, db, w_id, body, pdfs_dir, file_path_changing, other):\n"
        "        with (\n"
        "            work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path'))\n"
        "            if {cond}\n"
        "            else nullcontext()\n"
        "        ) as adopted_name:\n"
        "            if adopted_name:\n"
        "                body['file_path'] = f'/api/pdfs/{adopted_name}'\n"
        "            return db.update_work_metadata(w_id, body)\n"
    )

    def test_reviewed_conditional_guard_sites_pass(self):
        for label, template, cond in (
            ("create", self._CREATE_SITE, "not stored_name"),
            ("patch", self._PATCH_SITE, "file_path_changing"),
        ):
            with self.subTest(site=label):
                source = template.replace("{cond}", cond)
                self.assertEqual(_codes(source, "backend/server.py"), [])

    def test_unreviewed_conditional_guard_is_not_protective(self):
        """``guard(...) if c else nullcontext()`` may skip the guard, so it only
        protects the reviewed (file, function, condition) sites."""
        cases = {
            "independent_condition": (
                "from contextlib import nullcontext\n"
                "from backend.services.work_pdf_replace import managed_pdf_adoption_guard\n"
                "def create(db, pdfs_dir, fp, unrelated_condition):\n"
                "    with (\n"
                "        managed_pdf_adoption_guard(pdfs_dir, fp)\n"
                "        if unrelated_condition\n"
                "        else nullcontext()\n"
                "    ):\n"
                "        db.add_work(title='t', file_path=fp)\n",
                "backend/new_feature.py",
            ),
            "reviewed_create_with_other_condition": (
                self._CREATE_SITE.replace("{cond}", "data.get('adopt')"),
                "backend/server.py",
            ),
            "reviewed_patch_with_other_condition": (
                self._PATCH_SITE.replace("{cond}", "other"),
                "backend/server.py",
            ),
            "reviewed_condition_in_other_file": (
                self._PATCH_SITE.replace("{cond}", "file_path_changing"),
                "backend/new_feature.py",
            ),
            "reviewed_condition_in_other_function": (
                self._PATCH_SITE.replace("{cond}", "file_path_changing").replace(
                    "handle_api_patch", "handle_api_put"
                ),
                "backend/server.py",
            ),
            "reversed_arms": (
                self._PATCH_SITE.replace("{cond}", "file_path_changing").replace(
                    "work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path'))\n"
                    "            if file_path_changing\n"
                    "            else nullcontext()",
                    "nullcontext()\n"
                    "            if not file_path_changing\n"
                    "            else work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path'))",
                ),
                "backend/server.py",
            ),
        }
        for label, (source, relpath) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, relpath), ["INV-STORAGE-003"])

    def test_current_guarded_adoption_shapes_pass(self):
        cases = {
            "guarded_dict_other_key_written": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')) as n:\n"
                "        body['title'] = 'x'\n"
                "        body['file_path'] = f'/api/pdfs/{n}'\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "comprehension_shadows_guarded_dict": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, rows):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        _ = [apply_defaults(body) for body in rows]\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_tuple_unpack_owned": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')) as n:\n"
                "        body['file_path'], body['title'] = f'/api/pdfs/{n}', 't'\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_loop_without_mutation": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        for x in items:\n"
                "            if not x:\n"
                "                break\n"
                "            db.update_work_metadata(w_id, body)\n"
            ),
            "guarded_dict_read_passed_to_helper": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        helper(body.get('title'))\n"
                "        helper(body['title'], body.keys())\n"
                "        helper([x for body in items])\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "dict_mutated_only_on_returning_branch": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, other, pdfs_dir, fail):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body.get('file_path')):\n"
                "        if fail:\n"
                "            body['file_path'] = other\n"
                "            return None\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "guard_input_subscript_dict": (
                "from backend.services import work_pdf_replace\n"
                "def patch(db, w_id, body, pdfs_dir):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, body['file_path']):\n"
                "        db.update_work_metadata(w_id, body)\n"
            ),
            "list_comprehension_runs_under_guard": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp, items):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
                "        return [db.add_work(title=i, file_path=fp) for i in items]\n"
            ),
            "guarded_name_or_none": (
                "from backend.services import work_pdf_replace\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp) as name:\n"
                "        db.add_work(title='t', file_path=f'/api/pdfs/{name}' if name else None)\n"
                "        db.add_work(title='t', file_path=name and f'/api/pdfs/{name}')\n"
            ),
            "guard_import_alias": (
                "from backend.services.work_pdf_replace import managed_pdf_adoption_guard as adopt\n"
                "def create(db, pdfs_dir, fp):\n"
                "    with adopt(pdfs_dir, fp) as name:\n"
                "        db.add_work(title='t', file_path=f'/api/pdfs/{name}' if name else fp)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, "backend/server.py"), [])

    def test_package_relative_import_is_canonical(self):
        """``from . import work_pdf_replace`` inside ``backend/services`` is the
        canonical module, not ``backend.work_pdf_replace``."""
        source = (
            "from . import work_pdf_replace\n"
            "def create(db, pdfs_dir, body, fp):\n"
            "    stored = work_pdf_replace.store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
            "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, fp):\n"
            "        db.add_work(title='t', file_path=fp)\n"
        )
        self.assertEqual(_codes(source, "backend/services/new_feature.py"), [])
        self.assertEqual(
            _codes(source, "backend/new_feature.py"), ["INV-STORAGE-003", "INV-STORAGE-003"]
        )

    def test_newly_minted_exclusive_pdf_passes(self):
        cases = {
            "relative_import_minting": (
                "from .services.work_pdf_replace import store_new_managed_pdf_bytes\n"
                "def create(db, pdfs_dir, body):\n"
                "    stored = store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "module_alias_minting": (
                "import backend.services.work_pdf_replace as wpr\n"
                "def create(db, pdfs_dir, body):\n"
                "    stored = wpr.store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "partial_bound_minted_name": (
                "import functools\n"
                "from backend.services import work_pdf_replace as wpr\n"
                "def create(db, pdfs_dir, body):\n"
                "    stored = wpr.store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
                "    save = functools.partial(db.add_work, file_path=f'/api/pdfs/{stored}')\n"
                "    save(title='t')\n"
            ),
            "upload_bytes": (
                "from backend.services import work_pdf_replace as wpr\n"
                "def create(db, pdfs_dir, body):\n"
                "    stored = wpr.store_new_managed_pdf_bytes(pdfs_dir, 'a.pdf', body)\n"
                "    db.add_work(title='t', file_path=f'/api/pdfs/{stored}')\n"
            ),
            "processing_import_try": (
                "def import_file(self, pdfs_dir, src):\n"
                "    from backend.services.work_pdf_replace import (\n"
                "        ManagedPdfStoreError, store_new_managed_pdf_from_path)\n"
                "    try:\n"
                "        local_filename = store_new_managed_pdf_from_path(pdfs_dir, 'a', src)\n"
                "    except ManagedPdfStoreError:\n"
                "        raise ValueError('no')\n"
                "    try:\n"
                "        self.add_work(title='t', file_path=f'/api/pdfs/{local_filename}')\n"
                "    except Exception:\n"
                "        raise\n"
            ),
            "exclusive_retarget": (
                "from backend.services.work_pdf_replace import (\n"
                "    allocate_exclusive_managed_filename, retarget_work_managed_file_path)\n"
                "def cow(db, w_id, shared):\n"
                "    exclusive = allocate_exclusive_managed_filename(w_id, shared)\n"
                "    target_fp = '/api/pdfs/' + exclusive\n"
                "    retarget_work_managed_file_path(db, w_id, target_fp)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, "backend/server.py"), [])

    def test_clearing_and_non_managed_values_pass(self):
        cases = {
            "inline_nested_partial_outer_clear_wins": (
                "import functools\n"
                "def f(db, fp):\n"
                "    functools.partial(\n"
                "        functools.partial(db.add_work, 't', file_path=fp), file_path='')()\n"
            ),
            "no_file_path": "def f(db):\n    db.add_work(title='t')\n",
            "empty_file_path": "def f(db):\n    db.add_work(title='t', file_path='')\n",
            "none_file_path": "def f(db):\n    db.add_work(title='t', file_path=None)\n",
            "video": (
                "def f(db, url):\n"
                "    db.add_work(title='t', file_path='', source_url=url, source_kind='video')\n"
            ),
            "clear_on_patch": "def f(db, w):\n    db.update_work_metadata(w, {'file_path': ''})\n",
            "metadata_only_patch": "def f(db, w, t):\n    db.update_work_metadata(w, {'title': t})\n",
            "kwargs_literal_without_file_path": (
                "def f(db, t):\n    db.add_work(title='t', **{'status': t})\n"
            ),
            "dynamic_column_primitive": (
                "def set_field(conn, field, value, w):\n"
                "    conn.execute('UPDATE works SET %s = ? WHERE id = ?' % field, (value, w))\n"
            ),
            "sql_clear": (
                "def f(conn, w):\n"
                "    conn.execute('UPDATE works SET file_path = NULL WHERE id = ?', (w,))\n"
            ),
            "sql_file_path_only_in_where": (
                "def f(conn, t, fp):\n"
                "    conn.execute('UPDATE works SET title = (SELECT ?) WHERE file_path = ?', (t, fp))\n"
            ),
            "sql_file_path_only_in_update_from": (
                "def f(conn):\n"
                "    conn.execute('UPDATE works SET title = s.title FROM src AS s WHERE s.file_path = works.file_path')\n"
            ),
            "sql_insert_null_file_path": (
                "def f(conn, w, t):\n"
                "    conn.execute(\n"
                "        'INSERT INTO works (id, title, file_path) VALUES (?, ?, NULL)', (w, t))\n"
            ),
            "sql_other_columns": (
                "def f(conn, w, t):\n"
                "    conn.execute('UPDATE works SET title = ? WHERE id = ?', (t, w))\n"
            ),
            "sql_upsert_other_columns": (
                "def f(conn, w, t):\n"
                "    conn.execute(\n"
                "        'INSERT INTO works (id, title) VALUES (?, ?) '\n"
                "        'ON CONFLICT(id) DO UPDATE SET title = excluded.title', (w, t))\n"
            ),
            "sql_row_value_clear": (
                "def f(conn, w):\n"
                "    conn.execute('UPDATE works SET (file_path, status) = (NULL, ?) WHERE id = ?', ('s', w))\n"
            ),
            "sql_comment_mentions_file_path": (
                "def f(conn, w, t):\n"
                "    conn.execute('UPDATE works SET title = ? /* not file_path = ? */ WHERE id = ?', (t, w))\n"
            ),
            "sql_literal_clear": (
                "def f(conn):\n"
                "    conn.executescript(\"UPDATE works SET file_path='' WHERE id='1'\")\n"
            ),
            "sql_read": (
                "def f(conn, w):\n"
                "    conn.execute('SELECT file_path FROM works WHERE id = ?', (w,))\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source, "backend/server.py"), [])

    def test_persistence_primitives_are_function_level_exemptions(self):
        primitive = (
            "class PRKSDatabase:\n"
            "    def add_work(self, file_path):\n"
            "        self.execute_query(\n"
            "            'INSERT INTO works (id, file_path) VALUES (?, ?)', ('w', file_path))\n"
        )
        self.assertEqual(_codes(primitive, "backend/db_manager.py"), [])
        renamed = primitive.replace("def add_work", "def add_work_fast")
        self.assertEqual(_codes(renamed, "backend/db_manager.py"), ["INV-STORAGE-003"])


class WeakManagedPdfAliasTests(unittest.TestCase):
    """INV-STORAGE-004: referenced_managed_pdf_filename may only block."""

    _IMPORT = "from backend.db_manager import referenced_managed_pdf_filename\n"

    def test_blocks_weak_alias_authority(self):
        cases = {
            "mint_cleanup_claim": (
                "def f(conn, fp):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "settle_claim": (
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, referenced_managed_pdf_filename(fp))\n"
            ),
            "delete_authority": (
                "def f(db, fp, pdfs_dir):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    _remove_managed_pdf(db, name, pdfs_dir)\n"
            ),
            "delete_batch": (
                "def f(db, fp):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    cleanup_released_managed_pdfs(db, [name])\n"
            ),
            "raw_claim_sql": (
                "def f(conn, fp):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    conn.execute(\n"
                "        'INSERT INTO pending_pdf_cleanup (filename) VALUES (?) '\n"
                "        'ON CONFLICT(filename) DO NOTHING', (name,))\n"
            ),
            "loop_over_weak_names": (
                "def f(conn, fps):\n"
                "    for name in [referenced_managed_pdf_filename(fps[0])]:\n"
                "        record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "set_comprehension_batch": (
                "def f(db, rows):\n"
                "    names = {referenced_managed_pdf_filename(r['file_path']) for r in rows}\n"
                "    cleanup_released_managed_pdfs(db, names)\n"
            ),
            "weak_upper": (
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, referenced_managed_pdf_filename(fp).upper())\n"
            ),
            "path_name_of_weak": (
                "from pathlib import Path\n"
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, Path(referenced_managed_pdf_filename(fp)).name)\n"
            ),
            "relative_import_weak": (
                "from ..db_manager import referenced_managed_pdf_filename as loose\n"
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, loose(fp))\n"
            ),
            "loop_over_named_list": (
                "def f(conn, fp):\n"
                "    weak = [referenced_managed_pdf_filename(fp)]\n"
                "    for name in weak:\n"
                "        record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "claim_sql_replace_into_keyword": (
                "def f(db, fp):\n"
                "    db.execute_query(sql='REPLACE INTO pending_pdf_cleanup (filename) VALUES (?)',\n"
                "                     params=(referenced_managed_pdf_filename(fp),))\n"
            ),
            "bound_claim_helper": (
                "from backend import work_deletion\n"
                "def f(conn, fp):\n"
                "    claim = work_deletion.record_pending_pdf_cleanup_on_conn\n"
                "    claim(conn, referenced_managed_pdf_filename(fp))\n"
            ),
            "claim_sql_concatenated": (
                "def f(conn, fp, suffix):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    conn.execute('INSERT INTO pending_pdf_cleanup (filename) VALUES (?) ' + suffix, (name,))\n"
            ),
            "weak_str_replace": (
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, referenced_managed_pdf_filename(fp).replace('x', 'x'))\n"
            ),
            "weak_encode_fsdecode": (
                "import os\n"
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, os.fsdecode(referenced_managed_pdf_filename(fp).encode()))\n"
            ),
            "weak_encode_decode": (
                "def f(db, fp):\n"
                "    forget_pending_pdf_cleanup(db, referenced_managed_pdf_filename(fp).encode().decode())\n"
            ),
            "weak_str_join": (
                "def f(db, fp):\n"
                "    name = ''.join([referenced_managed_pdf_filename(fp)])\n"
                "    forget_pending_pdf_cleanup(db, name)\n"
            ),
            "claim_sql_interpolated_weak": (
                "def f(conn, fp):\n"
                "    weak = referenced_managed_pdf_filename(fp)\n"
                "    conn.execute(f\"INSERT INTO pending_pdf_cleanup (filename) VALUES ('{weak}')\")\n"
            ),
            "adoption_guard_input": (
                "from backend.services import work_pdf_replace\n"
                "def f(pdfs_dir, fp):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    with work_pdf_replace.managed_pdf_adoption_guard(pdfs_dir, f'/api/pdfs/{name}'):\n"
                "        pass\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(self._IMPORT + source), ["INV-STORAGE-004"])

    def test_weak_alias_as_ownership_fails_even_under_guard(self):
        source = self._IMPORT + (
            "from backend.services.work_pdf_replace import managed_pdf_adoption_guard\n"
            "def f(db, pdfs_dir, fp):\n"
            "    with managed_pdf_adoption_guard(pdfs_dir, fp):\n"
            "        name = referenced_managed_pdf_filename(fp)\n"
            "        db.add_work(title='t', file_path=f'/api/pdfs/{name}')\n"
        )
        # The weak name is not what the guard validated, so it is not owned
        # either: both the weak-authority and the adoption rule fire.
        self.assertEqual(sorted(_codes(source)), ["INV-STORAGE-003", "INV-STORAGE-004"])
        weak_dict = self._IMPORT + (
            "from backend.services.work_pdf_replace import managed_pdf_adoption_guard\n"
            "def f(db, w, pdfs_dir, fp):\n"
            "    fields = {'file_path': f'/api/pdfs/{referenced_managed_pdf_filename(fp)}'}\n"
            "    with managed_pdf_adoption_guard(pdfs_dir, fp):\n"
            "        db.update_work_metadata(w, fields)\n"
        )
        self.assertEqual(sorted(_codes(weak_dict)), ["INV-STORAGE-003", "INV-STORAGE-004"])
        unguarded = self._IMPORT + (
            "def f(db, w, fp):\n"
            "    name = referenced_managed_pdf_filename(fp).strip()\n"
            "    db.update_work_metadata(w, {'file_path': '/api/pdfs/' + name})\n"
        )
        self.assertEqual(sorted(_codes(unguarded)), ["INV-STORAGE-003", "INV-STORAGE-004"])

    def test_weak_alias_cannot_authorize_unlink_inside_capability(self):
        source = (
            "import os\n"
            + self._IMPORT
            + "def _remove_managed_pdf(db, fp, pdfs_dir):\n"
            "    name = referenced_managed_pdf_filename(fp)\n"
            "    os.remove(safe_pdf_path_under_dir(pdfs_dir, name))\n"
        )
        self.assertEqual(_codes(source, "backend/work_deletion.py"), ["INV-STORAGE-004"])

    def test_weak_alias_import_forms(self):
        cases = {
            "import_alias": (
                "from backend.db_manager import referenced_managed_pdf_filename as loose\n"
                "def f(conn, fp):\n"
                "    record_pending_pdf_cleanup_on_conn(conn, loose(fp))\n"
            ),
            "module_alias": (
                "import backend.db_manager as dbm\n"
                "def f(conn, fp):\n"
                "    name = dbm.referenced_managed_pdf_filename(fp)\n"
                "    record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "local_callable_alias": (
                "from backend import db_manager\n"
                "def f(conn, fp):\n"
                "    weak = db_manager.referenced_managed_pdf_filename\n"
                "    alias = weak(fp)\n"
                "    record_pending_pdf_cleanup_on_conn(conn, alias)\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(source), ["INV-STORAGE-004"])

    def test_weak_alias_message_explains_contract(self):
        [finding] = checker.check_source(
            self._IMPORT
            + "def f(conn, fp):\n"
            "    record_pending_pdf_cleanup_on_conn(conn, referenced_managed_pdf_filename(fp))\n",
            "backend/new_feature.py",
        )
        self.assertIn("fail-closed", finding.message)
        self.assertIn("record_pending_pdf_cleanup_on_conn()", finding.message)
        self.assertIn("managed_basenames_protected_by", finding.message)

    def test_fail_closed_blocking_uses_pass(self):
        cases = {
            "row_references_blocker": (
                "def row_references_managed_pdf(file_path, filename):\n"
                "    if referenced_managed_pdf_filename(file_path) == filename:\n"
                "        return True\n"
                "    return owned_managed_pdf_basename(file_path) == filename\n"
            ),
            "adoption_rejection": (
                "def _adoption_managed_basename(value):\n"
                "    trimmed = str(value or '').strip()\n"
                "    if trimmed.startswith('/api/pdfs/') or referenced_managed_pdf_filename(trimmed):\n"
                "        raise ManagedPdfStoreError('invalid_file_name', 'bad')\n"
                "    return None\n"
            ),
            "defer_strong_claim": (
                "def f(db, conn, fp, name):\n"
                "    if referenced_managed_pdf_filename(fp) == name:\n"
                "        _note_attempt(db, name)\n"
                "        return False\n"
                "    record_pending_pdf_cleanup_on_conn(conn, name)\n"
                "    _remove_managed_pdf(db, name, db.storage.pdfs_dir)\n"
            ),
            "blocked_flag": (
                "def f(conn, fp, name):\n"
                "    blocked = referenced_managed_pdf_filename(fp) is not None\n"
                "    if blocked:\n"
                "        return\n"
                "    record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "rebound_to_strong_identity": (
                "def f(conn, fp):\n"
                "    name = referenced_managed_pdf_filename(fp)\n"
                "    name = owned_managed_pdf_basename(fp)\n"
                "    record_pending_pdf_cleanup_on_conn(conn, name)\n"
            ),
            "shadowed_helper": (
                "def f(conn, fp, referenced_managed_pdf_filename):\n"
                "    record_pending_pdf_cleanup_on_conn(conn, referenced_managed_pdf_filename(fp))\n"
            ),
            "lock_only": (
                "def f(pdfs_dir, fp):\n"
                "    with managed_pdf_path_lock(pdfs_dir, referenced_managed_pdf_filename(fp)):\n"
                "        pass\n"
            ),
        }
        for label, source in cases.items():
            with self.subTest(case=label):
                self.assertEqual(_codes(self._IMPORT + source), [])

if __name__ == "__main__":
    unittest.main()