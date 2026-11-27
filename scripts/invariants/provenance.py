"""Lexical bindings and value provenance for the code-invariant rules.

Resolves import aliases, partials and attribute bindings, and tracks which
values are zipfile archives, pathlib Paths, managed-PDF paths, minted or
guarded basenames, and SQL text writing ``works.file_path``.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Iterable, Sequence, TypeGuard

from .boundaries import (
    MANAGED_PDF_DIR_NAMES,
    MANAGED_PDF_MINTING_HELPERS,
    MANAGED_PDF_PATH_HELPERS,
    PATHLIB_PATH_CLASSES,
    WEAK_MANAGED_PDF_ALIAS_HELPERS,
    ZIPFILE_ARCHIVE_CLASSES,
    _ALIASED_BUILTINS,
    _CAPABILITY_HELPER_HOMES,
    _DIR_ENTRY_ATTRS,
    _DIR_ITERATOR_FUNCS,
    _MAPPING_LOOKUP_METHODS,
    _OS_OPEN_WRITE_FLAGS,
    _PATH_CLASS_FACTORIES,
    _PATH_ITERATOR_METHODS,
    _PATH_OPEN_METHODS,
    _PATH_RETURNING_ATTRS,
    _PATH_RETURNING_METHODS,
    _PATH_STRING_ATTRS,
    _PATH_STRING_FUNCS,
    _PATH_STRING_METHODS,
    _PROCESS_CALLS,
    _PROCESS_CALL_PREFIXES,
    _PURE_PATH_CLASSES,
    _SQL_CLEARING_VALUES,
    _SQL_COMMENT_RE,
    _SQL_DOUBLE_QUOTED_RE,
    _SQL_FILE_PATH_ASSIGN_RE,
    _SQL_IDENTIFIER_QUOTES_RE,
    _SQL_KEYWORDS,
    _SQL_PENDING_CLEANUP_WRITE_RE,
    _SQL_ROW_VALUE_SET_RE,
    _SQL_SCHEMA_PREFIX_RE,
    _SQL_SET_END_RE,
    _SQL_STRING_LITERAL_RE,
    _SQL_UPSERT_SET_RE,
    _SQL_WORKS_COLUMNLESS_INSERT_RE,
    _SQL_WORKS_INSERT_RE,
    _SQL_WORKS_UPDATE_RE,
    _STR_TRANSFORM_METHODS,
    _TRACKED_HELPER_NAMES,
)


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
    # ``("partial_of", call)`` lets a later invocation be checked as the
    # combined ``helper(*bound, *args)`` call; an inner stored partial is
    # reached again through this call's own wrapped callable.
    kept = {b for b in inner if b[0] not in {"partial", "partial_of"}}
    return kept | {("partial", n + added) for n in prior} | {("partial_of", value)}


def _attribute_alias_bindings(
    value: ast.Attribute, scopes: list[_Scope]
) -> set[_Binding] | None:
    """``mod.attr`` / ``pkg.mod.attr`` keep their import identity, and a bound
    method of a tracked helper keeps its name (``save = db.add_work``);
    ``upd = Cls.method`` stays unbound (receiver passed explicitly)."""
    if value.attr == "rename" and _PATH in _expr_facts(value.value, scopes):
        # ``move = p.rename``: invoking it overwrites its destination argument.
        return {_BOUND_PATH_RENAME} | _expr_facts(value, scopes)
    if value.attr in _PATH_OPEN_METHODS and not _is_path_class_expr(value.value, scopes):
        receiver = _expr_facts(value.value, scopes)
        if _PATH in receiver:
            # ``writer = p.open``: the call's mode / ``exist_ok`` decides later
            # (INV-ADAPTER-001); whether ``p`` is a managed PDF is fixed now.
            return {("bound_path_method", value.attr, _MANAGED in receiver)} | _expr_facts(
                value, scopes
            )
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


def _is_partial_call(node: ast.expr, scopes: list[_Scope]) -> TypeGuard[ast.Call]:
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
    """``..pdf.work_pdf_replace`` from ``backend/x/y.py`` ->
    ``backend.pdf.work_pdf_replace``; absolute names are unchanged."""
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
            # The full module: ``backend.pdf.work_pdf_replace``, or a
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
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "?"
            for part in node.values
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
