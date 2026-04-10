"""
NeoGap — Kotak Neo data service.

Wraps the neo-api-client to provide live market quotes (LTP polling).
Historical OHLC is not available via the Kotak Neo API; all price data
is sourced from live market quotes only.

All methods return plain Python objects / dataclasses — no raw API dicts
leak into the rest of the system.
"""

from __future__ import annotations

import inspect
import textwrap
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
    """Synchronous retry with exponential backoff.

    TypeError is not retried — it signals a programming-level bug (e.g. the
    neo-api-client scrip=None issue) that will never resolve on its own.
    """
    backoff = _BASE_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except TypeError:
            raise  # deterministic — retrying wastes precious market-open seconds
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("Attempt %d failed (%s). Retrying in %ds…", attempt, exc, backoff)
            time.sleep(backoff)
            backoff *= 2


def _apply_neo_quotes_patch(client) -> None:
    """Fix a known neo-api-client bug at runtime.

    The library's ``NeoAPI.quotes()`` method initialises ``scrip = None`` and
    then immediately does ``scrip += exchange + "|" + token + "#"`` inside a
    loop.  The very first iteration raises::

        TypeError: unsupported operand type(s) for +=: 'NoneType' and 'str'

    This function detects whether the installed version still carries the bug
    by inspecting the method source and, if so, re-compiles a corrected copy
    and swaps it onto the class.  The patch is applied once per process; all
    subsequent ``NeoAPI`` instances inherit the fixed method automatically.

    If the source is unavailable (compiled .pyc only) the function returns
    silently — the ``TypeError`` will surface immediately (no retry delay)
    thanks to the guard in ``_retry``.
    """
    cls = type(client)
    original = getattr(cls, "quotes", None)
    if original is None:
        return

    try:
        src = inspect.getsource(original)
    except (OSError, TypeError):
        logger.warning(
            "neo-api-client source unavailable — quotes() scrip=None bug "
            "may still be present; upgrade the package to resolve it"
        )
        return

    if "scrip = None" not in src:
        return  # Already fixed in this installed version

    fixed_src = textwrap.dedent(src).replace("scrip = None", 'scrip = ""', 1)
    module = inspect.getmodule(original)
    mod_globals: dict = vars(module) if module is not None else {}
    ns: dict = {}
    try:
        exec(compile(fixed_src, "<neo_quotes_patch>", "exec"), mod_globals, ns)
    except Exception as exc:
        logger.warning("neo-api-client patch compilation failed: %s", exc)
        return

    patched_fn = ns.get("quotes")
    if callable(patched_fn):
        setattr(cls, "quotes", patched_fn)
        logger.info("Applied neo-api-client quotes() scrip-init fix (scrip=None → scrip='')")
    else:
        logger.warning("neo-api-client patch produced no callable — quotes() unchanged")


class NeoDataService:
    """
    Thin wrapper around neo_api_client for live quote data.

    Parameters
    ----------
    client : authenticated neo_api_client.NeoAPI instance
    """

    def __init__(self, client) -> None:
        self._client = client
        _apply_neo_quotes_patch(client)

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
