"""Order management: submission with retries, polling, and an audit journal.

The journal is an append-only JSONL file per trading day under
state_dir/orders/. It is both the audit trail and the crash-safety
mechanism: client_order_id is deterministic (one rebalance order per
instrument/side/day/strategy), so a restarted process can tell whether an
order was already handed to the broker. A journaled 'intent' with no
'submitted' ack is ambiguous — the broker may or may not have the order —
and is never auto-resubmitted (at-most-once semantics).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Callable

from finora.core.errors import BrokerError
from finora.core.log import get_logger
from finora.core.models import (
    Fill,
    InvalidTransitionError,
    Order,
    OrderSide,
    OrderStatus,
    utc_now,
)
from finora.execution.broker import Broker

log = get_logger(__name__)


def make_client_order_id(as_of: date, strategy: str, instrument: str, side: OrderSide) -> str:
    """Deterministic idempotency key: same inputs -> same id across restarts."""
    prefix = f"{as_of:%Y%m%d}-{strategy}-{instrument}-{side.value}"
    digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


class OMS:
    def __init__(
        self,
        broker: Broker,
        state_dir: Path,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.broker = broker
        self.state_dir = Path(state_dir)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.sleep_fn = sleep_fn

    # -- journal -----------------------------------------------------------
    def _journal_path(self, as_of: date | None = None) -> Path:
        day = as_of or utc_now().date()
        return self.state_dir / "orders" / f"{day:%Y-%m-%d}.jsonl"

    def _journal(self, event: str, order: Order) -> None:
        path = self._journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": utc_now().isoformat(),
            "event": event,
            "client_order_id": order.client_order_id,
            "instrument": order.instrument,
            "side": order.side.value,
            "qty": order.qty,
            "order_type": order.order_type.value,
            "limit_price": order.limit_price,
            "status": order.status.value,
            "broker_order_id": order.broker_order_id,
            "filled_qty": order.filled_qty,
            "avg_fill_price": order.avg_fill_price,
            "reject_reason": order.reject_reason,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _read_events(self, as_of: date | None = None) -> list[dict]:
        path = self._journal_path(as_of)
        if not path.exists():
            return []
        events: list[dict] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # A crash mid-append can truncate the last line; skip, don't crash.
                log.warning("skipping malformed journal line", path=str(path), lineno=lineno)
        return events

    # -- submission --------------------------------------------------------
    def submit(self, order: Order) -> Order:
        prior = [e for e in self._read_events() if e.get("client_order_id") == order.client_order_id]
        acked = [e for e in prior if e.get("broker_order_id")]
        if acked:
            snap = acked[-1]
            log.info(
                "duplicate submit suppressed; rehydrating from journal",
                client_order_id=order.client_order_id,
                broker_order_id=snap["broker_order_id"],
                status=snap["status"],
            )
            self._rehydrate(order, snap)
            return order
        if any(e.get("event") == "intent" for e in prior):
            # Intent journaled but no broker ack recorded: the process died
            # between journaling and the ack. The broker MAY hold a live
            # order; resubmitting could double it. Require a human.
            order.reject_reason = "needs manual review: ambiguous prior submit"
            order.transition(OrderStatus.REJECTED)
            log.error(
                "ambiguous prior submit; refusing to resubmit",
                client_order_id=order.client_order_id,
            )
            self._journal("error", order)
            return order

        self._journal("intent", order)
        last_exc: BrokerError | None = None
        for attempt in range(self.max_retries):
            try:
                broker_order_id = self.broker.submit_order(order)
            except BrokerError as exc:
                last_exc = exc
                log.warning(
                    "broker submit failed",
                    client_order_id=order.client_order_id,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt + 1 < self.max_retries:
                    self.sleep_fn(self.backoff_base * 2**attempt)
                continue
            order.broker_order_id = broker_order_id
            order.transition(OrderStatus.SUBMITTED)
            self._journal("submitted", order)
            return order

        order.reject_reason = f"submit failed after {self.max_retries} attempts: {last_exc}"
        order.transition(OrderStatus.REJECTED)
        log.error(
            "submit retries exhausted",
            client_order_id=order.client_order_id,
            attempts=self.max_retries,
            error=str(last_exc),
        )
        self._journal("error", order)
        return order

    def _rehydrate(self, order: Order, snap: dict) -> None:
        order.broker_order_id = snap.get("broker_order_id")
        order.status = OrderStatus(snap["status"])
        order.reject_reason = snap.get("reject_reason") or ""
        filled_qty = snap.get("filled_qty") or 0.0
        avg_price = snap.get("avg_fill_price")
        if filled_qty > 0 and avg_price is not None:
            order.fills = [
                Fill(
                    client_order_id=order.client_order_id,
                    qty=filled_qty,
                    price=avg_price,
                    ts=utc_now(),
                )
            ]

    # -- polling -----------------------------------------------------------
    def poll(self, order: Order) -> Order:
        if order.broker_order_id is None:
            return order
        try:
            status, fills = self.broker.get_order_status(order)
        except BrokerError as exc:
            log.warning(
                "poll failed", client_order_id=order.client_order_id, error=str(exc)
            )
            return order
        fills_changed = len(fills) != len(order.fills)
        order.fills = list(fills)
        if status is not order.status:
            try:
                order.transition(status)
            except InvalidTransitionError as exc:
                log.error(
                    "broker reported an illegal status transition",
                    client_order_id=order.client_order_id,
                    current=order.status.value,
                    reported=status.value,
                    error=str(exc),
                )
                self._journal("error", order)
                return order
            self._journal("terminal" if order.is_terminal else "status", order)
        elif fills_changed:
            self._journal("status", order)
        return order

    def poll_until_terminal(
        self,
        orders: list[Order],
        timeout_s: float = 60.0,
        interval_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> list[Order]:
        """Poll until every order is terminal or timeout_s elapses.

        Returns the orders regardless; some may still be non-terminal —
        the caller decides whether to sweep or alert.
        """
        start = clock()
        while True:
            for order in orders:
                if not order.is_terminal:
                    self.poll(order)
            if all(order.is_terminal for order in orders):
                break
            if clock() - start >= timeout_s:
                log.warning(
                    "poll_until_terminal timed out",
                    open_orders=[o.client_order_id for o in orders if not o.is_terminal],
                )
                break
            self.sleep_fn(interval_s)
        return orders

    def sweep_open_orders(self, orders: list[Order]) -> list[Order]:
        """Cancel every non-terminal order, then poll each once."""
        for order in orders:
            if order.is_terminal or order.broker_order_id is None:
                continue
            try:
                self.broker.cancel_order(order)
            except BrokerError as exc:
                log.error(
                    "cancel failed", client_order_id=order.client_order_id, error=str(exc)
                )
                self._journal("error", order)
                continue
            self.poll(order)
        return orders

    # -- reporting ---------------------------------------------------------
    def load_today(self, as_of: date | None = None) -> dict[str, dict]:
        """Latest journal snapshot per client_order_id for the given day."""
        latest: dict[str, dict] = {}
        for event in self._read_events(as_of):
            cid = event.get("client_order_id")
            if cid:
                latest[cid] = event
        return latest
