"""Internal-book vs broker-account reconciliation.

Run before trading: any difference beyond tolerance means the internal view
of the world is wrong, and orders sized off it would be wrong too.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from finora.core.config import RiskConfig
from finora.core.log import get_logger
from finora.core.models import PortfolioState, Position

logger = get_logger(__name__)


@dataclass
class ReconcileDiff:
    kind: str  # 'qty' | 'cash' | 'missing_internal' | 'missing_broker'
    instrument: str
    internal: float
    broker: float


@dataclass
class ReconcileResult:
    ok: bool
    diffs: list[ReconcileDiff] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "reconcile OK: internal book matches broker"
        lines = [f"reconcile FAILED: {len(self.diffs)} difference(s)"]
        for d in self.diffs:
            lines.append(
                f"  [{d.kind}] {d.instrument}: internal={d.internal:g} broker={d.broker:g}"
            )
        return "\n".join(lines)


def reconcile_positions(
    internal: PortfolioState,
    broker_positions: dict[str, Position],
    broker_cash: float,
    cfg: RiskConfig,
) -> ReconcileResult:
    """Compare the internal book against the broker's account snapshot.

    Zero-qty entries on either side are phantoms and are ignored. kind
    'missing_internal' means the broker holds a position our book lacks;
    'missing_broker' means our book holds a position the broker lacks.
    """
    internal_qty = {
        sym: pos.qty for sym, pos in internal.positions.items() if pos.qty != 0
    }
    broker_qty = {
        sym: pos.qty for sym, pos in broker_positions.items() if pos.qty != 0
    }

    diffs: list[ReconcileDiff] = []
    for sym in sorted(internal_qty.keys() | broker_qty.keys()):
        ours = internal_qty.get(sym)
        theirs = broker_qty.get(sym)
        if ours is None:
            diffs.append(
                ReconcileDiff(
                    kind="missing_internal", instrument=sym, internal=0.0, broker=theirs or 0.0
                )
            )
        elif theirs is None:
            diffs.append(
                ReconcileDiff(kind="missing_broker", instrument=sym, internal=ours, broker=0.0)
            )
        elif abs(ours - theirs) > cfg.reconcile_qty_tolerance:
            diffs.append(
                ReconcileDiff(kind="qty", instrument=sym, internal=ours, broker=theirs)
            )

    if abs(internal.cash - broker_cash) > cfg.reconcile_cash_tolerance:
        diffs.append(
            ReconcileDiff(kind="cash", instrument="CASH", internal=internal.cash, broker=broker_cash)
        )

    result = ReconcileResult(ok=not diffs, diffs=diffs)
    if not result.ok:
        logger.warning("reconciliation mismatch", summary=result.summary())
    return result
