"""AST rules over production Python (INV-DURABILITY/BACKUP/STORAGE/ADAPTER)."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .findings import Finding, REPO_ROOT
from .boundaries import (
    BANNED_SHUTIL_COPY_CALLS,
    BANNED_ZIPFILE_METHOD,
    CONDITIONAL_ADOPTION_GUARDS,
    MANAGED_PDF_ADOPTION_GUARD,
    MANAGED_PDF_REMOVE_CAPABILITIES,
    OS_FSYNC_ALLOWLIST,
    OS_REPLACE_ALLOWLIST,
    RAW_MANAGED_PDF_UNLINK_HELPERS,
    RAW_REMOVE_CALLS,
    WEAK_ALIAS_AUTHORITY_SINKS,
    WORK_FILE_PATH_CAPABILITIES,
    WORK_FILE_PATH_SINKS,
    _DICT_MUTATORS,
    _LOOP_PROCESS_METHODS,
    _MAX_PARTIAL_DEPTH,
    _OPEN_CALLS,
    _OVERWRITING_MOVE_CALLS,
    _PATH_OPEN_METHODS,
    _PATH_REMOVAL_METHODS,
    _PATH_UNBOUND_CREATE,
    _PATH_UNBOUND_UNLINK,
    _PATH_WRITE_METHODS,
    _PROCESS_MODULES,
    _SQL_EXECUTE_METHODS,
    _SQL_TEXT_KEYWORDS,
    _TEMPFILE_DIR_ARG,
    is_http_adapter_module,
)
from .provenance import (
    _ARCHIVE,
    _AdoptionGuard,
    _BOUND_PATH_RENAME,
    _Binding,
    _ClassInfo,
    _DICT_DIRTY,
    _FACT_TAGS,
    _FuncInfo,
    _MANAGED,
    _PATH,
    _Pairs,
    _Scope,
    _State,
    _UNSET,
    _WEAK,
    _arguments_pairs,
    _attr_key,
    _binding_pairs,
    _branch_values,
    _call_argument_facts,
    _call_identities,
    _callee_leaf_names,
    _canonical_capability_names,
    _class_infos,
    _copy_state,
    _dict_file_path_value,
    _enclosing_class,
    _expr_facts,
    _facts_of,
    _function_params,
    _getattr_as_attribute,
    _identity_passed_names,
    _is_builtin,
    _is_partial_call,
    _is_path_class_expr,
    _is_staticmethod,
    _is_super_call,
    _is_unbound_method_call,
    _is_zip_archive_expr,
    _is_zip_class_expr,
    _iter_scope_nodes,
    _join,
    _loop_target_pairs,
    _merge_states,
    _open_mode_effect,
    _os_open_flags_effect,
    _partial_bindings,
    _pattern_pairs,
    _positional_or_keyword,
    _process_primitives,
    _proven_owned,
    _qualified_names,
    _resolve,
    _terminates,
    _unwrap_walrus,
    _value_bindings,
    _with_pairs,
)


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
        # Provenance of a partial's pre-bound arguments where it was built, so
        # a later invocation judges them as they were bound (INV-ADAPTER-001).
        self._prebound_facts: dict[int, set[_Binding]] = {}

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
        self._visit_try_statement(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try_statement(node)

    def _visit_try_statement(self, node: ast.Try | ast.TryStar) -> None:
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
            is_http_adapter_module(self.relpath)
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
                    "backend.storage.fs_durability or an existing durable domain helper "
                    "(managed PDFs: backend.pdf.work_pdf_replace)"
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
                    "just minted inside backend.pdf.work_pdf_replace. Use "
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
                "(backend.pdf.work_pdf_replace.managed_pdf_adoption_guard: it "
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
            if is_http_adapter_module(self.relpath):
                self._record_prebound_facts(node)
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
        exactly as they do for the INV-STORAGE rules. Invoking a stored
        partial is checked as the combined call, reporting only what the
        invocation adds to what its construction already reported."""
        if not is_http_adapter_module(self.relpath):
            return
        self._check_passed_process_callables(node)
        findings = self._adapter_call_findings(node)
        partials = self._stored_partials(node.func)
        if partials:
            findings -= set().union(
                *(self._adapter_call_findings(self._merged_partial_call(p, None), 1) for p in partials)
            )
        for kind, primitive in sorted(findings):
            if kind == "process":
                self._report_adapter_process(node, primitive)
            elif kind == "exclusive":
                self._report_adapter_exclusive_create(node, primitive)
            else:
                self._report_adapter_store(node, primitive)

    def _stored_partials(self, func: ast.expr) -> list[ast.Call]:
        """``partial(...)`` constructions a stored callee may be bound to."""
        return [b[1] for b in self._callee_bindings(func) if b[0] == "partial_of"]

    def _merged_partial_call(self, partial_call: ast.Call, call: ast.Call | None) -> ast.Call:
        """``partial(helper, *bound)`` invoked as ``call`` (or with no
        arguments), as the single call ``helper(*bound, *args)``."""
        # Merging for analysis must not mark the construction as already checked.
        flattened = set(self._flattened_partials)
        try:
            at: ast.AST = call if call is not None else partial_call
            args = list(call.args) if call is not None else []
            keywords = list(call.keywords) if call is not None else []
            return self._flatten_partial(partial_call, args, keywords, at)
        finally:
            self._flattened_partials = flattened

    def _adapter_call_findings(self, call: ast.Call, depth: int = 0) -> set[tuple[str, str]]:
        """``(kind, primitive)`` the call amounts to: ``process``,
        ``exclusive`` (create) or ``store`` (managed-PDF write). A stored
        partial is resolved to the combined call it makes."""
        partials = self._stored_partials(call.func) if depth <= _MAX_PARTIAL_DEPTH else []
        if partials:
            return set().union(
                *(
                    self._adapter_call_findings(self._merged_partial_call(p, call), depth + 1)
                    for p in partials
                )
            )
        func = _unwrap_walrus(call.func)
        names = _qualified_names(func, self.scopes)
        if _is_builtin(func, self.scopes, "open"):
            names = names | {"builtins.open"}
        found: set[tuple[str, str]] = set()
        spawned = _process_primitives(names)
        if spawned:
            found.add(("process", f"{spawned[0]}()"))
        method = _getattr_as_attribute(func, self.scopes) or func
        if isinstance(method, ast.Attribute) and method.attr in _LOOP_PROCESS_METHODS:
            # ``asyncio.get_running_loop().subprocess_exec(...)``.
            found.add(("process", f"event loop .{method.attr}()"))
        for primitive, exclusive, writes, managed in self._adapter_creations(call, func, names):
            if exclusive:
                found.add(("exclusive", primitive))
            elif writes and managed:
                found.add(("store", primitive))
        return found

    def _check_passed_process_callables(self, node: ast.Call) -> None:
        """``executor.submit(os.system, cmd)`` / ``callback=getattr(os, "popen")``:
        a process callable handed to another API still runs from here. A
        literal is looked into only where it is unpacked into the call
        (``*[os.system, cmd]`` / ``**{"callback": os.system}``); an ordinary
        list argument is data, not a callable being passed."""
        values: list[ast.expr] = []
        for arg in node.args:
            inner = _unwrap_walrus(arg.value if isinstance(arg, ast.Starred) else arg)
            if isinstance(arg, ast.Starred) and isinstance(inner, (ast.List, ast.Tuple, ast.Set)):
                values.extend(e.value if isinstance(e, ast.Starred) else e for e in inner.elts)
            else:
                values.append(inner)
        for keyword in node.keywords:
            if keyword.arg is None and isinstance(keyword.value, ast.Dict):
                values.extend(
                    value for key, value in zip(keyword.value.keys, keyword.value.values) if key
                )
            else:
                values.append(keyword.value)
        for value in values:
            value = _unwrap_walrus(value)
            passed = _process_primitives(_qualified_names(value, self.scopes))
            if passed:
                self._report_adapter_process(value, f"{passed[0]} passed as a callable")

    def _record_prebound_facts(self, partial_call: ast.Call) -> None:
        """Provenance of each pre-bound argument (and of a bound method's
        receiver) as it is at construction, for every inline layer of
        ``partial(partial(helper, a), b)``: the inner layers are flattened
        into this one and never checked (or recorded) on their own."""
        bound: list[ast.expr] = []
        layer: ast.expr = partial_call
        while isinstance(layer, ast.Call) and _is_partial_call(layer, self.scopes) and layer.args:
            bound.extend([*layer.args[1:], *(kw.value for kw in layer.keywords)])
            layer = _unwrap_walrus(layer.args[0])
        if isinstance(layer, ast.Attribute):
            bound.append(layer.value)
        for value in bound:
            value = value.value if isinstance(value, ast.Starred) else value
            self._prebound_facts[id(value)] = _expr_facts(value, self.scopes)

    def _is_managed_target(self, target: ast.expr | None) -> bool:
        if target is None:
            return False
        facts = self._prebound_facts.get(id(target))
        if facts is None:
            facts = _expr_facts(target, self.scopes)
        return _MANAGED in facts

    def _adapter_creations(
        self, node: ast.Call, func: ast.expr, names: set[str]
    ) -> list[tuple[str, bool, bool, bool]]:
        """``(primitive, may create exclusively, may write, managed target)``
        for each file-creating primitive the call may be."""
        found: list[tuple[str, bool, bool, bool]] = []
        arg = self._call_argument
        managed = self._is_managed_target
        if names & _OPEN_CALLS:
            exclusive, writes = _open_mode_effect(arg(node, 1, "mode"))
            found.append(("open()", exclusive, writes, managed(arg(node, 0, "file"))))
        if "os.open" in names:
            exclusive, writes = _os_open_flags_effect(arg(node, 1, "flags"), self.scopes)
            found.append(("os.open()", exclusive, writes, managed(arg(node, 0, "path"))))
        for creator in sorted(names & set(_TEMPFILE_DIR_ARG)):
            directory = arg(node, _TEMPFILE_DIR_ARG[creator], "dir")
            found.append((f"{creator}()", False, True, managed(directory)))
        for unbound in sorted(names & _PATH_UNBOUND_CREATE):
            # ``Path.open(p, "xb")``: the receiver is the first argument.
            method = unbound.rsplit(".", 1)[1]
            effect = self._path_create_effect(node, method, 1)
            found.append((f"pathlib.Path.{method}()", *effect, managed(arg(node, 0, "self"))))
        bound = _getattr_as_attribute(func, self.scopes) or func
        if (
            isinstance(bound, ast.Attribute)
            and bound.attr in _PATH_OPEN_METHODS
            and not _is_path_class_expr(bound.value, self.scopes)
            and _PATH in _expr_facts(bound.value, self.scopes)
        ):
            effect = self._path_create_effect(node, bound.attr, 0)
            found.append((f"pathlib.Path.{bound.attr}()", *effect, managed(bound.value)))
        for binding in self._callee_bindings(func):
            if binding[0] == "bound_path_method":
                # ``writer = p.open; writer("wb")`` / ``self.writer(...)``.
                effect = self._path_create_effect(node, binding[1], 0)
                found.append((f"pathlib.Path.{binding[1]}()", *effect, binding[2]))
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
        if self._is_managed_target(target):
            self._report_adapter_store(node, primitive)

    def _report_adapter_store(self, node: ast.AST, primitive: str) -> None:
        self._report(
            "INV-ADAPTER-001",
            node,
            (
                f"{primitive} writes into a managed-PDF path (derived from pdfs_dir / "
                "safe_pdf_path_under_dir) from the HTTP adapter. backend/server.py must "
                "not store managed-PDF bytes itself: that skips the exclusive name "
                "claim, fsync-before-publish and rollback of unowned bytes. Store or "
                "replace through backend.pdf.work_pdf_replace "
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
                "backend.pdf.work_pdf_replace (store_new_managed_pdf_bytes / "
                "store_new_managed_pdf_from_path / allocate_exclusive_managed_filename); "
                "other storage: a focused backend module (e.g. "
                "backend.media.derived_cache_publish)"
            ),
        )

    def _report_adapter_process(self, node: ast.AST, what: str) -> None:
        self._report(
            "INV-ADAPTER-002",
            node,
            (
                f"{what} orchestrates an external process from the HTTP adapter. "
                "backend/server.py must not run qpdf or any other subprocess: PDF "
                "linearization belongs in backend.pdf.pdf_linearize (call "
                "linearize_pdf_to / is_pdf_linearized); other external "
                "tools belong in a focused backend module"
            ),
        )

    def _check_adapter_process_import(self, node: ast.Import | ast.ImportFrom) -> None:
        """``import subprocess`` / ``from subprocess import run`` in the adapter,
        which also covers passing a subprocess callable along uncalled."""
        if not is_http_adapter_module(self.relpath):
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
                            "publish managed PDFs through backend.pdf.work_pdf_replace "
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
                            "use backend.storage.fs_durability helpers"
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
    findings: list[Finding] = []
    for _ in range(_MAX_ANALYSIS_PASSES):
        visitor = _InvariantVisitor(relpath, class_registry, function_registry)
        visitor.visit(tree)
        findings = visitor.findings
        snapshot = _registry_snapshot(class_registry, function_registry)
        if snapshot == previous:
            break
        previous = snapshot
    return findings


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


def check_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_production_python(root):
        rel = path.relative_to(root).as_posix()
        findings.extend(check_source(path.read_text(encoding="utf-8"), rel))
    return findings
