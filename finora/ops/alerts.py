"""Operator alerting. Invariant: Notifier.send() NEVER raises — an alert
delivery failure must not crash the trading pipeline. Failures (network,
missing env secrets, SMTP errors) are logged and the alert is dropped.
"""
from __future__ import annotations

import enum
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

import requests

from finora.core.config import EmailConfig, OpsConfig, TelegramConfig
from finora.core.errors import ConfigError
from finora.core.log import get_logger

logger = get_logger(__name__)


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@runtime_checkable
class Notifier(Protocol):
    def send(self, subject: str, body: str, severity: Severity = Severity.INFO) -> None: ...


class StdoutNotifier:
    """Logs alerts through structlog at the level mapped from severity."""

    def send(self, subject: str, body: str, severity: Severity = Severity.INFO) -> None:
        try:
            method = {
                Severity.INFO: logger.info,
                Severity.WARNING: logger.warning,
                Severity.CRITICAL: logger.critical,
            }.get(severity, logger.info)
            method("alert", subject=subject, body=body, severity=str(severity.value))
        except Exception:
            # Even the logger must not be able to crash the caller.
            pass


class TelegramNotifier:
    def __init__(self, cfg: TelegramConfig) -> None:
        self._cfg = cfg

    def send(self, subject: str, body: str, severity: Severity = Severity.INFO) -> None:
        try:
            token = os.environ.get(self._cfg.token_env, "")
            chat_id = os.environ.get(self._cfg.chat_id_env, "")
            if not token or not chat_id:
                logger.warning(
                    "telegram alert dropped: credentials not set",
                    token_env=self._cfg.token_env,
                    chat_id_env=self._cfg.chat_id_env,
                )
                return
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"[{severity.value}] {subject}\n{body}"},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            try:
                logger.warning("telegram alert failed", error=str(exc), subject=subject)
            except Exception:
                pass


class EmailNotifier:
    def __init__(self, cfg: EmailConfig) -> None:
        self._cfg = cfg

    def send(self, subject: str, body: str, severity: Severity = Severity.INFO) -> None:
        try:
            cfg = self._cfg
            password = os.environ.get(cfg.password_env, "")
            if not cfg.smtp_host or not cfg.from_addr or not cfg.to_addr:
                logger.warning("email alert dropped: smtp_host/from_addr/to_addr not configured")
                return
            if not password:
                logger.warning(
                    "email alert dropped: password env not set", password_env=cfg.password_env
                )
                return
            msg = EmailMessage()
            msg["Subject"] = f"[{severity.value}] {subject}"
            msg["From"] = cfg.from_addr
            msg["To"] = cfg.to_addr
            msg.set_content(body)
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(cfg.from_addr, password)
                smtp.send_message(msg)
        except Exception as exc:
            try:
                logger.warning("email alert failed", error=str(exc), subject=subject)
            except Exception:
                pass


def build_notifier(cfg: OpsConfig) -> Notifier:
    if cfg.notifier == "stdout":
        return StdoutNotifier()
    if cfg.notifier == "telegram":
        return TelegramNotifier(cfg.telegram)
    if cfg.notifier == "email":
        return EmailNotifier(cfg.email)
    raise ConfigError(f"unknown notifier kind: {cfg.notifier!r}")
