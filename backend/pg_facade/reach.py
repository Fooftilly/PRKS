"""What the PostgreSQL runtime can reach of the backend (PG-C1 #640).

The facade's surface (:func:`backend.pg_facade.registry.facade_surface`) is
the ``PRKSDatabase`` methods the *PostgreSQL* runtime calls through
``db.<name>``. Code that only the SQLite runtime runs is left out of it,
because the PostgreSQL Library never executes it and PG-C2 (#641) deletes
it with SQLite. That code is named here, never inferred:

- :data:`SQLITE_ONLY`: top-level functions and classes, by module and name.
  Their bodies are SQLite code.
- A *SQLite branch*: code the PostgreSQL runtime skips by the predicate it
  runs under. That is the body of ``if not serves_postgres_library():``, the
  ``else`` of ``if serves_postgres_library():``, and the statements after an
  ``if serves_postgres_library():`` whose body ends in ``return`` or
  ``raise``. ``library_database is not None`` (``server.bind_storage``) is
  the same predicate before the binding exists. After
  ``if flag and serves_postgres_library(): ... return``, a later
  ``if flag:`` in that function is a SQLite branch too, until ``flag`` is
  assigned again.

:func:`reach_problems` proves the list honest: every reference to a
SQLite-only function or class, anywhere in the backend, is itself SQLite
code (another SQLite-only scope, a SQLite branch, or ``db_manager.py``, the
SQLite store). A reference anywhere else means the PostgreSQL runtime could
reach it, and the check names the file and line.

:data:`RETIRED_METHODS` are the ``PRKSDatabase`` methods of the legacy
processing inbox, which the Processing queue replaces (§11.5). The facade
never implements them; their routes are SQLite branches, so the PostgreSQL
runtime answers 404 there.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The legacy processing inbox, replaced by the Processing queue (§11.5).
RETIRED_METHODS: frozenset[str] = frozenset({
    "get_processing_file_pdf_path",
    "get_processing_files",
    "import_processing_file",
    "scan_processing_files",
    "update_processing_file",
})

# (path under backend/, top-level function or class): SQLite code PG-C2 deletes.
SQLITE_ONLY: frozenset[tuple[str, str]] = frozenset({
    # The SQLite sync protocol and its per-family handlers; PostgreSQL runs
    # backend.sync.protocol instead.
    ("sync_protocol.py", "process_operation"),
    ("person_sync.py", "apply"),
    ("tag_sync.py", "apply_merge"),
    ("work_open_sync.py", "apply"),
    ("work_note_sync.py", "set_research_note"),
    ("work_role_sync.py", "get_roles_state"),
    ("work_source_sync.py", "get_source_state"),
    # The SQLite Research Network; the facade's ResearchMixin serves PostgreSQL.
    ("research_network.py", "list_concepts"),
    ("research_network.py", "get_concept"),
    ("research_network.py", "create_concept"),
    ("research_network.py", "update_concept"),
    ("research_network.py", "delete_concept"),
    ("research_network.py", "replace_concept_aliases"),
    ("research_network.py", "replace_concept_parents"),
    ("research_network.py", "list_positions"),
    ("research_network.py", "get_position"),
    ("research_network.py", "create_position"),
    ("research_network.py", "update_position"),
    ("research_network.py", "delete_position"),
    ("research_network.py", "list_verdicts"),
    ("research_network.py", "list_arguments"),
    ("research_network.py", "get_argument"),
    ("research_network.py", "create_argument"),
    ("research_network.py", "update_argument"),
    ("research_network.py", "delete_argument"),
    ("research_network.py", "replace_argument_sources"),
    ("research_network.py", "replace_argument_targets"),
    ("research_network.py", "save_work_notes"),
    # The SQLite indexes; PostgreSQL binds PostgresResearchIndex and PostgresTextIndex.
    ("research_index.py", "PRKSResearchIndex"),
    ("research_graph.py", "ResearchGraphBuilder"),
    ("research_graph.py", "build_research_graph"),
    ("text_index.py", "PRKSTextIndex"),
    # SQLite Work deletion and its pending PDF cleanup; PostgreSQL records
    # cleanup claims in the delete's transaction (§11.2).
    ("work_deletion.py", "forget_pending_pdf_cleanup"),
    ("work_deletion.py", "pending_pdf_cleanup_count"),
    ("work_deletion.py", "_note_attempt"),
    ("work_deletion.py", "settle_claim_if_referenced"),
    ("work_deletion.py", "_catalogue_blocks_managed_pdf_unlink"),
    ("work_deletion.py", "retry_pending_pdf_cleanup"),
    ("work_deletion.py", "retry_pending_pdf_cleanup_at_startup"),
    ("work_deletion.py", "cleanup_released_managed_pdfs"),
    ("work_deletion.py", "_remove_managed_pdf"),
    ("work_deletion.py", "cleanup_after_work_delete"),
    ("work_deletion.py", "delete_work"),
    # SQLite managed-PDF replacement; PostgreSQL replaces through the facade.
    ("services/work_pdf_replace.py", "_sync_mentioned_roles"),
    ("services/work_pdf_replace.py", "other_works_share_managed_filename"),
    ("services/work_pdf_replace.py", "retarget_work_managed_file_path"),
    ("services/work_pdf_replace.py", "replace_managed_work_pdf"),
    ("services/work_pdf_replace.py", "discard_unowned_managed_pdf"),
    # The SQLite restore; PostgreSQL restores through backend.api.maintenance.
    ("backup_restore.py", "apply_restore"),
})

# SQLite sync handlers' ``apply``: bound at module level into the module's
# legacy handler object, which ``sync_protocol.register`` files and only
# ``sync_protocol.process_operation`` (SQLite-only) calls ``.apply(db, ...)``
# on. :func:`reach_problems` allows exactly that module-level binding and
# checks that every ``.apply(db, ...)`` call in the backend is SQLite code.
SQLITE_DISPATCH: frozenset[tuple[str, str]] = frozenset({
    ("person_sync.py", "apply"),
    ("tag_sync.py", "apply_merge"),
    ("work_open_sync.py", "apply"),
})

# The SQLite store itself: everything in it is SQLite code.
_SQLITE_FILES = frozenset({"db_manager.py"})
_SKIPPED_DIRS = frozenset({"__pycache__"})
_PG_PREDICATE = "serves_postgres_library"


def backend_files(backend_dir: str = BACKEND_DIR) -> Iterator[str]:
    """Every backend module, as a path relative to ``backend_dir`` (``/``-separated)."""
    for root, dirs, files in os.walk(backend_dir):
        dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                full = os.path.join(root, name)
                yield os.path.relpath(full, backend_dir).replace(os.sep, "/")


def _module_name(rel: str) -> str:
    return "backend." + rel[: -len(".py")].replace("/", ".")


def _is_pg_test(expr: ast.expr) -> bool:
    """``serves_postgres_library()``, or ``library_database is not None``."""
    if isinstance(expr, ast.Call) and not expr.args and not expr.keywords:
        func = expr.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        return name == _PG_PREDICATE
    return (
        isinstance(expr, ast.Compare)
        and isinstance(expr.left, ast.Name)
        and expr.left.id == "library_database"
        and len(expr.ops) == 1
        and isinstance(expr.ops[0], ast.IsNot)
        and isinstance(expr.comparators[0], ast.Constant)
        and expr.comparators[0].value is None
    )


def _is_not_pg_test(expr: ast.expr) -> bool:
    return isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not) and _is_pg_test(expr.operand)


def _exits(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise))


class _SqliteMarker:
    """Collects the ids of every node in SQLite code of one module."""

    def __init__(self, rel: str, scopes: frozenset[tuple[str, str]]) -> None:
        self.rel = rel
        self.scopes = scopes
        self.marked: set[int] = set()
        self._scope: list[ast.AST] = []  # the enclosing function (or module)

    def run(self, tree: ast.Module) -> set[int]:
        whole = self.rel in _SQLITE_FILES
        self._scope = [tree]
        for stmt in tree.body:
            scoped = isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                (self.rel, stmt.name) in self.scopes
            )
            self._node(stmt, whole or scoped, set())
        return self.marked

    def _block(self, body: list[ast.stmt], sqlite: bool, learned: set[str]) -> None:
        # A flag learned in this block holds only for the rest of this block:
        # a nested branch's return says nothing about the path that skipped
        # the branch.
        local = set(learned)
        for stmt in body:
            self._node(stmt, sqlite, local)
            if sqlite or not isinstance(stmt, ast.If) or not _exits(stmt.body):
                continue
            if _is_pg_test(stmt.test):
                sqlite = True  # the PostgreSQL runtime left this block above
            else:
                local.update(
                    name for name in _pg_conjunct_names(stmt.test)
                    if not _rebound_from(self._scope[-1], name, stmt)
                )

    def _node(self, node: ast.AST, sqlite: bool, learned: set[str] | None = None) -> None:
        function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        if learned is None or function or isinstance(node, ast.ClassDef):
            learned = set()  # what a flag means holds within one function body
        if function:
            self._scope.append(node)
        try:
            self._visit(node, sqlite, learned)
        finally:
            if function:
                self._scope.pop()

    def _visit(self, node: ast.AST, sqlite: bool, learned: set[str]) -> None:
        if sqlite:
            self.marked.add(id(node))
        if isinstance(node, ast.If):
            self._node(node.test, sqlite, learned)
            in_sqlite = _is_not_pg_test(node.test) or (isinstance(node.test, ast.Name) and node.test.id in learned)
            self._block(node.body, sqlite or in_sqlite, learned)
            self._block(node.orelse, sqlite or _is_pg_test(node.test), learned)
            return
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list):
                if value and all(isinstance(item, ast.stmt) for item in value):
                    self._block(value, sqlite, learned)
                else:
                    for item in value:
                        if isinstance(item, ast.AST):
                            self._node(item, sqlite, learned)
            elif isinstance(value, ast.AST):
                self._node(value, sqlite, learned)


def _bindings(scope: ast.AST, name: str) -> Iterable[ast.AST]:
    """Every node in ``scope`` (nested scopes included) that may bind ``name``."""
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name:
            yield node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            yield node
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            yield node
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name.partition(".")[0]) == name for alias in node.names
        ):
            yield node
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            yield node
        elif isinstance(node, ast.MatchMapping) and node.rest == name:
            yield node


def _rebound_from(scope: ast.AST, name: str, guard: ast.If) -> bool:
    """Whether ``name`` may hold a new value once ``guard`` has run.

    True when anything in the function binds it at or after the guard (in its
    ``else``, later in the body, or anywhere in a loop around both), or when a
    ``global``/``nonlocal`` lets other code rebind it. Only a flag nothing
    rebinds keeps the meaning the guard gave it, so this stays sound without
    tracking every path.
    """
    for node in ast.walk(scope):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
    start = (guard.lineno, guard.col_offset)
    loops = [
        node for node in ast.walk(scope)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        and any(sub is guard for sub in ast.walk(node))
    ]
    for binding in _bindings(scope, name):
        if (getattr(binding, "lineno", 0), getattr(binding, "col_offset", 0)) >= start:
            return True
        if any(any(sub is binding for sub in ast.walk(loop)) for loop in loops):
            return True  # a later iteration runs the guard after this binding
    return False


def _pg_conjunct_names(test: ast.expr) -> set[str]:
    """``flag`` for ``if flag and serves_postgres_library(): ... return``, with exactly one flag.

    Past that statement, the PostgreSQL runtime never runs code under
    ``if flag:`` (until ``flag`` is assigned again).
    """
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
        return set()
    if not any(_is_pg_test(value) for value in test.values):
        return set()
    rest = [value for value in test.values if not _is_pg_test(value)]
    # Only ``flag and serves_postgres_library()`` proves ``flag`` is false
    # past the return. With two flags, skipping it proves only that one of
    # them is false, so nothing is learned.
    if len(rest) == 1 and len(test.values) == 2 and isinstance(rest[0], ast.Name):
        return {rest[0].id}
    return set()


@dataclass(frozen=True)
class ParsedModule:
    rel: str
    tree: ast.Module
    sqlite: frozenset[int]

    def is_sqlite(self, node: ast.AST) -> bool:
        return id(node) in self.sqlite


def parse_module(rel: str, backend_dir: str = BACKEND_DIR,
                 scopes: frozenset[tuple[str, str]] = SQLITE_ONLY) -> ParsedModule:
    path = os.path.join(backend_dir, rel)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    return ParsedModule(rel, tree, frozenset(_SqliteMarker(rel, scopes).run(tree)))


def _module_level_ids(tree: ast.Module) -> set[int]:
    """Nodes outside every function body (module statements and class bodies)."""
    found: set[int] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            found.add(id(child))
            visit(child)

    visit(tree)
    return found


def _annotation_ids(tree: ast.Module) -> set[int]:
    """Nodes inside type annotations: a name there is never a call."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                roots.append(node.returns)
            all_args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            all_args += [a for a in (node.args.vararg, node.args.kwarg) if a is not None]
            roots.extend(a.annotation for a in all_args if a.annotation is not None)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return {id(sub) for root in roots for sub in ast.walk(root)}


def _dotted(node: ast.expr) -> str | None:
    """``a.b.c`` for a chain of attribute reads on a name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _absolute(node: ast.ImportFrom, importer: str) -> str | None:
    """The module ``from <node.module> import ...`` names, from module ``importer``."""
    if node.level == 0:
        return node.module
    # The package of ``backend.x.y`` (or of ``backend.x.__init__``) is ``backend.x``.
    package = importer.split(".")[:-1]
    if node.level - 1 >= len(package):
        return None
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _aliases(tree: ast.Module, module: str, name: str, importer: str = "") -> tuple[set[str], set[str]]:
    """Local names bound to ``module.name``, and local spellings of ``module``.

    A spelling is an alias (``from backend import legacy``, ``import
    backend.legacy as legacy``) or the dotted path itself once a plain
    ``import backend.legacy`` (or ``import backend``) binds its first name.
    """
    direct: set[str] = set()
    modules: set[str] = set()
    package, _, base = module.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _absolute(node, importer)
            for alias in node.names:
                if source == module and alias.name == name:
                    direct.add(alias.asname or alias.name)
                elif source == package and alias.name == base:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module and alias.asname:
                    modules.add(alias.asname)
                elif not alias.asname and (alias.name == module or module.startswith(alias.name + ".")):
                    modules.add(module)
    return direct, modules


@dataclass(frozen=True)
class Reference:
    rel: str
    line: int
    scope: tuple[str, str]

    def __str__(self) -> str:
        return f"backend/{self.rel}:{self.line} reaches {self.scope[0]}:{self.scope[1]}"


def _references(parsed: ParsedModule, scope: tuple[str, str]) -> Iterable[tuple[ast.AST, Reference]]:
    rel, name = scope
    module = _module_name(rel)
    annotations = _annotation_ids(parsed.tree)
    if parsed.rel == rel:
        direct, modules = {name}, set()
    else:
        direct, modules = _aliases(parsed.tree, module, name, _module_name(parsed.rel))
    if not direct and not modules:
        return
    for node in ast.walk(parsed.tree):
        if id(node) in annotations:
            continue
        hit = (
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in direct
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr == name
            and _dotted(node.value) in modules
        )
        if hit:
            yield node, Reference(parsed.rel, getattr(node, "lineno", 0), scope)


def reach_problems(backend_dir: str = BACKEND_DIR, *,
                   scopes: frozenset[tuple[str, str]] = SQLITE_ONLY,
                   dispatch: frozenset[tuple[str, str]] = SQLITE_DISPATCH) -> list[str]:
    """Every place outside SQLite code that refers to a SQLite-only scope.

    Also a scope this list names that does not exist (a renamed function would
    silently stop being checked).
    """
    parsed = [parse_module(rel, backend_dir, scopes) for rel in backend_files(backend_dir)]
    by_rel = {one.rel: one for one in parsed}
    problems: list[str] = []
    for scope in sorted(scopes):
        owner = by_rel.get(scope[0])
        defined = owner is not None and any(
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and stmt.name == scope[1]
            for stmt in owner.tree.body
        )
        if not defined:
            problems.append(f"SQLITE_ONLY names {scope[0]}:{scope[1]}, which is not a top-level definition")
            continue
        bound_here = _module_level_ids(owner.tree) if scope in dispatch else set()
        for module in parsed:
            for node, ref in _references(module, scope):
                if module.is_sqlite(node) or (module.rel == scope[0] and id(node) in bound_here):
                    continue
                problems.append(str(ref))
    for module in parsed:
        for node in ast.walk(module.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "apply"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "db"
                and not module.is_sqlite(node)
            ):
                problems.append(f"backend/{module.rel}:{node.lineno} calls a SQLite sync handler's apply")
    return sorted(set(problems))
