"""
NeoGap — Configuration dataclasses loaded from environment variables.
All tuneable parameters live here; strategy code never reads os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes")


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, default))


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, default))


# ---------------------------------------------------------------------------
# Broker credentials
# ---------------------------------------------------------------------------

@dataclass
class BrokerConfig:
    consumer_key: str = field(default_factory=lambda: os.getenv("NEO_CONSUMER_KEY", ""))
    consumer_secret: str = field(default_factory=lambda: os.getenv("NEO_CONSUMER_SECRET", ""))
    # Developer API access token — used as Authorization header in auth requests
    access_token: str = field(default_factory=lambda: os.getenv("NEO_ACCESS_TOKEN", ""))
    # 5-character client code (UCC) from the Kotak Neo developer portal
    ucc: str = field(default_factory=lambda: os.getenv("NEO_UCC", ""))
    environment: str = field(default_factory=lambda: os.getenv("NEO_ENVIRONMENT", "prod"))


# ---------------------------------------------------------------------------
# Gap detection parameters
# ---------------------------------------------------------------------------

@dataclass
class GapConfig:
    # Minimum gap % to qualify as a gap-up or gap-down
    min_gap_pct: float = field(default_factory=lambda: _float("MIN_GAP_PCT", 0.5))
    # Maximum gap % to avoid runaway movers with poor fill
    max_gap_pct: float = field(default_factory=lambda: _float("MAX_GAP_PCT", 8.0))
    # Number of historical trading days used for gap trend analysis
    lookback_days: int = field(default_factory=lambda: _int("GAP_LOOKBACK_DAYS", 20))
    # Minimum historical gap occurrences required to trust the trend
    min_gap_occurrences: int = field(default_factory=lambda: _int("MIN_GAP_OCCURRENCES", 5))
    # Continuation rate threshold: trade gap direction only if rate >= this
    continuation_threshold: float = field(default_factory=lambda: _float("GAP_CONTINUATION_THRESHOLD", 0.60))
    # Reversal rate threshold: trade against gap direction if reversal rate >= this
    reversal_threshold: float = field(default_factory=lambda: _float("GAP_REVERSAL_THRESHOLD", 0.60))
    # Minutes after open to wait before confirming gap direction (mini-ORB)
    confirmation_minutes: int = field(default_factory=lambda: _int("GAP_CONFIRMATION_MINUTES", 5))


# ---------------------------------------------------------------------------
# Risk management parameters
# ---------------------------------------------------------------------------

@dataclass
class RiskConfig:
    portfolio_value: float = field(default_factory=lambda: _float("PORTFOLIO_VALUE", 50000))
    risk_per_trade: float = field(default_factory=lambda: _float("RISK_PER_TRADE", 0.30))
    max_positions: int = field(default_factory=lambda: _int("MAX_POSITIONS", 3))
    max_daily_loss_pct: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_PCT", 0.02))
    stop_loss_pct: float = field(default_factory=lambda: _float("STOP_LOSS_PCT", 0.005))
    target_multiplier: float = field(default_factory=lambda: _float("TARGET_MULTIPLIER", 2.0))
    trailing_stop_pct: float = field(default_factory=lambda: _float("TRAILING_STOP_PCT", 0.003))
    partial_exit_pct: float = field(default_factory=lambda: _float("PARTIAL_EXIT_PCT", 0.50))


# ---------------------------------------------------------------------------
# Filter toggles
# ---------------------------------------------------------------------------

@dataclass
class FilterConfig:
    enable_trend_filter: bool = field(default_factory=lambda: _bool("ENABLE_TREND_FILTER", True))
    enable_volume_filter: bool = field(default_factory=lambda: _bool("ENABLE_VOLUME_FILTER", True))
    # Minimum average daily volume (shares) for a stock to be tradable
    min_avg_volume: int = field(default_factory=lambda: _int("MIN_AVG_VOLUME", 100_000))


# ---------------------------------------------------------------------------
# Previous day closing condition parameters
# ---------------------------------------------------------------------------

@dataclass
class PrevDayConditionConfig:
    # Master toggle — set ENABLE_PREV_DAY_CONDITION=false to bypass this filter
    enable: bool = field(default_factory=lambda: _bool("ENABLE_PREV_DAY_CONDITION", True))

    # Volume must be at least this multiple of the 20-day avg to count as a "surge"
    # e.g. 1.5 → prev day volume ≥ 1.5× average
    volume_surge_ratio: float = field(
        default_factory=lambda: _float("PREV_DAY_VOLUME_SURGE_RATIO", 1.5)
    )

    # Bullish (LONG) condition: close must be in the TOP fraction of the day's range.
    # e.g. 0.70 → close ≥ low + 70% of (high − low)
    close_near_high_threshold: float = field(
        default_factory=lambda: _float("PREV_DAY_CLOSE_NEAR_HIGH_THRESHOLD", 0.70)
    )

    # Bearish (SHORT) / distribution condition: close must be in the BOTTOM fraction.
    # e.g. 0.30 → close ≤ low + 30% of (high − low)
    close_near_low_threshold: float = field(
        default_factory=lambda: _float("PREV_DAY_CLOSE_NEAR_LOW_THRESHOLD", 0.30)
    )


# ---------------------------------------------------------------------------
# Operational settings
# ---------------------------------------------------------------------------

@dataclass
class OperationalConfig:
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "logs/gap_strategy.log"))
    # Seconds between polling loops for live quote updates
    poll_interval: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 5))


# ---------------------------------------------------------------------------
# Composite settings object
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    gap: GapConfig = field(default_factory=GapConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    ops: OperationalConfig = field(default_factory=OperationalConfig)
    prev_day: PrevDayConditionConfig = field(default_factory=PrevDayConditionConfig)


# Singleton — import this everywhere
settings = Settings()
