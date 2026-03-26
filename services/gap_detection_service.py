"""
NeoGap — Gap Detection Service.

Responsibilities:
  1. Given a dict of {symbol: prev_close} and live open prices,
     calculate the gap % for each symbol.
  2. Filter symbols that qualify as gap-up or gap-down based on
     the configured MIN_GAP_PCT / MAX_GAP_PCT thresholds.
  3. Return a list of GapEvent objects for further analysis.

Gap definitions
---------------
  Gap-Up  : today_open > prev_close by >= min_gap_pct %
  Gap-Down: today_open < prev_close by >= min_gap_pct %
"""

from __future__ import annotations

from datetime import datetime

from config.settings import settings
from models.trading_models import GapDirection, GapEvent, LiveQuote
from utils.logger import get_logger

logger = get_logger("gap_detection", settings.ops.log_level, settings.ops.log_file)


class GapDetectionService:

    def __init__(self) -> None:
        self._cfg = settings.gap

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def compute_gap(self, open_price: float, prev_close: float) -> tuple[GapDirection, float]:
        """
        Compute gap direction and gap percentage.

        Returns
        -------
        (GapDirection, gap_pct)
            gap_pct is positive for gap-up, negative for gap-down.
        """
        if prev_close == 0:
            return GapDirection.NONE, 0.0

        gap_pct = (open_price - prev_close) / prev_close * 100

        abs_gap = abs(gap_pct)
        if abs_gap < self._cfg.min_gap_pct or abs_gap > self._cfg.max_gap_pct:
            return GapDirection.NONE, gap_pct

        if gap_pct > 0:
            return GapDirection.UP, gap_pct
        return GapDirection.DOWN, gap_pct

    def detect_gaps(
        self,
        prev_closes: dict[str, float],
        live_quotes: dict[str, LiveQuote],
        avg_volumes: dict[str, int] | None = None,
    ) -> list[GapEvent]:
        """
        Detect gap-up / gap-down stocks.

        Parameters
        ----------
        prev_closes  : {symbol: previous_day_close}
        live_quotes  : {symbol: LiveQuote} at / just after market open
        avg_volumes  : {symbol: 20-day average volume}; optional, used for
                       volume ratio filtering.

        Returns
        -------
        List of GapEvent objects sorted by absolute gap_pct descending.
        """
        avg_volumes = avg_volumes or {}
        gap_events: list[GapEvent] = []

        for symbol, prev_close in prev_closes.items():
            quote = live_quotes.get(symbol)
            if not quote or quote.ltp == 0:
                continue

            direction, gap_pct = self.compute_gap(quote.ltp, prev_close)
            if direction == GapDirection.NONE:
                continue

            avg_vol = avg_volumes.get(symbol, 0)
            event = GapEvent(
                symbol=symbol,
                detected_at=datetime.now(),
                gap_direction=direction,
                gap_pct=abs(gap_pct),
                prev_close=prev_close,
                open_price=quote.ltp,
                avg_volume_20d=avg_vol,
                today_volume=quote.volume,
            )

            # Optional: filter low-volume stocks
            if settings.filters.enable_volume_filter and avg_vol > 0:
                if quote.volume < settings.filters.min_avg_volume // 10:
                    logger.debug(
                        "%s skipped: opening volume %d too low", symbol, quote.volume
                    )
                    continue

            gap_events.append(event)
            logger.info(
                "GAP %s | %s | %.2f%% | open=%.2f prev_close=%.2f",
                direction.value,
                symbol,
                abs(gap_pct),
                quote.ltp,
                prev_close,
            )

        gap_events.sort(key=lambda e: e.gap_pct, reverse=True)
        logger.info("Detected %d gap events", len(gap_events))
        return gap_events

    # ------------------------------------------------------------------
    # Gap confirmation (mini-ORB after open)
    # ------------------------------------------------------------------

    def confirm_gap_direction(
        self,
        event: GapEvent,
        confirmation_quote: LiveQuote,
    ) -> bool:
        """
        After `confirmation_minutes` (e.g. 5 min), verify the price has
        not closed the gap entirely — confirming the gap is holding.

        For gap-up: confirmation_price should still be above prev_close.
        For gap-down: confirmation_price should still be below prev_close.
        """
        price = confirmation_quote.ltp
        prev = event.prev_close

        if event.gap_direction == GapDirection.UP:
            holding = price > prev
        else:
            holding = price < prev

        if not holding:
            logger.info(
                "%s gap NOT confirmed — price %.2f crossed back through prev_close %.2f",
                event.symbol, price, prev,
            )
        else:
            logger.info(
                "%s gap CONFIRMED — price %.2f holding %s prev_close %.2f",
                event.symbol,
                price,
                "above" if event.gap_direction == GapDirection.UP else "below",
                prev,
            )
        return holding
