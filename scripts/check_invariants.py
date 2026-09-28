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
from typing import Any, Iterable

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
# Intentional gap: pathlib.Path.replace is the same atomic rename as os.replace
# but is not matched here (fn.value is typically a Call/Name that is not an
# ``os`` module alias). Cover Path.replace only with an explicit follow-up that
# tracks Path constructors / Path-typed names — do not treat every ``.replace``
# attribute call as os.replace.
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

_TRACKED_MODULES = frozenset({"os", "shutil", "zipfile"})


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
# ``obj.attr`` targets are bound under the dotted key ``"obj.attr"``.
_Binding = tuple[Any, ...]
_ARCHIVE: _Binding = ("archive",)
_OTHER: _Binding = ("other",)


class _ClassInfo:
    """One same-module class: is it a zipfile archive subclass, and which of
    its attributes are proven zipfile archives."""

    __slots__ = ("archive_attrs", "bases", "zip_base")

    def __init__(self) -> None:
        self.archive_attrs: set[str] = set()
        self.bases: list[_ClassInfo] = []
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

    def is_archive_class(self) -> bool:
        return any(info.zip_base for info in self._lineage())


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
        "class_registry",
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
        # Only used on the module scope: ClassDef node id -> its _ClassInfo.
        self.class_registry: dict[int, _ClassInfo] = {}

    @property
    def is_class(self) -> bool:
        return self.class_info is not None


def _resolve(scopes: list[_Scope], name: str) -> set[_Binding]:
    innermost = scopes[-1]
    if name in innermost.current:
        return innermost.current[name]
    if name in innermost.summary:
        return innermost.summary[name]
    # Class bodies are not enclosing scopes for the functions nested in them.
    for scope in reversed(scopes[:-1]):
        if not scope.is_class and name in scope.summary:
            return scope.summary[name]
    return set()


def _lookup_modules(scopes: list[_Scope], name: str) -> set[str]:
    return {b[1] for b in _resolve(scopes, name) if b[0] == "module"}


def _lookup_names(scopes: list[_Scope], name: str) -> set[tuple[str, str]]:
    return {(b[1], b[2]) for b in _resolve(scopes, name) if b[0] == "name"}


def _call_identities(node: ast.Call, scopes: list[_Scope]) -> list[tuple[str, str]]:
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return sorted((module, fn.attr) for module in _lookup_modules(scopes, fn.value.id))
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
        # A binding on the current path is authoritative over the class record.
        return _ARCHIVE in innermost.current[key]
    if _ARCHIVE in _resolve(scopes, key):
        return True
    return any(
        info.has_archive_attr(node.attr)
        for info in _class_infos(_resolve(scopes, node.value.id))
    )


def _branch_values(node: ast.expr) -> list[ast.expr] | None:
    """Values a conditional / boolean expression may evaluate to."""
    if isinstance(node, ast.IfExp):
        return [node.body, node.orelse]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    return None


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
    return _is_zip_constructor(node, scopes)


def _value_bindings(value: ast.expr, scopes: list[_Scope]) -> set[_Binding]:
    """What a name bound to ``value`` refers to (aliases carry through)."""
    branches = _branch_values(value)
    if branches is not None:
        return set().union(*(_value_bindings(branch, scopes) for branch in branches))
    if isinstance(value, ast.Name):
        return set(_resolve(scopes, value.id)) or {_OTHER}
    if _is_zip_archive_expr(value, scopes):
        return {_ARCHIVE}
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        modules = _lookup_modules(scopes, value.value.id)
        if modules:
            return {("name", module, value.attr) for module in modules}
    return {_OTHER}


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
    return [(name, {_OTHER}) for name in _stored_names(target)]


_Pairs = list[tuple[str, set[_Binding]]]


def _import_pairs(node: ast.Import, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for item in node.names:
        top = item.name.split(".", 1)[0]
        if item.asname is None:
            # ``import os.path`` still binds the top-level name ``os``.
            binding = ("module", top) if top in _TRACKED_MODULES else _OTHER
            pairs.append((top, {binding}))
        elif item.name in _TRACKED_MODULES:
            pairs.append((item.asname, {("module", item.name)}))
        else:
            # ``import os.path as p`` binds only ``p`` (to os.path).
            pairs.append((item.asname, {_OTHER}))
    return pairs


def _import_from_pairs(node: ast.ImportFrom, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    tracked = node.level == 0 and node.module in _TRACKED_MODULES
    for item in node.names:
        if item.name == "*":
            if tracked and node.module == "zipfile":
                pairs.extend(
                    (cls, {("name", "zipfile", cls)}) for cls in sorted(ZIPFILE_ARCHIVE_CLASSES)
                )
            continue
        binding = ("name", node.module, item.name) if tracked else _OTHER
        pairs.append((item.asname or item.name, {binding}))
    return pairs


def _assign_pairs(node: ast.Assign, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for target in node.targets:
        pairs.extend(_target_pairs(target, node.value, scopes))
    return pairs


def _ann_assign_pairs(node: ast.AnnAssign, scopes: list[_Scope]) -> _Pairs:
    key = node.target.id if isinstance(node.target, ast.Name) else _attr_key(node.target)
    if key is None:
        return []
    if _annotation_mentions_zip(node.annotation, scopes):
        return [(key, {_ARCHIVE})]
    if node.value is not None:
        return _target_pairs(node.target, node.value, scopes)
    return []


def _aug_assign_pairs(node: ast.AugAssign, scopes: list[_Scope]) -> _Pairs:
    return [(name, {_OTHER}) for name in _stored_names(node.target)]


def _named_expr_pairs(node: ast.NamedExpr, scopes: list[_Scope]) -> _Pairs:
    return _target_pairs(node.target, node.value, scopes)


def _for_pairs(node: ast.For | ast.AsyncFor, scopes: list[_Scope]) -> _Pairs:
    return _loop_target_pairs(node.target, node.iter, scopes)


def _with_pairs(node: ast.With | ast.AsyncWith, scopes: list[_Scope]) -> _Pairs:
    pairs: _Pairs = []
    for item in node.items:
        target = item.optional_vars
        if target is None:
            continue
        key = target.id if isinstance(target, ast.Name) else _attr_key(target)
        if key is not None and _is_zip_archive_expr(item.context_expr, scopes):
            pairs.append((key, {_ARCHIVE}))
        else:
            pairs.extend((name, {_OTHER}) for name in _stored_names(target))
    return pairs


def _def_pairs(node: ast.FunctionDef | ast.AsyncFunctionDef, scopes: list[_Scope]) -> _Pairs:
    return [(node.name, {_OTHER})]


def _class_def_pairs(node: ast.ClassDef, scopes: list[_Scope]) -> _Pairs:
    info = scopes[0].class_registry.setdefault(id(node), _ClassInfo())
    return [(node.name, {("class", info)})]


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
}


def _binding_pairs(node: ast.AST, scopes: list[_Scope]) -> _Pairs:
    """Names (or ``obj.attr`` keys) ``node`` binds in its own scope, with what each refers to."""
    handler = _BINDING_HANDLERS.get(type(node))
    return handler(node, scopes) if handler is not None else []


def _is_staticmethod(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list
    )


def _function_params(args: ast.arguments) -> list[ast.arg]:
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    params.extend(a for a in (args.vararg, args.kwarg) if a is not None)
    return params


def _iter_scope_nodes(body: list[ast.stmt]) -> Iterable[ast.AST]:
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
    return merged


class _InvariantVisitor(ast.NodeVisitor):
    """Walk the tree, resolving os/shutil/zipfile bindings in the current lexical scope."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.scopes: list[_Scope] = []
        self.findings: list[Finding] = []

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
    ) -> None:
        scope = _Scope(class_info=class_info, is_comprehension=is_comprehension)
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

    def _loop_entry_state(self, body: list[ast.stmt]) -> _State:
        """State at the top of a loop body, including later-iteration bindings."""
        scope = self.scopes[-1]
        state = _copy_state(scope.current)
        for node in _iter_scope_nodes(body):
            for name, _ in _binding_pairs(node, self.scopes):
                if name in scope.summary:
                    state.setdefault(name, set()).update(scope.summary[name])
        return state

    def _finish_loop(self, entry: _State, body_end: _State, orelse: list[ast.stmt]) -> None:
        after = _merge_states(entry, body_end)
        self.scopes[-1].current = _merge_states(after, self._run_branch(after, orelse))

    def visit_Module(self, node: ast.Module) -> None:
        self._push(node.body)
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
        self._push(node.body, params=param_pairs)
        for stmt in node.body:
            self.visit(stmt)
        self._pop()

    def _param_pairs(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Pairs:
        """Parameters are locals: they shadow enclosing imports and archive
        bindings unless annotated as a zipfile archive. A method's first
        parameter (``self``/``cls``) is bound to its class."""
        pairs: _Pairs = [
            (
                param.arg,
                {_ARCHIVE} if _annotation_mentions_zip(param.annotation, self.scopes) else {_OTHER},
            )
            for param in _function_params(node.args)
        ]
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
        self._push([], params=[(p.arg, {_OTHER}) for p in _function_params(args)])
        self.visit(node.body)
        self._pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(child)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        info = self.scopes[0].class_registry.setdefault(id(node), _ClassInfo())
        info.zip_base = any(_is_zip_class_expr(base, self.scopes) for base in node.bases)
        for base in node.bases:
            if isinstance(base, ast.Name):
                info.bases.extend(_class_infos(_resolve(self.scopes, base.id)))
        self._push(node.body, class_info=info)
        # Pre-collect every method body first so ``self.x = ZipFile(...)`` in
        # ``__init__`` classifies ``self.x`` in methods defined before it.
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._push(stmt.body, params=self._param_pairs(stmt))
                self._pop()
        for stmt in node.body:
            self.visit(stmt)
        self._pop()
        self._bind_node(node)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> None:
        self._push([], is_comprehension=True)
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
        self._pop()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    def visit_Import(self, node: ast.Import) -> None:
        self._bind_node(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._bind_node(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        self._bind_node(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        self._bind_node(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
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
        entry = self._loop_entry_state(node.body)
        self.scopes[-1].current = _copy_state(entry)
        self._bind_node(node)
        body_end = self._run_branch(self.scopes[-1].current, node.body)
        self._finish_loop(entry, body_end, node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        entry = self._loop_entry_state(node.body)
        self.scopes[-1].current = _copy_state(entry)
        self.visit(node.test)
        body_end = self._run_branch(entry, node.body)
        self._finish_loop(entry, body_end, node.orelse)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = _copy_state(self.scopes[-1].current)
        body_end = self._run_branch(before, node.body)
        orelse_end = self._run_branch(before, node.orelse)
        self.scopes[-1].current = _merge_states(body_end, orelse_end)

    def visit_Try(self, node: ast.Try) -> None:
        before = _copy_state(self.scopes[-1].current)
        body_end = self._run_branch(before, node.body)
        # A handler can start anywhere in the body.
        handler_start = _merge_states(before, body_end)
        handler_ends = [self._run_branch(handler_start, [h]) for h in node.handlers]
        orelse_end = self._run_branch(body_end, node.orelse)
        self.scopes[-1].current = _merge_states(orelse_end, *handler_ends)
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
        before = _copy_state(self.scopes[-1].current)
        case_ends = [self._run_branch(before, [case]) for case in node.cases]
        # No case may match, so the state before the match also flows on.
        self.scopes[-1].current = _merge_states(before, *case_ends)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
        self._bind_node(node)
        for stmt in node.body:
            self.visit(stmt)

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
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "super"
        ):
            return False
        class_info = next(
            (scope.class_info for scope in reversed(self.scopes) if scope.class_info), None
        )
        return class_info is not None and class_info.is_archive_class()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Match the attribute itself so method references (``f = zf.extractall``)
        # and unbound calls (``ZipFile.extractall(zf, dest)``) are covered too.
        if node.attr == BANNED_ZIPFILE_METHOD and self._is_zip_receiver(node.value):
            self._report_zip_extractall(node)
        self.generic_visit(node)

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
        for module, name in _call_identities(node, self.scopes):
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
                self.findings.append(
                    Finding(
                        "INV-DURABILITY-001",
                        self.relpath,
                        node.lineno,
                        (
                            "direct os.replace() is outside the approved durability boundary; "
                            "use backend.fs_durability or an existing durable domain helper"
                        ),
                    )
                )
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

    visitor = _InvariantVisitor(relpath)
    visitor.visit(tree)
    return visitor.findings


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