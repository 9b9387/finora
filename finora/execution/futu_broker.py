"""Futu OpenAPI broker adapter.

Requires a locally running OpenD gateway (host/port from FutuConfig) and the
`futu-api` package (`uv sync --extra futu`). This integration is UNTESTED
without a live OpenD; unit tests never touch it. Future integration tests
should be gated behind env FINORA_FUTU_TESTS=1 (pytest.mark.skipif
otherwise) — no network tests exist today.

trd_env SIMULATE is Futu paper trading; REAL is live money and requires the
trade-unlock password in the env var named by cfg.unlock_password_env.
"""
from __future__ import annotations

import os
from typing import Any

from finora.core.config import FutuConfig
from finora.core.errors import BrokerError, ConfigError
from finora.core.log import get_logger
from finora.core.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    utc_now,
)
from finora.execution.broker import Broker

log = get_logger(__name__)

_MARKET_PREFIX = "US."

# Futu order-status name -> Finora OrderStatus. Unknown in-flight states
# (e.g. CANCELLING_*) fall back to SUBMITTED so the poll loop keeps watching.
_STATUS_MAP: dict[str, OrderStatus] = {
    "WAITING_SUBMIT": OrderStatus.SUBMITTED,
    "SUBMITTING": OrderStatus.SUBMITTED,
    "SUBMITTED": OrderStatus.SUBMITTED,
    "FILLED_PART": OrderStatus.PARTIALLY_FILLED,
    "FILLED_ALL": OrderStatus.FILLED,
    "CANCELLED_PART": OrderStatus.CANCELLED,
    "CANCELLED_ALL": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.REJECTED,
    "SUBMIT_FAILED": OrderStatus.REJECTED,
    "DISABLED": OrderStatus.REJECTED,
    "DELETED": OrderStatus.REJECTED,
    "TIMEOUT": OrderStatus.EXPIRED,
}


def to_futu_symbol(symbol: str) -> str:
    """'AAPL' -> 'US.AAPL' (idempotent on already-prefixed codes)."""
    return symbol if symbol.startswith(_MARKET_PREFIX) else _MARKET_PREFIX + symbol


def from_futu_symbol(code: str) -> str:
    """'US.AAPL' -> 'AAPL'."""
    return code.split(".", 1)[1] if "." in code else code


class FutuBroker(Broker):
    def __init__(self, cfg: FutuConfig) -> None:
        try:
            import futu as ft
        except ImportError as exc:  # pragma: no cover - depends on extra
            raise ConfigError(
                "futu-api is not installed; run `uv sync --extra futu`"
            ) from exc
        self._ft = ft
        self._cfg = cfg
        self._trd_env = ft.TrdEnv.REAL if cfg.trd_env == "REAL" else ft.TrdEnv.SIMULATE
        self._trd = ft.OpenSecTradeContext(
            filter_trdmarket=ft.TrdMarket.US,
            host=cfg.host,
            port=cfg.port,
            security_firm=getattr(ft.SecurityFirm, cfg.security_firm),
        )
        self._quote = ft.OpenQuoteContext(host=cfg.host, port=cfg.port)
        if cfg.trd_env == "REAL":
            password = os.environ.get(cfg.unlock_password_env)
            if not password:
                self.close()
                raise ConfigError(
                    f"REAL trading requires env var {cfg.unlock_password_env} "
                    "(trade unlock password)"
                )
            self._check(self._trd.unlock_trade(password), "unlock_trade")

    # -- Broker interface ----------------------------------------------------
    def get_positions(self) -> dict[str, Position]:
        df = self._check(self._trd.position_list_query(trd_env=self._trd_env), "positions")
        positions: dict[str, Position] = {}
        for _, row in df.iterrows():
            qty = float(row["qty"])
            if qty == 0:
                continue
            symbol = from_futu_symbol(str(row["code"]))
            positions[symbol] = Position(
                instrument=symbol, qty=qty, avg_cost=float(row["cost_price"])
            )
        return positions

    def get_cash(self) -> float:
        df = self._check(self._trd.accinfo_query(trd_env=self._trd_env), "accinfo")
        if df.empty:
            raise BrokerError("accinfo_query returned no rows")
        return float(df["cash"].iloc[0])

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        codes = [to_futu_symbol(s) for s in symbols]
        df = self._check(self._quote.get_market_snapshot(codes), "market_snapshot")
        quotes: dict[str, Quote] = {}
        for _, row in df.iterrows():
            symbol = from_futu_symbol(str(row["code"]))
            quotes[symbol] = Quote(
                instrument=symbol, price=float(row["last_price"]), ts=utc_now()
            )
        return quotes

    def submit_order(self, order: Order) -> str:
        ft = self._ft
        if order.order_type is OrderType.MARKET:
            ft_type = ft.OrderType.MARKET
            price = 0.0
        else:
            ft_type = ft.OrderType.NORMAL
            assert order.limit_price is not None
            price = float(order.limit_price)
        side = ft.TrdSide.BUY if order.side is OrderSide.BUY else ft.TrdSide.SELL
        df = self._check(
            self._trd.place_order(
                price=price,
                qty=order.qty,
                code=to_futu_symbol(order.instrument),
                trd_side=side,
                order_type=ft_type,
                trd_env=self._trd_env,
                remark=order.client_order_id,
            ),
            "place_order",
        )
        if df.empty:
            raise BrokerError("place_order returned no rows")
        return str(df["order_id"].iloc[0])

    def get_order_status(self, order: Order) -> tuple[OrderStatus, list[Fill]]:
        if order.broker_order_id is None:
            raise BrokerError("order has no broker_order_id")
        df = self._check(
            self._trd.order_list_query(
                order_id=order.broker_order_id, trd_env=self._trd_env
            ),
            "order_list_query",
        )
        if df.empty:
            raise BrokerError(f"broker order {order.broker_order_id} not found")
        row = df.iloc[0]
        status = self._map_status(row["order_status"])
        fills: list[Fill] = []
        dealt_qty = float(row.get("dealt_qty") or 0.0)
        dealt_avg_price = float(row.get("dealt_avg_price") or 0.0)
        if dealt_qty > 0:
            fills.append(
                Fill(
                    client_order_id=order.client_order_id,
                    qty=dealt_qty,
                    price=dealt_avg_price,
                    ts=utc_now(),
                )
            )
        return status, fills

    def cancel_order(self, order: Order) -> None:
        if order.broker_order_id is None:
            raise BrokerError("order has no broker_order_id")
        ft = self._ft
        self._check(
            self._trd.modify_order(
                ft.ModifyOrderOp.CANCEL,
                order.broker_order_id,
                0,
                0,
                trd_env=self._trd_env,
            ),
            "cancel_order",
        )

    def close(self) -> None:
        for ctx in (getattr(self, "_trd", None), getattr(self, "_quote", None)):
            if ctx is None:
                continue
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                log.warning("error closing futu context", exc_info=True)

    # -- internals -------------------------------------------------------------
    def _check(self, result: tuple[Any, Any], what: str) -> Any:
        """Every futu call returns (ret, data); raise BrokerError unless RET_OK."""
        ret, data = result
        if ret != self._ft.RET_OK:
            raise BrokerError(f"futu {what} failed: {data}")
        return data

    def _map_status(self, raw: Any) -> OrderStatus:
        name = str(raw)
        if "." in name:  # tolerate enum reprs like 'OrderStatus.SUBMITTED'
            name = name.rsplit(".", 1)[1]
        status = _STATUS_MAP.get(name)
        if status is None:
            log.warning("unmapped futu order status; treating as SUBMITTED", raw=str(raw))
            return OrderStatus.SUBMITTED
        return status
