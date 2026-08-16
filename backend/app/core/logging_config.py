import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from . import settings as config
from .redaction import redact_sensitive_text


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_RETENTION_HOURS = 24
LOG_DIR_MODE = 0o700
LOG_FILE_MODE = 0o600


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


class SecureTimedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, *args, **kwargs):
        self._secure_disabled = False
        self._disable_warning_emitted = False
        super().__init__(*args, **kwargs)

    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.baseFilename, flags, LOG_FILE_MODE)
        try:
            os.chmod(self.baseFilename, LOG_FILE_MODE)
            return os.fdopen(
                fd,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
            )
        except Exception:
            os.close(fd)
            raise

    def emit(self, record: logging.LogRecord) -> None:
        if not self._secure_disabled:
            super().emit(record)

    def doRollover(self) -> None:
        super().doRollover()
        for path in Path(self.baseFilename).parent.glob("*.log*"):
            if path.is_file():
                os.chmod(path, LOG_FILE_MODE)

    def handleError(self, record: logging.LogRecord) -> None:
        self._secure_disabled = True
        try:
            self.close()
        except OSError:
            pass
        if not self._disable_warning_emitted:
            self._disable_warning_emitted = True
            logging.getLogger(__name__).warning(
                "File logging disabled because secure log permissions could not be enforced"
            )


def _parse_log_level(value: str | None) -> int:
    level_name = (value or DEFAULT_LOG_LEVEL).strip().upper()
    level = logging.getLevelName(level_name)
    return level if isinstance(level, int) else logging.INFO


def _parse_retention_hours(value: str | None) -> int:
    try:
        parsed = int((value or str(DEFAULT_LOG_RETENTION_HOURS)).strip())
    except (TypeError, ValueError):
        return DEFAULT_LOG_RETENTION_HOURS
    return max(1, parsed)


def _prune_expired_logs(log_dir: Path, retention_hours: int) -> None:
    cutoff = time.time() - (retention_hours * 3600)
    for path in log_dir.glob("*.log*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def setup_logging() -> None:
    log_level = _parse_log_level(os.getenv("LOG_LEVEL"))
    retention_hours = _parse_retention_hours(os.getenv("LOG_RETENTION_HOURS"))
    log_dir = Path(os.getenv("LOG_DIR", str(Path(config.DATA_DIR) / "logs")))
    file_logging_available = True
    try:
        log_dir.mkdir(parents=True, mode=LOG_DIR_MODE, exist_ok=True)
        if os.name != "nt":
            if log_dir.is_symlink():
                raise OSError("log directory must not be a symlink")
            os.chmod(log_dir, LOG_DIR_MODE)
            for path in log_dir.glob("*.log*"):
                if path.is_file():
                    if path.is_symlink():
                        raise OSError("log file must not be a symlink")
                    os.chmod(path, LOG_FILE_MODE)
        _prune_expired_logs(log_dir, retention_hours)
    except OSError:
        file_logging_available = False

    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s [pid=%(process)d] %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(log_level)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    try:
        if not file_logging_available:
            raise OSError("secure permissions unavailable")
        file_handler = SecureTimedRotatingFileHandler(
            filename=str(log_dir / f"app-{os.getpid()}.log"),
            when="H",
            interval=1,
            backupCount=retention_hours,
            encoding="utf-8",
            utc=False,
            delay=False,
        )
    except OSError:
        root.warning(
            "File logging disabled because secure log permissions could not be enforced"
        )
    else:
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    logging.captureWarnings(True)
