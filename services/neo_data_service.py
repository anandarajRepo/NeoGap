"""
NeoGap — Kotak Neo data service.

Wraps the neo-api-client to provide:
  - Historical OHLC data (via NSE/BSE REST endpoints)
  - Live quotes (LTP polling)
  - Previous day close price lookup

All methods return plain Python objects / dataclasses — no raw API dicts
leak into the rest of the system.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from config.settings import settings
from config.symbols import to_neo_format
from models.trading_models import DayOHLC, LiveQuote
from utils.logger import get_logger

logger = get_logger("neo_data_service", settings.ops.log_level, settings.ops.log_file)

# Retry parameters
_MAX_RETRIES = 4
_BASE_BACKOFF = 2  # seconds


def _retry(func, *args, **kwargs):
    """Synchronous retry with exponential backoff."""
    backoff = _BASE_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("Attempt %d failed (%s). Retrying in %ds…", attempt, exc, backoff)
            time.sleep(backoff)
            backoff *= 2


class NeoDataService:
    """
    Thin wrapper around neo_api_client for price/quote data.

    Parameters
    ----------
    client : authenticated neo_api_client.NeoAPI instance
    """

    def __init__(self, client) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Historical OHLC
    # ------------------------------------------------------------------

    def get_historical_ohlc(
        self,
        symbol: str,
        days: int = 30,
    ) -> list[DayOHLC]:
        """
        Fetch `days` trading days of daily OHLC for *symbol*.

        Uses the Kotak Neo chart history REST endpoint directly because
        neo_api_client.NeoAPI does not expose a historical_candles method.

        Returns a list sorted oldest → newest.
        """
        to_date = datetime.now()
        # Add buffer for weekends/holidays
        from_date = to_date - timedelta(days=days * 2)

        scrip = to_neo_format(symbol)
        logger.debug("Fetching %d-day OHLC for %s", days, symbol)

        base_url = getattr(self._client, "base_url", None)
        if not base_url:
            logger.error(
                "historical_ohlc skipped for %s: client.base_url is not set "
                "(re-authenticate with `python main.py auth`)",
                symbol,
            )
            return []

        access_token = getattr(self._client, "access_token", None)
        sid = getattr(self._client, "sid", None)

        url = f"{base_url.rstrip('/')}/charts/1.0/chart/history"
        params = {
            "exchange": scrip["exchange_segment"],
            "tradingSymbol": scrip["trading_symbol"],
            "from": int(from_date.timestamp()),
            "to": int(to_date.timestamp()),
            "resolution": "1D",
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "sid": sid or "",
            "Content-Type": "application/json",
        }

        try:
            def _fetch():
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.json()

            raw = _retry(_fetch)
        except Exception as exc:
            logger.error("historical_candles failed for %s: %s", symbol, exc)
            return []

        # The chart/history endpoint returns parallel arrays:
        # { "t": [timestamps], "o": [opens], "h": [highs], "l": [lows],
        #   "c": [closes], "v": [volumes], "s": "ok" }
        bars = []
        data = raw if isinstance(raw, dict) else {}
        if data.get("s") != "ok" and "data" not in data:
            # Fallback: try a list-of-dicts response shape
            raw_list = raw if isinstance(raw, list) else []
            for bar in raw_list:
                try:
                    bars.append(DayOHLC(
                        symbol=symbol,
                        date=datetime.strptime(bar.get("datetime") or bar.get("date", ""), "%d-%m-%Y"),
                        open=float(bar.get("open", 0)),
                        high=float(bar.get("high", 0)),
                        low=float(bar.get("low", 0)),
                        close=float(bar.get("close", 0)),
                        volume=int(bar.get("volume", 0)),
                    ))
                except Exception:
                    continue
        elif "data" in data:
            # Some Neo API versions wrap in a "data" key
            for bar in (data["data"] or []):
                try:
                    bars.append(DayOHLC(
                        symbol=symbol,
                        date=datetime.strptime(bar.get("datetime") or bar.get("date", ""), "%d-%m-%Y"),
                        open=float(bar.get("open", 0)),
                        high=float(bar.get("high", 0)),
                        low=float(bar.get("low", 0)),
                        close=float(bar.get("close", 0)),
                        volume=int(bar.get("volume", 0)),
                    ))
                except Exception:
                    continue
        else:
            # Parallel-arrays format
            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])
            for i, ts in enumerate(timestamps):
                try:
                    bars.append(DayOHLC(
                        symbol=symbol,
                        date=datetime.fromtimestamp(int(ts)),
                        open=float(opens[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        close=float(closes[i]),
                        volume=int(volumes[i]) if i < len(volumes) else 0,
                    ))
                except Exception:
                    continue

        bars.sort(key=lambda b: b.date)
        return bars[-days:]  # return at most `days` bars

    # ------------------------------------------------------------------
    # Live quote
    # ------------------------------------------------------------------

    def get_live_quote(self, symbol: str) -> Optional[LiveQuote]:
        scrip = to_neo_format(symbol)
        try:
            resp = _retry(
                self._client.quotes,
                instrument_tokens=[{
                    "instrument_token": scrip["trading_symbol"],
                    "exchange_segment": scrip["exchange_segment"],
                }],
                quote_type="ltp",
            )
            if not resp:
                return None
            data = resp if isinstance(resp, dict) else (resp[0] if resp else {})
            ltp = float(data.get("ltp", 0) or data.get("last_price", 0))
            return LiveQuote(
                symbol=symbol,
                ltp=ltp,
                bid=float(data.get("bid_price", 0) or 0),
                ask=float(data.get("ask_price", 0) or 0),
                volume=int(data.get("volume", 0) or 0),
                timestamp=datetime.now(),
            )
        except Exception as exc:
            logger.error("get_live_quote failed for %s: %s", symbol, exc)
            return None

    def get_live_quotes(self, symbols: list[str]) -> dict[str, LiveQuote]:
        """Batch fetch LTP for multiple symbols."""
        result: dict[str, LiveQuote] = {}
        # Batch in groups of 20 (Neo API limit)
        batch_size = 20
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i: i + batch_size]
            tokens = [
                {
                    "instrument_token": to_neo_format(s)["trading_symbol"],
                    "exchange_segment": to_neo_format(s)["exchange_segment"],
                }
                for s in batch
            ]
            try:
                resp = _retry(
                    self._client.quotes,
                    instrument_tokens=tokens,
                    quote_type="ltp",
                )
                raw_list = resp if isinstance(resp, list) else [resp] if resp else []
                for item in raw_list:
                    sym = (item.get("trading_symbol") or item.get("symbol", "")).upper()
                    if sym:
                        result[sym] = LiveQuote(
                            symbol=sym,
                            ltp=float(item.get("ltp", 0) or 0),
                            volume=int(item.get("volume", 0) or 0),
                            timestamp=datetime.now(),
                        )
            except Exception as exc:
                logger.error("Batch quote failed for %s: %s", batch, exc)
        return result

    # ------------------------------------------------------------------
    # Convenience: previous day close
    # ------------------------------------------------------------------

    def get_prev_close(self, symbol: str) -> Optional[float]:
        bars = self.get_historical_ohlc(symbol, days=5)
        if len(bars) >= 2:
            return bars[-2].close  # yesterday's close
        elif len(bars) == 1:
            return bars[0].close
        return None

    def get_prev_closes(self, symbols: list[str]) -> dict[str, float]:
        """Fetch previous close for all symbols. Returns {symbol: prev_close}."""
        result = {}
        for sym in symbols:
            prev_close = self.get_prev_close(sym)
            if prev_close:
                result[sym] = prev_close
        return result
