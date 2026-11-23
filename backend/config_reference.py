"""Every PRKS setting in one place: scope, default, validation, secrecy (#612 Phase A).

This is the configuration contract. Each :class:`Setting` names one key,
where it lives (its :class:`Scope`), who reads it, its default, whether it is
a secret, and whether a change needs a restart. ``docs/configuration-reference.md``
is generated from it (``python -m backend.config_reference --write``), and
tests keep both in step with the code: every ``PRKS_*`` name the backend
reads must be listed here, and every Compose variable in ``.env.example``.

Scopes (#612):

- ``deployment``: the host's environment, read once at process start.
  Only the operator changes it (environment, Compose ``.env``, a secret
  file), never the browser; a change takes effect after a restart.
- ``compose``: read by ``docker-compose.yml`` itself, not by PRKS.
- ``library``: stored in PostgreSQL per Library, edited in Settings by the
  Library's owner; takes effect at once and follows the Library in backups.
- ``retired``: no longer read. A process that finds one set refuses to start,
  so a stale deployment never runs with a setting silently ignored.
- ``internal``: test and packaging plumbing, not for operators.

Validation: :func:`problems` checks every set ``deployment`` value whose
:attr:`Setting.check` is defined, and flags every set ``retired`` key. Every
role runs it before it touches the database, the storage root or the network
(``prks_app.refuse_invalid_environment``), so an invalid value stops the
process with exit status 2 instead of falling back to a default. Settings
without a ``check`` are validated just as strictly by the module that parses
them (named in ``owner``), also before anything is served.

Nothing here reads a secret's value beyond whether it is set.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Scope(Enum):
    DEPLOYMENT = "deployment"
    COMPOSE = "compose"
    LIBRARY = "library"
    RETIRED = "retired"
    INTERNAL = "internal"


Check = Callable[[str], Optional[str]]

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")
# logging.getLevelName knows these names; WARN and FATAL are its aliases.
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL", "NOTSET")


def boolean(text: str) -> Optional[str]:
    return None if text.strip().lower() in _TRUE + _FALSE else "must be 1, true, yes, on, 0, false, no or off"


def zero_or_one(text: str) -> Optional[str]:
    return None if text.strip() in ("0", "1") else "must be 0 or 1"


def integer(low: int, high: Optional[int] = None) -> Check:
    def check(text: str) -> Optional[str]:
        try:
            value = int(text.strip(), 10)
        except ValueError:
            value = None
        if value is None or value < low or (high is not None and value > high):
            return f"must be a whole number from {low}" + (f" to {high}" if high is not None else " up")
        return None

    return check


def number(low: float, high: float) -> Check:
    def check(text: str) -> Optional[str]:
        try:
            value = float(text.strip())
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or not low <= value <= high:
            return f"must be a number from {low:g} to {high:g}"
        return None

    return check


def trusted_hosts(text: str) -> Optional[str]:
    from backend.server import parse_trusted_hosts

    try:
        parse_trusted_hosts(text)
    except ValueError:
        return "must be host names separated by commas (no ports, paths or wildcards)"
    return None


def log_level(text: str) -> Optional[str]:
    return None if text.strip().upper() in _LOG_LEVELS else "must be one of " + ", ".join(_LOG_LEVELS)


@dataclass(frozen=True)
class Setting:
    key: str
    scope: Scope
    summary: str
    default: str = "unset"
    roles: tuple[str, ...] = ()
    secret: bool = False
    # A deployment value is read at start: a change needs a restart.
    restart: bool = True
    check: Optional[Check] = None
    # The module that parses (and, without ``check``, validates) it.
    owner: str = ""

    @property
    def editable_in_browser(self) -> bool:
        return self.scope is Scope.LIBRARY


_ALL_ROLES = ("serve", "migrate", "worker", "admin")
_D = Scope.DEPLOYMENT

SETTINGS: tuple[Setting, ...] = (
    # --- storage and database ---------------------------------------------------------
    Setting("PRKS_STORAGE", _D, "The data root: managed PDFs, portraits, thumbnails and the root marker.",
            "data/ (testing: data_testing/)", ("serve", "migrate", "worker"), owner="backend.storage.config"),
    Setting("PRKS_CONFIG_FILE", _D, "Path of the bootstrap configuration file; its directory also holds "
            "the bootstrap-token file. Ignored in testing mode.",
            "the platform configuration directory", ("serve", "migrate", "worker", "admin"),
            owner="backend.storage.resolver"),
    Setting("PRKS_DATABASE_URL", _D, "PostgreSQL connection string. Set this or PRKS_DATABASE_URL_FILE.",
            "none (testing: the scripts/dev-postgres server)", _ALL_ROLES, secret=True,
            owner="backend.db.config"),
    Setting("PRKS_DATABASE_URL_FILE", _D, "File holding the connection string (a Compose secret). Preferred.",
            "unset", _ALL_ROLES, owner="backend.db.config"),
    Setting("PRKS_DB_POOL_MAX", _D, "Largest connection pool of the web process and of migrate.",
            "10", ("serve", "migrate"), owner="backend.db.config"),
    Setting("PRKS_MIGRATE_ON_START", _D, "1 applies the schema at start; 0 leaves it to migrate and only verifies it.",
            "1", ("serve",), check=zero_or_one, owner="prks_app"),
    # --- web server and sign-in -------------------------------------------------------
    Setting("PRKS_AUTH", _D, "session (password sign-in) or loopback-owner (testing on loopback only).",
            "session (testing: loopback-owner)", ("serve",), owner="backend.domain.identity.web_start"),
    Setting("PRKS_EMBEDDED_WORKER", _D, "1 runs a job worker inside the web process; 0 leaves jobs to a worker.",
            "1", ("serve",), check=zero_or_one, owner="backend.jobs.embedded"),
    Setting("PRKS_HTTP_THREADS", _D, "Requests that run PRKS work at the same time, 1 to 256.",
            "16", ("serve",), owner="backend.web.settings"),
    Setting("PRKS_TLS_CERT_FILE", _D, "PEM certificate chain; with PRKS_TLS_KEY_FILE PRKS serves HTTPS itself.",
            "unset", ("serve",), owner="backend.web.settings"),
    Setting("PRKS_TLS_KEY_FILE", _D, "Unencrypted PEM private key for PRKS_TLS_CERT_FILE.",
            "unset", ("serve",), owner="backend.web.settings"),
    Setting("PRKS_PUBLIC_ORIGIN", _D, "The origin browsers use behind an HTTPS reverse proxy.",
            "the request's own origin", ("serve",), owner="backend.domain.identity.web_start"),
    Setting("PRKS_TRUSTED_PROXIES", _D, "Proxy addresses or networks whose X-Forwarded-For is believed.",
            "none", ("serve",), owner="backend.domain.identity.login_limiter"),
    Setting("PRKS_TRUSTED_HOSTS", _D, "Extra Host header names the server accepts, comma-separated.",
            "none", ("serve",), check=trusted_hosts, owner="backend.server"),
    Setting("PRKS_SESSION_IDLE_DAYS", _D, "Days a session stays valid without use, 1 to 365.",
            "30", ("serve",), owner="backend.domain.identity.sessions"),
    Setting("PRKS_LOGIN_WINDOW_SECONDS", _D, "Length of the sign-in rate-limit window.",
            "900", ("serve",), owner="backend.domain.identity.login_limiter"),
    Setting("PRKS_LOGIN_ACCOUNT_LIMIT", _D, "Sign-in attempts per account per window from an unknown device.",
            "5", ("serve",), owner="backend.domain.identity.login_limiter"),
    Setting("PRKS_LOGIN_ADDRESS_LIMIT", _D, "Sign-in attempts per client address per window.",
            "20", ("serve",), owner="backend.domain.identity.login_limiter"),
    Setting("PRKS_LOGIN_DEVICE_LIMIT", _D, "Sign-in attempts per known sign-in device per window.",
            "5", ("serve",), owner="backend.domain.identity.login_limiter"),
    Setting("PRKS_BOOTSTRAP_TOKEN_FILE", _D, "An owner-only file holding the setup token an installer chose.",
            "unset", ("admin",), owner="backend.domain.identity.admin"),
    # --- logging and diagnostics ------------------------------------------------------
    Setting("PRKS_LOG_LEVEL", _D, "Stderr log level.", "INFO", ("serve", "worker"), check=log_level,
            owner="backend.log_config"),
    Setting("PRKS_LOG_FILE_LEVEL", _D, "Persistent file log level.", "ERROR", ("serve",), check=log_level,
            owner="backend.log_config"),
    Setting("PRKS_LOG_RETENTION_DAYS", _D, "Rotated persistent log copies to keep, 1 to 3650.", "7", ("serve",),
            check=integer(1, 3650), owner="backend.log_config"),
    Setting("PRKS_LOG_FILE", _D, "Path of the rotating error log.", "<data root>/prks-errors.log", ("serve",),
            owner="backend.storage.config"),
    Setting("PRKS_PERF_SLOW_MS", _D, "API request duration counted as slow, 10 to 60000 ms.", "250", ("serve",),
            check=number(10, 60000), owner="backend.performance"),
    Setting("PRKS_PERF_LOG_SLOW", _D, "Log a privacy-safe slow_request line for slow API requests.", "off",
            ("serve",), check=boolean, owner="backend.performance"),
    # --- files and processing ---------------------------------------------------------
    Setting("PRKS_BACKUP_MAX_UPLOAD_BYTES", _D, "Largest uploaded .prks-backup restore archive, in bytes.",
            "68719476736 (64 GiB)", ("serve",), check=integer(1), owner="backend.domain.maintenance.upload"),
    Setting("PRKS_PDF_LINEARIZE", _D, "Linearize PDFs on import. Unset: on when qpdf is installed.",
            "auto", ("serve", "worker"), check=boolean, owner="backend.pdf_linearize"),
    Setting("PRKS_THUMB_LOSSLESS", _D, "Lossless thumbnail cache encoding (debugging).", "off", ("serve",),
            check=boolean, owner="backend.server"),
    # --- compose (.env) ---------------------------------------------------------------
    Setting("UID", Scope.COMPOSE, "Host user the PRKS containers run as.", "1000", ("compose",)),
    Setting("GID", Scope.COMPOSE, "Host group the PRKS containers run as.", "1000", ("compose",)),
    Setting("PRKS_DATA_DIR", Scope.COMPOSE, "Host directory mounted at /data as the data root.", "./data",
            ("compose",)),
    Setting("PRKS_DB_PASSWORD_FILE", Scope.COMPOSE, "Owner-only file with the database password (db only).",
            "./secrets/db_password", ("compose",)),
    Setting("PRKS_DB_URL_FILE", Scope.COMPOSE, "Owner-only file with the database URL (the prks_database_url "
            "secret).", "./secrets/database_url", ("compose",)),
    Setting("PRKS_PUBLISH_HOST", Scope.COMPOSE, "Host address the web port is published on.", "127.0.0.1",
            ("compose",)),
    Setting("PRKS_PUBLISH_PORT", Scope.COMPOSE, "Host port the web port is published on.", "8080", ("compose",)),
    # --- library (Settings) -----------------------------------------------------------
    Setting("annotation_author", Scope.LIBRARY, "Author name written into PDF annotations.", "empty",
            restart=False, owner="backend.domain.settings.app_settings"),
    Setting("bibtex_export_fields", Scope.LIBRARY, "Fields included in BibTeX export.", "every field",
            restart=False, owner="backend.domain.settings.app_settings"),
    Setting("citation_style", Scope.LIBRARY, "The Library's default citation style.", "the built-in default",
            restart=False, owner="backend.domain.settings.app_settings"),
    # --- retired ----------------------------------------------------------------------
    Setting("PRKS_LIBRARY_BACKEND", Scope.RETIRED, "PostgreSQL is the only Library backend (PG-C2 #641); its database is PRKS_DATABASE_URL.",
            owner="prks_app"),
    Setting("PRKS_FOR_PROCESSING_DIR", Scope.RETIRED, "Files for Processing live in the Library (PG-C2 #641).",
            owner="backend.storage.config"),
    # --- internal ---------------------------------------------------------------------
    Setting("PRKS_TESTING", Scope.INTERNAL, "Set by --testing: testing paths, port 8070, stricter checks.",
            "off", _ALL_ROLES, owner="backend.storage.config"),
    Setting("PRKS_CONTAINER", Scope.INTERNAL, "Set by the Docker image: tailors dependency-gate advice.",
            "off", _ALL_ROLES, owner="backend.dependency_gate"),
    Setting("PRKS_E2E", Scope.INTERNAL, "Browser E2E harness marker.", "off", ("serve",),
            owner="backend.startup_profile"),
    Setting("PRKS_E2E_STARTUP_PROFILE", Scope.INTERNAL, "Browser E2E startup-phase profiling.", "off", ("serve",),
            owner="backend.startup_profile"),
)

BY_KEY: dict[str, Setting] = {setting.key: setting for setting in SETTINGS}


def in_scope(scope: Scope) -> tuple[Setting, ...]:
    return tuple(setting for setting in SETTINGS if setting.scope is scope)


def problems(environ: Mapping[str, str]) -> list[str]:
    """Every invalid set deployment value and every set retired key, as fixed text.

    A message names the variable and what it must be, never its value (a
    value can be a secret typed into the wrong variable).
    """
    found: list[str] = []
    for setting in SETTINGS:
        raw = environ.get(setting.key)
        if raw is None:
            continue
        if setting.scope is Scope.RETIRED:
            found.append(
                f"{setting.key} is no longer read: {setting.summary} Remove {setting.key} from the "
                "environment (and from any .env or Compose file that sets it)."
            )
            continue
        if setting.scope is not Scope.DEPLOYMENT or setting.check is None or not raw.strip():
            continue
        problem = setting.check(raw)
        if problem is not None:
            found.append(f"{setting.key} {problem}.")
    return found


@dataclass(frozen=True)
class Effective:
    key: str
    shown: str
    source: str  # "environment" or "default"


def effective(
    environ: Mapping[str, str], invalid: Collection[str] = (), unchecked: Collection[str] = ()
) -> list[Effective]:
    """The deployment settings as this environment sets them.

    A secret shows only that it is set, and an invalid value only that it is
    invalid (it may be a secret typed into the wrong variable). ``invalid``
    names the keys their owner's parser refused (settings without a ``check``);
    ``unchecked`` names the keys that parser could not reach because another
    of its problems stopped it, shown as ``(not checked)`` and never echoed.
    """
    rows: list[Effective] = []
    for setting in in_scope(Scope.DEPLOYMENT):
        raw = (environ.get(setting.key) or "").strip()
        if not raw:
            rows.append(Effective(setting.key, setting.default, "default"))
        elif setting.secret:
            rows.append(Effective(setting.key, "(set, hidden)", "environment"))
        elif setting.key in invalid or (setting.check is not None and setting.check(raw) is not None):
            rows.append(Effective(setting.key, "(invalid)", "environment"))
        elif setting.key in unchecked:
            rows.append(Effective(setting.key, "(not checked)", "environment"))
        else:
            rows.append(Effective(setting.key, raw, "environment"))
    return rows


# --- the generated reference ------------------------------------------------------------

DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs",
                        "configuration-reference.md")

_SCOPE_TEXT = {
    Scope.DEPLOYMENT: (
        "Deployment (environment)",
        "Set in the host's environment, Compose `.env` or a secret file, by whoever runs PRKS. Read once "
        "at start: a change needs a restart. The browser can never change them. An invalid value stops the "
        "process with exit status 2; nothing falls back to a default.",
    ),
    Scope.COMPOSE: (
        "Compose (`.env`)",
        "Read by `docker-compose.yml` itself; `.env.example` lists them. Never put a password or a database "
        "URL in `.env`.",
    ),
    Scope.LIBRARY: (
        "Library (Settings)",
        "Stored in PostgreSQL per Library and edited in Settings by the Library's owner. A change applies at "
        "once and follows the Library in its backups.",
    ),
    Scope.RETIRED: (
        "Retired",
        "No longer read. A process that finds one set refuses to start; remove it.",
    ),
    Scope.INTERNAL: (
        "Internal",
        "Set by PRKS's own tooling (`--testing`, the Docker image, the E2E harness). Not for operators.",
    ),
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_reference() -> str:
    lines = [
        "# Configuration reference",
        "",
        "<!-- Generated by `python -m backend.config_reference --write` from backend/config_reference.py."
        " Do not edit by hand. -->",
        "",
        "Every setting PRKS knows, by where it lives (#612). Browser-local preferences (theme, layout,"
        " workspace tabs) stay in each browser and are not listed here. Detailed behavior of each deployment"
        " setting is on the wiki's Configuration and Operations page.",
        "",
        "Check a deployment before starting it with `python prks_app.py config check`: it validates every"
        " setting the way a start does and prints the effective values and where each comes from, with"
        " secrets hidden. It reads the data root's path but not the root itself: a start can still refuse a"
        " root for its filesystem or its contents.",
        "",
    ]
    for scope in Scope:
        title, text = _SCOPE_TEXT[scope]
        lines += [f"## {title}", "", text, ""]
        rows = in_scope(scope)
        if scope is Scope.RETIRED:
            lines += ["| Key | Why |", "| --- | --- |"]
            lines += [f"| `{s.key}` | {_cell(s.summary)} |" for s in rows]
        elif scope is Scope.COMPOSE:
            lines += ["| Key | Default | Purpose |", "| --- | --- | --- |"]
            lines += [f"| `{s.key}` | {_cell(s.default)} | {_cell(s.summary)} |" for s in rows]
        else:
            lines += ["| Key | Read by | Default | Secret | Purpose |", "| --- | --- | --- | --- | --- |"]
            for s in rows:
                read_by = ", ".join(f"`{role}`" for role in s.roles) or "Settings"
                lines.append(
                    f"| `{s.key}` | {read_by} | {_cell(s.default)} | {'yes' if s.secret else 'no'} |"
                    f" {_cell(s.summary)} |"
                )
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.config_reference")
    parser.add_argument("--write", action="store_true", help=f"write {DOC_PATH}")
    args = parser.parse_args(argv)
    text = render_reference()
    if args.write:
        with open(DOC_PATH, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
