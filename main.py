"""
NeoGap — Entry point.

Usage
-----
  python main.py auth          Authenticate with Kotak Neo (interactive OTP)
  python main.py run           Start the gap trading strategy
  python main.py scan          One-shot gap scan (no orders placed)
  python main.py status        Show today's positions and metrics
"""

from __future__ import annotations

import asyncio
import sys

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("main", settings.ops.log_level, settings.ops.log_file)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_auth() -> None:
    """Interactive authentication — stores token for the trading session."""
    from utils.auth_helper import get_neo_client
    client = get_neo_client()
    logger.info("Authentication successful. Access token cached.")


def cmd_run() -> None:
    """Start the full gap trading strategy."""
    from utils.auth_helper import get_neo_client
    from strategy.gap_strategy import GapStrategy

    client = get_neo_client()
    strategy = GapStrategy(client)

    logger.info(
        "Starting NeoGap | DRY_RUN=%s | PORTFOLIO=RS.%.0f | MAX_POS=%d",
        settings.ops.dry_run,
        settings.risk.portfolio_value,
        settings.risk.max_positions,
    )
    logger.info(
        "Gap params | MIN=%.1f%% MAX=%.1f%% LOOKBACK=%dd CONT_THRESH=%.0f%%",
        settings.gap.min_gap_pct,
        settings.gap.max_gap_pct,
        settings.gap.lookback_days,
        settings.gap.continuation_threshold * 100,
    )

    try:
        asyncio.run(strategy.run())
    except KeyboardInterrupt:
        logger.info("NeoGap stopped by user (Ctrl+C).")


def cmd_scan() -> None:
    """
    One-shot gap scan: detect today's gaps and print trend analysis.
    No orders are placed regardless of DRY_RUN setting.
    """
    import os
    os.environ["DRY_RUN"] = "true"  # force dry-run for scan command

    from utils.auth_helper import get_neo_client
    from services.neo_data_service import NeoDataService
    from services.gap_detection_service import GapDetectionService
    from services.gap_trend_service import GapTrendService
    from config.symbols import get_all_symbols

    client = get_neo_client()
    data_svc = NeoDataService(client)
    gap_detect = GapDetectionService()
    gap_trend = GapTrendService()
    symbols = get_all_symbols()

    print("\n=== NeoGap One-Shot Scanner ===\n")

    # Fetch prev closes
    prev_closes: dict[str, float] = {}
    for sym in symbols:
        pc = data_svc.get_prev_close(sym)
        if pc:
            prev_closes[sym] = pc

    # Live quotes
    live_quotes = data_svc.get_live_quotes(list(prev_closes.keys()))
    gap_events = gap_detect.detect_gaps(prev_closes, live_quotes)

    if not gap_events:
        print("No qualifying gaps detected today.")
        return

    print(f"{'Symbol':<12} {'Direction':<10} {'Gap%':>6}  {'Cont%':>6}  {'Rev%':>6}  {'Score':>6}  {'Action'}")
    print("-" * 70)

    for event in gap_events:
        bars = data_svc.get_historical_ohlc(event.symbol, days=settings.gap.lookback_days + 5)
        if len(bars) < 2:
            continue
        trend = gap_trend.analyse(event.symbol, bars, event.gap_direction)
        if not gap_trend.has_sufficient_data(trend):
            action = "SKIP (insufficient data)"
        elif gap_trend.is_continuation_signal(trend):
            action = "TRADE → " + ("BUY" if event.gap_direction.value == "GAP_UP" else "SELL")
        elif gap_trend.is_reversal_signal(trend):
            action = "TRADE → " + ("SELL" if event.gap_direction.value == "GAP_UP" else "BUY")
        else:
            action = "SKIP (no clear edge)"

        print(
            f"{event.symbol:<12} {event.gap_direction.value:<10} "
            f"{event.gap_pct:>6.2f}%  "
            f"{trend.continuation_rate * 100:>5.1f}%  "
            f"{trend.reversal_rate * 100:>5.1f}%  "
            f"{trend.trend_score:>6.1f}  {action}"
        )

    print()


def cmd_status() -> None:
    """Print today's log tail for a quick status check."""
    import os
    log_file = settings.ops.log_file
    if not os.path.exists(log_file):
        print("No log file found. Has the strategy run today?")
        return
    with open(log_file) as f:
        lines = f.readlines()
    today = __import__("datetime").date.today().strftime("%Y-%m-%d")
    today_lines = [l for l in lines if today in l]
    print("".join(today_lines[-100:]))  # last 100 lines from today


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

_COMMANDS = {
    "auth": cmd_auth,
    "run": cmd_run,
    "scan": cmd_scan,
    "status": cmd_status,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(__doc__)
        print("Commands:", ", ".join(_COMMANDS))
        sys.exit(1)
    _COMMANDS[sys.argv[1]]()
