import logging
import os
from logging.handlers import TimedRotatingFileHandler


_DEFAULT_ROTATE_WHEN = "midnight"
_DEFAULT_ROTATE_INTERVAL = 1
_DEFAULT_RETENTION_DAYS = 7


def _is_testing_env() -> bool:
    v = (os.environ.get("PRKS_TESTING") or "").strip().lower()
    return v in ("1", "true", "yes")


def _resolve_log_file_path() -> str:
    configured = (os.environ.get("PRKS_LOG_FILE") or "").strip()
    if configured:
        return configured
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    storage_root = (os.environ.get("PRKS_STORAGE") or "").strip()
    if _is_testing_env():
        if storage_root and not (storage_root == "/data" or storage_root.startswith("/data/")):
            return os.path.join(storage_root, "prks-errors.log")
        return os.path.join(project_root, "data_testing", "prks-errors.log")
    if storage_root:
        return os.path.join(storage_root, "prks-errors.log")
    return os.path.join(project_root, "data", "prks-errors.log")


def _resolve_log_level() -> int:
    raw = (os.environ.get("PRKS_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _resolve_retention_days() -> int:
    raw = (os.environ.get("PRKS_LOG_RETENTION_DAYS") or "").strip()
    if not raw:
        return _DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except ValueError:
        return _DEFAULT_RETENTION_DAYS
    return max(1, days)


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_prks_logging_configured", False):
        return

    level = _resolve_log_level()
    log_file = _resolve_log_file_path()
    retention_days = _resolve_retention_days()
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = TimedRotatingFileHandler(
        log_file,
        when=_DEFAULT_ROTATE_WHEN,
        interval=_DEFAULT_ROTATE_INTERVAL,
        backupCount=retention_days,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
    root._prks_logging_configured = True

    logging.getLogger(__name__).info(
        "logging_initialized log_file=%s level=%s rotate_when=%s retention_days=%s",
        log_file,
        logging.getLevelName(level),
        _DEFAULT_ROTATE_WHEN,
        retention_days,
    )
