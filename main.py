"""
NeoGap — Entry point.

Usage
-----
  python main.py auth          Authenticate with Kotak Neo (interactive OTP)
  python main.py run           Start the gap trading strategy
  python main.py scan          One-shot gap scan (no orders placed)
  python main.py status        Show today's positions and metrics
  python main.py stop          Gracefully stop a running strategy (closes open positions)
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
    One-shot gap scan: detect today's gaps using live market quotes.
    No orders are placed regardless of DRY_RUN setting.
    """
    import os
    os.environ["DRY_RUN"] = "true"  # force dry-run for scan command

    from utils.auth_helper import get_neo_client
    from services.neo_data_service import NeoDataService
    from services.gap_detection_service import GapDetectionService
    from config.symbols import get_all_symbols

    client = get_neo_client()
    data_svc = NeoDataService(client)
    gap_detect = GapDetectionService()
    symbols = get_all_symbols()

    print("\n=== NeoGap One-Shot Scanner ===\n")

    # Fetch live quotes (prev_close is included in the quote response)
    live_quotes = data_svc.get_live_quotes(symbols)
    prev_closes = {
        sym: quote.prev_close
        for sym, quote in live_quotes.items()
        if quote.prev_close > 0
    }
    gap_events = gap_detect.detect_gaps(prev_closes, live_quotes)

    if not gap_events:
        print("No qualifying gaps detected today.")
        return

    print(f"{'Symbol':<12} {'Direction':<10} {'Gap%':>6}  {'LTP':>8}  {'PrevClose':>10}  {'Action'}")
    print("-" * 65)

    for event in gap_events:
        if event.gap_direction.value == "GAP_UP":
            action = "TRADE → BUY"
        else:
            action = "TRADE → SELL"

        quote = live_quotes.get(event.symbol)
        ltp = quote.ltp if quote else event.open_price

        print(
            f"{event.symbol:<12} {event.gap_direction.value:<10} "
            f"{event.gap_pct:>6.2f}%  "
            f"{ltp:>8.2f}  "
            f"{event.prev_close:>10.2f}  {action}"
        )

    print()


def cmd_stop() -> None:
    """Signal a running strategy to shut down gracefully after closing open positions."""
    from pathlib import Path
    from strategy.gap_strategy import STOP_FLAG_FILE
    STOP_FLAG_FILE.touch()
    print(f"Stop flag written to {STOP_FLAG_FILE}. The strategy will exit after the next poll cycle.")


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
    "stop": cmd_stop,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(__doc__)
        print("Commands:", ", ".join(_COMMANDS))
        sys.exit(1)
    _COMMANDS[sys.argv[1]]()
