"""
NeoGap — Market timing service.

Centralises all IST-aware time checks so strategy code stays clean.
"""

from __future__ import annotations

from datetime import datetime, time, date
import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE session boundaries (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PRE_OPEN_START = time(8, 55)   # when the scanner should start
GAP_SCAN_WINDOW_END = time(9, 20)  # latest time to detect gaps after open


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def is_trading_day() -> bool:
    """True on Monday–Friday. Does not account for NSE holidays."""
    return now_ist().weekday() < 5


def is_market_open() -> bool:
    t = now_ist().time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_pre_open() -> bool:
    t = now_ist().time()
    return PRE_OPEN_START <= t < MARKET_OPEN


def is_gap_scan_window() -> bool:
    """True during the first few minutes after market opens — gap detection window."""
    t = now_ist().time()
    return MARKET_OPEN <= t <= GAP_SCAN_WINDOW_END


def is_end_of_day() -> bool:
    t = now_ist().time()
    return t >= time(15, 15)


def seconds_until_market_open() -> float:
    now = now_ist()
    open_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now >= open_dt:
        return 0.0
    return (open_dt - now).total_seconds()


def seconds_until_close() -> float:
    now = now_ist()
    close_dt = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= close_dt:
        return 0.0
    return (close_dt - now).total_seconds()
