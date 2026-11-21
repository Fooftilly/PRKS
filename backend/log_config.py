import logging
import os
import time
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
    """The error log: owner-only, rotated at midnight, shared by processes.

    Several PRKS processes on one root (web processes, ``worker``) write the
    same file, each through its own handler. The stdlib rollover assumes one
    writer: a second process that finds the day already rotated returns
    without reopening, so it keeps writing into the rotated file and retries
    on every record. Here a rollover hard-links the file to its dated name,
    which only one process can do for a given day, and every process reopens
    the log when the name no longer points at the file it holds.
    """

    def emit(self, record):
        self._reopen_if_moved()
        super().emit(record)

    def _reopen_if_moved(self) -> None:
        """Reopen when another process rotated the file this handler holds."""
        if self.stream is None:
            return
        try:
            held = os.fstat(self.stream.fileno())
        except (OSError, ValueError):
            return
        try:
            if os.path.samestat(held, os.stat(self.baseFilename)):
                return
        except FileNotFoundError:
            pass
        except OSError:
            return
        self.stream.close()
        self.stream = self._open()

    def doRollover(self):
        current = int(time.time())
        t = self.rolloverAt - self.interval
        time_tuple = time.gmtime(t) if self.utc else time.localtime(t)
        if not self.utc:
            dst_now = time.localtime(current)[-1]
            if dst_now != time_tuple[-1]:
                time_tuple = time.localtime(t + (3600 if dst_now else -3600))
        dated = self.rotation_filename(self.baseFilename + "." + time.strftime(self.suffix, time_tuple))
        if self.stream:
            self.stream.close()
            self.stream = None
        if self._rotate_once(dated) and self.backupCount > 0:
            for old in self.getFilesToDelete():
                try:
                    os.remove(old)
                except FileNotFoundError:
                    pass  # another process removed it first
        if not self.delay:
            self.stream = self._open()
        self.rolloverAt = self.computeRollover(current)
        _chmod_owner_only(self.baseFilename, report=True)

    def _rotate_once(self, dated: str) -> bool:
        """Give the log its dated name unless a process already did; True when this one did."""
        try:
            os.link(self.baseFilename, dated)
        except (FileExistsError, FileNotFoundError):
            return False  # rotated already, by another process
        except OSError:
            # No hard links here: rename, which only a process that still
            # finds no dated file attempts.
            if os.path.exists(dated):
                return False
            try:
                os.rename(self.baseFilename, dated)
            except OSError:
                return False
            return True
        try:
            os.unlink(self.baseFilename)
        except OSError:
            # Another process still holds it open where that forbids the
            # unlink (Windows): undo, and keep writing to one file today.
            try:
                os.unlink(dated)
            except OSError:
                pass
            return False
        return True

    def _open(self):
        if os.name == "posix":
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            fd = os.open(self.baseFilename, flags, _OWNER_ONLY_MODE)
            try:
                # TimedRotatingFileHandler always opens in "a" mode (it takes
                # no mode argument), which matches O_APPEND above. The literal
                # keeps the stream a TextIOWrapper, as FileHandler._open returns.
                stream = os.fdopen(
                    fd,
                    "a",
                    encoding=self.encoding,
                    errors=getattr(self, "errors", None),
                )
            except Exception:
                os.close(fd)
                raise
            _chmod_owner_only(self.baseFilename)
            return stream
        return super()._open()


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
    # A marker on the root logger survives module reloads; Logger has no such attribute.
    setattr(root, "_prks_logging_configured", True)

    _chmod_owner_only(log_file, report=True)

    LOGGER.info(
        "logging_initialized level=%s file_level=%s retention_days=%s",
        logging.getLevelName(level),
        logging.getLevelName(file_level),
        retention_days,
    )
