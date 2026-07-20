import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from . import settings as config
from .redaction import redact_sensitive_text


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_RETENTION_HOURS = 24


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


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
    log_dir.mkdir(parents=True, exist_ok=True)
    _prune_expired_logs(log_dir, retention_hours)

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
        file_handler = TimedRotatingFileHandler(
            filename=str(log_dir / f"app-{os.getpid()}.log"),
            when="H",
            interval=1,
            backupCount=retention_hours,
            encoding="utf-8",
            utc=False,
            delay=True,
        )
    except OSError as exc:
        root.warning("File logging disabled: %s", exc)
    else:
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    logging.captureWarnings(True)
