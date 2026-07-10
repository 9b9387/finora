"""ops.logging: JSONL file output, stdlib passthrough, idempotent setup."""
from __future__ import annotations

import json
import logging

import pytest

import finora.ops.logging as ops_logging
from finora.core.config import OpsConfig
from finora.core.log import get_logger


@pytest.fixture()
def fresh_guard(monkeypatch):
    """Allow configure_logging to actually run inside this test."""
    monkeypatch.setattr(ops_logging, "_configured", False)


def _configure(tmp_path, name="logs"):
    cfg = OpsConfig(log_dir=tmp_path / name, state_dir=tmp_path / "state")
    ops_logging.configure_logging(cfg)
    return cfg


def test_configure_writes_parseable_jsonl(tmp_path, fresh_guard):
    cfg = _configure(tmp_path)

    log = get_logger("finora.test.emitter")
    log.warning("hello_event", answer=42)
    logging.getLogger("foreign.stdlib").error("stdlib message")

    files = list(cfg.log_dir.glob("finora-*.jsonl"))
    assert len(files) == 1

    lines = [ln for ln in files[0].read_text().splitlines() if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    assert parsed, "expected at least one JSON log line"
    for doc in parsed:
        assert "event" in doc
        assert "level" in doc
        assert "timestamp" in doc
        assert "logger" in doc

    ours = [d for d in parsed if d["event"] == "hello_event"]
    assert ours and ours[0]["answer"] == 42
    assert ours[0]["logger"] == "finora.test.emitter"
    assert ours[0]["level"] == "warning"

    foreign = [d for d in parsed if d["event"] == "stdlib message"]
    assert foreign, "stdlib logger output must land in the JSONL file too"
    assert foreign[0]["logger"] == "foreign.stdlib"


def test_double_configure_is_noop(tmp_path, fresh_guard):
    _configure(tmp_path, "first")
    handlers_before = list(logging.getLogger().handlers)

    second = OpsConfig(log_dir=tmp_path / "second", state_dir=tmp_path / "state")
    ops_logging.configure_logging(second)

    assert logging.getLogger().handlers == handlers_before
    assert not (tmp_path / "second").exists(), "second configure must not create files"


def test_configure_never_raises_on_bad_level(tmp_path, fresh_guard):
    cfg = OpsConfig(log_dir=tmp_path / "logs", state_dir=tmp_path / "state")
    ops_logging.configure_logging(cfg, level="NOT_A_LEVEL")  # must not raise
