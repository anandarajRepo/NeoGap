"""
NeoGap — Trading universe management.

Symbols are stored as plain NSE ticker strings.
Use `get_all_symbols()` to retrieve the full watchlist and
`to_neo_format(symbol)` to convert to the Kotak Neo exchange token format.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Watchlist — ~150 liquid NSE stocks grouped by sector
# ---------------------------------------------------------------------------

_WATCHLIST: dict[str, list[str]] = {
    "Energy": [
        "RELIANCE", "ONGC", "IOC", "BPCL", "HPCL",
        "GAIL", "PETRONET", "IGL", "MGL", "GSPL",
    ],
    "Renewables": [
        "ADANIGREEN", "TATAPOWER", "CESC", "TORNTPOWER", "NTPC",
        "POWERGRID", "SJVN", "NHPC", "INOXGREEN",
    ],
    "Defense": [
        "HAL", "BEL", "BEML", "BHEL", "MIDHANI",
        "PARAS", "MTAR", "COCHINSHIP", "GARDENREACH",
    ],
    "Pharma": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA",
        "LUPIN", "BIOCON", "IPCALAB", "ALKEM", "GLENMARK",
    ],
    "IT": [
        "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM",
        "LTIM", "MPHASIS", "PERSISTENT", "COFORGE", "KPIT",
    ],
    "Auto": [
        "MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT",
        "HEROMOTOCO", "TVSMOTORS", "ASHOKLEY", "TVSMOTOR", "MOTHERSON",
    ],
    "Banking": [
        "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
        "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IDFCFIRSTB",
    ],
    "NBFC": [
        "BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN",
        "MANAPPURAM", "M&MFIN", "LICHOUSFIN", "HDFCAMC",
    ],
    "FMCG": [
        "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR",
        "MARICO", "GODREJCP", "EMAMILTD", "COLPAL",
    ],
    "Metals": [
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "COALINDIA",
        "NMDC", "MOIL", "NATIONALUM", "SAIL",
    ],
    "Cement": [
        "ULTRACEMCO", "AMBUJACEM", "ACC", "SHREECEM", "RAMCOCEM",
        "JKCEMENT", "HEIDELBERG",
    ],
    "Paints": [
        "ASIANPAINT", "BERGEPAINT", "KANSAINER", "INDIGO",
    ],
    "Chemicals": [
        "PIDILITIND", "AARTIIND", "DEEPAKNTR", "GNFC", "TATACHEMICALS",
        "ALKYLAMINE", "NAVINFLUOR",
    ],
    "Infrastructure": [
        "LT", "ADANIPORTS", "ADANIENTER", "IRB", "KNR",
        "NBCC", "RVNL", "IRFC",
    ],
    "Telecom": [
        "BHARTIARTL", "IDEA",
    ],
    "Consumer_Durables": [
        "HAVELLS", "VOLTAS", "BLUESTAR", "POLYCAB", "KEI",
        "VGUARD", "CROMPTON",
    ],
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_all_symbols() -> list[str]:
    """Return flat list of all symbols in the watchlist."""
    symbols = []
    for sector_symbols in _WATCHLIST.values():
        symbols.extend(sector_symbols)
    return list(dict.fromkeys(symbols))  # deduplicate, preserve order


def get_symbols_by_sector(sector: str) -> list[str]:
    return _WATCHLIST.get(sector, [])


def get_sectors() -> list[str]:
    return list(_WATCHLIST.keys())


def to_neo_format(symbol: str) -> dict:
    """
    Convert a plain NSE symbol to the Kotak Neo scrip lookup format.
    Returns a dict with exchange_segment and trading_symbol.
    """
    return {
        "exchange_segment": "nse_cm",
        "trading_symbol": symbol.upper(),
    }


def normalize(symbol: str) -> str:
    """Uppercase and strip whitespace."""
    return symbol.strip().upper()
