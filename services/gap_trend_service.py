"""
NeoGap — Gap Trend Analysis Service.

Core logic of NeoGap: for each stock experiencing a gap today, analyse
what happened historically when the SAME DIRECTION gap occurred.

Algorithm
---------
1. Load last N days of OHLC for the symbol (N = GAP_LOOKBACK_DAYS).
2. For each consecutive pair of days, detect if day[i] gapped vs day[i-1].
3. For each historical gap matching TODAY'S direction:
     - If day[i].close > day[i].open  (gap-up) → continuation
     - If day[i].close < day[i].open  (gap-up, filled) → reversal
     - (symmetrically for gap-down)
4. Compute continuation_rate and reversal_rate.
5. Return a GapTrend summarising the historical behaviour.

Decision rule (used by GapStrategy)
-------------------------------------
  continuation_rate >= CONTINUATION_THRESHOLD → trade WITH gap direction
  reversal_rate     >= REVERSAL_THRESHOLD     → trade AGAINST gap direction
  otherwise                                   → no trade (insufficient signal)
"""

from __future__ import annotations

import statistics

from config.settings import settings
from models.trading_models import (
    DayOHLC,
    GapDirection,
    GapTrend,
    HistoricalGap,
)
from utils.logger import get_logger

logger = get_logger("gap_trend", settings.ops.log_level, settings.ops.log_file)


class GapTrendService:

    def __init__(self) -> None:
        self._cfg = settings.gap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(
        self,
        symbol: str,
        bars: list[DayOHLC],
        target_direction: GapDirection,
    ) -> GapTrend:
        """
        Analyse historical gap behaviour for *symbol* in *target_direction*.

        Parameters
        ----------
        symbol           : NSE ticker
        bars             : historical OHLC (oldest → newest), ≥ 2 bars
        target_direction : the direction of today's gap (UP or DOWN)

        Returns
        -------
        GapTrend with continuation/reversal statistics.
        """
        historical_gaps = self._extract_gaps(symbol, bars, target_direction)

        total = len(historical_gaps)
        cont_count = sum(1 for g in historical_gaps if g.is_continuation)
        rev_count = total - cont_count

        avg_gap = statistics.mean([g.gap_pct for g in historical_gaps]) if historical_gaps else 0.0
        cont_gaps = [g.continuation_pct for g in historical_gaps if g.is_continuation]
        rev_gaps = [g.continuation_pct for g in historical_gaps if not g.is_continuation]
        avg_cont = statistics.mean(cont_gaps) if cont_gaps else 0.0
        avg_rev = statistics.mean(rev_gaps) if rev_gaps else 0.0

        trend_score = self._compute_trend_score(historical_gaps)

        trend = GapTrend(
            symbol=symbol,
            direction=target_direction,
            total_gaps=total,
            continuation_count=cont_count,
            reversal_count=rev_count,
            avg_gap_pct=avg_gap,
            avg_continuation_pct=avg_cont,
            avg_reversal_pct=avg_rev,
            trend_score=trend_score,
        )

        logger.info(
            "%s | dir=%s | gaps=%d | cont_rate=%.1f%% | rev_rate=%.1f%% | score=%.1f",
            symbol,
            target_direction.value,
            total,
            trend.continuation_rate * 100,
            trend.reversal_rate * 100,
            trend_score,
        )
        return trend

    def has_sufficient_data(self, trend: GapTrend) -> bool:
        return trend.total_gaps >= self._cfg.min_gap_occurrences

    def is_continuation_signal(self, trend: GapTrend) -> bool:
        return (
            self.has_sufficient_data(trend)
            and trend.continuation_rate >= self._cfg.continuation_threshold
        )

    def is_reversal_signal(self, trend: GapTrend) -> bool:
        return (
            self.has_sufficient_data(trend)
            and trend.reversal_rate >= self._cfg.reversal_threshold
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_gaps(
        self,
        symbol: str,
        bars: list[DayOHLC],
        target_direction: GapDirection,
    ) -> list[HistoricalGap]:
        """
        Walk through OHLC bars and extract gap events matching target_direction.
        """
        gaps: list[HistoricalGap] = []
        min_gap_pct = self._cfg.min_gap_pct

        for i in range(1, len(bars)):
            prev = bars[i - 1]
            curr = bars[i]

            if prev.close == 0:
                continue

            gap_pct = (curr.open - prev.close) / prev.close * 100
            abs_gap = abs(gap_pct)

            if abs_gap < min_gap_pct:
                continue

            direction = GapDirection.UP if gap_pct > 0 else GapDirection.DOWN
            if direction != target_direction:
                continue

            # Determine continuation vs reversal
            # Continuation: price moved further in gap direction by end of day
            # Reversal: price closed closer to prev_close (or crossed it)
            if direction == GapDirection.UP:
                # Continuation: close > open (bullish day)
                is_cont = curr.close > curr.open
                cont_pct = abs((curr.close - curr.open) / curr.open * 100)
            else:
                # Continuation: close < open (bearish day)
                is_cont = curr.close < curr.open
                cont_pct = abs((curr.open - curr.close) / curr.open * 100)

            gaps.append(HistoricalGap(
                date=curr.date,
                gap_direction=direction,
                gap_pct=abs_gap,
                prev_close=prev.close,
                open_price=curr.open,
                day_close=curr.close,
                is_continuation=is_cont,
                continuation_pct=cont_pct,
            ))

        return gaps

    def _compute_trend_score(self, gaps: list[HistoricalGap]) -> float:
        """
        Score 0–100 reflecting how strongly the historical gaps trended.

        Components:
          - Consistency: max(continuation_rate, reversal_rate) weighted 60%
          - Sample size: up to 20 gaps normalised, weighted 20%
          - Avg move size: larger moves → stronger signal, weighted 20%
        """
        if not gaps:
            return 0.0

        total = len(gaps)
        cont = sum(1 for g in gaps if g.is_continuation)
        dominant_rate = max(cont / total, (total - cont) / total)
        consistency_score = dominant_rate * 60

        sample_score = min(total / 20, 1.0) * 20

        avg_cont_pct = statistics.mean([g.continuation_pct for g in gaps]) if gaps else 0.0
        move_score = min(avg_cont_pct / 2.0, 1.0) * 20  # cap at 2% average move

        return round(consistency_score + sample_score + move_score, 1)
