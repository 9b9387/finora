"""Structured logging setup: pretty console to stderr + JSON-lines file.

Uses structlog's stdlib integration so foreign stdlib loggers (qlib, futu,
urllib3, ...) are formatted through the same pipeline and land in the same
JSONL file. Configuration is deliberately defensive: logging setup must never
be the thing that crashes the trading pipeline.
"""
from __future__ import annotations

import logging
import sys
from datetime import date

import structlog

from finora.core.config import OpsConfig

_configured = False

# Marker attribute so re-configuration (tests) can find and remove our handlers.
_HANDLER_MARK = "_finora_ops_handler"


def configure_logging(cfg: OpsConfig, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging once. Subsequent calls are no-ops."""
    global _configured
    if _configured:
        return
    try:
        _do_configure(cfg, level)
    except Exception:  # pragma: no cover - last-resort fallback
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).warning(
            "structured logging setup failed; using basicConfig", exc_info=True
        )
    finally:
        # Never retry a failing setup on every call either.
        _configured = True


def _do_configure(cfg: OpsConfig, level: str) -> None:
    shared_processors: list = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)
    setattr(console_handler, _HANDLER_MARK, True)

    log_path = cfg.log_dir / f"finora-{date.today():%Y%m%d}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(file_formatter)
    setattr(file_handler, _HANDLER_MARK, True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
