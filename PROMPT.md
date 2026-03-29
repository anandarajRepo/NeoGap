# NeoGap — Project Prompt

## Project Overview

NeoGap is a fully automated intraday algorithmic trading system implementing a **Gap Trading** strategy for NSE stocks. It integrates with the **Kotak Neo** broker via their Python API (`neo-api-client`) to execute trades automatically based on gap direction and historical gap trend analysis.

---

## Core Strategy

### Gap Trading with Trend Analysis

**Step 1 — Gap Detection** (9:15 AM IST)
- At market open, compare each stock's opening price to the previous day's close.
- **Gap-Up**: `open > prev_close` by ≥ `MIN_GAP_PCT` (default 0.5%)
- **Gap-Down**: `open < prev_close` by ≥ `MIN_GAP_PCT`
- Discard extreme gaps above `MAX_GAP_PCT` (default 8%) to avoid illiquid movers.

**Step 2 — Historical Gap Trend Analysis**
- For each gapping stock, fetch `GAP_LOOKBACK_DAYS` (default 20) of daily OHLC.
- Identify all prior gaps in the **same direction** as today's gap.
- For each historical gap day, classify the outcome:
  - **Continuation**: Price closed further in the gap direction (gap-up → bullish close).
  - **Reversal**: Price reversed and closed against the gap direction (gap filled).
- Compute:
  - `continuation_rate` = continuation_count / total_same_direction_gaps
  - `reversal_rate`     = reversal_count / total_same_direction_gaps

**Step 3 — Signal Decision**
| Condition | Signal |
|---|---|
| `continuation_rate >= 60%` | Trade **WITH** gap direction (BUY on gap-up, SELL on gap-down) |
| `reversal_rate >= 60%` | Trade **AGAINST** gap direction (SELL on gap-up, BUY on gap-down) |
| Neither threshold met | No trade — insufficient directional edge |
| `total_gaps < MIN_GAP_OCCURRENCES` | No trade — insufficient historical data |

**Step 4 — Confirmation (mini-ORB, 5 minutes)**
- Wait 5 minutes after open.
- Verify the gap is still holding (price has not crossed back through `prev_close`).
- Only proceed with confirmed gaps.

**Step 5 — Entry & Exit**
- Enter at market price with `MIS` (intraday margin) product.
- `stop_loss = entry × (1 ± STOP_LOSS_PCT)` (default 0.5%)
- `target_1  = entry ± risk × TARGET_MULTIPLIER` (default 2× risk)
- `target_2  = entry ± risk × TARGET_MULTIPLIER × 2`
- **Partial exit**: Sell 50% at Target 1; hold remainder for Target 2.
- **Trailing stop**: Tighten stop to 0.3% behind peak price after Target 1.
- **EOD square-off**: All positions closed at 3:15 PM IST regardless.

---

## Architecture

```
NeoGap/
├── main.py                          # Entry point (auth / run / scan / status)
├── requirements.txt
├── .env.example                     # Environment variable template
├── run_gap.sh / stop_gap.sh         # Process management scripts
├── cron                             # Cron schedule
├── config/
│   ├── settings.py                  # All config via dataclasses + dotenv
│   └── symbols.py                   # ~150 NSE stocks watchlist
├── models/
│   └── trading_models.py            # Domain objects (GapEvent, GapSignal, Position…)
├── services/
│   ├── neo_data_service.py          # Kotak Neo REST data (OHLC, live quotes)
│   ├── gap_detection_service.py     # Gap-up / gap-down detection
│   ├── gap_trend_service.py         # Historical gap trend analysis (core logic)
│   └── market_timing_service.py     # IST-aware market hours checks
├── strategy/
│   ├── gap_strategy.py              # Main async state machine
│   └── order_manager.py             # Kotak Neo order placement / management
└── utils/
    ├── auth_helper.py               # Kotak Neo OAuth + token caching
    └── logger.py                    # colorlog + rotating file handler
```

**Tech Stack**: Python 3.11+, asyncio, neo-api-client, pandas, numpy, pytz, python-dotenv, colorlog

---

## Gap Trend Service (Core Algorithm)

`services/gap_trend_service.py` is the heart of NeoGap. It:

1. Walks N days of OHLC bars to find all gap days matching today's direction.
2. For gap-up: continuation = bullish close (close > open); reversal = bearish close.
3. For gap-down: continuation = bearish close; reversal = bullish close.
4. Computes a `trend_score` (0–100) using:
   - **Consistency** (60%): how dominant the winning outcome is.
   - **Sample size** (20%): more historical gaps → higher confidence.
   - **Average move** (20%): larger average continuation → stronger edge.

---

## Risk Management

| Parameter | Default | Description |
|---|---|---|
| `PORTFOLIO_VALUE` | RS.50,000 | Total allocated capital |
| `RISK_PER_TRADE` | 30% | Portion allocated per trade |
| `MAX_POSITIONS` | 3 | Maximum simultaneous open positions |
| `MAX_DAILY_LOSS_PCT` | 2% | Stop new entries if daily P&L drops below this |
| `STOP_LOSS_PCT` | 0.5% | Fixed SL from entry |
| `TARGET_MULTIPLIER` | 2× | Risk-reward ratio |
| `TRAILING_STOP_PCT` | 0.3% | Trail behind peak after T1 hit |
| `PARTIAL_EXIT_PCT` | 50% | Exit half at Target 1 |

---

## Configuration

Copy `.env.example` → `.env` and fill in your Kotak Neo credentials:

```env
NEO_CONSUMER_KEY=...
NEO_CONSUMER_SECRET=...
PORTFOLIO_VALUE=50000
DRY_RUN=true    # Start with paper trading!
```

---

## Operations

```bash
# Authenticate (one-time or after token expiry)
python main.py auth

# Paper trading (dry run)
DRY_RUN=true python main.py run

# Live trading
python main.py run

# One-shot gap scan (no orders)
python main.py scan

# Check today's activity
python main.py status

# Process management
./run_gap.sh    # Start via cron / background
./stop_gap.sh   # Graceful stop
```

**Cron Schedule** (IST):
```
55 8 * * 1-5   run_gap.sh    # Start 8:55 AM
30 15 * * 1-5  stop_gap.sh   # Stop  3:30 PM
```

---

## Development Guidelines

1. **Never modify live order logic without testing `DRY_RUN=true` first** — incorrect stop-loss or exit logic leads to real financial loss.
2. All configuration flows through `.env` → `config/settings.py` dataclasses. Strategy code never reads `os.environ` directly.
3. Services are stateless; all mutable state lives in `strategy/gap_strategy.py`.
4. Use `asyncio` throughout; use `run_in_executor` for synchronous Neo API calls.
5. All network calls use retry logic with exponential backoff (2s, 4s, 8s, 16s).
6. Use the project's `colorlog` logger; never use `print()` in strategy/service code.
7. Gap trend analysis requires `MIN_GAP_OCCURRENCES` (default 5) historical gaps before trusting the signal — this prevents overfitting to sparse data.
8. The confirmation step (mini-ORB) is mandatory — it filters out gap stocks that reverse immediately at open.
