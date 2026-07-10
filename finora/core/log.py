"""Logger access shim so every layer can log without depending on finora.ops.

finora.ops.logging owns the real configuration (JSON rendering, file output);
this just hands out structlog loggers that work with or without that setup.
"""
from __future__ import annotations

import structlog


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
