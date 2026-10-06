"""The storage-root resolver (storage-architecture §5.2, §5.3, §6, V1).

Exactly one function, :func:`resolve_storage_root`, decides which directory is
the PRKS data root and which source chose it. Precedence:

1. ``--storage-root`` on the command line (``cli``);
2. the ``PRKS_STORAGE`` environment variable (``env``);
3. ``storage.local_root`` in the bootstrap configuration file (``config_file``);
4. the default: the platform application-data location for a **packaged**
   distribution (``platform_default``), or the repository ``data/`` directory
   for a source checkout (``development_default``).

The first source that is set wins completely. A set-but-invalid source is an
error, never a fall-through: falling through could open a different, possibly
empty, library and look like data loss.

Testing mode never consults the bootstrap file or the platform default. It uses
the CLI or ``PRKS_STORAGE`` when set, otherwise ``<repo>/data_testing``.

This module never reads ``os.environ`` itself; the caller
(``StorageConfig.from_env``) passes the environment in, so the environment is
still parsed in exactly one place.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import sys
from typing import Callable, Mapping, NamedTuple, Optional

from backend.storage import distribution as _distribution
from backend.storage import paths
from backend.storage.errors import InvalidStorageRoot

SOURCE_CLI = "cli"
SOURCE_ENV = "env"
SOURCE_CONFIG_FILE = "config_file"
SOURCE_PLATFORM_DEFAULT = "platform_default"
SOURCE_DEVELOPMENT_DEFAULT = "development_default"
ROOT_SOURCES = (
    SOURCE_CLI,
    SOURCE_ENV,
    SOURCE_CONFIG_FILE,
    SOURCE_PLATFORM_DEFAULT,
    SOURCE_DEVELOPMENT_DEFAULT,
)
# Sources an operator set explicitly for this process. In-app relocation and
# root selection are unavailable for them (S10); the UI arrives in Phase D.
ADMINISTRATOR_SOURCES = frozenset({SOURCE_CLI, SOURCE_ENV})

PLATFORM_XDG = "xdg"
PLATFORM_MACOS = "macos"
PLATFORM_WINDOWS = "windows"

CONFIG_FILE_ENV = "PRKS_CONFIG_FILE"
STORAGE_ENV = "PRKS_STORAGE"

_POSIX_VAR_RE = re.compile(r"\$(?:(\w+)|\{([^}]*)\})")
_WINDOWS_VAR_RE = re.compile(r"%([^%]+)%")


class ResolvedRoot(NamedTuple):
    """``(root, source)``: the normalized absolute root and what selected it."""

    root: str
    source: str


def platform_family(override: Optional[str] = None) -> str:
    """``xdg``, ``macos`` or ``windows``. Unknown platforms use the XDG rule."""
    if override is not None:
        return override
    if os.name == "nt":
        return PLATFORM_WINDOWS
    if sys.platform == "darwin":
        return PLATFORM_MACOS
    return PLATFORM_XDG


def _pathmod(family: str):
    return ntpath if family == PLATFORM_WINDOWS else posixpath


def _home(environ: Mapping[str, str], family: str) -> str:
    key = "USERPROFILE" if family == PLATFORM_WINDOWS else "HOME"
    value = (environ.get(key) or "").strip()
    if value:
        return value
    return os.path.expanduser("~")


def _absolute_env_dir(environ: Mapping[str, str], key: str, family: str) -> Optional[str]:
    # The XDG base-directory spec says a relative value must be ignored, and the
    # same caution applies to %LOCALAPPDATA%: a relative location would move
    # with the working directory.
    value = (environ.get(key) or "").strip()
    if value and _pathmod(family).isabs(value):
        return value
    return None


def bootstrap_config_path(
    environ: Mapping[str, str],
    *,
    family: Optional[str] = None,
) -> str:
    """Where the bootstrap configuration file lives (§6), honoring ``PRKS_CONFIG_FILE``."""
    override = (environ.get(CONFIG_FILE_ENV) or "").strip()
    if override:
        return normalize_root_spelling(override, environ=environ, what=CONFIG_FILE_ENV)
    fam = platform_family(family)
    mod = _pathmod(fam)
    home = _home(environ, fam)
    if fam == PLATFORM_WINDOWS:
        base = _absolute_env_dir(environ, "LOCALAPPDATA", fam) or mod.join(home, "AppData", "Local")
        return mod.join(base, "PRKS", "config.json")
    if fam == PLATFORM_MACOS:
        return mod.join(home, "Library", "Application Support", "PRKS", "config.json")
    base = _absolute_env_dir(environ, "XDG_CONFIG_HOME", fam) or mod.join(home, ".config")
    return mod.join(base, "prks", "config.json")


def platform_default_root(
    environ: Mapping[str, str],
    *,
    family: Optional[str] = None,
) -> str:
    """The packaged per-user default data root (§6). Local, never roaming."""
    fam = platform_family(family)
    mod = _pathmod(fam)
    home = _home(environ, fam)
    if fam == PLATFORM_WINDOWS:
        base = _absolute_env_dir(environ, "LOCALAPPDATA", fam) or mod.join(home, "AppData", "Local")
        return mod.join(base, "PRKS", "Library")
    if fam == PLATFORM_MACOS:
        return mod.join(home, "Library", "Application Support", "PRKS", "Library")
    base = _absolute_env_dir(environ, "XDG_DATA_HOME", fam) or mod.join(home, ".local", "share")
    return mod.join(base, "prks", "library")


def development_default_root(*, testing: bool) -> str:
    """The source checkout's default root: ``data_testing/`` or ``data/``."""
    return paths.defaulted_storage_root(testing=testing, configured_root=None)


def _expand_vars(value: str, environ: Mapping[str, str]) -> str:
    """Expand ``$VAR``/``${VAR}`` (and ``%VAR%`` on Windows) from ``environ``.

    Like ``os.path.expandvars``, an unknown reference is left as written. It is
    reimplemented only so the caller's environment mapping is used instead of
    the process environment.
    """

    def posix(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2) or ""
        return environ[name] if name in environ else match.group(0)

    expanded = _POSIX_VAR_RE.sub(posix, value)
    if os.name == "nt":  # pragma: no cover - Windows only
        expanded = _WINDOWS_VAR_RE.sub(
            lambda m: environ.get(m.group(1), m.group(0)), expanded
        )
    return expanded


def _expand_user(value: str, environ: Mapping[str, str]) -> str:
    if value == "~" or value.startswith("~/") or (os.name == "nt" and value.startswith("~\\")):
        return _home(environ, platform_family()) + value[1:]
    if value.startswith("~"):
        return os.path.expanduser(value)
    return value


def normalize_root_spelling(
    raw: Optional[str],
    *,
    environ: Mapping[str, str],
    what: str,
    cwd: Optional[str] = None,
) -> str:
    """V1: expand ``~`` and environment references once, make absolute, collapse lexically.

    The result resolves identically after a restart from another working
    directory. Symlinks are **not** resolved here: the root itself may be a
    link (§7.3), and it is resolved once per bind by the binder.
    """
    if raw is None:
        raise InvalidStorageRoot("root_empty", f"{what} is empty.")
    value = str(raw).strip()
    if not value:
        raise InvalidStorageRoot("root_empty", f"{what} is empty.")
    if "\x00" in value:
        raise InvalidStorageRoot("root_unparseable", f"{what} is not a valid path.")
    value = _expand_user(_expand_vars(value, environ), environ)
    if not value.strip():
        raise InvalidStorageRoot("root_empty", f"{what} is empty.")
    if not os.path.isabs(value):
        value = os.path.join(cwd if cwd is not None else os.getcwd(), value)
    return os.path.normpath(value)


BootstrapReader = Callable[[str], Optional[object]]


def resolve_storage_root(
    *,
    cli_root: Optional[str],
    environ: Mapping[str, str],
    testing: bool,
    distribution: Optional[str] = None,
    family: Optional[str] = None,
    bootstrap_reader: Optional[BootstrapReader] = None,
) -> ResolvedRoot:
    """Return ``(root, source)`` under the §5.2 precedence.

    ``cli_root`` is ``None`` when ``--storage-root`` was not given; any other
    value, including an empty string, counts as set. ``PRKS_STORAGE`` set to
    only whitespace counts as unset, as it always has.
    """
    if cli_root is not None:
        return ResolvedRoot(
            normalize_root_spelling(cli_root, environ=environ, what="--storage-root"),
            SOURCE_CLI,
        )
    env_root = paths.parse_configured_root(environ.get(STORAGE_ENV))
    if env_root is not None:
        return ResolvedRoot(
            normalize_root_spelling(env_root, environ=environ, what=STORAGE_ENV),
            SOURCE_ENV,
        )
    if testing:
        # §5.3: never the bootstrap file, never the platform default.
        return ResolvedRoot(development_default_root(testing=True), SOURCE_DEVELOPMENT_DEFAULT)

    if bootstrap_reader is None:
        from backend.storage.bootstrap_config import read_bootstrap_config

        bootstrap_reader = read_bootstrap_config
    config = bootstrap_reader(bootstrap_config_path(environ, family=family))
    local_root = getattr(config, "local_root", None) if config is not None else None
    if local_root is not None:
        return ResolvedRoot(local_root, SOURCE_CONFIG_FILE)

    dist = _distribution.DISTRIBUTION if distribution is None else distribution
    if dist == _distribution.PACKAGED:
        return ResolvedRoot(
            os.path.normpath(platform_default_root(environ, family=family)),
            SOURCE_PLATFORM_DEFAULT,
        )
    return ResolvedRoot(development_default_root(testing=False), SOURCE_DEVELOPMENT_DEFAULT)
