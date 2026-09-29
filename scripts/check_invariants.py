#!/usr/bin/env python3
"""High-signal architectural invariant checks for production Python.

This is intentionally narrower than a general linter. It protects bug classes
where PRKS has a canonical capability boundary and where a direct low-level
call is almost certainly a regression.

Keep semantic invariants in tests; add checks here only when the forbidden
syntax has a clear approved replacement/boundary.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# First #69 Pyright slice: genuine basic type checking for backend/storage.
# Kept next to the AST invariants so Fast Static Analysis fails if the typed
# slice silently reverts to effectively-off mode.
PYRIGHT_DATAFLOW_CONFIG = "pyrightconfig.json"
PYRIGHT_DATAFLOW_REQUIRED_INCLUDE = "backend"
PYRIGHT_TYPED_SLICE_CONFIG = "pyrightconfig.typed-slice.json"
PYRIGHT_TYPED_SLICE_INCLUDE = ("backend/storage",)
PYRIGHT_TYPED_SLICE_ROOT = "backend/storage"
PYRIGHT_TYPED_SLICE_MODES = frozenset({"basic", "standard", "strict"})
# Only __pycache__ exclusions are permitted on the typed slice; anything else
# that covers backend/storage could silently suppress the checked scope.
PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES = frozenset(
    {
        "**/__pycache__",
        "**/__pycache__/**",
        "__pycache__",
        "__pycache__/",
    }
)
PYRIGHT_REQUIRED_DIAGNOSTICS = (
    "reportUndefinedVariable",
    "reportUnboundVariable",
    "reportUnusedExcept",
)
STATIC_ANALYSIS_WORKFLOW = ".github/workflows/static-analysis.yml"
_PYRIGHT_PROJECT_ARG_RE = re.compile(
    r"--project(?:\s+|=)(?P<q>[\"']?)(?P<path>[^\"'\s]+)(?P=q)"
)
_WORKFLOW_RUN_KEY_RE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")

# Calls that must not appear anywhere under backend/. New managed-file copies
# must go through the durable storage capability instead of ad-hoc copy calls.
BANNED_SHUTIL_COPY_CALLS = {"copy", "copy2", "copyfile"}

# Durable filesystem primitives are deliberately concentrated. Expanding these
# sets requires an explicit review of the durability/recovery contract.
#
# INV-DURABILITY-001 covers ``os.replace`` and ``pathlib.Path.replace``: they
# are the same atomic rename, so one invariant with one boundary. A
# ``.replace`` receiver is classified only when it is provably a pathlib Path
# (a Path constructor, a Path-returning Path method or ``/`` join, a name bound
# to one, or a parameter / variable annotated as one). ``str.replace`` and
# every other unrelated ``.replace`` method are never matched.
OS_REPLACE_ALLOWLIST = {
    "backend/backup_restore.py",
    # Disposable thumb / Person-image cache publication only — not canonical
    # library state. Keep ``backend/server.py`` non-exempt so new raw replaces
    # in the HTTP adapter fail INV-DURABILITY-001 by default.
    "backend/derived_cache_publish.py",
    "backend/fs_durability.py",
    "backend/pdf_linearize.py",
    "backend/services/work_pdf_replace.py",
}
# Bare os.fsync belongs only in fs_durability. Managed-PDF code must use
# fsync_open_file / fsync_directory — work_pdf_replace is not an fsync island.
OS_FSYNC_ALLOWLIST = {
    "backend/fs_durability.py",
}

# INV-BACKUP-001: ZipFile.extractall() trusts member names/types and is never
# acceptable on backup input. There is intentionally no production allowlist.
# A receiver is classified only when it provably originates from one of these
# zipfile classes (constructor call, a name or ``obj.attr`` bound to one, a
# parameter/variable/class attribute annotated with one, or an instance
# attribute such as ``self.archive`` assigned one in any method of the class
# or a same-module base class); unrelated ``.extractall()`` methods are not
# matched. Containers and values returned from helper functions are not tracked.
ZIPFILE_ARCHIVE_CLASSES = frozenset({"ZipFile", "PyZipFile"})
BANNED_ZIPFILE_METHOD = "extractall"

# pathlib classes whose instances expose the filesystem ``replace``/``unlink``.
# (PurePath has neither.)
PATHLIB_PATH_CLASSES = frozenset({"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"})
# Pure paths name the same file (``os.PathLike``) but cannot touch the
# filesystem themselves: they carry provenance without being a Path value.
_PURE_PATH_CLASSES = frozenset(
    {"pathlib.PurePath", "pathlib.PurePosixPath", "pathlib.PureWindowsPath"}
)
_PATH_CLASS_FACTORIES = frozenset({"cwd", "home"})
_PATH_RETURNING_METHODS = frozenset(
    {
        "absolute",
        "expanduser",
        "joinpath",
        "readlink",
        "relative_to",
        "resolve",
        "with_name",
        "with_stem",
        "with_suffix",
    }
)
_PATH_RETURNING_ATTRS = frozenset({"parent"})
# Path attributes naming (part of) the same file as a string.
_PATH_STRING_ATTRS = frozenset({"name", "stem"})
# Path methods that take the file away from its managed name.
_PATH_REMOVAL_METHODS = ("rename", "replace", "unlink")
_PATH_UNBOUND_UNLINK = frozenset(
    f"{cls}.{method}" for cls in PATHLIB_PATH_CLASSES for method in _PATH_REMOVAL_METHODS
)
# Path methods returning the same location as a string.
_PATH_STRING_METHODS = frozenset({"__fspath__", "__str__", "as_posix", "as_uri"})
# Path methods yielding Paths under the receiver (``for p in d.glob("*")``).
_PATH_ITERATOR_METHODS = frozenset({"glob", "iterdir", "rglob", "walk"})
# Stdlib calls whose string result is built from their arguments; a managed
# PDF / weak-alias provenance survives them (a Path value does not).
_PATH_STRING_FUNCS = frozenset(
    {
        "os.fsdecode",
        "os.fsencode",
        "os.fspath",
        "os.path.abspath",
        "os.path.basename",
        "os.path.join",
        "os.path.normcase",
        "os.path.normpath",
        "os.path.realpath",
        "os.path.relpath",
    }
)
# os functions yielding entries under their directory argument.
_DIR_ITERATOR_FUNCS = frozenset({"os.fwalk", "os.scandir", "os.walk"})
# DirEntry attributes naming the entry.
_DIR_ENTRY_ATTRS = frozenset({"name", "path"})
# Mapping lookups returning one of the mapping's values (or the default).
_MAPPING_LOOKUP_METHODS = frozenset({"get", "pop", "setdefault"})
_STR_TRANSFORM_METHODS = frozenset(
    {
        "capitalize",
        "casefold",
        "decode",
        "encode",
        "format",
        "join",
        "lower",
        "lstrip",
        "removeprefix",
        "removesuffix",
        "replace",
        "rstrip",
        "strip",
        "swapcase",
        "title",
        "upper",
    }
)

# --- Managed-PDF ownership boundary (INV-STORAGE-002/003/004) --------------
#
# Provenance is tracked per lexical binding with the same flow-sensitive,
# alias-aware machinery as INV-BACKUP-001. Helper names are matched on the
# name they were *imported/defined* as, so ``from m import helper as h`` and
# ``mod.helper`` both resolve; a parameter or local that shadows the name does
# not. Everything is intraprocedural: values returned from arbitrary helpers
# and container contents (other than literal lists/tuples/sets) are not
# tracked, and neither are paths passed across function boundaries.
#
# A value is a *managed-PDF filesystem path* when it is built from the managed
# PDF directory (a name or attribute spelled ``pdfs_dir`` -- the StorageConfig
# field every PRKS call site uses) or from the canonical containment helpers.
MANAGED_PDF_DIR_NAMES = frozenset({"pdfs_dir"})
MANAGED_PDF_PATH_HELPERS = frozenset(
    {"safe_pdf_path_under_dir", "_safe_pdf_path_in_pdfs_dir", "_safe_pdf_path_for_route"}
)
# Helpers that return a managed basename this request minted exclusively.
MANAGED_PDF_MINTING_HELPERS = frozenset(
    {
        "allocate_exclusive_managed_filename",
        "mint_managed_pdf_filename",
        "store_new_managed_pdf_bytes",
        "store_new_managed_pdf_from_path",
    }
)
# Fail-closed over-approximation; may only ever *block* a delete.
WEAK_MANAGED_PDF_ALIAS_HELPERS = frozenset({"referenced_managed_pdf_filename"})
MANAGED_PDF_ADOPTION_GUARD = "managed_pdf_adoption_guard"
# Where each ownership-granting helper is defined. Granting (guard entry,
# minted provenance) requires that canonical identity -- an import from that
# module, a module alias's attribute, or the definition itself -- never just a
# same-named method on an arbitrary object.
_CAPABILITY_HELPER_HOMES = {
    MANAGED_PDF_ADOPTION_GUARD: "backend/services/work_pdf_replace.py",
    "allocate_exclusive_managed_filename": "backend/services/work_pdf_replace.py",
    "store_new_managed_pdf_bytes": "backend/services/work_pdf_replace.py",
    "store_new_managed_pdf_from_path": "backend/services/work_pdf_replace.py",
    "mint_managed_pdf_filename": "backend/db_manager.py",
}

# INV-STORAGE-002: a raw removal of a managed-PDF path is survivor-aware
# cleanup authority. Only these (file, function) capabilities hold it; every
# other function -- including new functions in these same files -- fails.
# ``shutil.rmtree`` of the managed directory drops every PDF at once, live
# references included.
# ``os.rename`` / ``shutil.move`` of a managed source takes its bytes away
# from every live Work just as an unlink does.
# Primitives that also overwrite their destination (POSIX rename semantics).
# ``os.replace`` is excluded: replacing onto a canonical name is the reviewed
# durable publish, governed by INV-DURABILITY-001's allowlist.
_OVERWRITING_MOVE_CALLS = frozenset({"os.rename", "os.renames", "shutil.move"})
RAW_REMOVE_CALLS = frozenset(
    {
        "os.remove",
        "os.rename",
        "os.renames",
        "os.replace",
        "os.unlink",
        "shutil.move",
        "shutil.rmtree",
    }
)
MANAGED_PDF_REMOVE_CAPABILITIES: dict[tuple[str, str], str] = {
    # The canonical survivor-aware cleanup: under managed_pdf_path_lock it
    # settles the claim iff a live Work strongly references the name, keeps
    # a weak-alias-blocked claim pending, contains the path through
    # safe_pdf_path_under_dir, unlinks, then settles the claim.
    ("backend/work_deletion.py", "_remove_managed_pdf"): "survivor-aware cleanup",
    # The bounded retry pass performs the same locked sequence per claim.
    ("backend/work_deletion.py", "retry_pending_pdf_cleanup"): "survivor-aware retry",
    # Rollback of exclusive bytes the current request just minted (callers
    # restricted by RAW_MANAGED_PDF_UNLINK_HELPERS below).
    ("backend/services/work_pdf_replace.py", "unlink_managed_pdf_best_effort"): (
        "self-minted rollback"
    ),
}
# Raw managed-PDF unlink helpers without a survivor re-check: callable only
# where the name is bytes this request minted and nothing else can reference.
RAW_MANAGED_PDF_UNLINK_HELPERS: dict[str, frozenset[tuple[str, str]]] = {
    "unlink_managed_pdf_best_effort": frozenset(
        {
            # Partial exclusive create that never reported its name.
            ("backend/services/work_pdf_replace.py", "_exclusive_create_write_and_fsync"),
            # Holds managed_pdf_path_lock and re-checks live references first.
            ("backend/services/work_pdf_replace.py", "discard_unowned_managed_pdf"),
            # COW exclusive bytes whose retarget never committed.
            ("backend/services/work_pdf_replace.py", "replace_managed_work_pdf"),
        }
    ),
}

# INV-STORAGE-003: persisting ``works.file_path``. Sink -> (positional index
# after the receiver, keyword name, the value is a fields dict).
WORK_FILE_PATH_SINKS: dict[str, tuple[int, str, bool]] = {
    "add_work": (5, "file_path", False),
    "update_work_metadata": (1, "fields", True),
    "retarget_work_managed_file_path": (2, "file_path", False),
}
# ``guard(...) if <cond> else nullcontext()`` enters the guard only when
# ``<cond>`` holds, and the checker cannot relate ``<cond>`` to the value the
# body persists. So the conditional form protects nothing, except for these
# reviewed (file, function, exact condition) sites, where the no-op arm is
# taken only when nothing existing is adopted:
CONDITIONAL_ADOPTION_GUARDS: dict[tuple[str, str, str], str] = {
    # Create: ``stored_name`` is truthy only after this request minted it via
    # store_new_managed_pdf_bytes and set ``file_path = /api/pdfs/<stored_name>``;
    # a caller-supplied file_path leaves it None and takes the guard.
    ("backend/server.py", "PRKSHandler.handle_api_post", "not stored_name"): (
        "create: no-op arm only for a just-minted upload"
    ),
    # PATCH: ``file_path_changing`` is False only when the body has no
    # file_path or its unchanged echo was popped from the body.
    ("backend/server.py", "PRKSHandler.handle_api_patch", "file_path_changing"): (
        "PATCH: no-op arm only when file_path is absent or popped"
    ),
}
# Functions that own a file_path persistence primitive (raw SQL) or a
# documented non-adoption write. Their callers are what the rule checks.
WORK_FILE_PATH_CAPABILITIES: dict[tuple[str, str], str] = {
    # The persistence primitive behind every create; callers are checked.
    ("backend/db_manager.py", "PRKSDatabase.add_work"): "create primitive",
    # The COW retarget primitive; its one caller is the exemption below.
    ("backend/services/work_pdf_replace.py", "retarget_work_managed_file_path"): (
        "retarget primitive"
    ),
    # Copy-on-write retargets the Work to an exclusive name it just minted
    # (allocate_exclusive_managed_filename) while holding that name's lock.
    # The retarget runs behind a ``cow_retarget`` flag the flow analysis
    # cannot correlate with the minting branch, hence the function exemption.
    ("backend/services/work_pdf_replace.py", "replace_managed_work_pdf"): "COW retarget",
}
# Dict methods that may (re)write a guarded fields dict's file_path entry.
_DICT_MUTATORS = frozenset({"__ior__", "__setitem__", "setdefault", "update"})
_SQL_EXECUTE_METHODS = frozenset({"execute", "execute_query", "executemany", "executescript"})
_SQL_TEXT_KEYWORDS = frozenset({"query", "sql"})
_SQL_WORKS_INSERT_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?|REPLACE\s+)INTO\s+works\s*\(([^)]*)\)"
    r"\s*(?:VALUES\s*\(([^)]*)\))?",
    re.IGNORECASE,
)
_SQL_WORKS_COLUMNLESS_INSERT_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?|REPLACE\s+)INTO\s+works(?:\s+(?:AS\s+)?\w+)?"
    r"\s+(?:VALUES|SELECT|WITH)\b",
    re.IGNORECASE,
)
_SQL_WORKS_UPDATE_RE = re.compile(
    r"\bUPDATE\s+(?:OR\s+\w+\s+)?works(?:\s+(?:AS\s+)?\w+)?"
    r"(?:\s+(?:INDEXED\s+BY\s+\w+|NOT\s+INDEXED))?\s+SET\b([^;]*)",
    re.IGNORECASE,
)
_SQL_UPSERT_SET_RE = re.compile(r"\bDO\s+UPDATE\s+SET\b([^;]*)", re.IGNORECASE)
# Clauses that end a SET list -- only outside parentheses, so a scalar
# subquery's own ``WHERE`` (``SET t = (SELECT ... WHERE ...), file_path = ?``)
# does not truncate it.
_SQL_SET_END_RE = re.compile(r"[()]|\b(?:WHERE|FROM|RETURNING|ORDER|LIMIT)\b", re.IGNORECASE)
_SQL_ROW_VALUE_SET_RE = re.compile(r"\(([^()]*)\)\s*=\s*\(([^()]*)")
_SQL_FILE_PATH_ASSIGN_RE = re.compile(r"\bfile_path\s*=\s*([^,\s]+)", re.IGNORECASE)
_SQL_PENDING_CLEANUP_WRITE_RE = re.compile(
    r"\b(?:(?:INSERT\s+(?:OR\s+\w+\s+)?|REPLACE\s+)INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)"
    r"\s+pending_pdf_cleanup\b",
    re.IGNORECASE,
)
_SQL_CLEARING_VALUES = frozenset({"NULL", "''"})
# A string literal (kept) or a /* block */ / -- line comment (dropped).
_SQL_COMMENT_RE = re.compile(r"('(?:[^']|'')*')|/\*.*?\*/|--[^\n]*", re.DOTALL)
_SQL_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_SQL_DOUBLE_QUOTED_RE = re.compile(r'"((?:[^"]|"")*)"')
_SQL_KEYWORDS = frozenset(
    {
        # Every keyword a clause regex anchors on, including all SET-list
        # terminators, so a double-quoted spelling of one is masked.
        "AND", "AS", "DO", "FROM", "INDEXED", "INSERT", "INTO", "LIMIT", "NOT",
        "ON", "OR", "ORDER", "REPLACE", "RETURNING", "SELECT", "SET", "UPDATE",
        "VALUES", "WHERE", "WITH",
    }
)
_SQL_IDENTIFIER_QUOTES_RE = re.compile(r'["`\[\]]')
_SQL_SCHEMA_PREFIX_RE = re.compile(r"\b(?:main|temp)\.(?=\w)", re.IGNORECASE)

# INV-STORAGE-004: operations that treat a basename as ownership, cleanup
# claim, or deletion authority. A weak-alias value must never reach them.
WEAK_ALIAS_AUTHORITY_SINKS = frozenset(
    {
        "cleanup_released_managed_pdfs",
        "discard_unowned_managed_pdf",
        "forget_pending_pdf_cleanup",
        "managed_pdf_adoption_guard",
        "record_pending_pdf_cleanup_on_conn",
        "settle_claim_if_referenced",
        "unlink_managed_pdf_best_effort",
        "_remove_managed_pdf",
    }
)
# Helper names whose identity survives ``alias = obj.helper`` bound methods.
_TRACKED_HELPER_NAMES = (
    frozenset(WORK_FILE_PATH_SINKS)
    | frozenset(RAW_MANAGED_PDF_UNLINK_HELPERS)
    | WEAK_ALIAS_AUTHORITY_SINKS
    | MANAGED_PDF_PATH_HELPERS
    | MANAGED_PDF_MINTING_HELPERS
    | WEAK_MANAGED_PDF_ALIAS_HELPERS
    | _SQL_EXECUTE_METHODS
)

# --- HTTP adapter boundary (INV-ADAPTER-001/002) ---------------------------
#
# ``backend/server.py`` parses and validates requests, dispatches, maps
# responses, and serves static files. It must not become storage or process
# authority itself: claiming a filename exclusively, writing managed-PDF
# bytes, or spawning qpdf (or any other process) belongs behind the focused
# helpers it already calls. Only the adapter module is scoped; the same
# primitives stay legal inside those focused modules. Detection reuses the
# import/alias/partial resolution and managed-PDF provenance above; the
# durability primitives (shutil.copy*, os.replace, Path.replace, os.fsync)
# and raw managed-PDF removal are already INV-STORAGE-001/002 and
# INV-DURABILITY-001/002, which have no server.py exemption.
HTTP_ADAPTER_MODULE = "backend/server.py"
# Builtins whose identity survives a local alias (``o = open``).
_ALIASED_BUILTINS = frozenset({"open"})
_OPEN_CALLS = frozenset({"builtins.open", "io.open"})
# pathlib methods that create or write the receiver's file. ``open`` /
# ``touch`` are judged at the call (their mode / ``exist_ok`` decides);
# ``write_bytes`` / ``write_text`` at the method reference, so a saved bound
# method is covered too.
_PATH_OPEN_METHODS = frozenset({"open", "touch"})
_PATH_WRITE_METHODS = frozenset({"write_bytes", "write_text"})
_PATH_UNBOUND_CREATE = frozenset(
    f"{cls}.{method}"
    for cls in PATHLIB_PATH_CLASSES
    for method in (*_PATH_OPEN_METHODS, *_PATH_WRITE_METHODS)
)
_OS_OPEN_WRITE_FLAGS = frozenset(
    {"os.O_APPEND", "os.O_CREAT", "os.O_RDWR", "os.O_TRUNC", "os.O_WRONLY"}
)
# tempfile creators -> positional index of ``dir``: staging next to managed
# PDFs is the same exclusive create a helper already owns.
_TEMPFILE_DIR_ARG = {
    "tempfile.NamedTemporaryFile": 6,
    "tempfile.mkdtemp": 2,
    "tempfile.mkstemp": 2,
}
# Process creation. ``subprocess`` is banned as a module (import or any call).
_PROCESS_MODULES = frozenset({"asyncio.subprocess", "subprocess"})
_PROCESS_CALL_PREFIXES = ("os.exec", "os.posix_spawn", "os.spawn", "subprocess.")
_PROCESS_CALLS = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.fork",
        "os.forkpty",
        "os.popen",
        "os.system",
        "pty.spawn",
    }
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.code} {self.path}:{self.line}: {self.message}"


# A lexical binding, as far as these invariants care:
#   ("module", "os")                  import os / import zipfile as z
#   ("name", "zipfile", "ZipFile")    from zipfile import ZipFile as Z
#   ("archive",)                      a value known to be a zipfile archive
#   ("class", info) / ("instance", info)   a same-module class / its self or cls
#   ("other",)                        any other local binding (shadows imports)
#   ("path",)                         a value known to be a pathlib Path
#   ("managed_pdf",)                  may be a managed-PDF filesystem path
#   ("weak_alias",)                   may derive from referenced_managed_pdf_filename
#   ("minted", kind)                  a managed basename this request minted, or
#                                     its exact ``/api/pdfs/<name>`` path
#   ("guarded", id, kind)             an entered adoption guard's exact input path
#                                     or yielded basename (kind "path" / "name")
#   ("sql_write", target)             SQL text writing works.file_path / pending_pdf_cleanup
# ``obj.attr`` targets are bound under the dotted key ``"obj.attr"``. Every
# absolute import is recorded (``("module", ...)`` / ``("name", ...)``) so
# helper and stdlib identities resolve through ``import m as a`` /
# ``from m import f as g`` / ``g2 = g`` exactly like os/shutil/zipfile.
_Binding = tuple[Any, ...]
_ARCHIVE: _Binding = ("archive",)
_OTHER: _Binding = ("other",)
_PATH: _Binding = ("path",)
_MANAGED: _Binding = ("managed_pdf",)
_WEAK: _Binding = ("weak_alias",)
_MINTED: _Binding = ("minted", "name")
# A guarded fields dict mutated (possibly) on this control-flow path.
_DICT_DIRTY: _Binding = ("dict_dirty",)
# A saved bound ``Path.rename`` method (``move = p.rename``).
_BOUND_PATH_RENAME: _Binding = ("bound_path_rename",)
_FACT_TAGS = frozenset({"path", "managed_pdf", "weak_alias", "minted", "sql_write"})
# Tags a works.file_path value may be built from without claiming existing bytes.
_OWNED_TAGS = frozenset({"minted", "guarded"})
_MANAGED_ROUTE_PREFIX = "/api/pdfs/"
# An ``obj.attr`` key that some joined path never bound locally: the class-level
# archive-attribute record still applies on that path.
_UNSET: _Binding = ("unset",)


class _FuncInfo:
    """One same-module function or method: its name, and does it return a zipfile archive?"""

    __slots__ = ("name", "returns_archive")

    def __init__(self) -> None:
        self.name = ""
        self.returns_archive = False


class _ClassInfo:
    """One same-module class: is it a zipfile archive subclass, which of its
    attributes are proven zipfile archives, and what are its methods."""

    __slots__ = ("archive_attrs", "bases", "zip_base", "methods")

    def __init__(self) -> None:
        self.archive_attrs: set[str] = set()
        self.bases: list[_ClassInfo] = []
        self.methods: dict[str, _FuncInfo] = {}
        # A base expression resolves to zipfile.ZipFile / PyZipFile directly.
        self.zip_base = False

    def _lineage(self) -> Iterable[_ClassInfo]:
        seen: set[int] = set()
        stack: list[_ClassInfo] = [self]
        while stack:
            info = stack.pop()
            if id(info) in seen:
                continue
            seen.add(id(info))
            yield info
            stack.extend(info.bases)

    def has_archive_attr(self, attr: str) -> bool:
        return any(attr in info.archive_attrs for info in self._lineage())

    def method_returns_archive(self, name: str) -> bool:
        for info in self._lineage():
            if name in info.methods:
                return info.methods[name].returns_archive
        return False

    def is_archive_class(self) -> bool:
        return any(info.zip_base for info in self._lineage())


class _AdoptionGuard:
    """One entered ``managed_pdf_adoption_guard``: what it locked and re-checked.

    Only values derived from the guard's own input (the path it validated) or
    its yielded basename are protected by it. A fields dict whose
    ``file_path`` entry was that input (``body.get("file_path")``) carries a
    ``("guarded_dict", key)`` tag in the flow state, so invalidating it is
    path-local: a mutation on a branch that returns does not reach the join.
    """

    __slots__ = ("key",)

    def __init__(self) -> None:
        self.key = id(self)

    def tag(self, kind: str) -> _Binding:
        """``kind`` is ``"path"`` (the exact input) or ``"name"`` (the yield)."""
        return ("guarded", self.key, kind)


class _Scope:
    """One lexical scope (module, class body, function, lambda, comprehension).

    ``summary`` holds every binding made anywhere in the scope body, collected
    before the body is walked. Nested scopes resolve enclosing names through it
    because a function body runs after its enclosing scope has bound them, so
    imports placed after a ``def`` still resolve. ``current`` holds the
    bindings that may reach the statement being walked: a rebinding replaces a
    name's bindings on its own control-flow path, and branch states are
    unioned where if/loop/try/match paths join; loop bodies also see bindings
    from later iterations. A name the scope binds but has not reached yet
    falls back to ``summary``. Every set is a may-alias set: any archive entry
    classifies. ``global``/``nonlocal`` names are bound in their owner scope.
    """

    __slots__ = (
        "class_info",
        "is_comprehension",
        "summary",
        "current",
        "declared",
        "rebound_elsewhere",
        "class_registry",
        "function_registry",
        "function_info",
        "adoption_guards",
        "is_lazy",
        "relpath",
    )

    def __init__(
        self, *, class_info: _ClassInfo | None = None, is_comprehension: bool = False
    ) -> None:
        # Set for class bodies; ``None`` for every other scope kind.
        self.class_info = class_info
        self.is_comprehension = is_comprehension
        self.summary: dict[str, set[_Binding]] = {}
        self.current: dict[str, set[_Binding]] = {}
        # ``global``/``nonlocal`` name -> the scope that owns its bindings.
        self.declared: dict[str, _Scope] = {}
        # Names a nested scope rebinds via ``global``/``nonlocal``; the value
        # may change whenever that scope runs, so uses also see ``summary``.
        self.rebound_elsewhere: set[str] = set()
        # Only used on the module scope: def/class node id -> its info. They
        # are shared across the analysis passes of one module.
        self.class_registry: dict[int, _ClassInfo] = {}
        self.function_registry: dict[int, _FuncInfo] = {}
        # Set for function bodies: the function whose ``return``s are seen.
        self.function_info: _FuncInfo | None = None
        # Enclosing ``with managed_pdf_adoption_guard(...)`` blocks.
        self.adoption_guards: list[_AdoptionGuard] = []
        # A generator expression body runs lazily, after its creator returns.
        self.is_lazy = False
        # Only set on the module scope: the repo-relative path being checked.
        self.relpath = ""

    @property
    def is_class(self) -> bool:
        return self.class_info is not None


def _resolve(scopes: list[_Scope], name: str) -> set[_Binding]:
    innermost = scopes[-1]
    if name in innermost.current:
        if name in innermost.rebound_elsewhere:
            return innermost.current[name] | innermost.summary.get(name, set())
        return innermost.current[name]
    if name in innermost.summary:
        return innermost.summary[name]
    # A list/set/dict comprehension runs immediately, so the enclosing
    # scope's flow state at that point applies (a generator runs later).
    enclosing = scopes[:-1]
    if (
        innermost.is_comprehension
        and not innermost.is_lazy
        and enclosing
        and not enclosing[-1].is_class
    ):
        return _resolve(enclosing, name)
    # Class bodies are not enclosing scopes for the functions nested in them.
    for scope in reversed(scopes[:-1]):
        if not scope.is_class and name in scope.summary:
            return scope.summary[name]
    return set()


def _lookup_modules(scopes: list[_Scope], name: str) -> set[str]:
    return {b[1] for b in _resolve(scopes, name) if b[0] == "module"}


def _lookup_names(scopes: list[_Scope], name: str) -> set[tuple[str, str]]:
    return {(b[1], b[2]) for b in _resolve(scopes, name) if b[0] == "name"}


def _unwrap_walrus(node: ast.expr) -> ast.expr:
    """``(name := value)`` evaluates to ``value``."""
    while isinstance(node, ast.NamedExpr):
        node = node.value
    return node


def _call_identities(node: ast.Call, scopes: list[_Scope]) -> list[tuple[str, str]]:
    fn = _unwrap_walrus(node.func)
    constant = _constant_getattr(fn, scopes)
    if constant is not None:
        # ``getattr(shutil, "copy2")(...)``.
        pairs: set[tuple[str, str]] = set()
        for name in _qualified_names(fn, scopes):
            owner, _, attr = name.rpartition(".")
            if owner:
                pairs.add((owner, attr))
        return sorted(pairs)
    if isinstance(fn, ast.Attribute):
        # ``mod.fn(...)``, and a callable stored on a tracked one-level
        # attribute (``self.cp = shutil.copy2; self.cp(...)``).
        key = _attr_key(fn)
        stored = _lookup_names(scopes, key) if key is not None else set()
        # The receiver may be a module name or a module stored on a tracked
        # one-level attribute (``self.s = shutil; self.s.copy2(...)``).
        receiver = fn.value.id if isinstance(fn.value, ast.Name) else _attr_key(fn.value)
        via_module = (
            {(module, fn.attr) for module in _lookup_modules(scopes, receiver)}
            if receiver is not None
            else set()
        )
        return sorted(stored | via_module)
    if isinstance(fn, ast.Name):
        return sorted(_lookup_names(scopes, fn.id))
    return []


def _is_zip_class_expr(node: ast.expr, scopes: list[_Scope]) -> bool:
    """``zipfile.ZipFile`` / ``z.ZipFile`` / ``ZipFile`` / ``Z`` (import alias) / a same-module subclass."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (
            node.attr in ZIPFILE_ARCHIVE_CLASSES
            and "zipfile" in _lookup_modules(scopes, node.value.id)
        )
    if isinstance(node, ast.Name):
        return any(_is_zip_class_binding(b) for b in _resolve(scopes, node.id))
    return False


def _is_zip_class_binding(binding: _Binding) -> bool:
    """An imported zipfile archive class, or a same-module subclass of one."""
    if binding[0] == "name":
        return binding[1] == "zipfile" and binding[2] in ZIPFILE_ARCHIVE_CLASSES
    return binding[0] == "class" and binding[1].is_archive_class()


def _is_zip_constructor(node: ast.expr, scopes: list[_Scope]) -> bool:
    return isinstance(node, ast.Call) and _is_zip_class_expr(node.func, scopes)


def _annotation_mentions_zip(node: ast.expr | None, scopes: list[_Scope]) -> bool:
    """True for ``ZipFile``, ``ZipFile | None``, ``Optional[ZipFile]``, or a string form."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    return any(
        isinstance(sub, ast.expr) and _is_zip_class_expr(sub, scopes)
        for sub in ast.walk(node)
    )


def _attr_key(node: ast.expr) -> str | None:
    """``"obj.attr"`` for a one-level attribute on a bare name."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _class_infos(bindings: set[_Binding]) -> list[_ClassInfo]:
    return [b[1] for b in bindings if b[0] in {"class", "instance"}]


def _is_archive_attribute(node: ast.Attribute, scopes: list[_Scope]) -> bool:
    key = _attr_key(node)
    if key is None or not isinstance(node.value, ast.Name):
        return False
    innermost = scopes[-1]
    if key in innermost.current:
        # A binding on every path reaching here is authoritative over the
        # class record; a path that never rebound the key still defers to it.
        local = innermost.current[key]
        if _ARCHIVE in local:
            return True
        if _UNSET not in local:
            return False
    if _ARCHIVE in _resolve(scopes, key):
        return True
    return any(
        info.has_archive_attr(node.attr)
        for info in _class_infos(_resolve(scopes, node.value.id))
    )


def _branch_values(node: ast.expr) -> list[ast.expr] | None:
    """Values a conditional / boolean / assignment expression may evaluate to."""
    if isinstance(node, (ast.NamedExpr, ast.Await)):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return [node.body, node.orelse]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    return None


def _call_returns_archive(node: ast.Call, scopes: list[_Scope]) -> bool:
    """A call to a same-module function or method proven to return an archive."""
    fn = node.func
    if isinstance(fn, ast.Name):
        return any(
            b[0] == "function" and b[1].returns_archive for b in _resolve(scopes, fn.id)
        )
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return any(
            info.method_returns_archive(fn.attr)
            for info in _class_infos(_resolve(scopes, fn.value.id))
        )
    if isinstance(fn, ast.Attribute) and _is_super_call(fn.value):
        return _super_method_returns_archive(fn.attr, scopes)
    return False


def _super_method_returns_archive(method: str, scopes: list[_Scope]) -> bool:
    """``super().method()`` resolves through the enclosing class's bases."""
    class_info = _enclosing_class(scopes)
    return class_info is not None and any(
        base.method_returns_archive(method) for base in class_info.bases
    )


def _is_super_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
    )


def _enclosing_class(scopes: list[_Scope]) -> _ClassInfo | None:
    return next((scope.class_info for scope in reversed(scopes) if scope.class_info), None)


def _is_zip_archive_expr(node: ast.expr, scopes: list[_Scope]) -> bool:
    branches = _branch_values(node)
    if branches is not None:
        return any(_is_zip_archive_expr(branch, scopes) for branch in branches)
    if isinstance(node, ast.Name):
        bindings = _resolve(scopes, node.id)
        return _ARCHIVE in bindings or any(
            b[0] == "instance" and b[1].is_archive_class() for b in bindings
        )
    if isinstance(node, ast.Attribute):
        return _is_archive_attribute(node, scopes)
    if isinstance(node, ast.Call):
        return _is_zip_constructor(node, scopes) or _call_returns_archive(node, scopes)
    return False


def _value_bindings(value: ast.expr, scopes: list[_Scope]) -> set[_Binding]:
    """What a name bound to ``value`` refers to (aliases carry through)."""
    branches = _branch_values(value)
    if branches is not None:
        return set().union(*(_value_bindings(branch, scopes) for branch in branches))
    if isinstance(value, ast.Name):
        resolved = set(_resolve(scopes, value.id))
        if not resolved and value.id in _ALIASED_BUILTINS:
            # ``o = open`` keeps the builtin's identity.
            resolved = {("name", "builtins", value.id)}
        return (resolved or {_OTHER}) | _expr_facts(value, scopes)
    if _is_zip_archive_expr(value, scopes):
        return {_ARCHIVE}
    if _is_partial_call(value, scopes) and value.args:
        return _partial_bindings(value, scopes)
    value = _getattr_as_attribute(value, scopes) or value
    if isinstance(value, ast.Attribute):
        aliased = _attribute_alias_bindings(value, scopes)
        if aliased is not None:
            return aliased
    owned = _proven_owned(value, scopes)
    if owned is not None:
        return owned
    return {_OTHER} | _expr_facts(value, scopes)


def _partial_bindings(value: ast.Call, scopes: list[_Scope]) -> set[_Binding]:
    """``save = partial(db.add_work, *bound)`` keeps the wrapped helper
    identity and how many positionals it pre-binds; nested partials
    (``partial(partial(f, a), b)``) accumulate their offsets."""
    inner = _value_bindings(value.args[0], scopes)
    # A branch-joined inner partial may pre-bind any of several counts;
    # each one shifts by this layer's positionals.
    prior = {b[1] for b in inner if b[0] == "partial"} or {0}
    added = len(value.args) - 1
    return {b for b in inner if b[0] != "partial"} | {("partial", n + added) for n in prior}


def _attribute_alias_bindings(
    value: ast.Attribute, scopes: list[_Scope]
) -> set[_Binding] | None:
    """``mod.attr`` / ``pkg.mod.attr`` keep their import identity, and a bound
    method of a tracked helper keeps its name (``save = db.add_work``);
    ``upd = Cls.method`` stays unbound (receiver passed explicitly)."""
    if value.attr == "rename" and _PATH in _expr_facts(value.value, scopes):
        # ``move = p.rename``: invoking it overwrites its destination argument.
        return {_BOUND_PATH_RENAME} | _expr_facts(value, scopes)
    key = _attr_key(value)
    stored = set(_resolve(scopes, key)) if key is not None else set()
    if stored:
        # ``x = self.m`` / ``x = self.move`` copies whatever the tracked
        # one-level attribute holds (a module, a callable, a saved rename).
        return stored | _expr_facts(value, scopes)
    qualified = _qualified_names(value, scopes)
    if qualified:
        return {
            ("name", *name.rsplit(".", 1)) if "." in name else ("module", name)
            for name in qualified
        } | _expr_facts(value, scopes)
    if value.attr in _TRACKED_HELPER_NAMES:
        owner = "<unbound>" if _is_class_receiver(value.value, scopes) else "<bound>"
        return {("name", owner, value.attr)} | _expr_facts(value, scopes)
    return None


# --- qualified names and value provenance (Path / managed PDF / weak alias) ---


def _dict_file_path_value(node: ast.Dict, key: str = "file_path") -> ast.expr | None:
    """The value a dict literal gives ``key``; the dict itself when a
    ``**spread`` may supply or override it; ``None`` when it cannot."""
    if None in node.keys:
        return node
    return next(
        (
            v
            for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and k.value == key
        ),
        None,
    )


def _qualified_names(node: ast.expr, scopes: list[_Scope]) -> set[str]:
    """Import-resolved dotted names ``node`` may denote (``os.path.join``,
    ``pathlib.Path``, ``backend.db_manager.helper``); same-module defs resolve
    to their bare name. Empty for locals, parameters and unknown names."""
    if isinstance(node, ast.Name):
        return _names_of_bindings(_resolve(scopes, node.id))
    if isinstance(node, ast.NamedExpr):
        return _qualified_names(node.value, scopes)
    constant = _constant_getattr(node, scopes)
    if constant is not None:
        # ``getattr(os, "remove")`` names ``os.remove``.
        obj, attr = constant
        return {f"{base}.{attr}" for base in _qualified_names(obj, scopes)}
    if isinstance(node, ast.Attribute):
        # A callable stored on a tracked one-level attribute
        # (``self.rm = os.remove``) keeps its identity.
        key = _attr_key(node)
        stored = _names_of_bindings(_resolve(scopes, key)) if key is not None else set()
        return stored | {f"{base}.{node.attr}" for base in _qualified_names(node.value, scopes)}
    return set()


def _constant_getattr(node: ast.expr, scopes: list[_Scope]) -> tuple[ast.expr, str] | None:
    """``(obj, "attr")`` for the builtin ``getattr(obj, "attr"[, default])``."""
    if (
        isinstance(node, ast.Call)
        and _is_builtin(node.func, scopes, "getattr")
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[0], node.args[1].value
    return None


def _getattr_as_attribute(node: ast.expr, scopes: list[_Scope]) -> ast.Attribute | None:
    """``getattr(obj, "attr")`` as the equivalent ``obj.attr`` node."""
    constant = _constant_getattr(node, scopes)
    if constant is None:
        return None
    return ast.copy_location(ast.Attribute(value=constant[0], attr=constant[1], ctx=ast.Load()), node)


def _names_of_bindings(bindings: Iterable[_Binding]) -> set[str]:
    names: set[str] = set()
    for binding in bindings:
        if binding[0] == "module":
            names.add(binding[1])
        elif binding[0] == "name":
            # ``from . import m`` binds module ``"."``: no extra separator.
            sep = "" if binding[1].endswith(".") else "."
            names.add(f"{binding[1]}{sep}{binding[2]}")
        elif binding[0] == "function" and binding[1].name:
            names.add(binding[1].name)
    return names


def _is_partial_call(node: ast.expr, scopes: list[_Scope]) -> bool:
    return isinstance(node, ast.Call) and "functools.partial" in _qualified_names(
        node.func, scopes
    )


def _is_class_receiver(node: ast.expr, scopes: list[_Scope]) -> bool:
    """``Cls`` in ``Cls.method``: a same-module class or an imported class-like
    (CapWords) name, so the method is called unbound."""
    if isinstance(node, ast.Name) and any(
        b[0] == "class" for b in _resolve(scopes, node.id)
    ):
        return True
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return False
    return any(name.rsplit(".", 1)[-1][:1].isupper() for name in _qualified_names(node, scopes))


def _is_unbound_method_call(func: ast.expr, scopes: list[_Scope]) -> bool:
    """``Cls.method(receiver, ...)`` or an alias of such an unbound method,
    including a walrus callee and one saved on a tracked one-level attribute
    (``self.save = partial(DB.add_work, db)``)."""
    func = _unwrap_walrus(func)
    if isinstance(func, ast.Call) and _is_partial_call(func, scopes) and func.args:
        return _is_unbound_method_call(func.args[0], scopes)
    if isinstance(func, ast.Attribute):
        if _is_class_receiver(func.value, scopes):
            return True
        key = _attr_key(func)
        bindings = _resolve(scopes, key) if key is not None else set()
    elif isinstance(func, ast.Name):
        bindings = _resolve(scopes, func.id)
    else:
        return False
    for binding in bindings:
        if binding[0] != "name":
            continue
        owner = str(binding[1])
        if owner == "<unbound>" or owner.rsplit(".", 1)[-1][:1].isupper():
            return True
    return False


def _callee_leaf_names(func: ast.expr, scopes: list[_Scope]) -> set[str]:
    """The name(s) a PRKS helper was defined/imported as.

    ``obj.helper`` -> ``helper``. A bare name resolves through its imports and
    aliases; a name bound only to a parameter/local matches nothing, and an
    unbound (global from elsewhere) name matches its own spelling.
    """
    if isinstance(func, ast.NamedExpr):
        # ``(sink := db.add_work)(...)`` calls the assigned value.
        return _callee_leaf_names(func.value, scopes)
    if isinstance(func, ast.Call) and _is_partial_call(func, scopes) and func.args:
        # ``partial(db.add_work, "t")(...)`` calls the wrapped helper.
        return _callee_leaf_names(func.args[0], scopes)
    constant = _constant_getattr(func, scopes)
    if constant is not None:
        # ``getattr(db, "add_work")(...)`` -> ``add_work``.
        return {constant[1]} | {name.rsplit(".", 1)[-1] for name in _qualified_names(func, scopes)}
    if isinstance(func, ast.Attribute):
        # ``self.save = db.add_work; self.save(...)`` -> ``add_work`` too.
        return {func.attr} | {
            name.rsplit(".", 1)[-1] for name in _qualified_names(func, scopes)
        }
    if isinstance(func, ast.Name):
        bindings = _resolve(scopes, func.id)
        if not bindings:
            return {func.id}
        return {name.rsplit(".", 1)[-1] for name in _qualified_names(func, scopes)}
    return set()


def _absolute_module(module: str, relpath: str) -> str:
    """``..services.work_pdf_replace`` from ``backend/x/y.py`` ->
    ``backend.services.work_pdf_replace``; absolute names are unchanged."""
    level = len(module) - len(module.lstrip("."))
    if not level:
        return module
    package = relpath.split("/")[:-1]
    if level - 1 > len(package):
        return module
    base = package[: len(package) - (level - 1)]
    rest = module[level:]
    return ".".join([*base, rest] if rest else base)


def _canonical_capability_names(func: ast.expr, scopes: list[_Scope]) -> set[str]:
    """Ownership-granting helpers ``func`` is *canonically* bound to.

    Unlike ``_callee_leaf_names`` (fine for sinks, which over-report), an
    attribute on an unknown receiver (``fake.managed_pdf_adoption_guard``) or
    a same-named function defined in another module grants nothing.
    """
    found: set[str] = set()
    for qualified in _qualified_names(func, scopes):
        module, _, leaf = qualified.rpartition(".")
        home = _CAPABILITY_HELPER_HOMES.get(leaf)
        if home is None:
            continue
        if module:
            # The full module: ``backend.services.work_pdf_replace``, or a
            # relative import resolved against this file's package.
            if _absolute_module(module, scopes[0].relpath) == home.removesuffix(".py").replace(
                "/", "."
            ):
                found.add(leaf)
        elif scopes[0].relpath == home:
            # Defined (or used unqualified) in its own home module.
            found.add(leaf)
    return found


def _is_builtin(func: ast.expr, scopes: list[_Scope], name: str) -> bool:
    return isinstance(func, ast.Name) and func.id == name and not _resolve(scopes, func.id)


def _process_primitives(names: Iterable[str]) -> list[str]:
    """The process-creating callables (INV-ADAPTER-002) among ``names``."""
    return sorted(
        name
        for name in names
        if name in _PROCESS_CALLS or name.startswith(_PROCESS_CALL_PREFIXES)
    )


def _open_mode_effect(mode: ast.expr | None) -> tuple[bool, bool]:
    """``(may create exclusively, may write)`` for an ``open`` mode argument.
    A missing mode reads; a mode that is not a string literal is opaque, so
    both hold (fail closed)."""
    if mode is None:
        return False, False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "x" in mode.value, bool(set(mode.value) & set("wax+"))
    return True, True


def _os_open_flags_effect(flags: ast.expr | None, scopes: list[_Scope]) -> tuple[bool, bool]:
    """``(may create exclusively, may write)`` for ``os.open`` flags. Only a
    ``|`` of import-resolved ``os.O_*`` constants is interpreted; anything
    else (a variable, a number, a call) is opaque, so both hold."""
    if flags is None:
        # Pre-bound by a partial (checked where it was built).
        return False, False
    terms: list[set[str]] = []
    stack = [flags]
    while stack:
        term = stack.pop()
        if isinstance(term, ast.BinOp) and isinstance(term.op, ast.BitOr):
            stack.extend((term.left, term.right))
            continue
        names = _qualified_names(term, scopes)
        if not names or not all(name.startswith("os.O_") for name in names):
            return True, True
        terms.append(names)
    used = set().union(*terms)
    return "os.O_EXCL" in used, bool(used & (_OS_OPEN_WRITE_FLAGS | {"os.O_EXCL"}))


def _is_path_class_expr(node: ast.expr, scopes: list[_Scope]) -> bool:
    return bool(_qualified_names(node, scopes) & PATHLIB_PATH_CLASSES)


def _annotation_mentions_path(node: ast.expr | None, scopes: list[_Scope]) -> bool:
    """True for ``Path``, ``Path | None``, ``Optional[pathlib.Path]``, or a string form."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    return any(
        isinstance(sub, ast.expr) and _is_path_class_expr(sub, scopes) for sub in ast.walk(node)
    )


def _sql_write_targets(text: str) -> set[str]:
    """Which ownership tables a SQL literal writes: ``works.file_path`` (to a
    bound value -- ``NULL``/``''`` clears are not adoption) and
    ``pending_pdf_cleanup``."""
    # SQLite identifier quoting ("x", `x`, [x]) and a schema prefix name the
    # same table/column; '' stays (it is a string literal, not an identifier).
    text = _SQL_COMMENT_RE.sub(lambda m: m.group(1) or " ", text)
    # Mask literal contents so keywords inside strings (``'WHERE'``) cannot
    # end a clause early; an empty literal stays ``''`` (a clearing value).
    text = _SQL_STRING_LITERAL_RE.sub(lambda m: "''" if m.group(0) == "''" else "'s'", text)
    # A double-quoted plain name is an identifier; anything else (including a
    # quoted keyword such as "WHERE") may be SQLite's legacy string literal.
    text = _SQL_DOUBLE_QUOTED_RE.sub(_double_quoted_token, text)
    text = _SQL_SCHEMA_PREFIX_RE.sub("", _SQL_IDENTIFIER_QUOTES_RE.sub("", text))
    targets: set[str] = set()
    if _SQL_PENDING_CLEANUP_WRITE_RE.search(text):
        targets.add("pending_pdf_cleanup")
    for match in _SQL_WORKS_INSERT_RE.finditer(text):
        if _insert_writes_file_path(match) or _upsert_writes_file_path(text[match.end() :]):
            targets.add("works.file_path")
    if _SQL_WORKS_COLUMNLESS_INSERT_RE.search(text):
        # ``INSERT INTO works VALUES (...)`` / ``SELECT``: every column, in
        # schema order, including file_path.
        targets.add("works.file_path")
    for match in _SQL_WORKS_UPDATE_RE.finditer(text):
        if _set_clause_writes_file_path(match.group(1)):
            targets.add("works.file_path")
    return targets


def _double_quoted_token(match: re.Match[str]) -> str:
    content = match.group(1)
    if re.fullmatch(r"\w+", content) and content.upper() not in _SQL_KEYWORDS:
        return content
    return "'s'"


def _insert_writes_file_path(match: re.Match[str]) -> bool:
    columns = [c.strip().lower() for c in match.group(1).split(",")]
    if "file_path" not in columns:
        return False
    values = [v.strip() for v in (match.group(2) or "").split(",")]
    return not (
        len(values) == len(columns)
        and values[columns.index("file_path")].upper() in _SQL_CLEARING_VALUES
    )


def _upsert_writes_file_path(tail: str) -> bool:
    """``ON CONFLICT ... DO UPDATE SET file_path = ...`` after an insert."""
    upsert = _SQL_UPSERT_SET_RE.search(tail.split(";", 1)[0])
    return upsert is not None and _set_clause_writes_file_path(upsert.group(1))


def _top_level_set_list(tail: str) -> str:
    """The SET assignments: ``tail`` up to the first clause keyword at depth 0."""
    depth = 0
    for token in _SQL_SET_END_RE.finditer(tail):
        text = token.group(0)
        if text == "(":
            depth += 1
        elif text == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            return tail[: token.start()]
    return tail


def _set_clause_writes_file_path(tail: str) -> bool:
    clause = _top_level_set_list(tail)
    if any(
        assigned.group(1).upper() not in _SQL_CLEARING_VALUES
        for assigned in _SQL_FILE_PATH_ASSIGN_RE.finditer(clause)
    ):
        return True
    # Row-value form: ``SET (file_path, status) = (?, ?)`` or ``= (SELECT ...)``.
    for match in _SQL_ROW_VALUE_SET_RE.finditer(clause):
        columns = [c.strip().lower() for c in match.group(1).split(",")]
        if "file_path" not in columns:
            continue
        values = [v.strip() for v in match.group(2).split(",")]
        if len(values) != len(columns) or (
            values[columns.index("file_path")].upper() not in _SQL_CLEARING_VALUES
        ):
            return True
    return False


def _facts_of(bindings: Iterable[_Binding]) -> set[_Binding]:
    return {b for b in bindings if b[0] in _FACT_TAGS}


def _without_path(facts: set[_Binding]) -> set[_Binding]:
    """Provenance that survives conversion to a string / path component."""
    return {f for f in facts if f != _PATH}


def _iteration_facts(facts: set[_Binding]) -> set[_Binding]:
    """What iterating a collection / Path iterator yields: all but SQL text."""
    return {f for f in facts if f[0] != "sql_write"}


def _element_facts(facts: set[_Binding]) -> set[_Binding]:
    """What an element / entry of a container carries: managed / weak provenance."""
    return {f for f in facts if f != _PATH and f[0] != "sql_write"}


def _static_sql_text(node: ast.expr) -> str | None:
    """Literal text of a SQL string built from constants, f-strings, ``+`` and
    ``%``; each dynamic part becomes ``?``. ``None`` when nothing is literal.

    A dynamic *column name* (``SET %s = ?``) is deliberately not treated as a
    ``file_path`` write: those builders are persistence primitives
    (``update_work_metadata``, the per-field sync writer) whose callers are
    what INV-STORAGE-003 checks.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "?" for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _static_sql_text(node.left), _static_sql_text(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else "?") + (right if right is not None else "?")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _static_sql_text(node.left)
    return None


def _sql_facts(node: ast.expr) -> set[_Binding]:
    text = _static_sql_text(node)
    if text is None:
        return set()
    return {("sql_write", target) for target in _sql_write_targets(text)}


def _expr_facts(node: ast.expr | None, scopes: list[_Scope]) -> set[_Binding]:
    """May-provenance of ``node``: Path value, managed-PDF path, weak alias, SQL write."""
    if node is None:
        return set()
    branches = _branch_values(node)
    if branches is not None:
        return set().union(*(_expr_facts(branch, scopes) for branch in branches))
    if isinstance(node, ast.Name):
        facts = _facts_of(_resolve(scopes, node.id))
        if node.id in MANAGED_PDF_DIR_NAMES:
            facts.add(_MANAGED)
        return facts
    if isinstance(node, ast.Attribute):
        return _attribute_facts(node, scopes)
    if isinstance(node, ast.Call):
        return _call_facts(node, scopes)
    if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
        return _string_facts(node, scopes)
    return _container_facts(node, scopes)


def _string_facts(
    node: ast.Constant | ast.JoinedStr | ast.BinOp, scopes: list[_Scope]
) -> set[_Binding]:
    """Literal SQL text, f-strings, and ``+`` / ``%`` / Path ``/`` joins."""
    if isinstance(node, ast.JoinedStr):
        return _sql_facts(node) | set().union(
            *(
                _without_path(_expr_facts(part.value, scopes))
                for part in node.values
                if isinstance(part, ast.FormattedValue)
            )
        )
    if isinstance(node, ast.BinOp):
        left = _expr_facts(node.left, scopes)
        right = _expr_facts(node.right, scopes)
        if isinstance(node.op, ast.Div) and (_PATH in left or _PATH in right):
            return {_PATH} | _element_facts(left) | _element_facts(right)
        return _sql_facts(node) | _without_path(left) | _without_path(right)
    return _sql_facts(node)


def _comprehension_facts(
    node: ast.ListComp | ast.SetComp | ast.GeneratorExp, scopes: list[_Scope]
) -> set[_Binding]:
    """Element facts, plus a generator iterable's facts when the element uses
    that generator's target (``[p for p in d.iterdir()]``)."""
    facts = _expr_facts(node.elt, scopes)
    used = {sub.id for sub in ast.walk(node.elt) if isinstance(sub, ast.Name)}
    for generator in node.generators:
        if used & set(_stored_names(generator.target)):
            facts |= _expr_facts(generator.iter, scopes)
    return _iteration_facts(facts)


def _container_facts(node: ast.expr, scopes: list[_Scope]) -> set[_Binding]:
    """Collections, comprehensions and element access carry their elements' facts."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_expr_facts(elt, scopes) for elt in node.elts))
    if isinstance(node, ast.Dict):
        parts: list[ast.expr | None] = [*node.keys, *node.values]
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _comprehension_facts(node, scopes)
    elif isinstance(node, ast.DictComp):
        parts = [node.key, node.value]
    elif isinstance(node, (ast.Starred, ast.Subscript)):
        return _expr_facts(node.value, scopes)
    else:
        return set()
    return set().union(*(_element_facts(_expr_facts(part, scopes)) for part in parts))


def _attribute_facts(node: ast.Attribute, scopes: list[_Scope]) -> set[_Binding]:
    facts: set[_Binding] = set()
    if node.attr in MANAGED_PDF_DIR_NAMES:
        facts.add(_MANAGED)
    key = _attr_key(node)
    if key is not None:
        facts |= _facts_of(_resolve(scopes, key))
    if node.attr in _DIR_ENTRY_ATTRS:
        # ``entry.path`` of an ``os.scandir`` entry under a managed directory.
        receiver = _expr_facts(node.value, scopes)
        if _MANAGED in receiver and _PATH not in receiver:
            facts |= _element_facts(receiver)
    if node.attr in _PATH_RETURNING_ATTRS or node.attr in _PATH_STRING_ATTRS:
        receiver = _expr_facts(node.value, scopes)
        if _PATH in receiver:
            # ``p.parent`` is a Path; ``p.name`` is a string naming the same
            # file, so it keeps the managed / weak provenance.
            facts |= receiver if node.attr in _PATH_RETURNING_ATTRS else _without_path(receiver)
    return facts


def _call_argument_facts(node: ast.Call, scopes: list[_Scope]) -> set[_Binding]:
    values = [*node.args, *(kw.value for kw in node.keywords)]
    return set().union(*(_expr_facts(value, scopes) for value in values))


def _call_facts(node: ast.Call, scopes: list[_Scope]) -> set[_Binding]:
    func = node.func
    if _is_path_class_expr(func, scopes):
        return {_PATH} | _element_facts(_call_argument_facts(node, scopes))
    if isinstance(func, ast.Attribute):
        facts = _method_call_facts(node, func, scopes)
        if facts is not None:
            return facts
    if _qualified_names(func, scopes) & _DIR_ITERATOR_FUNCS:
        # Entries under the directory argument carry its provenance.
        return _element_facts(_call_argument_facts(node, scopes))
    if (
        _qualified_names(func, scopes) & (_PATH_STRING_FUNCS | _PURE_PATH_CLASSES)
        or _is_builtin(func, scopes, "str")
    ):
        return _element_facts(_call_argument_facts(node, scopes))
    leaves = _callee_leaf_names(func, scopes)
    if leaves & MANAGED_PDF_PATH_HELPERS:
        return {_MANAGED} | _element_facts(_call_argument_facts(node, scopes))
    if leaves & WEAK_MANAGED_PDF_ALIAS_HELPERS:
        return {_WEAK}
    return set()


def _method_call_facts(
    node: ast.Call, func: ast.Attribute, scopes: list[_Scope]
) -> set[_Binding] | None:
    """Facts of ``receiver.method(...)``, or ``None`` when the method is not modelled."""
    if func.attr in _PATH_CLASS_FACTORIES and _is_path_class_expr(func.value, scopes):
        return {_PATH}
    receiver = _expr_facts(func.value, scopes)
    if _PATH in receiver:
        facts = _path_method_facts(node, func.attr, receiver, scopes)
        if facts is not None:
            return facts
    elif receiver and func.attr in _PATH_RETURNING_METHODS:
        # A pure path (``PurePath(d).joinpath(n)``) derives another pure path
        # naming a file under the same provenance, still without ``path``.
        return receiver | _element_facts(_call_argument_facts(node, scopes))
    if func.attr in _MAPPING_LOOKUP_METHODS and _PATH not in receiver:
        # ``names.get("pdf")``: an element of the mapping, or the default.
        defaults = set().union(*(_expr_facts(arg, scopes) for arg in node.args[1:]))
        return _element_facts(receiver) | _element_facts(defaults)
    if func.attr not in _STR_TRANSFORM_METHODS or (func.attr == "replace" and _PATH in receiver):
        # ``Path.replace(target)`` renames; it is not a string transform.
        return None
    # SQL text and managed / weak provenance survive ``.strip()`` /
    # ``.replace()`` / ``.format()`` and the like; ``.format()`` and
    # ``.replace()`` also build their result from their arguments.
    facts = _without_path(receiver)
    if func.attr == "join":
        # ``sep.join(parts)``: built from the iterable's elements.
        facts |= _element_facts(_call_argument_facts(node, scopes))
    if func.attr in ("format", "replace"):
        facts |= _element_facts(_call_argument_facts(node, scopes))
    return facts


def _positional_or_keyword(
    node: ast.Call, index: int, keywords: tuple[str, ...]
) -> ast.expr | None:
    """The argument bound to the parameter at ``index`` (spelled one of
    ``keywords``). An explicit keyword wins -- the same parameter cannot also
    be bound positionally -- or a dict-literal ``**`` spread naming it; then
    the positional at ``index`` (or a ``*args`` spread at or before it); then
    an opaque ``**mapping`` that may supply it."""
    explicit = next((kw.value for kw in node.keywords if kw.arg in keywords), None)
    if explicit is not None:
        return explicit
    # ``**{'dst': x}`` names the parameter as decisively as ``dst=x``.
    for kw in node.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Dict):
            for keyword in keywords:
                found = _dict_file_path_value(kw.value, key=keyword)
                if found is not None:
                    return found
    for position, arg in enumerate(node.args):
        if isinstance(arg, ast.Starred):
            return arg.value
        if position == index:
            return arg
    # An opaque ``**mapping`` may supply it.
    return next(
        (kw.value for kw in node.keywords if kw.arg is None and not isinstance(kw.value, ast.Dict)),
        None,
    )


def _identity_passed_names(node: ast.expr) -> list[str]:
    """Names whose *object* an argument expression passes on: the name itself,
    unpacked / collected into a display, or chosen by a conditional, walrus
    or ``and``/``or``. Attribute, call and subscript interiors only read."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        # A tracked one-level alias (``self.fields = body``) is the object.
        key = _attr_key(node)
        return [key] if key is not None else []
    if isinstance(node, (ast.Starred, ast.NamedExpr)):
        return _identity_passed_names(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [name for elt in node.elts for name in _identity_passed_names(elt)]
    if isinstance(node, ast.Dict):
        return [name for value in node.values for name in _identity_passed_names(value)]
    if isinstance(node, ast.IfExp):
        return [*_identity_passed_names(node.body), *_identity_passed_names(node.orelse)]
    if isinstance(node, ast.BoolOp):
        return [name for value in node.values for name in _identity_passed_names(value)]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        # ``[body for _ in items]`` passes ``body`` itself; a name the
        # comprehension binds (``[body for body in rows]``) is its own local.
        element = node.value if isinstance(node, ast.DictComp) else node.elt
        local = {name for gen in node.generators for name in _stored_names(gen.target)}
        return [name for name in _identity_passed_names(element) if name not in local]
    return []


def _path_method_facts(
    node: ast.Call, method: str, receiver: set[_Binding], scopes: list[_Scope]
) -> set[_Binding] | None:
    """A method on a Path value: same file as a string, an iterator of
    Paths under it, or another Path derived from it."""
    if method in _PATH_STRING_METHODS:
        return _without_path(receiver)
    if method in _PATH_ITERATOR_METHODS:
        return receiver
    if method in _PATH_RETURNING_METHODS:
        return receiver | _element_facts(_call_argument_facts(node, scopes))
    return None


def _proven_owned(node: ast.expr, scopes: list[_Scope]) -> set[_Binding] | None:
    """The owned tags (``minted`` / ``guarded``) ``node`` is built from, when every
    value it may hold is exactly an owned value; otherwise ``None``.

    Ownership survives only identity-preserving shapes: the minting helpers'
    results, an entered guard's exact input path and yielded basename, names
    bound only to those, ``str()``, and the canonical ``/api/pdfs/{name}``
    wrapper around an owned *basename*. Any other prefix or suffix names
    different bytes than the ones minted or locked, so ownership is dropped.
    """
    branches = _branch_values(node)
    if branches is not None:
        return _all_owned(branches, scopes)
    if isinstance(node, ast.Name):
        bindings = _resolve(scopes, node.id)
        if bindings and all(b[0] in _OWNED_TAGS for b in bindings):
            return set(bindings)
        return None
    if isinstance(node, ast.Call):
        if _canonical_capability_names(node.func, scopes) & MANAGED_PDF_MINTING_HELPERS:
            return {_MINTED}
        if _is_builtin(node.func, scopes, "str") and len(node.args) == 1:
            return _proven_owned(node.args[0], scopes)
        return None
    name = _managed_route_basename_expr(node)
    if name is None:
        return None
    owned = _proven_owned(name, scopes)
    if owned is None or any(tag[-1] != "name" for tag in owned):
        return None
    return {(*tag[:-1], "path") for tag in owned}


def _managed_route_basename_expr(node: ast.expr) -> ast.expr | None:
    """``x`` for exactly ``f"/api/pdfs/{x}"`` or ``"/api/pdfs/" + x``."""
    if isinstance(node, ast.JoinedStr) and len(node.values) == 2:
        prefix, value = node.values
        if (
            isinstance(prefix, ast.Constant)
            and prefix.value == _MANAGED_ROUTE_PREFIX
            and isinstance(value, ast.FormattedValue)
            and value.conversion == -1
            and value.format_spec is None
        ):
            return value.value
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and isinstance(node.left, ast.Constant)
        and node.left.value == _MANAGED_ROUTE_PREFIX
    ):
        return node.right
    return None


def _all_owned(nodes: Sequence[ast.expr], scopes: list[_Scope]) -> set[_Binding] | None:
    tags: set[_Binding] = set()
    for node in nodes:
        owned = _proven_owned(node, scopes)
        if owned is None:
            return None
        tags |= owned
    return tags or None


def _iterable_yields_archive(iterable: ast.expr, scopes: list[_Scope]) -> bool:
    return isinstance(iterable, (ast.Tuple, ast.List, ast.Set)) and any(
        _is_zip_archive_expr(elt, scopes) for elt in iterable.elts
    )


def _stored_names(target: ast.expr) -> list[str]:
    return [
        sub.id
        for sub in ast.walk(target)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
    ]


def _target_pairs(
    target: ast.expr, value: ast.expr, scopes: list[_Scope]
) -> list[tuple[str, set[_Binding]]]:
    key = target.id if isinstance(target, ast.Name) else _attr_key(target)
    if key is not None:
        return [(key, _value_bindings(value, scopes))]
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(e, ast.Starred) for e in (*target.elts, *value.elts))
    ):
        pairs: list[tuple[str, set[_Binding]]] = []
        for sub_target, sub_value in zip(target.elts, value.elts):
            pairs.extend(_target_pairs(sub_target, sub_value, scopes))
        return pairs
    return [(name, {_OTHER}) for name in _stored_names(target)]


def _loop_target_pairs(
    target: ast.expr, iterable: ast.expr, scopes: list[_Scope]
) -> list[tuple[str, set[_Binding]]]:
    key = target.id if isinstance(target, ast.Name) else _attr_key(target)
    if key is not None and _iterable_yields_archive(iterable, scopes):
        return [(key, {_ARCHIVE})]
    # Elements of a collection (or a Path iterator) carry its provenance.
    carried = {_OTHER} | _iteration_facts(_expr_facts(iterable, scopes))
    if key is not None:
        return [(key, carried)]
    pairs = [(name, {_OTHER}) for name in _stored_names(target)]
    if _is_path_walk(iterable) and isinstance(target, (ast.Tuple, ast.List)) and target.elts:
        # ``for root, dirs, files in p.walk()``: ``root`` is a Path under ``p``.
        root = target.elts[0]
        if isinstance(root, ast.Name):
            pairs = [(root.id, carried), *(pair for pair in pairs if pair[0] != root.id)]
    return pairs


def _is_path_walk(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"fwalk", "walk"}
    )


_Pairs = list[tuple[str, set[_Binding]]]


def _import_pairs(node: ast.Import, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for item in node.names:
        top = item.name.split(".", 1)[0]
        if item.asname is None:
            # ``import os.path`` still binds the top-level name ``os``.
            pairs.append((top, {("module", top)}))
        else:
            # ``import os.path as p`` binds only ``p`` (to os.path).
            pairs.append((item.asname, {("module", item.name)}))
    return pairs


def _import_from_pairs(node: ast.ImportFrom, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    # Relative imports keep a ``.``-prefixed pseudo-module so imported helper
    # names still resolve (stdlib identities never come from them).
    absolute = node.level == 0 and bool(node.module)
    module = node.module if absolute else "." * node.level + (node.module or "")
    for item in node.names:
        if item.name == "*":
            if absolute and node.module == "zipfile":
                pairs.extend(
                    (cls, {("name", "zipfile", cls)}) for cls in sorted(ZIPFILE_ARCHIVE_CLASSES)
                )
            continue
        pairs.append((item.asname or item.name, {("name", module, item.name)}))
    return pairs


def _assign_pairs(node: ast.Assign, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for target in node.targets:
        pairs.extend(_target_pairs(target, node.value, scopes))
    return pairs


def _is_class_binding(binding: _Binding) -> bool:
    """A binding naming a class (or module), not an instance of it."""
    if binding[0] == "name":
        return _is_zip_class_binding(binding) or (
            f"{binding[1]}.{binding[2]}" in PATHLIB_PATH_CLASSES
        )
    return binding[0] in {"class", "module"}


def _ann_assign_pairs(node: ast.AnnAssign, scopes: list[_Scope]) -> _Pairs:
    key = node.target.id if isinstance(node.target, ast.Name) else _attr_key(node.target)
    if key is None:
        return []
    bindings = _value_bindings(node.value, scopes) if node.value is not None else set()
    # ``x: ZipFile`` declares an archive; ``Z: type[ZipFile] = ZipFile`` keeps
    # the class binding of its value.
    is_class_value = any(_is_class_binding(b) for b in bindings)
    if _annotation_mentions_zip(node.annotation, scopes) and not is_class_value:
        bindings.add(_ARCHIVE)
    if _annotation_mentions_path(node.annotation, scopes) and not is_class_value:
        bindings.add(_PATH)
    return [(key, bindings)] if bindings else []


def _aug_assign_pairs(node: ast.AugAssign, scopes: list[_Scope]) -> _Pairs:
    return [(name, {_OTHER}) for name in _stored_names(node.target)]


def _named_expr_pairs(node: ast.NamedExpr, scopes: list[_Scope]) -> _Pairs:
    return _target_pairs(node.target, node.value, scopes)


def _for_pairs(node: ast.For | ast.AsyncFor, scopes: list[_Scope]) -> _Pairs:
    return _loop_target_pairs(node.target, node.iter, scopes)


def _dir_iterator_context_facts(node: ast.expr, scopes: list[_Scope]) -> set[_Binding]:
    """Provenance an ``as`` target receives from a directory-iterator context
    manager, which returns itself: ``os.scandir(d)``, ``(cm := os.scandir(d))``,
    ``a if c else b``, or a name bound to one (``cm = os.scandir(d)``)."""
    if isinstance(node, ast.NamedExpr):
        return _dir_iterator_context_facts(node.value, scopes)
    if isinstance(node, ast.IfExp):
        return _dir_iterator_context_facts(node.body, scopes) | _dir_iterator_context_facts(
            node.orelse, scopes
        )
    if isinstance(node, ast.Call) and _qualified_names(node.func, scopes) & _DIR_ITERATOR_FUNCS:
        return _value_bindings(node, scopes)
    if isinstance(node, ast.Name):
        return _facts_of(_resolve(scopes, node.id))
    return set()


def _with_pairs(node: ast.With | ast.AsyncWith, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for item in node.items:
        target = item.optional_vars
        if target is None:
            continue
        key = target.id if isinstance(target, ast.Name) else _attr_key(target)
        if key is not None and _is_zip_archive_expr(item.context_expr, scopes):
            pairs.append((key, {_ARCHIVE}))
        elif key is not None and (entered := _dir_iterator_context_facts(item.context_expr, scopes)):
            # ``with os.scandir(d) as entries`` yields the iterator itself.
            pairs.append((key, entered))
        else:
            pairs.extend((name, {_OTHER}) for name in _stored_names(target))
    return pairs


def _def_pairs(node: ast.FunctionDef | ast.AsyncFunctionDef, scopes: list[_Scope]) -> _Pairs:
    info = scopes[0].function_registry.setdefault(id(node), _FuncInfo())
    info.name = node.name
    if _annotation_mentions_zip(node.returns, scopes):
        info.returns_archive = True
    return [(node.name, {("function", info)})]


def _class_def_pairs(node: ast.ClassDef, scopes: list[_Scope]) -> _Pairs:
    info = scopes[0].class_registry.setdefault(id(node), _ClassInfo())
    return [(node.name, {("class", info)})]


def _match_pairs(node: ast.Match, scopes: list[_Scope]) -> _Pairs:
    subject = _value_bindings(node.subject, scopes)
    pairs: _Pairs = []
    for case in node.cases:
        pairs.extend(_pattern_pairs(case.pattern, subject))
    return pairs


def _except_pairs(node: ast.ExceptHandler, scopes: list[_Scope]) -> _Pairs:
    return [(node.name, {_OTHER})] if node.name else []


_BINDING_HANDLERS: dict[type, Any] = {
    ast.Import: _import_pairs,
    ast.ImportFrom: _import_from_pairs,
    ast.Assign: _assign_pairs,
    ast.AnnAssign: _ann_assign_pairs,
    ast.AugAssign: _aug_assign_pairs,
    ast.NamedExpr: _named_expr_pairs,
    ast.For: _for_pairs,
    ast.AsyncFor: _for_pairs,
    ast.With: _with_pairs,
    ast.AsyncWith: _with_pairs,
    ast.FunctionDef: _def_pairs,
    ast.AsyncFunctionDef: _def_pairs,
    ast.ClassDef: _class_def_pairs,
    ast.ExceptHandler: _except_pairs,
    ast.Match: _match_pairs,
}


def _binding_pairs(node: ast.AST, scopes: list[_Scope]) -> _Pairs:
    """Names (or ``obj.attr`` keys) ``node`` binds in its own scope, with what each refers to."""
    handler = _BINDING_HANDLERS.get(type(node))
    return handler(node, scopes) if handler is not None else []


def _is_staticmethod(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list
    )


def _pattern_pairs(
    pattern: ast.pattern, subject: set[_Binding], *, whole: bool = True
) -> _Pairs:
    """Match-pattern captures. A capture of the whole subject (``case a``,
    ``case X() as a``, alternatives of those) takes the subject's bindings;
    captures of sub-parts are ordinary locals."""
    if isinstance(pattern, (ast.MatchAs, ast.MatchOr)):
        return _alias_pattern_pairs(pattern, subject, whole=whole)
    rest = pattern.name if isinstance(pattern, ast.MatchStar) else getattr(pattern, "rest", None)
    pairs: _Pairs = [(rest, {_OTHER})] if rest else []
    for child in ast.iter_child_nodes(pattern):
        if isinstance(child, ast.pattern):
            pairs.extend(_pattern_pairs(child, subject, whole=False))
    return pairs


def _alias_pattern_pairs(
    pattern: ast.MatchAs | ast.MatchOr, subject: set[_Binding], *, whole: bool
) -> _Pairs:
    """``case a`` / ``case P as a`` / ``case P | Q``: these match the same value."""
    if isinstance(pattern, ast.MatchOr):
        alternatives = pattern.patterns
        pairs: _Pairs = []
    else:
        alternatives = [pattern.pattern] if pattern.pattern is not None else []
        pairs = [(pattern.name, set(subject) if whole else {_OTHER})] if pattern.name else []
    for alternative in alternatives:
        pairs.extend(_pattern_pairs(alternative, subject, whole=whole))
    return pairs


def _function_params(args: ast.arguments) -> list[ast.arg]:
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    params.extend(a for a in (args.vararg, args.kwarg) if a is not None)
    return params


def _param_defaults(args: ast.arguments) -> dict[str, ast.expr]:
    """Parameter name -> its default expression, where it has one."""
    defaults: dict[str, ast.expr] = {}
    if args.defaults:
        # Positional defaults belong to the last positional parameters.
        tail = [*args.posonlyargs, *args.args][-len(args.defaults) :]
        defaults.update(zip((param.arg for param in tail), args.defaults))
    for param, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            defaults[param.arg] = default
    return defaults


def _arguments_pairs(args: ast.arguments, scopes: list[_Scope]) -> _Pairs:
    """Parameters are locals: they shadow enclosing imports and archive
    bindings. A parameter is an archive when annotated as one, and may be one
    when its default (evaluated in the enclosing scope) is; callers can still
    pass something else, so the default is merged, not substituted."""
    defaults = _param_defaults(args)
    pairs: _Pairs = []
    for param in _function_params(args):
        bindings: set[_Binding] = {_OTHER}
        if _annotation_mentions_zip(param.annotation, scopes):
            bindings.add(_ARCHIVE)
        if _annotation_mentions_path(param.annotation, scopes):
            bindings.add(_PATH)
        if param.arg in defaults:
            bindings |= _value_bindings(defaults[param.arg], scopes)
        pairs.append((param.arg, bindings))
    return pairs


def _iter_scope_nodes(body: Sequence[ast.AST]) -> Iterable[ast.AST]:
    """Nodes evaluated in this scope, in source order, excluding nested scope bodies."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        children: list[ast.AST]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            children = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
            if not isinstance(node, ast.Lambda):
                children = [*node.decorator_list, *children]
        elif isinstance(node, ast.ClassDef):
            children = [*node.decorator_list, *node.bases, *node.keywords]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            # Only walrus targets escape a comprehension into this scope.
            children = [sub for sub in ast.walk(node) if isinstance(sub, ast.NamedExpr)]
            stack.extend(reversed(children))
            continue
        else:
            children = list(ast.iter_child_nodes(node))
        stack.extend(reversed(children))


_State = dict[str, set[_Binding]]


def _copy_state(state: _State) -> _State:
    return {name: set(bindings) for name, bindings in state.items()}


def _merge_states(*states: _State) -> _State:
    merged: _State = {}
    for state in states:
        for name, bindings in state.items():
            merged.setdefault(name, set()).update(bindings)
    for name, bindings in merged.items():
        if "." in name and any(name not in state for state in states):
            bindings.add(_UNSET)
    return merged


def _terminates(stmts: Sequence[ast.AST]) -> bool:
    """The block never falls through to the statement after it."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return bool(last.orelse) and _terminates(last.body) and _terminates(last.orelse)
    return False


def _join(flows: list[tuple[_State, bool]]) -> _State:
    """Merge the states of the paths that reach the join point.

    ``return``/``raise`` paths never reach it; ``break``/``continue`` paths are
    already covered by the loop's later-iteration widening.
    """
    reaching = [state for state, terminated in flows if not terminated]
    return _merge_states(*(reaching or [state for state, _ in flows]))


class _InvariantVisitor(ast.NodeVisitor):
    """Walk the tree, resolving os/shutil/zipfile bindings in the current lexical scope."""

    def __init__(
        self,
        relpath: str,
        class_registry: dict[int, _ClassInfo] | None = None,
        function_registry: dict[int, _FuncInfo] | None = None,
    ) -> None:
        self.relpath = relpath
        self._class_registry = {} if class_registry is None else class_registry
        self._function_registry = {} if function_registry is None else function_registry
        self.scopes: list[_Scope] = []
        self.findings: list[Finding] = []
        # Enclosing def/class names, for function-level capability exemptions.
        self._qualname: list[str] = []
        # Every ``(scope, name)`` a guarded dict was marked dirty in. The tag
        # is added outside ``_bind``, so ``summary``-based widening (loop tops,
        # exception handlers) cannot see it; this log can.
        self._dirty_log: list[tuple[_Scope, str]] = []
        # Inner ``partial(...)`` calls already checked as part of the outer
        # partial they were flattened into (whose keywords may override theirs).
        self._flattened_partials: set[int] = set()

    @property
    def _function(self) -> tuple[str, str]:
        """``(relpath, "Class.method")`` of the def being walked."""
        return (self.relpath, ".".join(self._qualname))

    def _declared_targets(self, nodes: list[ast.AST]) -> dict[str, _Scope]:
        """``global`` / ``nonlocal`` names of the innermost scope -> owning scope."""
        declared: dict[str, _Scope] = {}
        for node in nodes:
            if isinstance(node, ast.Global):
                for name in node.names:
                    declared[name] = self.scopes[0]
            elif isinstance(node, ast.Nonlocal):
                for name in node.names:
                    owner = next(
                        (
                            scope
                            for scope in reversed(self.scopes[1:-1])
                            if not scope.is_class and name in scope.summary
                        ),
                        None,
                    )
                    if owner is not None:
                        declared[name] = owner
        return declared

    def _push(
        self,
        body: list[ast.stmt],
        *,
        class_info: _ClassInfo | None = None,
        params: _Pairs | None = None,
        is_comprehension: bool = False,
        function_info: _FuncInfo | None = None,
    ) -> None:
        scope = _Scope(class_info=class_info, is_comprehension=is_comprehension)
        scope.function_info = function_info
        if not self.scopes:
            scope.class_registry = self._class_registry
            scope.function_registry = self._function_registry
        self.scopes.append(scope)
        for name, bindings in params or ():
            scope.summary[name] = set(bindings)
            scope.current[name] = set(bindings)
        nodes = list(_iter_scope_nodes(body))
        scope.declared = self._declared_targets(nodes)
        # Imports first so later assignments in the scope can resolve them.
        for pass_imports in (True, False):
            for node in nodes:
                if isinstance(node, (ast.Import, ast.ImportFrom)) != pass_imports:
                    continue
                for name, bindings in _binding_pairs(node, self.scopes):
                    owner = scope.declared.get(name, scope)
                    owner.summary.setdefault(name, set()).update(bindings)
                    if owner is not scope:
                        owner.rebound_elsewhere.add(name)
        for name, bindings in scope.summary.items():
            self._record_archive_attr(name, bindings)

    def _pop(self) -> None:
        self.scopes.pop()

    def _record_archive_attr(self, name: str, bindings: set[_Binding]) -> None:
        """Remember ``self.x = <archive>`` / class-body ``x = <archive>`` on the class."""
        if _ARCHIVE not in bindings:
            return
        root, dot, attr = name.partition(".")
        if not dot:
            class_info = self.scopes[-1].class_info
            if class_info is not None:
                class_info.archive_attrs.add(name)
            return
        for info in _class_infos(_resolve(self.scopes, root)):
            info.archive_attrs.add(attr)

    def _bind(self, pairs: _Pairs, *, may: bool = False) -> None:
        """Bind in the innermost scope; ``may`` merges instead of replacing."""
        scope = self.scopes[-1]
        for name, bindings in pairs:
            self._record_archive_attr(name, bindings)
            owner = scope.declared.get(name, scope)
            if owner is not scope:
                # ``global``/``nonlocal``: the binding belongs to the owner.
                owner.summary.setdefault(name, set()).update(bindings)
                owner.rebound_elsewhere.add(name)
            elif may:
                scope.current.setdefault(name, set()).update(bindings)
            else:
                scope.current[name] = set(bindings)

    def _bind_node(self, node: ast.AST) -> None:
        self._bind(_binding_pairs(node, self.scopes))

    # --- control flow: each branch starts from the state before it, and the
    # --- branch states are unioned where control flow joins again.

    def _run_branch(self, start: _State, stmts: list[ast.stmt] | list[ast.AST]) -> _State:
        scope = self.scopes[-1]
        scope.current = _copy_state(start)
        for stmt in stmts:
            self.visit(stmt)
        return scope.current

    def _with_body_bindings(self, state: _State, nodes: Sequence[ast.AST]) -> _State:
        """``state`` plus every binding ``nodes`` can make anywhere.

        Used where control can arrive from an arbitrary point in ``nodes``: the
        top of a loop (later iterations) and an exception handler (any
        statement of the ``try`` body may raise). ``summary`` is a safe
        over-approximation of what each such name may hold.
        """
        scope = self.scopes[-1]
        state = _copy_state(state)
        for node in _iter_scope_nodes(nodes):
            for name, _ in _binding_pairs(node, self.scopes):
                if name in scope.summary:
                    if "." in name and name not in state:
                        state[name] = {_UNSET}
                    state.setdefault(name, set()).update(scope.summary[name])
        return state

    def _loop_entry_state(self, nodes: Sequence[ast.AST]) -> _State:
        """State at the top of a loop, including later-iteration bindings."""
        return self._with_body_bindings(self.scopes[-1].current, nodes)

    def _run_loop(self, entry: _State, run: Callable[[_State], _State]) -> tuple[_State, _State]:
        """Run a loop body from ``entry`` until no new dict is dirtied.

        A guarded dict dirtied anywhere in the body (including just before a
        ``break``/``continue``) reaches later iterations and the loop exit, so
        the entry is widened and the body re-walked; findings from the
        discarded walk are dropped. Only bodies that dirty a dict re-run.
        """
        while True:
            findings, mark = len(self.findings), len(self._dirty_log)
            body_end = run(entry)
            dirtied = {
                name
                for name in self._dirtied_since(mark)
                if name in entry and _DICT_DIRTY not in entry[name]
            }
            if not dirtied:
                return entry, body_end
            del self.findings[findings:]
            entry = self._with_dirty(entry, dirtied)

    def _finish_loop(self, entry: _State, body_end: _State, orelse: list[ast.stmt]) -> None:
        after = _merge_states(entry, body_end)
        self.scopes[-1].current = _merge_states(after, self._run_branch(after, orelse))

    def visit_Module(self, node: ast.Module) -> None:
        self._push(node.body)
        self.scopes[0].relpath = self.relpath
        for stmt in node.body:
            self.visit(stmt)
        self._pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults and annotations evaluate in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        args = node.args
        params = _function_params(args)
        for default in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
            self.visit(default)
        for param in params:
            if param.annotation is not None:
                self.visit(param.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        param_pairs = self._param_pairs(node)
        self._bind_node(node)
        info = self.scopes[0].function_registry.setdefault(id(node), _FuncInfo())
        self._push(node.body, params=param_pairs, function_info=info)
        self._qualname.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self._qualname.pop()
        self._pop()

    def _param_pairs(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Pairs:
        """See ``_arguments_pairs``; a method's first parameter (``self`` /
        ``cls``) is additionally bound to its class."""
        pairs = _arguments_pairs(node.args, self.scopes)
        class_info = self.scopes[-1].class_info
        positional = [*node.args.posonlyargs, *node.args.args]
        if class_info is not None and positional and not _is_staticmethod(node):
            pairs[0] = (positional[0].arg, {("instance", class_info)})
        return pairs

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        args = node.args
        for default in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
            self.visit(default)
        self._push([], params=_arguments_pairs(args, self.scopes))
        # Runs later, so it never inherits the enclosing capability exemption.
        self._qualname.append("<lambda>")
        self.visit(node.body)
        self._qualname.pop()
        self._pop()

    def _describe_class(self, node: ast.ClassDef) -> _ClassInfo:
        """Refresh the class's zipfile base, same-module bases and methods."""
        info = self.scopes[0].class_registry.setdefault(id(node), _ClassInfo())
        info.zip_base = any(_is_zip_class_expr(base, self.scopes) for base in node.bases)
        info.bases = [
            base_info
            for base in node.bases
            if isinstance(base, ast.Name)
            for base_info in _class_infos(_resolve(self.scopes, base.id))
        ]
        info.methods = {
            stmt.name: self.scopes[0].function_registry.setdefault(id(stmt), _FuncInfo())
            for stmt in node.body
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        return info

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(child)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        info = self._describe_class(node)
        self._push(node.body, class_info=info)
        # Pre-collect every method body first so ``self.x = ZipFile(...)`` in
        # ``__init__`` classifies ``self.x`` in methods defined before it.
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._push(stmt.body, params=self._param_pairs(stmt))
                self._pop()
        self._qualname.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self._qualname.pop()
        self._pop()
        self._bind_node(node)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> None:
        lazy = isinstance(node, ast.GeneratorExp)
        self._push([], is_comprehension=True)
        self.scopes[-1].is_lazy = lazy
        if lazy:
            # A generator body runs later: no capability exemption carries over.
            self._qualname.append("<genexpr>")
        for generator in node.generators:
            self.visit(generator.iter)
            self._bind(_loop_target_pairs(generator.target, generator.iter, self.scopes))
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        if lazy:
            self._qualname.pop()
        self._pop()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    def visit_Return(self, node: ast.Return) -> None:
        self.generic_visit(node)
        function_info = self.scopes[-1].function_info
        if (
            function_info is not None
            and node.value is not None
            and _is_zip_archive_expr(node.value, self.scopes)
        ):
            function_info.returns_archive = True

    def visit_Import(self, node: ast.Import) -> None:
        self._check_adapter_process_import(node)
        self._bind_node(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_adapter_process_import(node)
        self._bind_node(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._check_guarded_dict_store(target, node.value)
        self._bind_node(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if node.value is not None:
            self._check_guarded_dict_store(node.target, node.value)
        self._bind_node(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
        self._check_guarded_dict_store(node.target, None)
        if isinstance(node.op, ast.BitOr):
            # ``d |= {...}`` updates in place, possibly through an alias.
            self._forget_all_guarded_dicts()
        self._bind_node(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.generic_visit(node)
        pairs = _binding_pairs(node, self.scopes)
        depth = len(self.scopes)
        while depth > 1 and self.scopes[depth - 1].is_comprehension:
            depth -= 1
        if depth == len(self.scopes):
            self._bind(pairs)
            return
        # A walrus inside a comprehension binds in the containing scope, and
        # may run any number of times, so it merges into that scope's state.
        inner = self.scopes[depth:]
        del self.scopes[depth:]
        try:
            self._bind(pairs, may=True)
        finally:
            self.scopes.extend(inner)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)

        def run(entry: _State) -> _State:
            self.scopes[-1].current = _copy_state(entry)
            self._check_guarded_dict_store(node.target, None)
            self._bind_node(node)
            return self._run_branch(self.scopes[-1].current, node.body)

        entry, body_end = self._run_loop(self._loop_entry_state(node.body), run)
        self._finish_loop(entry, body_end, node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        # The test runs before every iteration and before leaving the loop, so
        # its (walrus) bindings reach both the body and the code after it.
        after_test: _State = {}

        def run(entry: _State) -> _State:
            nonlocal after_test
            self.scopes[-1].current = _copy_state(entry)
            self.visit(node.test)
            after_test = self.scopes[-1].current
            return self._run_branch(after_test, node.body)

        _, body_end = self._run_loop(self._loop_entry_state([node.test, *node.body]), run)
        self._finish_loop(after_test, body_end, node.orelse)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = _copy_state(self.scopes[-1].current)
        self.scopes[-1].current = _join(
            [
                (self._run_branch(before, branch), _terminates(branch))
                for branch in (node.body, node.orelse)
            ]
        )

    def visit_Try(self, node: ast.Try) -> None:
        before = _copy_state(self.scopes[-1].current)
        mark = len(self._dirty_log)
        body_end = self._run_branch(before, node.body)
        # A handler can start after any statement of the body.
        handler_start = _merge_states(
            body_end,
            self._with_dirty(
                self._with_body_bindings(before, node.body), self._dirtied_since(mark)
            ),
        )
        flows = [
            (self._run_branch(handler_start, [handler]), _terminates(handler.body))
            for handler in node.handlers
        ]
        orelse_end = self._run_branch(body_end, node.orelse)
        flows.append((orelse_end, _terminates(node.body) or _terminates(node.orelse)))
        self.scopes[-1].current = _join(flows)
        for stmt in node.finalbody:
            self.visit(stmt)

    visit_TryStar = visit_Try

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        self._bind_node(node)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        subject = _value_bindings(node.subject, self.scopes)
        before = _copy_state(self.scopes[-1].current)
        # No case may match, so the state before the match also flows on.
        flows: list[tuple[_State, bool]] = [(before, False)]
        for case in node.cases:
            self.scopes[-1].current = _copy_state(before)
            self._bind(_pattern_pairs(case.pattern, subject))
            if case.guard is not None:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)
            flows.append((self.scopes[-1].current, _terminates(case.body)))
        self.scopes[-1].current = _join(flows)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        # Items are entered left to right: a later manager's expression runs
        # while an earlier guard is already held.
        scope = self.scopes[-1]
        entered: list[_AdoptionGuard] = []
        for item in node.items:
            self.visit(item.context_expr)
            self._bind(_with_pairs(ast.With(items=[item], body=[]), self.scopes))
            call = self._entered_adoption_guard(item.context_expr)
            if call is not None:
                guard = self._enter_adoption_guard(call, item.optional_vars)
                entered.append(guard)
                scope.adoption_guards.append(guard)
        for stmt in node.body:
            self.visit(stmt)
        del scope.adoption_guards[len(scope.adoption_guards) - len(entered) :]

    def _is_adoption_guard_call(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and MANAGED_PDF_ADOPTION_GUARD in (
            _canonical_capability_names(node.func, self.scopes)
        )

    def _entered_adoption_guard(self, node: ast.expr) -> ast.Call | None:
        """The guard call this context expression actually enters.

        ``guard(...)`` itself. A guard merely constructed inside another
        context manager -- ``nullcontext(guard(...))`` -- is never entered and
        does not count.

        ``guard(...) if cond else nullcontext()`` may skip the guard, and
        which arm runs is runtime state the checker cannot relate to the
        persisted value; it counts only at the reviewed sites in
        ``CONDITIONAL_ADOPTION_GUARDS`` (same file, function and condition,
        guard in the true arm). Anywhere else the body is unguarded.
        """
        if isinstance(node, ast.Call) and self._is_adoption_guard_call(node):
            return node
        if (
            isinstance(node, ast.IfExp)
            and isinstance(node.body, ast.Call)
            and self._is_adoption_guard_call(node.body)
            and self._is_nullcontext_call(node.orelse)
            and (*self._function, ast.unparse(node.test)) in CONDITIONAL_ADOPTION_GUARDS
        ):
            return node.body
        return None

    def _is_nullcontext_call(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and (
            "contextlib.nullcontext" in _qualified_names(node.func, self.scopes)
        )

    def _enter_adoption_guard(self, call: ast.Call, target: ast.expr | None) -> _AdoptionGuard:
        """Tag the guard's input and yielded name as protected by this guard."""
        guard = _AdoptionGuard()
        value = call.args[1] if len(call.args) > 1 else next(
            (kw.value for kw in call.keywords if kw.arg == "file_path_value"), None
        )
        pairs: _Pairs = []
        if isinstance(value, ast.Name):
            # The exact value the guard validated and locked; any weak
            # provenance it had is kept so INV-STORAGE-004 still sees it.
            weak = {f for f in _expr_facts(value, self.scopes) if f == _WEAK}
            pairs.append((value.id, {guard.tag("path")} | weak))
        fields = self._file_path_entry_owner(value)
        if fields is not None:
            # Only the guarded identity (plus provenance facts): a later
            # may-rebinding joins other bindings in, which then disqualify it.
            facts = _facts_of(_resolve(self.scopes, fields))
            pairs.append((fields, facts | {("guarded_dict", guard.key)}))
        if isinstance(target, ast.Name):
            pairs.append((target.id, {guard.tag("name")}))
        self._bind(pairs)
        return guard

    @staticmethod
    def _file_path_entry_owner(node: ast.expr | None) -> str | None:
        """``body`` for ``body.get("file_path", ...)`` / ``body["file_path"]``."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "file_path"
        ):
            return node.func.value.id
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "file_path"
        ):
            return node.value.id
        return None

    def _dirtiable_scopes(self) -> list[_Scope]:
        """Scopes, innermost first, whose flow state a mutation here may reach.

        A comprehension (or nested def/lambda) body mutating an enclosing
        guarded dict must dirty the scope that owns the binding: the inner
        scope's state is discarded when it is popped. Class bodies are not
        enclosing scopes for what is nested in them."""
        return [self.scopes[-1], *(s for s in reversed(self.scopes[:-1]) if not s.is_class)]

    def _check_guarded_dict_store(self, target: ast.expr, value: ast.expr | None) -> None:
        """``body["file_path"] = <owned>`` keeps a guarded dict protected; any
        other write to (possibly) that entry invalidates it.

        Dict identity is not tracked, so a write through *any* other
        expression (``fields = body; fields["file_path"] = x``) may alias a
        guarded dict and invalidates every one."""
        if isinstance(target, ast.Starred):
            self._check_guarded_dict_store(target.value, None)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            # ``body["file_path"], x = other, 1``: pair elements when the
            # shapes line up; otherwise each element's value is unknown.
            values: Sequence[ast.expr | None] = [None] * len(target.elts)
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(value.elts) == len(target.elts)
                and not any(isinstance(e, ast.Starred) for e in (*target.elts, *value.elts))
            ):
                values = value.elts
            for sub_target, sub_value in zip(target.elts, values):
                self._check_guarded_dict_store(sub_target, sub_value)
            return
        if not isinstance(target, ast.Subscript):
            return
        key = target.slice
        if isinstance(key, ast.Constant) and key.value != "file_path":
            return
        if (
            isinstance(key, ast.Constant)
            and value is not None
            and self._is_owned_value(value)
        ):
            return
        self._forget_all_guarded_dicts()

    def _check_guarded_dict_mutation(self, node: ast.Call, leaves: set[str]) -> None:
        """``d.update(...)`` / ``setdefault`` / ``__setitem__`` on any (possibly
        aliasing) receiver may replace a guarded dict's file_path; so may any
        callee the dict escapes to, other than the persistence sink itself."""
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _DICT_MUTATORS:
            self._forget_all_guarded_dicts()
            return
        if leaves & set(WORK_FILE_PATH_SINKS):
            return
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            # ``mutate(body)``, ``mutate(*(body,))``, ``mutate([body])``, ...:
            # the dict object itself reaches the callee. A read through it
            # (``helper(body.get("title"))``) does not.
            if any(
                any(b[0] == "guarded_dict" for b in _resolve(self.scopes, name))
                for name in _identity_passed_names(arg)
            ):
                # Dict identity is not tracked: ``alias = body; mutate(alias)``
                # may rewrite ``body`` too, so every guarded alias is dirtied.
                self._forget_all_guarded_dicts()
                return

    def _forget_all_guarded_dicts(self) -> None:
        for scope in self._dirtiable_scopes():
            for name, bindings in list(scope.current.items()):
                if any(b[0] == "guarded_dict" for b in bindings):
                    self._mark_dirty(scope, name)

    def _mark_dirty(self, scope: _Scope, name: str) -> None:
        scope.current[name] = scope.current[name] | {_DICT_DIRTY}
        self._dirty_log.append((scope, name))

    def _dirtied_since(self, mark: int) -> set[str]:
        """Names dirtied in the current scope since ``len(self._dirty_log) == mark``."""
        scope = self.scopes[-1]
        return {name for s, name in self._dirty_log[mark:] if s is scope}

    @staticmethod
    def _with_dirty(state: _State, names: set[str]) -> _State:
        state = _copy_state(state)
        for name in names:
            if name in state:
                state[name].add(_DICT_DIRTY)
        return state

    def _active_adoption_guards(self) -> list[_AdoptionGuard]:
        guards: list[_AdoptionGuard] = []
        for scope in reversed(self.scopes):
            guards.extend(scope.adoption_guards)
            # A nested def/lambda/generator body runs later, outside the guard.
            if scope.is_lazy or not scope.is_comprehension:
                break
        return guards

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _report_zip_extractall(self, node: ast.AST) -> None:
        self.findings.append(
            Finding(
                "INV-BACKUP-001",
                self.relpath,
                getattr(node, "lineno", 1),
                (
                    "direct ZipFile.extractall() is forbidden in production PRKS code; "
                    "it trusts archive member names and types. Restore must use the "
                    "validated, staged per-member extraction in backend.backup_restore"
                ),
            )
        )

    def _is_zip_receiver(self, node: ast.expr) -> bool:
        return (
            _is_zip_archive_expr(node, self.scopes)
            or _is_zip_class_expr(node, self.scopes)
            or self._is_archive_super(node)
        )

    def _is_archive_super(self, node: ast.expr) -> bool:
        """``super()`` inside a method of a zipfile archive subclass."""
        if not _is_super_call(node):
            return False
        class_info = _enclosing_class(self.scopes)
        return class_info is not None and class_info.is_archive_class()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Match the attribute itself so method references (``f = zf.extractall``)
        # and unbound calls (``ZipFile.extractall(zf, dest)``) are covered too.
        if node.attr == BANNED_ZIPFILE_METHOD and self._is_zip_receiver(node.value):
            self._report_zip_extractall(node)
        self._check_path_method_reference(node)
        self.generic_visit(node)

    def _check_path_method_reference(self, node: ast.Attribute) -> None:
        """``p.replace`` / ``p.unlink`` / ``p.rename`` (or the class form),
        called now or saved for later."""
        if (
            node.attr == "replace"
            and self.relpath not in OS_REPLACE_ALLOWLIST
            and self._is_path_receiver(node.value)
        ):
            self._report_replace(node, "pathlib.Path.replace()")
        if (
            node.attr in _PATH_REMOVAL_METHODS
            and not _is_path_class_expr(node.value, self.scopes)
            and _PATH in _expr_facts(node.value, self.scopes)
        ):
            # Covers ``p.unlink()`` / ``p.rename(dst)`` and a saved bound method.
            self._check_removal_of(node, f"pathlib.Path.{node.attr}()", node.value)
        if (
            self.relpath == HTTP_ADAPTER_MODULE
            and node.attr in _PATH_WRITE_METHODS
            and not _is_path_class_expr(node.value, self.scopes)
            and _PATH in _expr_facts(node.value, self.scopes)
        ):
            # ``p.write_bytes(data)`` or a saved ``w = p.write_bytes``.
            self._check_adapter_write(node, f"pathlib.Path.{node.attr}()", node.value)

    def _is_path_receiver(self, node: ast.expr) -> bool:
        return _PATH in _expr_facts(node, self.scopes) or _is_path_class_expr(node, self.scopes)

    def _report_replace(self, node: ast.AST, primitive: str) -> None:
        self.findings.append(
            Finding(
                "INV-DURABILITY-001",
                self.relpath,
                getattr(node, "lineno", 1),
                (
                    f"direct {primitive} is outside the approved durability boundary; "
                    "it is the same atomic rename as os.replace and publishes bytes "
                    "without the fsync-before/fsync-after contract. Use "
                    "backend.fs_durability or an existing durable domain helper "
                    "(managed PDFs: backend.services.work_pdf_replace)"
                ),
            )
        )

    # --- managed-PDF ownership boundary -----------------------------------

    def _report(self, code: str, node: ast.AST, message: str) -> None:
        self.findings.append(Finding(code, self.relpath, getattr(node, "lineno", 1), message))

    def _removal_targets(self, node: ast.Call) -> list[tuple[str, ast.expr | None]]:
        """``(primitive, target)`` for os.remove/os.unlink/os.rename/shutil.move/
        shutil.rmtree and unbound Path.unlink/Path.rename calls: the removed or
        moved-away source, plus the destination a rename/move may overwrite."""
        func = node.func
        names = _qualified_names(func, self.scopes)
        primitives = names & RAW_REMOVE_CALLS
        if primitives:
            primitive = f"{sorted(primitives)[0]}()"
            # ``os.remove(path=)`` / ``os.rename(src=, dst=)`` /
            # ``os.renames(old=, new=)`` / ``shutil.move(src=, dst=)``.
            targets = [(primitive, _positional_or_keyword(node, 0, ("path", "src", "old")))]
            if primitives & _OVERWRITING_MOVE_CALLS:
                targets.append((primitive, _positional_or_keyword(node, 1, ("dst", "new"))))
            return targets
        unbound = names & _PATH_UNBOUND_UNLINK
        if unbound:
            # ``Path.unlink(p)`` or an alias of it: the path is the first argument.
            method = sorted(unbound)[0].rsplit(".", 1)[1]
            targets = [(f"pathlib.Path.{method}()", node.args[0] if node.args else None)]
            if method == "rename":
                targets.append((f"pathlib.Path.{method}()", self._call_argument(node, 1, "target")))
            return targets
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "rename"
            and _PATH in _expr_facts(func.value, self.scopes)
        ) or _BOUND_PATH_RENAME in self._callee_bindings(func):
            # ``p.rename(dst)``: the source is checked at the attribute
            # (visit_Attribute); the overwritten destination is checked here.
            return [("pathlib.Path.rename()", self._call_argument(node, 0, "target"))]
        # ``path.unlink`` on a Path value is checked at the attribute itself
        # (visit_Attribute), so a saved bound method is covered too.
        return []

    def _callee_bindings(self, func: ast.expr) -> set[_Binding]:
        """What a bare-name or tracked one-level attribute callee is bound to
        (through a walrus: ``(s := save)(...)``)."""
        func = _unwrap_walrus(func)
        if isinstance(func, ast.Call) and _is_partial_call(func, self.scopes) and func.args:
            # An inline ``partial(helper, *bound)(...)``.
            return _partial_bindings(func, self.scopes)
        if isinstance(func, ast.Name):
            return set(_resolve(self.scopes, func.id))
        key = _attr_key(func) if isinstance(func, ast.Attribute) else None
        return set(_resolve(self.scopes, key)) if key is not None else set()

    def _check_managed_pdf_removal(self, node: ast.Call) -> None:
        for primitive, target in self._removal_targets(node):
            self._check_removal_of(node, primitive, target)

    def _check_removal_of(self, node: ast.AST, primitive: str, target: ast.expr | None) -> None:
        facts = _expr_facts(target, self.scopes)
        if _WEAK in facts:
            self._report_weak_alias(node, f"the target of {primitive}")
        if _MANAGED in facts and self._function not in MANAGED_PDF_REMOVE_CAPABILITIES:
            self._report(
                "INV-STORAGE-002",
                node,
                (
                    f"raw {primitive} of a managed-PDF path (derived from pdfs_dir / "
                    "safe_pdf_path_under_dir) bypasses survivor-aware cleanup: another "
                    "Work may still reference, or be adopting, those bytes. Record a "
                    "pending_pdf_cleanup claim and remove through "
                    "backend.work_deletion (cleanup_released_managed_pdfs / "
                    "_remove_managed_pdf), which re-checks live references under "
                    "managed_pdf_path_lock; roll back a just-minted upload with "
                    "work_pdf_replace.discard_unowned_managed_pdf"
                ),
            )

    def _check_raw_unlink_helper(self, node: ast.Call, leaves: set[str]) -> None:
        for helper in sorted(leaves & set(RAW_MANAGED_PDF_UNLINK_HELPERS)):
            if self._function in RAW_MANAGED_PDF_UNLINK_HELPERS[helper]:
                continue
            self._report(
                "INV-STORAGE-002",
                node,
                (
                    f"{helper}() unlinks a managed PDF without a live-reference "
                    "re-check and is reserved for rolling back bytes the same request "
                    "just minted inside backend.services.work_pdf_replace. Use "
                    "work_pdf_replace.discard_unowned_managed_pdf (locked, "
                    "survivor-checked) or backend.work_deletion cleanup instead"
                ),
            )

    def _file_path_sink_value(
        self, node: ast.Call, leaves: set[str]
    ) -> tuple[str, ast.expr] | None:
        values = self._file_path_sink_values(node, leaves)
        return values[0] if values else None

    def _file_path_sink_values(
        self, node: ast.Call, leaves: set[str]
    ) -> list[tuple[str, ast.expr]]:
        """``(sink, value)`` when the call may persist ``works.file_path``.

        ``value`` is opaque (the ``fields`` dict, a ``**kwargs`` mapping or a
        ``*args`` sequence) when ``file_path`` cannot be singled out. A dict
        literal without that key and without ``**spread`` writes nothing.
        """
        unbound = _is_unbound_method_call(node.func, self.scopes)
        found: list[tuple[str, ast.expr]] = []
        for sink in sorted(leaves & set(WORK_FILE_PATH_SINKS)):
            base, keyword, is_fields = WORK_FILE_PATH_SINKS[sink]
            if unbound and sink != "retarget_work_managed_file_path":
                # ``PRKSDatabase.add_work(db, ...)``: the receiver comes first.
                base += 1
            # ``save = partial(db.add_work, "t")``: positionals shift left; one
            # bound at construction was checked there. A joined binding may
            # pre-bind different counts on different paths: check each.
            for prebound in self._partial_prebound_counts(node.func):
                value = self._call_argument(node, base - prebound, keyword)
                if value is None:
                    continue
                if is_fields and isinstance(value, ast.Dict):
                    value = _dict_file_path_value(value)
                    if value is None:
                        continue
                found.append((sink, value))
        return found

    def _partial_prebound_counts(self, func: ast.expr) -> list[int]:
        """Positionals a ``functools.partial`` bound to ``func`` may pre-supply."""
        # A bare name or a tracked one-level attribute (``self.save = partial(...)``).
        counts = {b[1] for b in self._callee_bindings(func) if b[0] == "partial"}
        return sorted(counts) if counts else [0]

    @staticmethod
    def _call_argument(node: ast.Call, index: int, keyword: str) -> ast.expr | None:
        """The expression a call binds to parameter ``keyword`` (at ``index``).

        A negative ``index`` means a partial already bound that position, so
        only an explicit keyword can name it; otherwise the resolution order
        of ``_positional_or_keyword`` applies (a dict-literal ``**`` naming the
        parameter is as decisive as ``keyword=`` and precedes ``*args``)."""
        if index < 0:
            return next((kw.value for kw in node.keywords if kw.arg == keyword), None)
        return _positional_or_keyword(node, index, (keyword,))

    def _is_owned_value(self, value: ast.expr, *, under_guard: bool = True) -> bool:
        """A clear / non-managed literal, minted bytes, or a value an active
        adoption guard validated (its input, its yielded basename, or the
        fields dict whose ``file_path`` it read). Every branch of a
        conditional / short-circuit value must qualify on its own.
        ``under_guard=False`` asks whether it is owned without any guard."""
        branches = _branch_values(value)
        if branches is not None and not isinstance(value, (ast.NamedExpr, ast.Await)):
            return all(self._is_owned_value(branch, under_guard=under_guard) for branch in branches)
        if isinstance(value, ast.Constant):
            return not (
                isinstance(value.value, str) and value.value.strip().startswith("/api/pdfs")
            )
        active = {g.key for g in self._active_adoption_guards()} if under_guard else set()
        if isinstance(value, ast.Name):
            # Every reaching binding must be this guarded dict: after
            # ``if c: body = other`` the joined value may be ``other``.
            identities = [b for b in _resolve(self.scopes, value.id) if b[0] not in _FACT_TAGS]
            if identities and all(
                b[0] == "guarded_dict" and b[1] in active for b in identities
            ):
                return True
        owned = _proven_owned(value, self.scopes)
        if owned is None:
            return False
        return all(tag[0] == "minted" or tag[1] in active for tag in owned)

    def _check_file_path_write(self, node: ast.Call, leaves: set[str]) -> None:
        values = self._file_path_sink_values(node, leaves)
        weak = next((sink for sink, value in values if _WEAK in _expr_facts(value, self.scopes)), None)
        if weak is not None:
            self._report_weak_alias(node, f"the works.file_path written by {weak}()")
        if self._function not in WORK_FILE_PATH_CAPABILITIES:
            unowned = next((sink for sink, value in values if not self._is_owned_value(value)), None)
            if unowned is not None:
                self._report_adoption(node, f"{unowned}()")
        if leaves & _SQL_EXECUTE_METHODS:
            self._check_sql_write(node)

    def _check_sql_write(self, node: ast.Call) -> None:
        """Raw SQL writing works.file_path / pending_pdf_cleanup."""
        query = self._sql_argument(node)
        if query is not None:
            sql = _expr_facts(query, self.scopes)
            params = set().union(
                *(_expr_facts(arg, self.scopes) for arg in node.args if arg is not query),
                *(_expr_facts(kw.value, self.scopes) for kw in node.keywords if kw.value is not query),
            )
            targets = {f[1] for f in sql if f[0] == "sql_write"}
            # A weak alias interpolated into the SQL text is as much an
            # authority as one passed as a bound parameter.
            if targets and _WEAK in (params | sql):
                self._report_weak_alias(node, f"a SQL write to {', '.join(sorted(targets))}")
            # Raw SQL cannot say which parameter is file_path, so it is
            # confined to the persistence primitives even under the guard.
            if "works.file_path" in targets and self._function not in WORK_FILE_PATH_CAPABILITIES:
                self._report_adoption(node, "raw SQL writing works.file_path")

    def _sql_argument(self, node: ast.Call) -> ast.expr | None:
        """The SQL text: first positional (after the receiver of an unbound
        ``Connection.execute(conn, sql)``), or ``query=`` / ``sql=``."""
        index = 1 if _is_unbound_method_call(node.func, self.scopes) else 0
        if len(node.args) > index:
            return node.args[index]
        return next((kw.value for kw in node.keywords if kw.arg in _SQL_TEXT_KEYWORDS), None)

    def _report_adoption(self, node: ast.AST, what: str) -> None:
        self._report(
            "INV-STORAGE-003",
            node,
            (
                f"{what} persists a works.file_path that is neither proven freshly "
                "minted nor the value an entered managed_pdf_adoption_guard validated. "
                "Claiming an EXISTING /api/pdfs/<name> must hold that guard "
                "(backend.services.work_pdf_replace.managed_pdf_adoption_guard: it "
                "takes managed_pdf_path_lock, re-checks the bytes still exist and "
                "yields the exact ownership basename to persist), or post-delete "
                "cleanup can unlink the bytes the new row now points at. New uploads "
                "persist the name returned by store_new_managed_pdf_bytes/_from_path"
            ),
        )

    def _report_weak_alias(self, node: ast.AST, authority: str) -> None:
        self._report(
            "INV-STORAGE-004",
            node,
            (
                f"a referenced_managed_pdf_filename() result reaches {authority}. "
                "That helper is a weak, fail-closed over-approximation (it maps "
                "traversal, nested and %2F spellings the PDF route cannot serve) and "
                "may only BLOCK or defer a delete / reject a path. Ownership, "
                "adoption, cleanup claims and deletion must use the strong "
                "identities: managed_pdf_filename / owned_managed_pdf_basename / "
                "managed_basenames_protected_by / row_strongly_references_managed_pdf"
            ),
        )

    def _flatten_partial(
        self,
        func: ast.expr,
        args: list[ast.expr],
        keywords: list[ast.keyword],
        at: ast.AST,
    ) -> ast.Call:
        """The call ``partial(partial(helper, a), b)(c)`` amounts to:
        ``helper(a, b, c)``. Inner partials are recorded so their own
        construction is not checked a second time."""
        func = _unwrap_walrus(func)
        while isinstance(func, ast.Call) and _is_partial_call(func, self.scopes) and func.args:
            self._flattened_partials.add(id(func))
            func, args, keywords = (
                _unwrap_walrus(func.args[0]),
                [*func.args[1:], *args],
                [*func.keywords, *keywords],
            )
        # As in ``functools.partial``, the outermost binding of a keyword wins.
        named = {kw.arg: kw for kw in keywords if kw.arg is not None}
        keywords = [kw for kw in keywords if kw.arg is None or named[kw.arg] is kw]
        return ast.copy_location(ast.Call(func=func, args=args, keywords=keywords), at)

    def _effective_call(self, node: ast.Call) -> ast.Call:
        """An immediately-invoked ``partial(fn, *bound)(*args)`` is checked as
        ``fn(*bound, *args)``; any other call as itself."""
        callee = _unwrap_walrus(node.func)
        if isinstance(callee, ast.Call) and _is_partial_call(callee, self.scopes) and callee.args:
            return self._flatten_partial(callee, list(node.args), list(node.keywords), node)
        return node

    def _check_managed_pdf_boundary(self, node: ast.Call) -> None:
        if id(node) in self._flattened_partials:
            return
        if _is_partial_call(node, self.scopes) and node.args:
            # ``partial(helper, *bound, **bound_kw)`` is checked as the call it
            # prepares; arguments supplied at invocation are checked there.
            prepared = self._flatten_partial(
                node.args[0], node.args[1:], list(node.keywords), node
            )
            self._check_managed_pdf_boundary(prepared)
            self._check_partial_bound_file_path(prepared)
            return
        self._check_http_adapter_boundary(node)
        leaves = _callee_leaf_names(node.func, self.scopes)
        self._check_guarded_dict_mutation(node, leaves)
        self._check_managed_pdf_removal(node)
        self._check_raw_unlink_helper(node, leaves)
        self._check_file_path_write(node, leaves)
        for sink in sorted(leaves & WEAK_ALIAS_AUTHORITY_SINKS):
            if _WEAK in _call_argument_facts(node, self.scopes):
                self._report_weak_alias(node, f"{sink}()")

    # --- HTTP adapter boundary (INV-ADAPTER-001/002) ------------------------

    def _check_http_adapter_boundary(self, node: ast.Call) -> None:
        """Storage creation and process spawning in ``backend/server.py``.

        Called for every effective call and for the call a ``partial(...)``
        prepares, so aliases, ``getattr`` spellings and partials resolve
        exactly as they do for the INV-STORAGE rules."""
        if self.relpath != HTTP_ADAPTER_MODULE:
            return
        func = _unwrap_walrus(node.func)
        names = _qualified_names(func, self.scopes)
        if _is_builtin(func, self.scopes, "open"):
            names = names | {"builtins.open"}
        spawned = _process_primitives(names)
        if spawned:
            self._report_adapter_process(node, f"{spawned[0]}()")
        for value in [*node.args, *(kw.value for kw in node.keywords)]:
            # ``executor.submit(os.system, cmd)`` / ``callback=getattr(os, "popen")``:
            # a process callable handed to another API still runs from here.
            value = _unwrap_walrus(value.value if isinstance(value, ast.Starred) else value)
            passed = _process_primitives(_qualified_names(value, self.scopes))
            if passed:
                self._report_adapter_process(value, f"{passed[0]} passed as a callable")
        for primitive, exclusive, writes, target in self._adapter_creations(node, func, names):
            if exclusive:
                self._report_adapter_exclusive_create(node, primitive)
            elif writes:
                self._check_adapter_write(node, primitive, target)

    def _adapter_creations(
        self, node: ast.Call, func: ast.expr, names: set[str]
    ) -> list[tuple[str, bool, bool, ast.expr | None]]:
        """``(primitive, may create exclusively, may write, target path)`` for
        each file-creating primitive the call may be. A position a partial
        pre-bound was checked where the partial was built."""
        found: list[tuple[str, bool, bool, ast.expr | None]] = []
        prebound_counts = self._partial_prebound_counts(node.func)
        arg = self._call_argument
        for pre in prebound_counts:
            if names & _OPEN_CALLS:
                exclusive, writes = _open_mode_effect(arg(node, 1 - pre, "mode"))
                found.append(("open()", exclusive, writes, arg(node, -pre, "file")))
            if "os.open" in names:
                exclusive, writes = _os_open_flags_effect(arg(node, 1 - pre, "flags"), self.scopes)
                found.append(("os.open()", exclusive, writes, arg(node, -pre, "path")))
            for creator in sorted(names & set(_TEMPFILE_DIR_ARG)):
                directory = arg(node, _TEMPFILE_DIR_ARG[creator] - pre, "dir")
                found.append((f"{creator}()", False, True, directory))
            for unbound in sorted(names & _PATH_UNBOUND_CREATE):
                # ``Path.open(p, "xb")``: the receiver is the first argument.
                method = unbound.rsplit(".", 1)[1]
                effect = self._path_create_effect(node, method, 1 - pre)
                found.append((f"pathlib.Path.{method}()", *effect, arg(node, -pre, "self")))
        bound = _getattr_as_attribute(func, self.scopes) or func
        if (
            isinstance(bound, ast.Attribute)
            and bound.attr in _PATH_OPEN_METHODS
            and not _is_path_class_expr(bound.value, self.scopes)
            and _PATH in _expr_facts(bound.value, self.scopes)
        ):
            effect = self._path_create_effect(node, bound.attr, 0)
            found.append((f"pathlib.Path.{bound.attr}()", *effect, bound.value))
        return found

    def _path_create_effect(self, node: ast.Call, method: str, base: int) -> tuple[bool, bool]:
        """``Path.open(mode)`` follows ``open``; ``Path.touch`` creates, and
        exclusively unless ``exist_ok`` is left at / set to ``True``;
        ``write_bytes`` / ``write_text`` write."""
        if method == "open":
            return _open_mode_effect(self._call_argument(node, base, "mode"))
        if method == "touch":
            exist_ok = self._call_argument(node, base + 1, "exist_ok")
            exclusive = exist_ok is not None and not (
                isinstance(exist_ok, ast.Constant) and exist_ok.value is True
            )
            return exclusive, True
        return False, True

    def _check_adapter_write(self, node: ast.AST, primitive: str, target: ast.expr | None) -> None:
        if _MANAGED not in _expr_facts(target, self.scopes):
            return
        self._report(
            "INV-ADAPTER-001",
            node,
            (
                f"{primitive} writes into a managed-PDF path (derived from pdfs_dir / "
                "safe_pdf_path_under_dir) from the HTTP adapter. backend/server.py must "
                "not store managed-PDF bytes itself: that skips the exclusive name "
                "claim, fsync-before-publish and rollback of unowned bytes. Store or "
                "replace through backend.services.work_pdf_replace "
                "(store_new_managed_pdf_bytes / store_new_managed_pdf_from_path / "
                "replace_managed_work_pdf)"
            ),
        )

    def _report_adapter_exclusive_create(self, node: ast.AST, primitive: str) -> None:
        self._report(
            "INV-ADAPTER-001",
            node,
            (
                f"{primitive} may create a file exclusively (open mode 'x', O_EXCL, "
                "touch(exist_ok=False), or a mode/flags value that is not a literal) "
                "in the HTTP adapter. An exclusive create claims a filename as storage "
                "authority, which backend/server.py must not own. Managed PDFs: "
                "backend.services.work_pdf_replace (store_new_managed_pdf_bytes / "
                "store_new_managed_pdf_from_path / allocate_exclusive_managed_filename); "
                "other storage: a focused backend module (e.g. "
                "backend.derived_cache_publish)"
            ),
        )

    def _report_adapter_process(self, node: ast.AST, what: str) -> None:
        self._report(
            "INV-ADAPTER-002",
            node,
            (
                f"{what} orchestrates an external process from the HTTP adapter. "
                "backend/server.py must not run qpdf or any other subprocess: PDF "
                "linearization belongs in backend.pdf_linearize (call "
                "maybe_linearize_pdf_in_place / is_pdf_linearized); other external "
                "tools belong in a focused backend module"
            ),
        )

    def _check_adapter_process_import(self, node: ast.Import | ast.ImportFrom) -> None:
        """``import subprocess`` / ``from subprocess import run`` in the adapter,
        which also covers passing a subprocess callable along uncalled."""
        if self.relpath != HTTP_ADAPTER_MODULE:
            return
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif node.level:
            return
        else:
            module = node.module or ""
            modules = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        for module in modules:
            if module in _PROCESS_MODULES or module.startswith("subprocess."):
                self._report_adapter_process(node, f"import of {module}")
                return

    def _check_partial_bound_file_path(self, prepared: ast.Call) -> None:
        """A partial may be invoked after the guard exits, so a pre-bound
        ``file_path`` owned only through that guard is not proven locked
        where the row is actually written."""
        leaves = _callee_leaf_names(prepared.func, self.scopes)
        sink_value = self._file_path_sink_value(prepared, leaves)
        if sink_value is None or self._function in WORK_FILE_PATH_CAPABILITIES:
            return
        sink, value = sink_value
        if self._is_owned_value(value) and not self._is_owned_value(value, under_guard=False):
            self._report_adoption(
                prepared,
                f"functools.partial({sink}, ...) pre-binding a guard-validated file_path "
                "(it may run after the guard exits; call the sink inside the guard)",
            )

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == BANNED_ZIPFILE_METHOD
            and self._is_zip_receiver(node.args[0])
        ):
            self._report_zip_extractall(node)
        as_attribute = _getattr_as_attribute(node, self.scopes)
        if as_attribute is not None:
            # ``getattr(p, "unlink")`` / ``getattr(Path, "replace")`` are the
            # same method references as ``p.unlink`` / ``Path.replace``.
            self._check_path_method_reference(as_attribute)
        effective = self._effective_call(node)
        for module, name in _call_identities(effective, self.scopes):
            if module == "shutil" and name in BANNED_SHUTIL_COPY_CALLS:
                self.findings.append(
                    Finding(
                        "INV-STORAGE-001",
                        self.relpath,
                        node.lineno,
                        (
                            f"direct shutil.{name}() is forbidden in backend production code; "
                            "publish managed PDFs through backend.services.work_pdf_replace "
                            "(store_new_managed_pdf_bytes/store_new_managed_pdf_from_path), "
                            "or use a domain-specific storage capability"
                        ),
                    )
                )
            elif module == "os" and name == "replace" and self.relpath not in OS_REPLACE_ALLOWLIST:
                self._report_replace(node, "os.replace()")
            elif module == "os" and name == "fsync" and self.relpath not in OS_FSYNC_ALLOWLIST:
                self.findings.append(
                    Finding(
                        "INV-DURABILITY-002",
                        self.relpath,
                        node.lineno,
                        (
                            "direct os.fsync() is outside the approved durability boundary; "
                            "use backend.fs_durability helpers"
                        ),
                    )
                )
        self._check_managed_pdf_boundary(effective)
        self.generic_visit(node)


def check_source(source: str, relpath: str) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as exc:
        return [
            Finding(
                "INV-PARSE-001",
                relpath,
                int(exc.lineno or 1),
                "could not parse file while checking engineering invariants",
            )
        ]

    # Facts about same-module defs (helpers returning archives, archive
    # attributes, archive subclasses) can be used before the def is reached,
    # so re-run the walk until they stop changing; they only ever grow.
    class_registry: dict[int, _ClassInfo] = {}
    function_registry: dict[int, _FuncInfo] = {}
    previous: object = None
    for _ in range(_MAX_ANALYSIS_PASSES):
        visitor = _InvariantVisitor(relpath, class_registry, function_registry)
        visitor.visit(tree)
        snapshot = _registry_snapshot(class_registry, function_registry)
        if snapshot == previous:
            break
        previous = snapshot
    return visitor.findings


_MAX_ANALYSIS_PASSES = 10


def _registry_snapshot(
    class_registry: dict[int, _ClassInfo], function_registry: dict[int, _FuncInfo]
) -> object:
    return (
        frozenset((key, info.returns_archive) for key, info in function_registry.items()),
        frozenset(
            (key, frozenset(info.archive_attrs), info.zip_base)
            for key, info in class_registry.items()
        ),
    )


def iter_production_python(root: Path) -> Iterable[Path]:
    """Yield production Python paths the invariant checker must cover.

    Includes the process entry ``prks_app.py`` (same set Ruff checks) plus
    every file under ``backend/``. Scripts and tests are out of scope.
    """
    app = root / "prks_app.py"
    if app.is_file():
        yield app
    backend = root / "backend"
    yield from sorted(p for p in backend.rglob("*.py") if p.is_file())


# Back-compat alias for earlier call sites / imports.
iter_backend_python = iter_production_python


def check_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_production_python(root):
        rel = path.relative_to(root).as_posix()
        findings.extend(check_source(path.read_text(encoding="utf-8"), rel))
    return findings


def _load_json_object(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _require_diagnostic_errors(cfg: dict, relpath: str) -> list[Finding]:
    findings: list[Finding] = []
    for key in PYRIGHT_REQUIRED_DIAGNOSTICS:
        if cfg.get(key) != "error":
            findings.append(
                Finding(
                    "INV-PYRIGHT-001",
                    relpath,
                    1,
                    f"{key} must remain \"error\" (found {cfg.get(key)!r})",
                )
            )
    return findings


def _normalize_pyright_path(entry: object) -> str:
    return str(entry).replace("\\", "/").rstrip("/")


def _normalize_pyright_glob_pattern(pattern: str) -> str | None:
    """Drop empty/``.`` segments and resolve ``..`` against a preceding literal.

    Returns ``None`` when ``..`` cannot be resolved (no preceding literal, or
    the preceding segment is a glob such as ``**``). Callers treat ``None`` as
    fail-closed: the entry covers the protected root.
    """
    text = pattern.replace("\\", "/").strip()
    out: list[str] = []
    for segment in text.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if not out:
                return None
            prev = out[-1]
            if prev == "**" or any(ch in prev for ch in "*?["):
                return None
            out.pop()
            continue
        out.append(segment)
    return "/".join(out)


def _is_allowed_cache_exclude(entry: object) -> bool:
    raw = str(entry).replace("\\", "/").strip()
    if raw in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES:
        return True
    normalized = _normalize_pyright_glob_pattern(raw)
    if normalized is None:
        return False
    allowed = {
        _normalize_pyright_path(item) for item in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES
    }
    return _normalize_pyright_path(normalized) in allowed


def _is_allowed_typed_slice_exclude(entry: object) -> bool:
    return _is_allowed_cache_exclude(entry)


def _pyright_glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against a Pyright/gitignore-style glob.

    ``**`` matches zero or more directories (unlike stdlib ``fnmatch``, where
    mid-path ``**`` does not consume an empty directory span).
    ``*`` and ``?`` match within a single path segment.
    """
    path = path.replace("\\", "/").strip("/")
    pattern = pattern.replace("\\", "/").strip("/")
    if pattern in {"", "**"}:
        return True
    path_parts = path.split("/") if path else []
    pat_parts = pattern.split("/") if pattern else []

    def match_from(pi: int, pti: int) -> bool:
        while pti < len(pat_parts):
            token = pat_parts[pti]
            if token == "**":
                # Zero-or-more directories: try consuming nothing, then 1..N parts.
                if pti == len(pat_parts) - 1:
                    return True
                for skip in range(pi, len(path_parts) + 1):
                    if match_from(skip, pti + 1):
                        return True
                return False
            if pi >= len(path_parts):
                return False
            if not fnmatch.fnmatchcase(path_parts[pi], token):
                return False
            pi += 1
            pti += 1
        return pi == len(path_parts)

    return match_from(0, 0)


def _literal_prefix_before_glob(pattern: str) -> str:
    """Path segments before the first glob token (``*``, ``?``, ``**``, ``[…]``)."""
    parts: list[str] = []
    for segment in pattern.replace("\\", "/").strip("/").split("/"):
        if not segment:
            continue
        if segment == "**" or "*" in segment or "?" in segment or "[" in segment:
            break
        parts.append(segment)
    return "/".join(parts)


def _glob_overlaps_typed_root(pattern: str, root: str) -> bool:
    """True if ``pattern`` can match ``root``, an ancestor, or any path under it."""
    root_parts = root.replace("\\", "/").strip("/").split("/")
    pat_parts = [p for p in pattern.replace("\\", "/").strip("/").split("/") if p]
    if not pat_parts:
        return True

    def dfs(pti: int, ri: int) -> bool:
        if pti == len(pat_parts):
            # Pattern exhausted on an ancestor or the root itself.
            return ri <= len(root_parts)

        token = pat_parts[pti]
        if token == "**":
            if pti == len(pat_parts) - 1:
                return True
            for skip in range(ri, len(root_parts) + 1):
                if dfs(pti + 1, skip):
                    return True
            # Remaining tokens can match invented descendants under root.
            return True

        if ri < len(root_parts):
            if fnmatch.fnmatchcase(root_parts[ri], token):
                return dfs(pti + 1, ri + 1)
            return False

        # Past the root: any further pattern segments match some descendant.
        return True

    return dfs(0, 0)


def _path_covers_root(entry: object, root: str) -> bool:
    """True when ignore/exclude can match ``root`` or anything under it.

    Dot-segments are normalized first (``.`` dropped; ``..`` resolved against a
    preceding literal). Unresolvable ``..`` (``../x``, ``**/..``) fails closed.
    Cache-only ``__pycache__`` excludes are allowlisted.
    """
    if _is_allowed_cache_exclude(entry):
        return False
    raw = str(entry).replace("\\", "/").strip()
    normalized = _normalize_pyright_glob_pattern(raw)
    if normalized is None:
        return True
    pattern = normalized.rstrip("/")
    if pattern in {"", ".", "*", "**", "**/*", "**/**"}:
        return True

    lit = _literal_prefix_before_glob(pattern)
    # Clearly rooted at or under the protected root (globs may follow).
    if lit == root or lit.startswith(root + "/"):
        return True
    # Literal-only parent of the root (no glob metacharacters anywhere).
    if lit and not any(ch in pattern for ch in "*?["):
        if root.startswith(lit + "/"):
            return True

    return _glob_overlaps_typed_root(pattern, root)


def _path_covers_typed_slice(entry: object) -> bool:
    """True when ignore/exclude can match ``backend/storage`` or anything under it."""
    return _path_covers_root(entry, PYRIGHT_TYPED_SLICE_ROOT)


def _reject_ignore_exclude_covering(
    cfg: dict,
    *,
    protected_root: str,
    config_name: str,
    code: str,
) -> list[Finding]:
    """Reject ignore/exclude entries that would silence ``protected_root``."""
    findings: list[Finding] = []
    for key in ("ignore", "exclude"):
        value = cfg.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            findings.append(
                Finding(
                    code,
                    config_name,
                    1,
                    f"{config_name} {key} must be a list when present (found {type(value).__name__})",
                )
            )
            continue
        for entry in value:
            if _path_covers_root(entry, protected_root):
                findings.append(
                    Finding(
                        code,
                        config_name,
                        1,
                        (
                            f"{config_name} {key} entry {entry!r} would suppress "
                            f"{protected_root}; only __pycache__ exclusions are allowed"
                        ),
                    )
                )
    return findings


def _reject_typed_slice_suppression(cfg: dict) -> list[Finding]:
    """Reject ignore/exclude entries that would silence the typed slice."""
    return _reject_ignore_exclude_covering(
        cfg,
        protected_root=PYRIGHT_TYPED_SLICE_ROOT,
        config_name=PYRIGHT_TYPED_SLICE_CONFIG,
        code="INV-PYRIGHT-002",
    )


def _workflow_run_scripts(workflow_text: str) -> list[str]:
    """Collect executable ``run:`` script bodies (not YAML ``#`` comments).

    Dependency-free: Fast Static Analysis runs this checker without PyYAML.
    Handles single-line ``run:`` and block scalars (``|`` / ``>``).
    """
    scripts: list[str] = []
    lines = workflow_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if raw.lstrip().startswith("#"):
            i += 1
            continue
        match = _WORKFLOW_RUN_KEY_RE.match(raw)
        if match is None:
            i += 1
            continue
        indent = len(match.group(1))
        rest = match.group(2).rstrip()
        block = rest in {"", "|", ">", "|-", ">-", "|+", ">+"} or rest.startswith(("|", ">"))
        if not block:
            scripts.append(rest)
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() == "":
                body.append("")
                i += 1
                continue
            content_indent = len(nxt) - len(nxt.lstrip(" "))
            if content_indent <= indent:
                break
            body.append(nxt)
            i += 1
        scripts.append("\n".join(body))
    return scripts


def _strip_shell_comment_lines(script: str) -> str:
    kept: list[str] = []
    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _shell_chunk_invokes_pyright(chunk: str) -> bool:
    """True when ``pyright`` is an invoked command, not text inside ``echo``."""
    # Drop quoted strings so ``echo "pyright --project X"`` does not count.
    unquoted = re.sub(r'"[^"]*"', ' "" ', chunk)
    unquoted = re.sub(r"'[^']*'", " '' ", unquoted)
    tokens = unquoted.split()
    if not tokens:
        return False
    head = tokens[0].rsplit("/", 1)[-1]
    if head in {"echo", "printf", "cat"}:
        return False
    for index, token in enumerate(tokens):
        name = token.rsplit("/", 1)[-1]
        if name != "pyright":
            continue
        if index == 0:
            return True
        # npm/npx exec … -- pyright  (or similar package runners)
        if head in {"npm", "npx", "yarn", "pnpm"} and "--" in tokens[:index]:
            return True
        if tokens[index - 1] in {"--", "time", "command", "exec", "env"}:
            return True
    return False


def _executable_pyright_projects(workflow_text: str) -> set[str]:
    """Project paths from real ``pyright --project`` invocations in ``run`` steps."""
    projects: set[str] = set()
    for script in _workflow_run_scripts(workflow_text):
        cleaned = _strip_shell_comment_lines(script)
        for chunk in re.split(r"[;\n|&]+", cleaned):
            chunk = chunk.strip()
            if not chunk or not _shell_chunk_invokes_pyright(chunk):
                continue
            for match in _PYRIGHT_PROJECT_ARG_RE.finditer(chunk):
                projects.add(match.group("path"))
    return projects


def check_pyright_configs(root: Path = REPO_ROOT) -> list[Finding]:
    """Keep the #69 typed slice from silently becoming effectively-off.

    The data-flow config may stay on ``typeCheckingMode: off`` (narrow
    diagnostics only) but must still include ``backend``. The typed-slice
    config must enable genuine analysis for ``backend/storage`` without
    ignore/exclude suppression, and CI must execute ``pyright --project``
    for both configs (filename mentions in comments do not count).
    """
    findings: list[Finding] = []

    dataflow_path = root / PYRIGHT_DATAFLOW_CONFIG
    if not dataflow_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-001",
                PYRIGHT_DATAFLOW_CONFIG,
                1,
                "missing Pyright data-flow config",
            )
        )
    else:
        dataflow = _load_json_object(dataflow_path)
        if dataflow is None:
            findings.append(
                Finding(
                    "INV-PYRIGHT-001",
                    PYRIGHT_DATAFLOW_CONFIG,
                    1,
                    "Pyright data-flow config is not a JSON object",
                )
            )
        else:
            findings.extend(_require_diagnostic_errors(dataflow, PYRIGHT_DATAFLOW_CONFIG))
            include = dataflow.get("include")
            include_paths = (
                {_normalize_pyright_path(item) for item in include}
                if isinstance(include, list)
                else set()
            )
            if PYRIGHT_DATAFLOW_REQUIRED_INCLUDE not in include_paths:
                findings.append(
                    Finding(
                        "INV-PYRIGHT-001",
                        PYRIGHT_DATAFLOW_CONFIG,
                        1,
                        (
                            "data-flow include must contain "
                            f"{PYRIGHT_DATAFLOW_REQUIRED_INCLUDE!r} "
                            f"(found {sorted(include_paths)!r})"
                        ),
                    )
                )
            findings.extend(
                _reject_ignore_exclude_covering(
                    dataflow,
                    protected_root=PYRIGHT_DATAFLOW_REQUIRED_INCLUDE,
                    config_name=PYRIGHT_DATAFLOW_CONFIG,
                    code="INV-PYRIGHT-001",
                )
            )

    typed_path = root / PYRIGHT_TYPED_SLICE_CONFIG
    if not typed_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "missing Pyright typed-slice config (first #69 scope)",
            )
        )
        return findings

    typed = _load_json_object(typed_path)
    if typed is None:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "Pyright typed-slice config is not a JSON object",
            )
        )
        return findings

    findings.extend(_require_diagnostic_errors(typed, PYRIGHT_TYPED_SLICE_CONFIG))

    mode = typed.get("typeCheckingMode")
    if mode not in PYRIGHT_TYPED_SLICE_MODES:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                (
                    "typed-slice typeCheckingMode must be one of "
                    f"{sorted(PYRIGHT_TYPED_SLICE_MODES)} "
                    f"(found {mode!r}); off would silently disable real type analysis"
                ),
            )
        )

    include = typed.get("include")
    if not isinstance(include, list) or not include:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "typed-slice include must be a non-empty list",
            )
        )
    else:
        normalized = tuple(_normalize_pyright_path(item) for item in include)
        if normalized != PYRIGHT_TYPED_SLICE_INCLUDE:
            findings.append(
                Finding(
                    "INV-PYRIGHT-002",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        "typed-slice include must be exactly "
                        f"{list(PYRIGHT_TYPED_SLICE_INCLUDE)} "
                        f"(found {list(normalized)!r}); expand only in a focused follow-up PR"
                    ),
                )
            )

    findings.extend(_reject_typed_slice_suppression(typed))

    workflow_path = root / STATIC_ANALYSIS_WORKFLOW
    if not workflow_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-003",
                STATIC_ANALYSIS_WORKFLOW,
                1,
                "missing Fast Static Analysis workflow",
            )
        )
    else:
        projects = _executable_pyright_projects(workflow_path.read_text(encoding="utf-8"))
        if PYRIGHT_TYPED_SLICE_CONFIG not in projects:
            findings.append(
                Finding(
                    "INV-PYRIGHT-003",
                    STATIC_ANALYSIS_WORKFLOW,
                    1,
                    (
                        f"workflow must execute pyright --project {PYRIGHT_TYPED_SLICE_CONFIG} "
                        "in a run step (comments / filename mentions do not count)"
                    ),
                )
            )
        if PYRIGHT_DATAFLOW_CONFIG not in projects:
            findings.append(
                Finding(
                    "INV-PYRIGHT-003",
                    STATIC_ANALYSIS_WORKFLOW,
                    1,
                    (
                        f"workflow must execute pyright --project {PYRIGHT_DATAFLOW_CONFIG} "
                        "in a run step (comments / filename mentions do not count)"
                    ),
                )
            )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to the checker script's parent repository)",
    )
    args = parser.parse_args(argv)

    findings = check_repo(args.root.resolve())
    findings.extend(check_pyright_configs(args.root.resolve()))
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"engineering invariant check failed: {len(findings)} violation(s)")
        return 1

    print("engineering invariant check: OK")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())