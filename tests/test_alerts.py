"""ops.alerts: send() never raises; Telegram payloads; notifier dispatch."""
from __future__ import annotations

import pytest
import requests

import finora.ops.alerts as alerts
from finora.core.config import EmailConfig, OpsConfig, TelegramConfig
from finora.ops.alerts import (
    EmailNotifier,
    Severity,
    StdoutNotifier,
    TelegramNotifier,
    build_notifier,
)

TG = TelegramConfig(token_env="TEST_TG_TOKEN", chat_id_env="TEST_TG_CHAT")


class PostRecorder:
    def __init__(self, exc: Exception | None = None):
        self.calls: list[tuple[tuple, dict]] = []
        self.exc = exc

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc

        class Resp:
            def raise_for_status(self):
                pass

        return Resp()


class TestStdoutNotifier:
    @pytest.mark.parametrize("sev", list(Severity))
    def test_send_never_raises(self, sev):
        StdoutNotifier().send("subject", "body", sev)

    def test_send_swallows_logger_failure(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("logger exploded")

        monkeypatch.setattr(alerts.logger, "info", boom)
        monkeypatch.setattr(alerts.logger, "warning", boom)
        monkeypatch.setattr(alerts.logger, "critical", boom)
        StdoutNotifier().send("subject", "body", Severity.CRITICAL)  # must not raise


class TestTelegramNotifier:
    def test_posts_expected_url_and_payload(self, monkeypatch):
        monkeypatch.setenv("TEST_TG_TOKEN", "tok123")
        monkeypatch.setenv("TEST_TG_CHAT", "chat456")
        recorder = PostRecorder()
        monkeypatch.setattr(requests, "post", recorder)

        TelegramNotifier(TG).send("Halt", "drawdown breach", Severity.CRITICAL)

        assert len(recorder.calls) == 1
        args, kwargs = recorder.calls[0]
        assert args[0] == "https://api.telegram.org/bottok123/sendMessage"
        assert kwargs["json"] == {
            "chat_id": "chat456",
            "text": "[CRITICAL] Halt\ndrawdown breach",
        }
        assert kwargs["timeout"] == 10

    def test_connection_error_is_swallowed(self, monkeypatch):
        monkeypatch.setenv("TEST_TG_TOKEN", "tok")
        monkeypatch.setenv("TEST_TG_CHAT", "chat")
        recorder = PostRecorder(exc=ConnectionError("network down"))
        monkeypatch.setattr(requests, "post", recorder)

        result = TelegramNotifier(TG).send("s", "b", Severity.WARNING)
        assert result is None
        assert len(recorder.calls) == 1

    def test_missing_env_drops_alert_without_post(self, monkeypatch):
        monkeypatch.delenv("TEST_TG_TOKEN", raising=False)
        monkeypatch.delenv("TEST_TG_CHAT", raising=False)
        recorder = PostRecorder()
        monkeypatch.setattr(requests, "post", recorder)

        TelegramNotifier(TG).send("s", "b")  # must not raise
        assert recorder.calls == []


class TestEmailNotifier:
    def test_unconfigured_email_drops_without_raising(self, monkeypatch):
        monkeypatch.delenv("TEST_SMTP_PWD", raising=False)
        cfg = EmailConfig(password_env="TEST_SMTP_PWD")  # no host/from/to
        EmailNotifier(cfg).send("s", "b", Severity.INFO)  # must not raise

    def test_smtp_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setenv("TEST_SMTP_PWD", "pwd")
        cfg = EmailConfig(
            smtp_host="smtp.invalid",
            from_addr="a@b.c",
            to_addr="d@e.f",
            password_env="TEST_SMTP_PWD",
        )

        def boom(*a, **k):
            raise OSError("cannot connect")

        monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
        EmailNotifier(cfg).send("s", "b", Severity.CRITICAL)  # must not raise


class TestBuildNotifier:
    def test_dispatch(self):
        assert isinstance(build_notifier(OpsConfig(notifier="stdout")), StdoutNotifier)
        assert isinstance(build_notifier(OpsConfig(notifier="telegram")), TelegramNotifier)
        assert isinstance(build_notifier(OpsConfig(notifier="email")), EmailNotifier)
