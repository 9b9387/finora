"""Kill switch: engage/status/release roundtrip and persistence across instances."""
from __future__ import annotations

import json
from pathlib import Path

from finora.risk.kill_switch import KillSwitch


def test_fresh_switch_not_engaged(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path)
    assert not switch.engaged()
    assert switch.status() is None


def test_engage_status_release_roundtrip(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path)
    switch.engage("reconciliation mismatch")
    assert switch.engaged()

    status = switch.status()
    assert status is not None
    assert status["reason"] == "reconciliation mismatch"
    assert status["engaged_at"]  # ISO timestamp recorded

    switch.release()
    assert not switch.engaged()
    assert switch.status() is None


def test_engaged_survives_new_instance(tmp_path: Path) -> None:
    KillSwitch(tmp_path).engage("manual halt")

    other = KillSwitch(tmp_path)
    assert other.engaged()
    status = other.status()
    assert status is not None
    assert status["reason"] == "manual halt"

    other.release()
    assert not KillSwitch(tmp_path).engaged()


def test_release_when_not_engaged_is_noop(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path)
    switch.release()  # must not raise
    assert not switch.engaged()


def test_kill_file_is_json_at_expected_path(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path)
    switch.engage("test")
    kill_file = tmp_path / "KILL"
    assert kill_file.exists()
    payload = json.loads(kill_file.read_text())
    assert payload["reason"] == "test"


def test_hand_touched_kill_file_counts_as_engaged(tmp_path: Path) -> None:
    """A human `touch state/KILL` (non-JSON) must still halt trading."""
    (tmp_path / "KILL").write_text("stop everything\n")
    switch = KillSwitch(tmp_path)
    assert switch.engaged()
    status = switch.status()
    assert status is not None
    assert status["reason"] == "stop everything"


def test_engage_creates_missing_state_dir(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "nested" / "state")
    switch.engage("boom")
    assert switch.engaged()
