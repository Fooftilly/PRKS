"""Capability boundaries and allowlists the code-invariant rules enforce.

INV-DURABILITY-*, INV-BACKUP-001, INV-STORAGE-* and INV-ADAPTER-* name
the reviewed homes of each low-level primitive here; ``code_rules`` reports
any use outside them.
"""
from __future__ import annotations

import re


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
    "backend/media/derived_cache_publish.py",
    "backend/storage/fs_durability.py",
    "backend/pdf/pdf_linearize.py",
    "backend/pdf/work_pdf_replace.py",
    # The StorageBackend's local publication boundary (storage-architecture
    # §10.3): put_new/replace are fs_durability's fsync-before/sync-after
    # contract, the same one work_pdf_replace implements today.
    "backend/storage/objects.py",
    # The V6 capability probe must exercise os.replace itself; it renames only
    # its own probe files inside .prks-maintenance/preflight/.
    "backend/storage/preflight.py",
}
# Bare os.fsync belongs only in fs_durability. Managed-PDF code must use
# fsync_open_file / fsync_directory — work_pdf_replace is not an fsync island.
OS_FSYNC_ALLOWLIST = {
    "backend/storage/fs_durability.py",
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
    MANAGED_PDF_ADOPTION_GUARD: "backend/pdf/work_pdf_replace.py",
    "allocate_exclusive_managed_filename": "backend/pdf/work_pdf_replace.py",
    "store_new_managed_pdf_bytes": "backend/pdf/work_pdf_replace.py",
    "store_new_managed_pdf_from_path": "backend/pdf/work_pdf_replace.py",
    "mint_managed_pdf_filename": "backend/pdf/managed_pdf_names.py",
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
    ("backend/pdf/work_pdf_replace.py", "unlink_managed_pdf_best_effort"): (
        "self-minted rollback"
    ),
}
# Raw managed-PDF unlink helpers without a survivor re-check: callable only
# where the name is bytes this request minted and nothing else can reference.
RAW_MANAGED_PDF_UNLINK_HELPERS: dict[str, frozenset[tuple[str, str]]] = {
    "unlink_managed_pdf_best_effort": frozenset(
        {
            # Partial exclusive create that never reported its name.
            ("backend/pdf/work_pdf_replace.py", "_exclusive_create_write_and_fsync"),
            # Holds managed_pdf_path_lock and re-checks live references first.
            ("backend/pdf/work_pdf_replace.py", "discard_unowned_managed_pdf"),
            # COW exclusive bytes whose retarget never committed.
            ("backend/pdf/work_pdf_replace.py", "replace_managed_work_pdf"),
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
    ("backend/pdf/work_pdf_replace.py", "retarget_work_managed_file_path"): (
        "retarget primitive"
    ),
    # Copy-on-write retargets the Work to an exclusive name it just minted
    # (allocate_exclusive_managed_filename) while holding that name's lock.
    # The retarget runs behind a ``cow_retarget`` flag the flow analysis
    # cannot correlate with the minting branch, hence the function exemption.
    ("backend/pdf/work_pdf_replace.py", "replace_managed_work_pdf"): "COW retarget",
    # The PostgreSQL Library adopts an existing file inside the create's own
    # transaction (PostgresLibrary.add_work); its cleanup settle re-checks
    # every Asset under the per-object lock (§7.4, §11.2), so the SQLite
    # basename lock has nothing to protect there.
    ("backend/server.py", "_create_postgres_work"): "PostgreSQL create",
    # The PostgreSQL Work PATCH drops file_path before the write, and the
    # Library refuses one (NotPortedError): nothing existing is adopted.
    ("backend/server.py", "_update_postgres_work_metadata"): "PostgreSQL PATCH, no file_path",
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
# The adapter is ``backend/server.py`` plus the HTTP layer (``backend/web``)
# and the route families (``backend/api``, #1424).
HTTP_ADAPTER_PACKAGES = ("backend/web/", "backend/api/")


def is_http_adapter_module(relpath: str) -> bool:
    """Whether ``relpath`` is part of the HTTP adapter (INV-ADAPTER-001/002)."""
    return relpath == HTTP_ADAPTER_MODULE or relpath.startswith(HTTP_ADAPTER_PACKAGES)
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
# Process creation. ``subprocess`` is banned as a module (import); its
# launchers are listed explicitly so constants such as ``subprocess.PIPE``
# passed as arguments are not mistaken for process callables. Only the
# reviewed ``multiprocessing`` launchers are listed: importing the module
# itself is not process creation.
_PROCESS_MODULES = frozenset({"asyncio.subprocess", "subprocess"})
_PROCESS_CALL_PREFIXES = ("os.exec", "os.posix_spawn", "os.spawn")
_PROCESS_CALLS = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "asyncio.subprocess.create_subprocess_exec",
        "asyncio.subprocess.create_subprocess_shell",
        "multiprocessing.Pool",
        "multiprocessing.Process",
        "multiprocessing.pool.Pool",
        "os.fork",
        "os.forkpty",
        "os.popen",
        "os.startfile",
        "os.system",
        "pty.spawn",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.run",
    }
)
# asyncio event-loop process launchers. The receiver's type is not tracked:
# these method names are specific to process creation, and the rule only
# covers backend/server.py, so the leaf name alone is reported.
_LOOP_PROCESS_METHODS = frozenset({"subprocess_exec", "subprocess_shell"})
# Nested stored partials resolved per call (``p2 = partial(p1, ...)``).
_MAX_PARTIAL_DEPTH = 4
