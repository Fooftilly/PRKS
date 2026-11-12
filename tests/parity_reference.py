"""The recorded SQLite reference of the parity tests (PG-C2 #641).

A parity test does the same calls on the PostgreSQL facade and on a reference,
and compares the results. The reference was SQLite's ``PRKSDatabase``; PG-C2
deleted SQLite, and the parity tests must keep proving that PostgreSQL answers
as SQLite did (``docs/postgresql-architecture.md`` §13.4: a guarantee is
retired only with a replacement). So the reference side is a
:class:`Reference`: a recording of every call SQLite made, with its result or
its error, replayed in the same order. A test with more than one reference (a
library and its research index) replays them in one order: each step records
its place among all the test's calls, so swapping two references' calls fails.

The recordings in ``tests/parity_reference/<module>.json`` were made with the
real ``PRKSDatabase`` before it was deleted, and nothing can record them again:
a test that changes its calls fails its replay, and its SQLite comparison is
then retired like any other SQLite test (``tests/sqlite_retirement/``). The
recording machinery stays, under ``PRKS_PARITY_RECORD=1``, for a reference
whose live object still exists (``tests/test_parity_reference.py``). A replay
checks each call against the recording: the method name and the arguments must
match.

Values a run makes up (entity ids, op and device UUIDs) differ between the
recording and a replay. The recording stores them as placeholders, numbered
by first appearance; a replay binds each placeholder to the value this run
uses at the same place, and a placeholder that first appears in a result (an
id SQLite generated) becomes a fresh id of the same kind.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import importlib
import json
import os
import re
import threading
import uuid
from typing import Any, Callable, Optional

RECORD_ENV = "PRKS_PARITY_RECORD"
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parity_reference")
# Entity ids (``backend.entity_ids.generate``) and UUIDs (op and device ids).
_VARYING = re.compile(
    r"\b(?:[A-Z]{1,4}-[0-9A-F]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})\b"
)
# Timestamps a run makes (``created_at`` of rows a test hands back): equal
# when they have the same format.
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?")
_PLACEHOLDER = re.compile(r"<([A-Z]{1,4}|~uuid|~UUID)#(\d+)>")
# Today's UTC date standing alone, as a run writes it: a source change sets the
# Work's ``urldate`` to it and BibTeX takes it from the day of the last edit.
# It is recorded as this placeholder and replayed as the replaying day, so a
# reference recorded yesterday still holds "the day the reference ran". The
# date inside a timestamp is left to ``_MOMENT``.
_TODAY_PLACEHOLDER = "<~today>"
_STANDALONE_DATE = r"(?<![\d-])%s(?![\d-]|[ T]\d\d:)"
_lock = threading.Lock()
# module file name -> {test id -> steps}; written once per module at exit.
_recorded: dict[str, dict[str, list[dict[str, Any]]]] = {}


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def recording() -> bool:
    return os.environ.get(RECORD_ENV, "").strip() == "1"


class _Varying:
    """Placeholders <-> this run's values, for one test."""

    def __init__(self) -> None:
        self.to_placeholder: dict[str, str] = {}
        self.to_value: dict[str, str] = {}

    def _bind(self, value: str, placeholder: str) -> None:
        self.to_placeholder[value] = placeholder
        self.to_value[placeholder] = value

    def hide(self, text: str) -> str:
        """``text`` with this run's varying values as placeholders (new ones numbered)."""
        def one(match: re.Match[str]) -> str:
            value = match.group(0)
            if value not in self.to_placeholder:
                prefix, _, rest = value.partition("-")
                if "-" not in rest:  # an entity id; a UUID has five groups, even one that opens in A-F
                    kind = prefix
                else:
                    kind = "~UUID" if any(c in "ABCDEF" for c in value) else "~uuid"
                self._bind(value, "<%s#%d>" % (kind, len(self.to_placeholder)))
            return self.to_placeholder[value]

        text = _VARYING.sub(one, text)
        return re.sub(_STANDALONE_DATE % re.escape(_today()), _TODAY_PLACEHOLDER, text)

    def show(self, text: str) -> str:
        """``text`` with placeholders as this run's values (unbound ones minted)."""
        def one(match: re.Match[str]) -> str:
            placeholder = match.group(0)
            if placeholder not in self.to_value:
                kind = match.group(1)
                if kind == "~uuid":
                    minted = str(uuid.uuid4())
                elif kind == "~UUID":
                    minted = str(uuid.uuid4()).upper()
                else:
                    minted = "%s-%s" % (kind, uuid.uuid4().hex.upper())
                self._bind(minted, placeholder)
            return self.to_value[placeholder]

        return _PLACEHOLDER.sub(one, text).replace(_TODAY_PLACEHOLDER, _today())


# --- encoding -----------------------------------------------------------------------------


def _encode(value: Any, text: Callable[[str], str]) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return text(value)
    if isinstance(value, list):
        return [_encode(item, text) for item in value]
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(item, text) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((_encode(item, text) for item in value), key=json.dumps)}
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {"__dict__": [[text(key), _encode(item, text)] for key, item in value.items()]}
        return {"__pairs__": [[_encode(key, text), _encode(item, text)] for key, item in value.items()]}
    if isinstance(value, Reference):
        return {"__reference__": object.__getattribute__(value, "_name")}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        fields = {f.name: _encode(getattr(value, f.name), text) for f in dataclasses.fields(value)}
        return {"__dataclass__": "%s:%s" % (cls.__module__, cls.__qualname__), "fields": fields}
    raise TypeError("the parity reference cannot record a %s" % type(value).__name__)


def _load_class(path: str) -> type:
    module, _, name = path.partition(":")
    target: Any = importlib.import_module(module)
    for part in name.split("."):
        target = getattr(target, part)
    return target


def _decode(value: Any, text: Callable[[str], str]) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return text(value)
    if isinstance(value, list):
        return [_decode(item, text) for item in value]
    if "__tuple__" in value:
        return tuple(_decode(item, text) for item in value["__tuple__"])
    if "__set__" in value:
        return {_decode(item, text) for item in value["__set__"]}
    if "__bytes__" in value:
        return base64.b64decode(value["__bytes__"])
    if "__dict__" in value:
        return {text(key): _decode(item, text) for key, item in value["__dict__"]}
    if "__pairs__" in value:
        return {_decode(key, text): _decode(item, text) for key, item in value["__pairs__"]}
    if "__dataclass__" in value:
        cls = _load_class(value["__dataclass__"])
        return cls(**{name: _decode(item, text) for name, item in value["fields"].items()})
    raise ValueError("unknown recorded value %r" % (value,))


def _error_record(exc: BaseException, text: Callable[[str], str]) -> dict[str, Any]:
    cls = type(exc)
    attrs = {}
    for name, item in vars(exc).items():
        try:
            attrs[name] = _encode(item, text)
        except TypeError:
            continue
    return {"class": "%s:%s" % (cls.__module__, cls.__qualname__),
            "args": _encode(list(exc.args), text), "attrs": attrs}


def _rebuild_error(record: dict[str, Any], text: Callable[[str], str]) -> BaseException:
    cls = _load_class(record["class"])
    exc = cls.__new__(cls)
    BaseException.__init__(exc, *_decode(record["args"], text))
    for name, item in record["attrs"].items():
        setattr(exc, name, _decode(item, text))
    return exc


class _Turns:
    """The order of every call of one test, across its references."""

    def __init__(self) -> None:
        self.next = 0

    def take(self) -> int:
        turn, self.next = self.next, self.next + 1
        return turn


# --- the reference -------------------------------------------------------------------------


class Reference:
    """One reference object of a parity test: live and recorded, or replayed.

    Attribute access returns a callable for every method name; calling it
    runs (recording) or replays one step. Two names are not methods:

    - ``sql(statement, params)``: one statement on the live connection, committed
      (a test arranging rows no method writes);
    - ``apply("module:function", *args)``: a module function of the reference.

    A ``Reference`` passed as an argument stands for its live object.
    """

    def __init__(self, test_id: str, name: str, module_file: str, open_live: Optional[Callable[[], Any]],
                 varying: _Varying, turns: Optional[_Turns] = None) -> None:
        object.__setattr__(self, "_test_id", test_id)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_module", os.path.basename(module_file).rsplit(".", 1)[0])
        object.__setattr__(self, "_varying", varying)
        object.__setattr__(self, "_position", 0)
        object.__setattr__(self, "_turns", turns or _Turns())
        if recording():
            if open_live is None:
                raise AssertionError("%s: its %s reference was SQLite, which PG-C2 deleted; its recording "
                                     "cannot be made again" % (test_id, name))
            object.__setattr__(self, "_live", open_live())
            object.__setattr__(self, "_steps", [])
            with _lock:
                _recorded.setdefault(self._module, {})[self._key] = self._steps
        else:
            object.__setattr__(self, "_live", None)
            object.__setattr__(self, "_steps", _load(self._module).get(self._key))
            if self._steps is None:
                raise AssertionError(
                    "no parity reference recorded for %s; run it with %s=1" % (test_id, RECORD_ENV))
            siblings = [key for key in _load(self._module) if key.split("#", 1)[0] == test_id]
            if len(siblings) > 1 and any("at" not in step for step in self._steps):
                raise AssertionError("%s has %d references but its recording does not order them; run it with "
                                     "%s=1" % (test_id, len(siblings), RECORD_ENV))

    @property
    def _key(self) -> str:
        name = object.__getattribute__(self, "_name")
        test_id = object.__getattribute__(self, "_test_id")
        return test_id if name == "library" else "%s#%s" % (test_id, name)

    def _hide(self, text: str) -> str:
        return object.__getattribute__(self, "_varying").hide(text)

    def _show(self, text: str) -> str:
        return object.__getattribute__(self, "_varying").show(text)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._step(name, args, kwargs)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the parity reference is read-only")

    def _step(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        hide, show = self._hide, self._show
        call = {"call": name, "args": _encode(list(args), hide), "kwargs": _encode(kwargs, hide)}
        live = object.__getattribute__(self, "_live")
        steps: list[dict[str, Any]] = object.__getattribute__(self, "_steps")
        position: int = object.__getattribute__(self, "_position")
        object.__setattr__(self, "_position", position + 1)
        turn = object.__getattribute__(self, "_turns").take()
        if live is not None:
            args = tuple(_live_of(arg) for arg in args)
            kwargs = {key: _live_of(arg) for key, arg in kwargs.items()}
            try:
                if name == "sql":
                    with live.connection() as conn:
                        conn.execute(*args, **kwargs)
                    result = None
                elif name == "apply":
                    result = _load_class(args[0])(*args[1:], **kwargs)
                else:
                    result = getattr(live, name)(*args, **kwargs)
            except Exception as exc:
                steps.append(call | {"at": turn, "raises": _error_record(exc, hide)})
                raise
            steps.append(call | {"at": turn, "result": _encode(result, hide)})
            return result
        if position >= len(steps):
            raise AssertionError("%s: call %d (%s) was never recorded" % (self._test_id, position, name))
        step = steps[position]
        if "at" in step and step["at"] != turn:
            raise AssertionError("%s: call %d (%s) of the %s reference was recorded as call %d of the test, "
                                 "not %d: its references ran in another order"
                                 % (self._test_id, position, name, object.__getattribute__(self, "_name"),
                                    step["at"], turn))
        recorded = {key: step[key] for key in ("call", "args", "kwargs")}
        if _moments(recorded) != _moments(call):
            raise AssertionError("%s: call %d differs from the recording:\n  recorded %s\n  now      %s"
                                 % (self._test_id, position, json.dumps(recorded), json.dumps(call)))
        if "raises" in step:
            raise _rebuild_error(step["raises"], show)
        return _decode(step["result"], show)

    def close(self) -> None:
        """Every recorded step must have been replayed."""
        if object.__getattribute__(self, "_live") is not None:
            return
        steps = object.__getattribute__(self, "_steps")
        position = object.__getattribute__(self, "_position")
        if position != len(steps):
            raise AssertionError("%s: %d recorded calls were not replayed"
                                 % (self._test_id, len(steps) - position))


def _moments(call: dict[str, Any]) -> str:
    return _MOMENT.sub(lambda match: "<moment %d>" % len(match.group(0)), json.dumps(call, sort_keys=True))


def _live_of(value: Any) -> Any:
    return object.__getattribute__(value, "_live") if isinstance(value, Reference) else value


_cache: dict[str, dict[str, Any]] = {}


def _path(module: str) -> str:
    return os.path.join(_DIR, module + ".json")


def _load(module: str) -> dict[str, Any]:
    with _lock:
        if module not in _cache:
            try:
                with open(_path(module), encoding="utf-8") as handle:
                    _cache[module] = json.load(handle)
            except FileNotFoundError:
                _cache[module] = {}
        return _cache[module]


def write_recordings() -> None:
    """Write every recording this process made, one file per module (merging tests)."""
    with _lock:
        modules = {module: dict(tests) for module, tests in _recorded.items()}
    os.makedirs(_DIR, exist_ok=True)
    for module, tests in modules.items():
        path = _path(module)
        try:
            with open(path, encoding="utf-8") as handle:
                existing = json.load(handle)
        except FileNotFoundError:
            existing = {}
        existing.update(tests)
        # One test per line, so a re-recording diffs by test.
        lines = ["%s: %s" % (json.dumps(test_id), json.dumps(steps, ensure_ascii=False, sort_keys=True,
                                                            separators=(",", ":")))
                 for test_id, steps in sorted(existing.items())]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{\n" + ",\n".join(lines) + "\n}\n")


if recording():
    import atexit

    atexit.register(write_recordings)


def reference_for(test: Any, open_live: Optional[Callable[[], Any]] = None, *,
                  name: str = "library") -> Reference:
    """The reference ``name`` of ``test`` (a ``unittest.TestCase``); replay is checked at cleanup.

    ``open_live`` opens the live object a recording runs; the parity tests'
    references were SQLite and pass none, so they can only be replayed.

    The references of one test share their placeholders, so an id one of
    them returned means the same thing passed to another, and one order of
    calls, so a replay runs them interleaved as the recording did.
    """
    test_id = _test_id(test)
    varying = test.__dict__.setdefault("_parity_reference_varying", _Varying())
    turns = test.__dict__.setdefault("_parity_reference_turns", _Turns())
    created = test.__dict__.get("_parity_reference_names")
    if created is None:
        created = test.__dict__["_parity_reference_names"] = set()
        if not recording():
            # Registered first, so it runs after every reference's own close().
            test.addCleanup(check_every_reference_created, test)
    created.add(name)
    ref = Reference(test_id, name, _module_file(test), open_live, varying, turns)
    test.addCleanup(ref.close)
    return ref


def check_every_reference_created(test: Any) -> None:
    """A replayed test must create every reference its recording holds.

    Each reference's ``close()`` checks its own steps; a reference the test
    no longer creates has no ``close()``, so its recording would be dropped
    silently.
    """
    test_id = _test_id(test)
    module = os.path.basename(_module_file(test)).rsplit(".", 1)[0]
    recorded = {key.partition("#")[2] or "library" for key in _load(module) if key.split("#", 1)[0] == test_id}
    missing = sorted(recorded - test.__dict__.get("_parity_reference_names", set()))
    if missing:
        raise AssertionError("%s: the recording holds the %s reference(s), which the test never created; "
                             "run it with %s=1" % (test_id, ", ".join(missing), RECORD_ENV))


def _test_id(test: Any) -> str:
    return "%s.%s" % (type(test).__name__, test._testMethodName)


def _module_file(test: Any) -> str:
    return importlib.import_module(type(test).__module__).__file__ or ""
