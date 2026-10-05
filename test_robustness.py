import math
import tempfile
from pathlib import Path

def test_signature():
    from roostoo_client import RoostooClient
    sig, total = RoostooClient._sign({"b": 2, "a": 1}, "secret")
    assert total == "a=1&b=2"
    assert len(sig) == 64

def test_ema_exact_cross_rules():
    from indicators import EMATracker
    t = EMATracker(35, 60)
    t.seed([100.0] * 60)
    for p in [90, 80, 70, 60, 50]:
        t.update(p)
    assert t.state.fast < t.state.slow

def test_risk_priority():
    from strategy import crossover_decision
    assert crossover_decision(1, 1, 100, 108, 8, 2) == ("SELL", "TAKE_PROFIT")
    assert crossover_decision(-1, 1, 100, 98, 8, 2) == ("SELL", "STOP_LOSS")

def test_candle_validation():
    from market_data import validate_candles
    ok, _ = validate_candles([
        {"timestamp": 0, "open": 10, "high": 11, "low": 9, "close": 10},
        {"timestamp": 3600000, "open": 10, "high": 12, "low": 9, "close": 11},
    ])
    assert ok
    bad, _ = validate_candles([
        {"timestamp": 0, "open": 10, "high": 9, "low": 8, "close": 9}
    ])
    assert not bad

def test_precision():
    from risk import floor_precision
    assert floor_precision(1.239, 2) == 1.23

def test_atomic_state():
    from state import load_state, save_state
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "state.json"
        state = load_state(path)
        state["pairs"]["TRX/USD"] = {
            "position": {"qty": 1.25, "entry_price": 10},
            "pending_signal": 0,
            "pending_signal_candle": None,
            "last_processed_execution_hour": None,
            "last_completed_candle": None,
            "last_ticker_server_time_ms": None,
            "last_action_key": None,
            "last_order": {},
        }
        save_state(state, path)
        assert load_state(path)["pairs"]["TRX/USD"]["position"]["qty"] == 1.25

def test_process_lock():
    from process_lock import ProcessLock, ProcessLockError
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bot.lock"
        a, b = ProcessLock(p), ProcessLock(p)
        a.acquire()
        try:
            try:
                b.acquire()
                assert False
            except ProcessLockError:
                pass
        finally:
            a.release()
        b.acquire()
        b.release()

def test_nonfinite_ema_rejected():
    from indicators import EMATracker
    t = EMATracker(35, 60)
    try:
        t.update(float("nan"))
        assert False
    except ValueError:
        pass

def main():
    tests = [
        value for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"ROBUSTNESS PASS: {len(tests)} tests")

if __name__ == "__main__":
    main()
