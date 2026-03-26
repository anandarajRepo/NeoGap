"""
NeoGap — Order Manager.

Handles all Kotak Neo order placement, modification, and cancellation.
Abstracts away the raw API so GapStrategy stays clean.

All order calls respect DRY_RUN mode — in dry-run, orders are logged
but NOT sent to the broker.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from config.settings import settings
from models.trading_models import (
    GapSignal,
    OrderSide,
    OrderType,
    Position,
    PositionStatus,
)
from utils.logger import get_logger

logger = get_logger("order_manager", settings.ops.log_level, settings.ops.log_file)

_MAX_RETRIES = 3
_BASE_BACKOFF = 2


def _retry_order(func, *args, **kwargs):
    backoff = _BASE_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("Order attempt %d failed: %s. Retrying in %ds…", attempt, exc, backoff)
            time.sleep(backoff)
            backoff *= 2


class OrderManager:

    def __init__(self, neo_client) -> None:
        self._client = neo_client
        self._dry_run = settings.ops.dry_run

    # ------------------------------------------------------------------
    # Position entry
    # ------------------------------------------------------------------

    def place_entry_order(self, signal: GapSignal, quantity: int) -> Optional[Position]:
        """
        Place a market entry order for the given signal.

        Returns a Position object on success, None on failure.
        """
        side = OrderSide.BUY if signal.signal_direction.value == "BUY" else OrderSide.SELL
        order_id = self._place_order(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=0,
            tag="GAP_ENTRY",
        )
        if not order_id:
            return None

        position = Position(
            symbol=signal.symbol,
            signal=signal,
            entry_price=signal.entry_price,
            quantity=quantity,
            order_side=side,
            status=PositionStatus.OPEN,
            entry_order_id=order_id,
            highest_price=signal.entry_price,
            lowest_price=signal.entry_price,
            trailing_stop=signal.stop_loss,
            opened_at=datetime.now(),
        )
        logger.info(
            "ENTRY | %s | %s | qty=%d | price=%.2f | sl=%.2f | t1=%.2f | t2=%.2f",
            signal.symbol,
            side.value,
            quantity,
            signal.entry_price,
            signal.stop_loss,
            signal.target_1,
            signal.target_2,
        )
        return position

    # ------------------------------------------------------------------
    # Stop-loss order
    # ------------------------------------------------------------------

    def place_stop_loss_order(self, position: Position) -> str:
        """Place a stop-loss market order. Returns order_id."""
        sl_side = OrderSide.SELL if position.order_side == OrderSide.BUY else OrderSide.BUY
        order_id = self._place_order(
            symbol=position.symbol,
            side=sl_side,
            quantity=position.quantity,
            order_type=OrderType.SL_MARKET,
            price=0,
            trigger_price=position.signal.stop_loss,
            tag="GAP_SL",
        )
        return order_id or ""

    # ------------------------------------------------------------------
    # Partial / full exit
    # ------------------------------------------------------------------

    def place_exit_order(
        self,
        position: Position,
        quantity: int,
        price: float = 0,
        tag: str = "GAP_EXIT",
    ) -> str:
        exit_side = OrderSide.SELL if position.order_side == OrderSide.BUY else OrderSide.BUY
        order_type = OrderType.LIMIT if price > 0 else OrderType.MARKET
        order_id = self._place_order(
            symbol=position.symbol,
            side=exit_side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            tag=tag,
        )
        return order_id or ""

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self._dry_run:
            logger.info("[DRY RUN] Cancel order %s for %s", order_id, symbol)
            return True
        try:
            _retry_order(
                self._client.cancel_order,
                order_id=order_id,
            )
            logger.info("Cancelled order %s for %s", order_id, symbol)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    # ------------------------------------------------------------------
    # Order status / fill price
    # ------------------------------------------------------------------

    def get_order_fill_price(self, order_id: str) -> Optional[float]:
        if self._dry_run:
            return None
        try:
            orders = _retry_order(self._client.order_report)
            raw_list = orders if isinstance(orders, list) else (orders or {}).get("data", [])
            for order in raw_list:
                if str(order.get("nOrdNo") or order.get("order_id", "")) == str(order_id):
                    return float(order.get("avgPrc") or order.get("average_price", 0) or 0)
        except Exception as exc:
            logger.error("get_order_fill_price(%s) failed: %s", order_id, exc)
        return None

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        price: float,
        trigger_price: float = 0,
        tag: str = "",
    ) -> Optional[str]:
        if self._dry_run:
            fake_id = f"DRY_{symbol}_{side.value}_{int(time.time())}"
            logger.info(
                "[DRY RUN] %s | %s | qty=%d | type=%s | price=%.2f | sl=%.2f | tag=%s → %s",
                symbol, side.value, quantity, order_type.value, price, trigger_price, tag, fake_id,
            )
            return fake_id

        try:
            resp = _retry_order(
                self._client.place_order,
                exchange_segment="nse_cm",
                product="MIS",           # Intraday margin product
                price=str(price),
                order_type=order_type.value,
                quantity=str(quantity),
                validity="DAY",
                trading_symbol=symbol,
                transaction_type=side.value,
                amo="NO",
                disclosed_quantity="0",
                market_protection="0",
                pf="N",
                trigger_price=str(trigger_price) if trigger_price else "0",
                tag=tag,
            )
            order_id = str(resp.get("nOrdNo") or resp.get("order_id", "")) if resp else ""
            if order_id:
                logger.info(
                    "ORDER PLACED | %s | %s | qty=%d | type=%s | price=%.2f | id=%s",
                    symbol, side.value, quantity, order_type.value, price, order_id,
                )
            else:
                logger.error("Order placement returned no order_id: %s", resp)
            return order_id or None
        except Exception as exc:
            logger.error("_place_order failed for %s: %s", symbol, exc)
            return None
