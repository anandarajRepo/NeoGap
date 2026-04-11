"""
NeoGap — Kotak Neo data service.

Wraps the neo-api-client to provide live market quotes (LTP polling).
Historical OHLC is not available via the Kotak Neo API; all price data
is sourced from live market quotes only.

All methods return plain Python objects / dataclasses — no raw API dicts
leak into the rest of the system.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from config.settings import settings
from config.symbols import to_neo_format
from models.trading_models import LiveQuote
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
    Thin wrapper around neo_api_client for live quote data.

    Parameters
    ----------
    client : authenticated neo_api_client.NeoAPI instance
    """

    def __init__(self, client) -> None:
        self._client = client

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
            prev_close = float(
                data.get("prev_close", 0)
                or data.get("previous_close", 0)
                or data.get("close", 0)
                or 0
            )
            return LiveQuote(
                symbol=symbol,
                ltp=ltp,
                bid=float(data.get("bid_price", 0) or 0),
                ask=float(data.get("ask_price", 0) or 0),
                volume=int(data.get("volume", 0) or 0),
                prev_close=prev_close,
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
                        prev_close = float(
                            item.get("prev_close", 0)
                            or item.get("previous_close", 0)
                            or item.get("close", 0)
                            or 0
                        )
                        result[sym] = LiveQuote(
                            symbol=sym,
                            ltp=float(item.get("ltp", 0) or 0),
                            volume=int(item.get("volume", 0) or 0),
                            prev_close=prev_close,
                            timestamp=datetime.now(),
                        )
            except Exception as exc:
                logger.error("Batch quote failed for %s: %s", batch, exc)
        return result
