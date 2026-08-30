import logging
import os
from logging.handlers import TimedRotatingFileHandler

from backend.log_safety import PrivacySafeFormatter, safe_error_type
from backend.storage.config import StorageConfig


_DEFAULT_ROTATE_WHEN = "midnight"
_DEFAULT_ROTATE_INTERVAL = 1
_DEFAULT_RETENTION_DAYS = 7
_OWNER_ONLY_MODE = 0o600
LOGGER = logging.getLogger("prks.log_config")


def _resolve_log_level() -> int:
    raw = (os.environ.get("PRKS_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _resolve_file_log_level() -> int:
    raw = (os.environ.get("PRKS_LOG_FILE_LEVEL") or "ERROR").strip().upper()
    return getattr(logging, raw, logging.ERROR)


def _resolve_retention_days() -> int:
    raw = (os.environ.get("PRKS_LOG_RETENTION_DAYS") or "").strip()
    if not raw:
        return _DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except ValueError:
        return _DEFAULT_RETENTION_DAYS
    return max(1, days)


def _chmod_owner_only(path: str, *, report: bool = False) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, _OWNER_ONLY_MODE)
        mode = os.stat(path).st_mode
    except OSError as exc:
        if report:
            LOGGER.error("log_file_mode_failed error_type=%s", safe_error_type(exc))
        return
    if report and (mode & 0o077):
        LOGGER.error("log_file_mode_insecure")


class _OwnerOnlyTimedRotatingFileHandler(TimedRotatingFileHandler):
    def _open(self):
        if os.name == "posix":
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            fd = os.open(self.baseFilename, flags, _OWNER_ONLY_MODE)
            try:
                stream = os.fdopen(
                    fd,
                    self.mode,
                    encoding=self.encoding,
                    errors=getattr(self, "errors", None),
                )
            except Exception:
                os.close(fd)
                raise
            _chmod_owner_only(self.baseFilename)
            return stream
        return super()._open()

    def doRollover(self):
        super().doRollover()
        _chmod_owner_only(self.baseFilename, report=True)


def setup_logging(config: StorageConfig) -> None:
    root = logging.getLogger()
    if getattr(root, "_prks_logging_configured", False):
        return

    level = _resolve_log_level()
    file_level = _resolve_file_log_level()
    log_file = config.log_file
    retention_days = _resolve_retention_days()
    parent = os.path.dirname(log_file) or "."
    os.makedirs(parent, exist_ok=True)

    formatter = PrivacySafeFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = _OwnerOnlyTimedRotatingFileHandler(
        log_file,
        when=_DEFAULT_ROTATE_WHEN,
        interval=_DEFAULT_ROTATE_INTERVAL,
        backupCount=retention_days,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
    root._prks_logging_configured = True

    _chmod_owner_only(log_file, report=True)

    LOGGER.info(
        "logging_initialized level=%s file_level=%s retention_days=%s",
        logging.getLevelName(level),
        logging.getLevelName(file_level),
        retention_days,
    )
