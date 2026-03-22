import logging
import logging.config
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Optional

from concurrent_log_handler import ConcurrentRotatingFileHandler  # noqa: F401 - needed for dictConfig
from pydantic import SecretStr

from config.config import agentSettings


_trace_context: ContextVar[str] = ContextVar("trace_context", default="-")
_MASKED_SECRET = "**********"
_SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
}
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"jina_[A-Za-z0-9_-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]
_BEARER_PATTERN = re.compile(r"(Bearer\s+)([A-Za-z0-9._-]{10,})", re.IGNORECASE)
_ASSIGNMENT_PATTERN = re.compile(
    r"((?:api[_-]?key|authorization|password|passwd|secret(?:_key)?|token|access_token|refresh_token)\s*[:=]\s*['\"]?)([^'\",\s}]+)",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(_MASKED_SECRET, redacted)
    redacted = _BEARER_PATTERN.sub(r"\1" + _MASKED_SECRET, redacted)
    redacted = _ASSIGNMENT_PATTERN.sub(r"\1" + _MASKED_SECRET, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return _MASKED_SECRET
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: Dict[Any, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_KEY_NAMES:
                redacted[key] = _MASKED_SECRET if item else item
            else:
                redacted[key] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, set):
        return {redact_value(item) for item in value}
    return value


class TraceContextFilter(logging.Filter):
    """Inject trace context fields into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_context.get()
        return True


class RedactSecretsFilter(logging.Filter):
    """Mask common secret formats before log records are formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_value(record.msg)
        if record.args:
            record.args = redact_value(record.args)
        return True


class HourlyConcurrentRotatingFileHandler(ConcurrentRotatingFileHandler):
    """
    多进程安全的按小时+大小轮转 Handler。

    规则：
      - 小时变化或超出 maxBytes 都触发轮转；
      - 文件名形如 task-pilot-agent.2025-12-09.19.1.log。
    """

    def __init__(self, filename: str, maxBytes: int, backupCount: int, encoding: str = "utf-8") -> None:
        super().__init__(
            filename=filename,
            mode="a",
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=True,
        )
        self.current_hour = self._current_hour_slot()
        self.size_index = 0
        self._pending_time_rollover = False

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802
        msg = self.format(record)
        payload_size = self._payload_size(msg)

        now_slot = self._current_hour_slot()
        if now_slot != self.current_hour:
            self._pending_time_rollover = True
            return True

        if self.maxBytes > 0:
            if self.stream is None:
                self.stream = self._open()
            self.stream.seek(0, os.SEEK_END)
            current_size = self.stream.tell()
            if current_size + payload_size > self.maxBytes:
                self._pending_time_rollover = False
                return True

        self._pending_time_rollover = False
        return False

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None

        base_path = Path(self.baseFilename)
        slot = self.current_hour or self._current_hour_slot()

        if getattr(self, "_pending_time_rollover", False):
            # 小时变化时重置序号
            self.size_index = 0

        self.size_index += 1
        target = base_path.with_name(
            f"{base_path.stem}.{slot:%Y-%m-%d.%H}.{self.size_index}{base_path.suffix}"
        )

        # 确保不覆盖已有文件
        counter = 1
        candidate = target
        while candidate.exists():
            candidate = candidate.with_name(f"{target.stem}.{counter}{target.suffix}")
            counter += 1

        if base_path.exists():
            base_path.rename(candidate)

        self.current_hour = self._current_hour_slot()
        self._pending_time_rollover = False

        if not self.delay:
            self.stream = self._open()

    def _current_hour_slot(self):
        from datetime import datetime

        now = datetime.now()
        return now.replace(minute=0, second=0, microsecond=0)

    def _payload_size(self, message: str) -> int:
        payload = (message + self.terminator).encode(self.encoding or "utf-8")
        return len(payload)


def _normalize_level(value: str | int) -> int:
    if isinstance(value, str):
        level = logging.getLevelName(value.upper())
        if isinstance(level, int):
            return level
    if isinstance(value, int):
        return value
    return logging.INFO


def _level_name(value: str | int) -> str:
    return logging.getLevelName(_normalize_level(value))


def build_logging_config(cfg=None) -> Dict[str, Any]:
    """Build a dictConfig using ConcurrentRotatingFileHandler for multiprocess-safe logging."""
    cfg = cfg or agentSettings.logging
    Path(cfg.directory).mkdir(parents=True, exist_ok=True)
    app_path = Path(cfg.directory) / f"{cfg.filename_prefix}.log"

    fmt_default = (
        "%(asctime)s [%(levelname)s] [pid=%(process)d] %(name)s:%(lineno)d "
        "[trace=%(trace_id)s] %(message)s"
    )

    level_name = _level_name(cfg.level)

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "trace_context": {"()": TraceContextFilter},
            "redact_secrets": {"()": RedactSecretsFilter},
        },
        "formatters": {
            "default": {"format": fmt_default},
        },
        "handlers": {
            "file": {
                "class": "utils.logger.HourlyConcurrentRotatingFileHandler",
                "filename": str(app_path),
                "maxBytes": cfg.max_bytes,
                "backupCount": cfg.backup_count,
                "encoding": "utf-8",
                "formatter": "default",
                "filters": ["trace_context", "redact_secrets"],
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": level_name, "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": level_name, "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "app": {"handlers": ["file"], "level": level_name, "propagate": False},
        },
        "root": {"handlers": ["file"], "level": level_name},
    }


def configure_logging(force: bool = False) -> None:
    """
    Apply logging config if not yet configured or when force=True.
    Safe for multi-process because ConcurrentRotatingFileHandler handles file locks.
    """
    root_logger = logging.getLogger()
    if root_logger.handlers and not force:
        return
    logging.config.dictConfig(build_logging_config())


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Fetch a logger ensuring the global configuration is applied."""
    configure_logging()
    return logging.getLogger(name)


def configure_log_context(trace_id: Optional[str] = None) -> None:
    """Update the contextual identifier injected into each log record."""
    if trace_id:
        _trace_context.set(trace_id)


def clear_log_context() -> None:
    """Reset logging context identifier."""
    _trace_context.set("-")
