"""Daily health report: data freshness, safety state, orders, signals.

Every section is assembled defensively — one broken or missing component
(fresh checkout, sibling module not deployed, corrupt state file) must never
prevent the rest of the report from rendering.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finora.core.config import Settings
from finora.core.log import get_logger
from finora.core.models import utc_now

logger = get_logger(__name__)


def generate_health_report(settings: Settings, as_of: date | None = None) -> str:
    as_of = as_of or utc_now().date()
    lines: list[str] = [f"# Finora Health Report — {as_of:%Y-%m-%d}", ""]

    for title, builder in (
        ("Data freshness", _data_freshness_section),
        ("Safety state", _safety_section),
        ("Today's orders", _orders_section),
        ("Signals", _signals_section),
    ):
        lines.append(f"## {title}")
        try:
            lines.extend(builder(settings, as_of))
        except Exception as exc:  # a broken section must not kill the report
            logger.warning("health report section failed", section=title, error=str(exc))
            lines.append(f"- section unavailable: {exc}")
        lines.append("")

    lines.append("---")
    lines.append(f"Generated at {utc_now().isoformat()}")
    return "\n".join(lines) + "\n"


def write_health_report(settings: Settings, as_of: date | None = None) -> Path:
    as_of = as_of or utc_now().date()
    report = generate_health_report(settings, as_of)
    path = settings.ops.log_dir / "health" / f"{as_of:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    logger.info("health report written", path=str(path))
    return path


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _data_freshness_section(settings: Settings, as_of: date) -> list[str]:
    lines: list[str] = []
    try:
        latest = _store_latest_date(settings)
    except Exception as exc:
        return [f"- market store unavailable: {exc}"]

    if latest is None:
        lines.append("- no data in market store")
        return lines

    lines.append(f"- latest bar date: {latest:%Y-%m-%d}")
    session = _last_nyse_session(as_of)
    if session is None:
        lines.append("- last NYSE session: unknown (calendar unavailable)")
    else:
        lines.append(f"- last NYSE session on/before {as_of:%Y-%m-%d}: {session:%Y-%m-%d}")
        if latest < session:
            lines.append(f"- status: ⚠ STALE (latest bar {latest:%Y-%m-%d} < {session:%Y-%m-%d})")
        else:
            lines.append("- status: OK (fresh)")
    return lines


def _safety_section(settings: Settings, as_of: date) -> list[str]:
    state_dir = settings.ops.state_dir
    return [
        f"- kill switch: {_kill_switch_status(state_dir)}",
        f"- circuit breaker: {_circuit_breaker_status(settings)}",
    ]


def _orders_section(settings: Settings, as_of: date) -> list[str]:
    path = settings.ops.state_dir / "orders" / f"{as_of:%Y-%m-%d}.jsonl"
    records = _read_jsonl(path)
    if records is None:
        return [f"- no orders file for {as_of:%Y-%m-%d}"]
    if not records:
        return ["- orders file is empty"]

    counts: Counter[str] = Counter(str(r.get("status", "UNKNOWN")) for r in records)
    lines = [f"- total: {len(records)}"]
    lines.extend(f"- {status}: {n}" for status, n in sorted(counts.items()))
    rejects = [r for r in records if str(r.get("status", "")) == "REJECTED"]
    if rejects:
        lines.append("- rejects:")
        for r in rejects:
            instrument = r.get("instrument", "?")
            reason = r.get("reject_reason", "") or "(no reason recorded)"
            lines.append(f"  - {instrument}: {reason}")
    return lines


def _signals_section(settings: Settings, as_of: date) -> list[str]:
    path = settings.ops.state_dir / "signals" / f"{as_of:%Y-%m-%d}.jsonl"
    records = _read_jsonl(path)
    if records is None:
        return [f"- no signals file for {as_of:%Y-%m-%d}"]
    if not records:
        return ["- signals file is empty"]
    counts: Counter[str] = Counter(
        str(r.get("source") or r.get("strategy") or "unknown") for r in records
    )
    return [f"- {strategy}: {n}" for strategy, n in sorted(counts.items())]


# --------------------------------------------------------------------------
# Helpers (each tolerant of missing sibling modules / files)
# --------------------------------------------------------------------------


def _store_latest_date(settings: Settings) -> date | None:
    """Latest bar date in the market store, or None when the store is empty.

    Raises on store import/construction/read failure — the caller renders
    the error inside the section instead of crashing the report.
    """
    from finora.data.store import MarketStore  # lazy: sibling module

    store = MarketStore(settings.data)
    latest: Any = store.latest_date
    if callable(latest):
        latest = latest()
    return _as_date(latest)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    # datetime / pandas.Timestamp / numpy datetime64-ish
    if hasattr(value, "date") and callable(value.date):
        return value.date()
    return None


def _last_nyse_session(as_of: date) -> date | None:
    try:
        import pandas_market_calendars as mcal  # heavier import, keep lazy

        cal = mcal.get_calendar("XNYS")
        sched = cal.schedule(start_date=as_of - timedelta(days=14), end_date=as_of)
        if sched.empty:
            return None
        return sched.index[-1].date()
    except Exception as exc:
        logger.warning("NYSE calendar lookup failed", error=str(exc))
        return None


def _kill_switch_status(state_dir: Path) -> str:
    try:
        from finora.risk.kill_switch import KillSwitch  # lazy: sibling module

        ks = KillSwitch(state_dir)
        active = _probe(ks, ("is_active", "active", "engaged", "is_engaged"))
        if active is not None:
            return "ACTIVE" if active else "inactive"
    except Exception as exc:
        logger.debug("kill switch class unavailable, using file fallback", error=str(exc))
    # Fallback: any kill* file in state_dir means the switch is thrown.
    try:
        if state_dir.is_dir() and any(
            p.name.lower().startswith("kill") for p in state_dir.iterdir() if p.is_file()
        ):
            return "ACTIVE (kill file present)"
    except OSError:
        pass
    return "inactive"


def _circuit_breaker_status(settings: Settings) -> str:
    state_path = settings.ops.state_dir / "circuit_breaker.json"  # shared path convention
    try:
        from finora.risk.circuit_breaker import CircuitBreaker  # lazy: sibling module

        breaker = _construct_breaker(CircuitBreaker, settings, state_path)
        if breaker is not None:
            state = _probe(breaker, ("state", "status", "level", "current_state"))
            if state is not None:
                return str(getattr(state, "value", state))
    except Exception as exc:
        logger.debug("circuit breaker class unavailable, reading state file", error=str(exc))
    if not state_path.exists():
        return "inactive (no state file)"
    try:
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        return json.dumps(doc, sort_keys=True, default=str)
    except Exception as exc:
        return f"state file unreadable: {exc}"


def _construct_breaker(cls: type, settings: Settings, state_path: Path) -> Any | None:
    for args, kwargs in (
        ((settings.risk.circuit_breaker,), {"state_path": state_path}),
        ((), {"config": settings.risk.circuit_breaker, "state_path": state_path}),
        ((), {"state_path": state_path}),
        ((state_path,), {}),
    ):
        try:
            return cls(*args, **kwargs)
        except Exception:
            continue
    return None


def _probe(obj: Any, names: tuple[str, ...]) -> Any | None:
    for name in names:
        value = getattr(obj, name, None)
        if value is None:
            continue
        try:
            return value() if callable(value) else value
        except Exception:
            continue
    return None


def _read_jsonl(path: Path) -> list[dict] | None:
    """Parsed records, [] for an empty file, None when the file is absent."""
    if not path.exists():
        return None
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping malformed JSONL line", path=str(path))
            continue
        if isinstance(doc, dict):
            records.append(doc)
    return records
