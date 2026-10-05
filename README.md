# HK Hackathon — Roostoo EMA Crossover Bot

## What I built

I built this bot as the final live implementation of the EMA strategy I selected during my research phase.

I deliberately kept the trading logic fixed after the research stage. I am **not** adding another indicator, a different entry rule, machine learning, reinforcement learning, or a second strategy.

The final configuration is:

| Parameter | Value |
|---|---:|
| Assets | TRX/USD, AVAX/USD, DOT/USD |
| Timeframe | 1 hour |
| Fast EMA | 35 |
| Slow EMA | 60 |
| Take Profit | +8% |
| Stop Loss | -2% |
| BUY allocation | 10% of free USD |
| Position type | Long-only |
| Execution | Market orders |

The three assets are the portfolio selected from my earlier train/validation research. The test period was deliberately kept out of the selection rule.

---

## My strategy

The strategy is a simple EMA crossover strategy.

For every completed 1-hour candle:

- **BUY** when the fast EMA crosses from at/below the slow EMA to above it.
- **SELL** when the fast EMA crosses from at/above the slow EMA to below it, while I am already long.
- Otherwise **HOLD**.

Formally:

```text
BUY:
previous_fast <= previous_slow
AND
current_fast > current_slow

SELL:
previous_fast >= previous_slow
AND
current_fast < current_slow
```

The strategy is long-only. A SELL never opens a short position.

### Execution timing

I do not trade on an unfinished candle.

A completed candle produces the signal. The bot then waits for the next hourly execution window and uses the first accepted Roostoo price observation in that new hour as the execution reference.

If the bot misses an hourly execution window, it does **not** fabricate a historical trade. It catches its EMA state up to the newest continuous Roostoo candle and waits for the next valid signal window.

---

## TP and SL

TP/SL is separate from the hourly crossover decision.

I check the current Roostoo ticker on every accepted polling cycle:

- +8% from the recorded entry price → SELL for `TAKE_PROFIT`
- -2% from the recorded entry price → SELL for `STOP_LOSS`

The risk exit is checked before a new crossover action.

This means a position does not have to wait for the next hourly candle for its TP/SL check.

---

## Why Binance is used

Roostoo's live ticker gives me current prices, but I need enough historical closes to initialize a 60-period EMA when the bot starts.

For that initial warm-up only, I use `python-binance` to retrieve completed **1-hour Binance candles** for the explicitly mapped symbols:

```text
TRX/USD  -> TRXUSDT
AVAX/USD -> AVAXUSDT
DOT/USD  -> DOTUSDT
```

I use those historical closes to initialize the EMA tracker.

After live Roostoo data begins, new EMA observations come from **Roostoo only**.

I do not use Binance prices to execute orders, and I do not use Binance to generate a separate trading signal.

This separation is intentional: Binance solves the historical warm-up problem; Roostoo remains the live market/execution source.

---

## Multi-asset portfolio

All three assets share the same USD balance.

Each BUY attempts to allocate 10% of the currently available USD balance, subject to Roostoo's pair-specific `MiniOrder` and `AmountPrecision`.

The bot never assumes a universal minimum order size.

Before an order is sent, it:

1. Reads the current USD/base-asset balance.
2. Reads the pair's current exchange rules.
3. Floors quantity to the allowed amount precision.
4. Checks the resulting quantity against `MiniOrder`.
5. Refuses the order if the resulting quantity is invalid.

---

## Live architecture

```text
                    ┌──────────────────────┐
                    │ Binance 1h history   │
                    │ warm-up only         │
                    └──────────┬───────────┘
                               │
                               ▼
                         EMA 35 / EMA 60
                               ▲
                               │
┌─────────────────┐     ┌─────┴─────────┐
│ Roostoo ticker  │────►│ 1h aggregation │
│ TRX/USD         │     │ completed only │
│ AVAX/USD        │     └─────┬─────────┘
│ DOT/USD         │           │
└─────────────────┘           ▼
                         crossover signal
                               │
                     ┌─────────┴─────────┐
                     │                   │
                   TP/SL             EMA action
                     │                   │
                     └─────────┬─────────┘
                               ▼
                         risk validation
                               │
                               ▼
                        Roostoo market order
                               │
                               ▼
                     persistent state + logs
```

---

## Robustness is the main design goal

I designed the bot around the principle:

> **When the bot is uncertain, it should stop rather than guess.**

### No blind order retries

This is one of the most important protections.

If a market-order request times out, I cannot safely assume that the order failed. It may have reached Roostoo and filled successfully.

Therefore:

```text
place order
    │
    ├── clear response → process it
    │
    └── timeout/transport failure
             │
             ▼
       query order history
             │
       ┌─────┴─────┐
       │           │
    exactly 1    anything else
       │           │
    reconcile     HALT
```

The bot never blindly resubmits an order after an ambiguous response.

---

## Startup reconciliation

On startup, the bot:

1. Acquires a single-process lock.
2. Loads persistent state.
3. Synchronizes Roostoo server time.
4. Validates that every required pair exists and is tradable.
5. Checks pending orders.
6. Reconciles recorded positions with wallet balances.
7. Warm-starts the EMA trackers.
8. Only then enters the live loop.

If the account state disagrees with the local state, the bot halts instead of guessing.

---

## Persistent state

The bot keeps its important state in:

```text
data/bot_state.json
```

The state includes, per pair:

- current position quantity
- entry price
- pending signal
- signal candle
- last completed candle
- last processed execution hour
- last ticker timestamp
- last action key
- last order metadata

The state file is written atomically using a temporary file followed by `os.replace()`.

A partially written state file should therefore not replace a previously valid state file.

---

## Market-data journal

Every accepted Roostoo ticker is appended to a pair-specific JSONL file:

```text
data/TRX_USD_ticker.jsonl
data/AVAX_USD_ticker.jsonl
data/DOT_USD_ticker.jsonl
```

Derived hourly candles are also persisted.

This gives me a local audit trail of the observations that actually reached the bot.

The raw ticker records are deliberately kept simple:

- Roostoo server timestamp
- local receive timestamp
- last price
- bid/ask fields when available

I do not fabricate volume because the ticker stream is not a proper historical OHLCV feed.

---

## Logging

Logging is intentionally extensive because live debugging without an audit trail is painful.

Logs are written to:

```text
logs/bot.log
```

with rotating backups.

Important events are logged for:

- startup and shutdown
- server-time synchronization
- exchange-rule validation
- Binance warm-up
- every API request/result
- read retries
- stale ticker rejection
- candle-gap recovery
- EMA warm-up
- every completed-candle signal
- TP/SL decisions
- risk rejection
- order submission
- pending orders
- fills
- order reconciliation
- state halts
- unexpected exceptions

I want the logs to answer:

> **What did the bot know, what did it decide, and why did it act?**

without needing to guess from the final account state.

---

## API discipline

I deliberately avoid unnecessary polling.

Under normal operation the bot requests one Roostoo ticker observation per configured pair per loop. It does not continuously poll balance, exchange information, or order history.

Those endpoints are used only when needed:

- `exchangeInfo` → startup and immediately before an order
- `balance` → startup reconciliation and immediately before an order
- `pending_count` → startup
- `query_order` → pending-order reconciliation or ambiguous-order reconciliation
- `place_order` → only when the strategy produces an action

Read-only requests have a small bounded retry budget.

Order placement does **not** have an automatic retry.

---

## Precision and minimum-order handling

Roostoo provides pair-specific trading rules.

The bot reads:

- `CanTrade`
- `MiniOrder`
- `AmountPrecision`
- `PricePrecision`

The quantity is rounded down before submission.

For a BUY, the allocation is calculated from the current free USD balance and then converted to quantity.

For a SELL, the bot uses the available base-asset balance and floors it to the pair's allowed amount precision.

If the resulting order would fall below `MiniOrder`, the bot refuses to submit it.

---

## Safety modes

The `.env.example` defaults to:

```text
DRY_RUN=true
ALLOW_LIVE_TRADING=false
```

I keep those defaults intentionally conservative.

Before testing with keys, I first run the local checks.

I do not commit my actual `.env` file or API secrets.

---

## Project structure

```text
roostoo_ema_bot/
├── main.py
├── config.py
├── roostoo_client.py
├── binance_warmup.py
├── market_data.py
├── indicators.py
├── strategy.py
├── risk.py
├── execution.py
├── state.py
├── process_lock.py
├── logging_utils.py
├── smoke_test.py
├── test_robustness.py
├── run_checks.py
├── requirements.txt
├── .env.example
├── .gitignore
├── data/              # runtime, ignored
└── logs/              # runtime, ignored
```

---

## Installation

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create my local configuration:

```text
copy .env.example .env
```

Then put my test credentials into `.env`.

I keep:

```text
DRY_RUN=true
ALLOW_LIVE_TRADING=false
```

while doing the initial local checks.

---

## Verification

I run:

```bash
python run_checks.py
```

The checks are local and mocked. They do not submit an order.

They cover:

- EMA configuration
- crossover rules
- TP/SL priority
- candle validation
- quantity precision
- atomic state persistence
- process locking
- malformed input handling
- non-finite EMA input

---

## Running the bot

```bash
python main.py
```

For the first stage, I use my testing credentials and keep the bot in the safe mode configured for testing.

I watch:

```text
logs/bot.log
```

and:

```text
data/bot_state.json
```

before treating the deployment as ready.

---

## What I deliberately did not add

I did not add:

- AI/ML
- reinforcement learning
- extra indicators
- a second trading strategy
- short selling
- leverage
- martingale logic
- recovery trades
- arbitrary signal generation to force daily orders
- blind order retries
- fabricated historical executions

The purpose of this final bot is not to look complicated.

It is to implement the strategy I researched, exactly, with enough state management, logging, validation, and failure handling that I can understand what happened when something goes wrong.
