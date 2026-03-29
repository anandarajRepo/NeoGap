"""
NeoGap — Gap Strategy State Machine.

Flow
----
1. PRE_OPEN (8:55–9:15): Fetch previous closes for all watchlist symbols.
2. GAP_SCAN  (9:15–9:20): Detect gap-up / gap-down stocks at market open.
3. TREND_ANALYSIS       : For each gap stock, analyse historical gap trend.
4. SIGNAL_FILTER        : Filter by trend strength and direction alignment.
5. CONFIRMATION (wait N minutes after open): Confirm gap is still holding.
6. ORDER_ENTRY          : Place entry order + stop-loss for top signals.
7. POSITION_MONITOR     : Poll live quotes; manage trailing stops, targets, EOD exit.

State diagram:
  IDLE → PRE_OPEN → GAP_SCAN → TREND_ANALYSIS
       → SIGNAL_FILTER → CONFIRMATION → TRADING → CLOSING → IDLE

Risk rules (enforced every loop tick):
  - Max concurrent open positions = MAX_POSITIONS
  - Daily loss >= MAX_DAILY_LOSS_PCT → halt new entries
  - End-of-day (15:15 IST) → close all positions at market
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from config.settings import settings
from config.symbols import get_all_symbols
from models.trading_models import (
    ExitReason,
    GapDirection,
    GapEvent,
    GapSignal,
    GapTrend,
    OrderSide,
    Position,
    PositionStatus,
    SignalBasis,
    SignalDirection,
    StrategyMetrics,
    TradeResult,
)
from services.gap_detection_service import GapDetectionService
from services.gap_trend_service import GapTrendService
from services.market_timing_service import (
    is_end_of_day,
    is_gap_scan_window,
    is_market_open,
    is_pre_open,
    is_trading_day,
    now_ist,
    seconds_until_market_open,
)
from services.neo_data_service import NeoDataService
from strategy.order_manager import OrderManager
from utils.logger import get_logger

logger = get_logger("gap_strategy", settings.ops.log_level, settings.ops.log_file)


class StrategyState(Enum):
    IDLE = auto()
    PRE_OPEN = auto()
    GAP_SCAN = auto()
    TREND_ANALYSIS = auto()
    CONFIRMATION = auto()
    TRADING = auto()
    CLOSING = auto()


class GapStrategy:
    """
    Main orchestrator for the NeoGap intraday gap trading strategy.
    """

    def __init__(self, neo_client) -> None:
        self._client = neo_client
        self._data_svc = NeoDataService(neo_client)
        self._gap_detect = GapDetectionService()
        self._gap_trend = GapTrendService()
        self._order_mgr = OrderManager(neo_client)

        self._state = StrategyState.IDLE
        self._prev_closes: dict[str, float] = {}
        self._avg_volumes: dict[str, int] = {}
        self._gap_events: list[GapEvent] = []
        self._gap_trends: dict[str, GapTrend] = {}
        self._pending_confirmation: list[GapEvent] = []  # awaiting mini-ORB confirm
        self._signals: list[GapSignal] = []
        self._positions: dict[str, Position] = {}  # symbol → Position
        self._trade_results: list[TradeResult] = []
        self._metrics = StrategyMetrics(date=datetime.now())

        self._symbols = get_all_symbols()

    # ------------------------------------------------------------------
    # Main async run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        logger.info("=" * 60)
        logger.info("NeoGap Strategy starting | %s", now_ist().strftime("%Y-%m-%d"))
        logger.info("=" * 60)

        if not is_trading_day():
            logger.info("Not a trading day — exiting.")
            return

        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.error("Unhandled error in strategy tick: %s", exc, exc_info=True)
            await asyncio.sleep(settings.ops.poll_interval)

    async def _tick(self) -> None:
        if self._state == StrategyState.IDLE:
            if is_pre_open() or is_gap_scan_window() or is_market_open():
                self._state = StrategyState.PRE_OPEN
            else:
                secs = seconds_until_market_open()
                if secs > 60:
                    logger.info("Market opens in %.0f min — waiting…", secs / 60)

        elif self._state == StrategyState.PRE_OPEN:
            await self._pre_open_phase()

        elif self._state == StrategyState.GAP_SCAN:
            if is_gap_scan_window():
                await self._gap_scan_phase()
            elif is_market_open():
                # Gap scan window passed without scanning — move ahead
                self._state = StrategyState.TREND_ANALYSIS

        elif self._state == StrategyState.TREND_ANALYSIS:
            await self._trend_analysis_phase()

        elif self._state == StrategyState.CONFIRMATION:
            await self._confirmation_phase()

        elif self._state == StrategyState.TRADING:
            await self._trading_phase()

        elif self._state == StrategyState.CLOSING:
            await self._closing_phase()

    # ------------------------------------------------------------------
    # Phase 1: Pre-open — fetch historical data
    # ------------------------------------------------------------------

    async def _pre_open_phase(self) -> None:
        logger.info("[PRE_OPEN] Fetching previous closes and 20-day avg volumes…")
        loop = asyncio.get_event_loop()

        prev_closes, avg_volumes = await loop.run_in_executor(
            None, self._fetch_pre_open_data
        )
        self._prev_closes = prev_closes
        self._avg_volumes = avg_volumes
        logger.info(
            "[PRE_OPEN] Ready: %d symbols with prev_close data", len(prev_closes)
        )
        self._state = StrategyState.GAP_SCAN

    def _fetch_pre_open_data(self) -> tuple[dict[str, float], dict[str, int]]:
        prev_closes: dict[str, float] = {}
        avg_volumes: dict[str, int] = {}

        for symbol in self._symbols:
            bars = self._data_svc.get_historical_ohlc(symbol, days=25)
            if len(bars) >= 2:
                prev_closes[symbol] = bars[-2].close
                if len(bars) >= 20:
                    avg_volumes[symbol] = int(
                        sum(b.volume for b in bars[-20:]) / 20
                    )
            elif len(bars) == 1:
                prev_closes[symbol] = bars[0].close

        return prev_closes, avg_volumes

    # ------------------------------------------------------------------
    # Phase 2: Gap scan — detect gaps at open
    # ------------------------------------------------------------------

    async def _gap_scan_phase(self) -> None:
        if self._gap_events:
            return  # already scanned

        logger.info("[GAP_SCAN] Market open — scanning for gaps…")
        loop = asyncio.get_event_loop()
        self._gap_events = await loop.run_in_executor(None, self._scan_gaps)
        logger.info("[GAP_SCAN] Found %d gap stocks", len(self._gap_events))

        if self._gap_events:
            self._state = StrategyState.TREND_ANALYSIS
        else:
            logger.info("[GAP_SCAN] No qualifying gaps today — moving to TRADING (monitor only)")
            self._state = StrategyState.TRADING

    def _scan_gaps(self) -> list[GapEvent]:
        live_quotes = self._data_svc.get_live_quotes(list(self._prev_closes.keys()))
        return self._gap_detect.detect_gaps(
            self._prev_closes, live_quotes, self._avg_volumes
        )

    # ------------------------------------------------------------------
    # Phase 3: Trend analysis — analyse historical gaps per stock
    # ------------------------------------------------------------------

    async def _trend_analysis_phase(self) -> None:
        logger.info("[TREND_ANALYSIS] Analysing gap trends for %d stocks…", len(self._gap_events))
        loop = asyncio.get_event_loop()
        self._gap_trends = await loop.run_in_executor(None, self._analyse_trends)

        await self._generate_signals()
        self._state = StrategyState.CONFIRMATION

    def _analyse_trends(self) -> dict[str, GapTrend]:
        trends: dict[str, GapTrend] = {}
        for event in self._gap_events:
            bars = self._data_svc.get_historical_ohlc(
                event.symbol, days=settings.gap.lookback_days + 5
            )
            if len(bars) < 2:
                logger.warning("%s: insufficient historical data for trend analysis", event.symbol)
                continue
            trend = self._gap_trend.analyse(event.symbol, bars, event.gap_direction)
            trends[event.symbol] = trend
        return trends

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    async def _generate_signals(self) -> None:
        signals: list[GapSignal] = []

        for event in self._gap_events:
            trend = self._gap_trends.get(event.symbol)
            if trend is None:
                continue

            if not self._gap_trend.has_sufficient_data(trend):
                logger.info(
                    "%s: only %d historical gaps — insufficient (need %d)",
                    event.symbol, trend.total_gaps, settings.gap.min_gap_occurrences,
                )
                continue

            # Determine signal direction and basis
            if self._gap_trend.is_continuation_signal(trend):
                # Trade WITH the gap direction
                if event.gap_direction == GapDirection.UP:
                    direction = SignalDirection.BUY
                else:
                    direction = SignalDirection.SELL
                basis = SignalBasis.CONTINUATION

            elif self._gap_trend.is_reversal_signal(trend):
                # Trade AGAINST the gap direction (fade the gap)
                if event.gap_direction == GapDirection.UP:
                    direction = SignalDirection.SELL
                else:
                    direction = SignalDirection.BUY
                basis = SignalBasis.REVERSAL

            else:
                logger.info(
                    "%s: no clear trend (cont=%.1f%% rev=%.1f%%) — skipping",
                    event.symbol,
                    trend.continuation_rate * 100,
                    trend.reversal_rate * 100,
                )
                continue

            entry_price, stop_loss, target_1, target_2 = self._compute_levels(event, direction)
            confidence = trend.trend_score

            signal = GapSignal(
                symbol=event.symbol,
                generated_at=now_ist(),
                gap_event=event,
                gap_trend=trend,
                signal_direction=direction,
                signal_basis=basis,
                confidence_score=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
            )
            signals.append(signal)
            logger.info(
                "SIGNAL | %s | %s (%s) | conf=%.0f | entry=%.2f sl=%.2f t1=%.2f t2=%.2f",
                event.symbol,
                direction.value,
                basis.value,
                confidence,
                entry_price, stop_loss, target_1, target_2,
            )

        # Sort by confidence descending; keep top MAX_POSITIONS
        signals.sort(key=lambda s: s.confidence_score, reverse=True)
        self._signals = signals[: settings.risk.max_positions]
        self._pending_confirmation = [s.gap_event for s in self._signals]
        self._metrics.total_signals += len(self._signals)

    def _compute_levels(
        self,
        event: GapEvent,
        direction: SignalDirection,
    ) -> tuple[float, float, float, float]:
        entry = event.open_price
        sl_pct = settings.risk.stop_loss_pct
        tgt_mult = settings.risk.target_multiplier

        if direction == SignalDirection.BUY:
            stop_loss = entry * (1 - sl_pct)
            risk = entry - stop_loss
            target_1 = entry + risk * tgt_mult
            target_2 = entry + risk * tgt_mult * 2
        else:
            stop_loss = entry * (1 + sl_pct)
            risk = stop_loss - entry
            target_1 = entry - risk * tgt_mult
            target_2 = entry - risk * tgt_mult * 2

        return round(entry, 2), round(stop_loss, 2), round(target_1, 2), round(target_2, 2)

    # ------------------------------------------------------------------
    # Phase 4: Confirmation — wait N minutes, verify gap is holding
    # ------------------------------------------------------------------

    async def _confirmation_phase(self) -> None:
        if not self._pending_confirmation:
            self._state = StrategyState.TRADING
            return

        loop = asyncio.get_event_loop()
        symbols = [e.symbol for e in self._pending_confirmation]
        quotes = await loop.run_in_executor(
            None, lambda: self._data_svc.get_live_quotes(symbols)
        )

        confirmed_signals: list[GapSignal] = []
        for signal in self._signals:
            event = signal.gap_event
            quote = quotes.get(event.symbol)
            if not quote:
                continue
            if self._gap_detect.confirm_gap_direction(event, quote):
                confirmed_signals.append(signal)

        self._signals = confirmed_signals
        self._pending_confirmation = []
        logger.info(
            "[CONFIRMATION] %d/%d signals confirmed after mini-ORB check",
            len(confirmed_signals),
            self._metrics.total_signals,
        )
        self._state = StrategyState.TRADING

    # ------------------------------------------------------------------
    # Phase 5: Trading — enter positions, manage open positions
    # ------------------------------------------------------------------

    async def _trading_phase(self) -> None:
        if is_end_of_day():
            self._state = StrategyState.CLOSING
            return

        loop = asyncio.get_event_loop()

        # Enter new positions for confirmed signals
        for signal in list(self._signals):
            if signal.symbol in self._positions:
                continue
            if len(self._positions) >= settings.risk.max_positions:
                break
            if self._daily_loss_exceeded():
                logger.warning("Daily loss limit reached — no new entries")
                break

            quantity = self._compute_quantity(signal)
            if quantity <= 0:
                continue

            position = await loop.run_in_executor(
                None, lambda s=signal, q=quantity: self._order_mgr.place_entry_order(s, q)
            )
            if position:
                self._positions[signal.symbol] = position
                self._metrics.total_trades += 1

        self._signals = []  # signals consumed

        # Monitor existing positions
        if self._positions:
            symbols = list(self._positions.keys())
            quotes = await loop.run_in_executor(
                None, lambda: self._data_svc.get_live_quotes(symbols)
            )
            for symbol, position in list(self._positions.items()):
                quote = quotes.get(symbol)
                if not quote:
                    continue
                await self._manage_position(position, quote.ltp)

    async def _manage_position(self, position: Position, current_price: float) -> None:
        position.compute_unrealised_pnl(current_price)
        position.update_trailing_stop(current_price, settings.risk.trailing_stop_pct)

        # Check stop-loss (trailing or initial)
        sl = position.trailing_stop if position.trailing_stop else position.signal.stop_loss
        sl_triggered = (
            (position.order_side == OrderSide.BUY and current_price <= sl) or
            (position.order_side == OrderSide.SELL and current_price >= sl)
        )
        if sl_triggered:
            await self._close_position(position, current_price, ExitReason.TRAILING_STOP)
            return

        # Partial exit at Target 1
        if not position.partial_exit_done:
            t1 = position.signal.target_1
            t1_hit = (
                (position.order_side == OrderSide.BUY and current_price >= t1) or
                (position.order_side == OrderSide.SELL and current_price <= t1)
            )
            if t1_hit:
                partial_qty = math.ceil(position.quantity * settings.risk.partial_exit_pct)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self._order_mgr.place_exit_order(position, partial_qty, tag="GAP_T1"),
                )
                position.partial_exit_done = True
                pnl = (current_price - position.entry_price) * partial_qty * (
                    1 if position.order_side == OrderSide.BUY else -1
                )
                position.realised_pnl += pnl
                logger.info(
                    "PARTIAL EXIT | %s | qty=%d | price=%.2f | pnl=%.2f",
                    position.symbol, partial_qty, current_price, pnl,
                )

        # Full exit at Target 2
        t2 = position.signal.target_2
        t2_hit = (
            (position.order_side == OrderSide.BUY and current_price >= t2) or
            (position.order_side == OrderSide.SELL and current_price <= t2)
        )
        if t2_hit:
            await self._close_position(position, current_price, ExitReason.TARGET_2)

    async def _close_position(
        self,
        position: Position,
        exit_price: float,
        reason: ExitReason,
    ) -> None:
        remaining_qty = position.quantity
        if position.partial_exit_done:
            remaining_qty = math.floor(position.quantity * (1 - settings.risk.partial_exit_pct))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._order_mgr.place_exit_order(
                position, remaining_qty, tag=f"GAP_{reason.value}"
            ),
        )

        pnl = (exit_price - position.entry_price) * remaining_qty * (
            1 if position.order_side == OrderSide.BUY else -1
        )
        position.realised_pnl += pnl
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.now()
        position.exit_reason = reason

        result = TradeResult(
            symbol=position.symbol,
            order_side=position.order_side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.opened_at or datetime.now(),
            exit_time=position.closed_at,
            pnl=position.realised_pnl,
            exit_reason=reason,
            gap_pct=position.signal.gap_event.gap_pct,
            signal_basis=position.signal.signal_basis,
        )
        self._trade_results.append(result)
        self._update_metrics(result)

        del self._positions[position.symbol]
        logger.info(
            "CLOSED | %s | exit=%.2f | reason=%s | pnl=%.2f",
            position.symbol, exit_price, reason.value, position.realised_pnl,
        )

    # ------------------------------------------------------------------
    # Phase 6: Closing — EOD square-off all positions
    # ------------------------------------------------------------------

    async def _closing_phase(self) -> None:
        if not self._positions:
            self._print_daily_summary()
            self._state = StrategyState.IDLE
            return

        logger.info("[EOD] Squaring off %d open position(s)…", len(self._positions))
        loop = asyncio.get_event_loop()
        symbols = list(self._positions.keys())
        quotes = await loop.run_in_executor(
            None, lambda: self._data_svc.get_live_quotes(symbols)
        )

        for symbol, position in list(self._positions.items()):
            quote = quotes.get(symbol)
            exit_price = quote.ltp if quote else position.entry_price
            await self._close_position(position, exit_price, ExitReason.END_OF_DAY)

        self._print_daily_summary()
        self._state = StrategyState.IDLE

    # ------------------------------------------------------------------
    # Risk helpers
    # ------------------------------------------------------------------

    def _daily_loss_exceeded(self) -> bool:
        max_loss = settings.risk.portfolio_value * settings.risk.max_daily_loss_pct
        return self._metrics.daily_loss <= -max_loss

    def _compute_quantity(self, signal: GapSignal) -> int:
        alloc = settings.risk.portfolio_value * settings.risk.risk_per_trade
        qty = int(alloc / signal.entry_price)
        return max(qty, 1)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_metrics(self, result: TradeResult) -> None:
        self._metrics.cumulative_pnl += result.pnl
        self._metrics.daily_loss = min(self._metrics.daily_loss, result.pnl)
        self._metrics.peak_pnl = max(self._metrics.peak_pnl, self._metrics.cumulative_pnl)
        drawdown = self._metrics.cumulative_pnl - self._metrics.peak_pnl
        self._metrics.max_drawdown = min(self._metrics.max_drawdown, drawdown)
        if result.pnl > 0:
            self._metrics.winning_trades += 1
        else:
            self._metrics.losing_trades += 1

    def _print_daily_summary(self) -> None:
        m = self._metrics
        logger.info("=" * 60)
        logger.info("DAILY SUMMARY | %s", now_ist().strftime("%Y-%m-%d"))
        logger.info("  Signals generated : %d", m.total_signals)
        logger.info("  Trades executed   : %d", m.total_trades)
        logger.info("  Win / Loss        : %d / %d", m.winning_trades, m.losing_trades)
        logger.info("  Win rate          : %.1f%%", m.win_rate * 100)
        logger.info("  Cumulative P&L    : RS.%.2f", m.cumulative_pnl)
        logger.info("  Max drawdown      : RS.%.2f", m.max_drawdown)
        logger.info("=" * 60)
