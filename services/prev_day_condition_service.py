"""
NeoGap — Previous Day Closing Condition Service.

Analyses the previous trading day's OHLC bar to confirm directional bias
before entering a trade.  This acts as an additional filter on top of the
gap-trend analysis, ensuring the prior session's price action supports the
intended trade direction.

Signals
-------
Bullish  → supports LONG  (BUY):
  Volume surge AND close near the day's high.
  Interpretation: strong buying interest; late-session demand absorbed supply,
  suggesting momentum continuation the following day.

Bearish  → supports SHORT (SELL):
  High volume AND price fails to hold highs (distribution).
  Interpretation: sellers absorbed buying pressure; despite high volume the
  close drifted back toward the lows — a classic distribution / supply signal.

Volume surge threshold
  prev_day.volume  >=  avg_volume  *  volume_surge_ratio   (default 1.5×)

Close-position metric
  close_pos = (close − low) / (high − low)   (0 = closed at low, 1 = at high)

  Bullish:  close_pos  >=  close_near_high_threshold   (default 0.70)
  Bearish:  close_pos  <=  close_near_low_threshold    (default 0.30)

Toggle
------
  Set ENABLE_PREV_DAY_CONDITION=false in .env (or environment) to bypass
  this filter entirely.  When disabled every signal passes unconditionally.
"""

from __future__ import annotations

from models.trading_models import DayOHLC, SignalDirection
from config.settings import settings
from utils.logger import get_logger

logger = get_logger("prev_day_condition", settings.ops.log_level, settings.ops.log_file)


class PrevDayConditionService:
    """
    Checks whether the previous day's closing conditions support a given
    signal direction.

    Usage
    -----
    Instantiate once and call ``check()`` for each candidate signal during
    signal-generation.  Call ``is_enabled()`` first if you want to skip the
    check early without constructing any intermediate data.
    """

    def __init__(self) -> None:
        self._cfg = settings.prev_day

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True when the previous-day condition filter is active."""
        return self._cfg.enable

    def check(
        self,
        symbol: str,
        prev_bar: DayOHLC,
        avg_volume: int,
        signal_direction: SignalDirection,
    ) -> bool:
        """
        Return True if the previous day's bar supports *signal_direction*.

        Parameters
        ----------
        symbol           : NSE ticker — used only for log messages.
        prev_bar         : The most recent completed trading day's OHLC bar.
        avg_volume       : 20-day average daily volume for the symbol.
        signal_direction : BUY (long) or SELL (short).

        Returns
        -------
        True  → condition passes; proceed with the signal.
        False → condition fails; discard the signal.

        Notes
        -----
        * If the filter is disabled (``enable=False``) this method always
          returns True without any computation.
        * A zero-range day (high == low) is treated as a pass because there
          is no price action to evaluate against.
        * If avg_volume is 0 the volume-surge check is skipped (treated as
          no surge), so the overall result is False — the signal is dropped
          to avoid trading with no volume reference.
        """
        if not self._cfg.enable:
            return True

        day_range = prev_bar.high - prev_bar.low
        if day_range == 0:
            logger.debug("%s: prev day has zero range — bypassing condition check", symbol)
            return True

        # ----------------------------------------------------------------
        # Volume surge: prev day volume >= avg * surge_ratio
        # ----------------------------------------------------------------
        volume_surge = (
            avg_volume > 0
            and prev_bar.volume >= avg_volume * self._cfg.volume_surge_ratio
        )

        # ----------------------------------------------------------------
        # Where did price close within the day's high-low range?
        # 0.0 = closed exactly at low, 1.0 = closed exactly at high
        # ----------------------------------------------------------------
        close_position = (prev_bar.close - prev_bar.low) / day_range

        # ----------------------------------------------------------------
        # Direction-specific evaluation
        # ----------------------------------------------------------------
        if signal_direction == SignalDirection.BUY:
            # Bullish signal: volume surge + close near the day's high
            passes = volume_surge and close_position >= self._cfg.close_near_high_threshold
            logger.info(
                "%s PREV_DAY [LONG] | vol_surge=%s (vol=%d avg=%d ×%.1f) | "
                "close_pos=%.2f (need ≥ %.2f) | %s",
                symbol,
                volume_surge,
                prev_bar.volume,
                avg_volume,
                self._cfg.volume_surge_ratio,
                close_position,
                self._cfg.close_near_high_threshold,
                "PASS" if passes else "FAIL",
            )
        else:
            # Bearish / distribution signal: high volume + close near the day's low
            passes = volume_surge and close_position <= self._cfg.close_near_low_threshold
            logger.info(
                "%s PREV_DAY [SHORT] | vol_surge=%s (vol=%d avg=%d ×%.1f) | "
                "close_pos=%.2f (need ≤ %.2f) | %s",
                symbol,
                volume_surge,
                prev_bar.volume,
                avg_volume,
                self._cfg.volume_surge_ratio,
                close_position,
                self._cfg.close_near_low_threshold,
                "PASS" if passes else "FAIL",
            )

        return passes
