import signal
import time
from logging_utils import get_logger
from config import (
    PAIRS, BINANCE_SYMBOLS, EMA_FAST, EMA_SLOW, TAKE_PROFIT_PCT,
    STOP_LOSS_PCT, CAPITAL_ALLOCATION_PCT, POLL_SECONDS, DRY_RUN,
)
from state import load_state, save_state, ensure_pair, halt
from process_lock import ProcessLock, ProcessLockError
from roostoo_client import RoostooClient, APIError, APIUnavailable, OrderOutcomeUnknown
from market_data import refresh_pair, completed_candles, load_tickers, aggregate_hourly
from binance_warmup import load_hourly_closes
from indicators import EMATracker
from strategy import crossover_decision
from execution import execute

log = get_logger("bot")
STOP = False

def stop(*_):
    global STOP
    STOP = True
    log.warning("SHUTDOWN_REQUESTED")

def _position_value(position):
    return float(position.get("qty", 0) or 0)

def _validate_startup_balances(api, state):
    if DRY_RUN:
        log.warning("DRY_RUN | startup balance reconciliation skipped")
        return
    wallet = api.balance().get("Wallet", {})
    expected_assets = {"USD"}
    for pair in PAIRS:
        expected_assets.add(pair.split("/")[0])

    for asset in expected_assets:
        item = wallet.get(asset, {})
        free = float(item.get("Free", 0) or 0)
        locked = float(item.get("Lock", 0) or 0)
        if free < 0 or locked < 0 or free != free or locked != locked:
            raise RuntimeError(f"invalid startup wallet | asset={asset}")

    for pair in PAIRS:
        base = pair.split("/")[0]
        actual = float(wallet.get(base, {}).get("Free", 0) or 0) + float(wallet.get(base, {}).get("Lock", 0) or 0)
        recorded = _position_value(state["pairs"][pair]["position"])
        tolerance = max(1e-8, abs(recorded) * 1e-6)
        if abs(actual - recorded) > tolerance:
            halt(state, f"STARTUP_POSITION_MISMATCH:{pair}")
            save_state(state)
            raise RuntimeError(
                f"startup position mismatch | pair={pair} | state={recorded} | wallet={actual}"
            )
        state["pairs"][pair]["position"]["qty"] = actual
        if actual > 0 and not state["pairs"][pair]["position"].get("entry_price"):
            halt(state, f"STARTUP_ENTRY_PRICE_MISSING:{pair}")
            save_state(state)
            raise RuntimeError(f"position has no entry price | pair={pair}")
    save_state(state)

def _validate_exchange(api):
    info = api.exchange_info()
    trade_pairs = info.get("TradePairs", {})
    for pair in PAIRS:
        rules = trade_pairs.get(pair)
        if not isinstance(rules, dict) or not rules.get("CanTrade"):
            raise RuntimeError(f"required pair unavailable/not tradable | pair={pair}")
        log.info(
            "PAIR_RULES | pair=%s | MiniOrder=%s | AmountPrecision=%s | PricePrecision=%s",
            pair, rules.get("MiniOrder"), rules.get("AmountPrecision"), rules.get("PricePrecision")
        )

def _warmup_trackers(state):
    trackers = {}
    seed_cutoffs = {}
    for pair in PAIRS:
        symbol = BINANCE_SYMBOLS[pair]
        closes, binance_last_open_ms = load_hourly_closes(symbol)
        tracker = EMATracker(EMA_FAST, EMA_SLOW)
        fast, slow = tracker.seed(closes)

        # On restart, replay only the persisted Roostoo candles that occurred
        # after the Binance seed. This prevents double-counting live candles.
        persisted = aggregate_hourly(load_tickers(pair))
        pair_state = state["pairs"][pair]
        last_completed = pair_state.get("last_completed_candle")
        replay = []
        if last_completed is not None:
            replay = [
                c for c in persisted
                if binance_last_open_ms < int(c["timestamp"]) <= int(last_completed)
                and int(c["timestamp"]) + 3_600_000 <= int(time.time() * 1000)
            ]
        for candle in replay:
            tracker.update(float(candle["close"]))

        trackers[pair] = tracker
        seed_cutoffs[pair] = binance_last_open_ms
        log.info(
            "EMA_WARMED | pair=%s | source=Binance-history-plus-Roostoo-replay | "
            "binance_candles=%d | roostoo_replay=%d | fast=%.8f | slow=%.8f",
            pair, len(closes), len(replay), tracker.state.fast, tracker.state.slow
        )
    return trackers, seed_cutoffs

def _reconcile_pending(api, state):
    if DRY_RUN:
        return
    pending = api.pending_count()
    total = int(pending.get("TotalPending", 0) or 0)
    known = {
        str(ps["last_order"].get("order_id"))
        for ps in state["pairs"].values()
        if ps.get("last_order", {}).get("status") == "PENDING"
        and ps.get("last_order", {}).get("order_id")
    }
    if total > 0 and not known:
        halt(state, "UNKNOWN_PENDING_ORDER_ON_STARTUP")
        save_state(state)
        raise RuntimeError("unknown pending order exists at startup")
    if total > 0 and known:
        for pair, ps in state["pairs"].items():
            order = ps.get("last_order", {})
            if order.get("status") != "PENDING":
                continue
            q = api.query_order(order_id=order.get("order_id"))
            matches = q.get("OrderMatched", []) or []
            if len(matches) != 1:
                halt(state, f"PENDING_ORDER_RECONCILIATION_FAILED:{pair}")
                save_state(state)
                raise RuntimeError(f"pending order cannot be reconciled | pair={pair}")
            _apply_order_status(state, pair, matches[0])
        save_state(state)

def _apply_order_status(state, pair, detail):
    ps = state["pairs"][pair]
    status = str(detail.get("Status", "")).upper()
    ps["last_order"].update({"status": status, "order_id": detail.get("OrderID")})
    if status == "FILLED":
        side = str(detail.get("Side", "")).upper()
        qty = float(detail.get("FilledQuantity", 0) or 0)
        avg = float(detail.get("FilledAverPrice", 0) or 0)
        if qty <= 0 or avg <= 0:
            raise RuntimeError(f"filled pending order lacks execution data | pair={pair}")
        if side == "BUY":
            ps["position"] = {"qty": qty, "entry_price": avg}
        elif side == "SELL":
            old_qty = _position_value(ps["position"])
            if qty + max(1e-8, qty * 1e-6) < old_qty:
                raise RuntimeError(f"pending SELL partial fill | pair={pair}")
            ps["position"] = {"qty": 0.0, "entry_price": None}
        else:
            raise RuntimeError(f"invalid pending order side | pair={pair}")
    elif status in {"CANCELED", "REJECTED", "EXPIRED"}:
        log.warning("PENDING_ORDER_TERMINAL | pair=%s | status=%s", pair, status)

def _update_tracker_from_completed_candles(tracker, candles, last_completed):
    done = completed_candles(candles, int(time.time() * 1000))
    if not done:
        return 0, None, None, None

    # The Binance seed is the warm-up only. From the first live completed
    # Roostoo candle onward, all new EMA observations come from Roostoo.
    new = [c for c in done if last_completed is None or int(c["timestamp"]) > int(last_completed)]
    signal = 0
    fast = slow = None
    for candle in new:
        signal, fast, slow = tracker.update(candle["close"])
    latest = done[-1]
    return signal, fast, slow, latest

def run():
    global STOP
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    lock = ProcessLock()
    try:
        lock.acquire()
    except ProcessLockError as exc:
        log.critical("BOT_START_REFUSED | %s", exc)
        return

    try:
        api = RoostooClient()
        state = load_state()
        for pair in PAIRS:
            ensure_pair(state, pair)

        log.info(
            "BOT_START | pairs=%s | timeframe=1h | EMA=%d/%d | TP=%.2f%% | SL=%.2f%% | allocation=%.2f%% | dry_run=%s",
            ",".join(PAIRS), EMA_FAST, EMA_SLOW, TAKE_PROFIT_PCT,
            STOP_LOSS_PCT, CAPITAL_ALLOCATION_PCT, DRY_RUN
        )

        if state.get("halted"):
            log.critical("BOT_HALTED | reason=%s", state.get("halt_reason"))
            return

        api.sync_time()
        _validate_exchange(api)
        _reconcile_pending(api, state)
        _validate_startup_balances(api, state)
        trackers, seed_cutoffs = _warmup_trackers(state)

        log.info("BOT_READY | all startup checks passed")
        next_poll = time.monotonic()

        while not STOP:
            loop_started = time.monotonic()
            try:
                for pair in PAIRS:
                    if STOP:
                        break

                    ps = state["pairs"][pair]

                    # A known pending order is always resolved before another action.
                    if ps.get("last_order", {}).get("status") == "PENDING":
                        q = api.query_order(order_id=ps["last_order"].get("order_id"))
                        matches = q.get("OrderMatched", []) or []
                        if len(matches) != 1:
                            raise RuntimeError(f"pending order disappeared | pair={pair}")
                        _apply_order_status(state, pair, matches[0])
                        save_state(state)
                        if ps["last_order"].get("status") == "PENDING":
                            log.info("PENDING_ORDER_STILL_ACTIVE | pair=%s", pair)
                            continue

                    previous_ticker = ps.get("last_ticker_server_time_ms")
                    row, candles = refresh_pair(api, pair, previous_ticker)
                    ticker_ms = int(row["server_time_ms"])
                    price = float(row["last_price"])
                    ps["last_ticker_server_time_ms"] = ticker_ms

                    position = _position_value(ps["position"])
                    entry = ps["position"].get("entry_price")

                    # TP/SL is checked on every accepted Roostoo ticker.
                    if position > 0 and entry is not None:
                        action, reason = crossover_decision(
                            0, position, entry, price,
                            TAKE_PROFIT_PCT, STOP_LOSS_PCT
                        )
                        if action == "SELL":
                            current_hour = (ticker_ms // 3_600_000) * 3_600_000
                            log.warning(
                                "RISK_EXIT | pair=%s | reason=%s | price=%.12g | entry=%.12g",
                                pair, reason, price, entry
                            )
                            execute(api, state, pair, "SELL", price, current_hour)
                            ps["last_processed_execution_hour"] = current_hour
                            save_state(state)
                            continue

                    done = completed_candles(candles, ticker_ms)
                    if not done:
                        log.info("WARMUP_LIVE_DATA | pair=%s | completed_candles=0", pair)
                        continue

                    latest = done[-1]
                    latest_ts = int(latest["timestamp"])
                    last_completed = ps.get("last_completed_candle")

                    if last_completed is None:
                        # If the latest persisted/live Roostoo candle is the same
                        # candle already represented by the Binance warm-up, do not
                        # feed it into the EMA a second time.
                        if latest_ts > seed_cutoffs[pair]:
                            signal_value, fast, slow = trackers[pair].update(float(latest["close"]))
                        else:
                            signal_value, fast, slow = 0, trackers[pair].state.fast, trackers[pair].state.slow
                        ps["last_completed_candle"] = latest_ts
                        ps["pending_signal"] = signal_value
                        ps["pending_signal_candle"] = latest_ts
                        log.info(
                            "FIRST_ROOSTOO_CANDLE | pair=%s | candle=%s | signal=%s | "
                            "fast=%.8f | slow=%.8f | duplicate_warmup=%s",
                            pair, latest_ts, signal_value, fast, slow,
                            latest_ts <= seed_cutoffs[pair]
                        )
                        save_state(state)
                        continue

                    if latest_ts < int(last_completed):
                        raise RuntimeError(f"candle history moved backwards | pair={pair}")

                    if latest_ts > int(last_completed):
                        delta = latest_ts - int(last_completed)
                        new_candles = [
                            c for c in done if int(c["timestamp"]) > int(last_completed)
                        ]
                        if delta != 3_600_000:
                            log.critical(
                                "TRADING_SKIPPED | pair=%s | missed_candles=%d | last=%s | latest=%s",
                                pair, len(new_candles), last_completed, latest_ts
                            )
                            # Catch the tracker up so future signals are based on
                            # the current continuous Roostoo history, without inventing
                            # an execution for a missed candle.
                            for candle in new_candles:
                                trackers[pair].update(float(candle["close"]))
                            ps["last_completed_candle"] = latest_ts
                            ps["pending_signal"] = 0
                            ps["pending_signal_candle"] = latest_ts
                            save_state(state)
                            continue

                        # Normal case: this newly completed candle is the signal candle.
                        signal_value, fast, slow = trackers[pair].update(float(latest["close"]))
                        ps["pending_signal"] = signal_value
                        ps["pending_signal_candle"] = latest_ts
                        ps["last_completed_candle"] = latest_ts
                        save_state(state)
                        log.info(
                            "SIGNAL_CANDLE | pair=%s | candle=%s | signal=%s | fast=%.8f | slow=%.8f",
                            pair, latest_ts, signal_value, fast, slow
                        )

                        # The first ticker in the new hour is the next-candle
                        # execution observation. We never backfill a missed window.
                        execution_hour = (ticker_ms // 3_600_000) * 3_600_000
                        if execution_hour <= latest_ts:
                            log.info("WAITING_FOR_NEXT_CANDLE_OPEN | pair=%s", pair)
                            continue

                        if ps.get("last_processed_execution_hour") == execution_hour:
                            continue

                        action, reason = crossover_decision(
                            signal_value, position, entry, price,
                            TAKE_PROFIT_PCT, STOP_LOSS_PCT
                        )
                        log.warning(
                            "SIGNAL_EXECUTION | pair=%s | signal_candle=%s | execution_hour=%s | "
                            "price=%.12g | action=%s | reason=%s | fast=%.8f | slow=%.8f",
                            pair, latest_ts, execution_hour, price, action, reason,
                            fast if fast is not None else float("nan"),
                            slow if slow is not None else float("nan"),
                        )
                        if action != "HOLD":
                            execute(api, state, pair, action, price, execution_hour)
                        ps["last_processed_execution_hour"] = execution_hour
                        save_state(state)

                elapsed = time.monotonic() - loop_started
                log.info("LOOP_COMPLETE | elapsed_ms=%d", int(elapsed * 1000))

            except OrderOutcomeUnknown as exc:
                halt(state, "ORDER_OUTCOME_UNKNOWN")
                save_state(state)
                log.critical("TRADING_HALTED | %s", exc)
                break
            except (APIUnavailable, APIError, RuntimeError, ValueError, KeyError) as exc:
                log.error("RUNTIME_ERROR | %s", exc, exc_info=True)
                # No action is invented after a failed observation.
                time.sleep(min(max(POLL_SECONDS, 30), 60))
            except Exception as exc:
                halt(state, "UNEXPECTED_RUNTIME_ERROR")
                save_state(state)
                log.critical("TRADING_HALTED | UNEXPECTED | %s", exc, exc_info=True)
                break

            next_poll += POLL_SECONDS if "next_poll" in locals() else POLL_SECONDS
            delay = max(0.0, next_poll - time.monotonic())
            if delay:
                time.sleep(delay)
            else:
                next_poll = time.monotonic()

        log.info("BOT_STOP")
    finally:
        lock.release()

if __name__ == "__main__":
    run()
