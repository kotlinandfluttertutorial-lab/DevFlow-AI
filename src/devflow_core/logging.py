"""Structured application logging and credential redaction."""

import logging
import sys
from collections.abc import Mapping
from typing import cast

import structlog
from pydantic import SecretBytes, SecretStr
from structlog.typing import EventDict, FilteringBoundLogger, Processor, WrappedLogger

from devflow_core.config import get_settings

_configured = False
_SENSITIVE_KEYS = ("key", "token", "secret", "password", "credential", "authorization", "cookie")
_REDACTED = "[REDACTED]"


class RedactSecretsProcessor:
    """Mask credential fields in structured events, including nested payloads."""

    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        self._secret_values = tuple(sorted(filter(None, secret_values), key=len, reverse=True))

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        return {key: self._redact_field(key, value) for key, value in event_dict.items()}

    def _redact_field(self, key: object, value: object) -> object:
        if any(part in str(key).lower() for part in _SENSITIVE_KEYS):
            return _REDACTED
        if isinstance(value, SecretStr | SecretBytes):
            return _REDACTED
        if isinstance(value, str):
            for secret in self._secret_values:
                value = value.replace(secret, _REDACTED)
            return value
        if isinstance(value, Mapping):
            return {key: self._redact_field(key, item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._redact_field("", item) for item in value]
        return value


def configure_logging(log_level: str | None = None, log_format: str | None = None) -> None:
    """Configure application logging once, with readable DEBUG output in tests."""
    global _configured
    # Driver DEBUG messages embed raw SQL parameters outside structlog's redaction pipeline.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    if _configured:
        return

    settings = get_settings()
    level = log_level or settings.LOG_LEVEL
    output = log_format or settings.LOG_FORMAT
    if settings.ENVIRONMENT == "test":
        level, output = "DEBUG", "text"

    secret_values = tuple(
        value.get_secret_value()
        for name in type(settings).model_fields
        if isinstance(value := getattr(settings, name), SecretStr)
    )
    level_number = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    renderer: Processor
    if output == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False, event_key="message")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("message"),
            RedactSecretsProcessor(secret_values),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_number),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level_number)
    _configured = True


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a logger with a stable name in its event context."""
    return cast(FilteringBoundLogger, structlog.get_logger().bind(logger=name))
