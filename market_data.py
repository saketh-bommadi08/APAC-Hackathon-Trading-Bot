import json
import math
import os
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
from logging_utils import get_logger
from config import DATA_DIR, MAX_TICKER_AGE_SECONDS, MAX_CANDLE_GAP_SECONDS

log = get_logger("market")

def pair_slug(pair):
    return pair.replace("/", "_").replace(":", "_")

def ticker_file(pair):
    return DATA_DIR / f"{pair_slug(pair)}_ticker.jsonl"

def candle_file(pair):
    return DATA_DIR / f"{pair_slug(pair)}_hourly.jsonl"

def validate_ticker(payload):
    if not isinstance(payload, dict):
        raise ValueError("ticker payload is not an object")
    try:
        price = float(payload["LastPrice"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ticker is missing a valid LastPrice") from exc
    if not math.isfinite(price) or price <= 0:
        raise ValueError("invalid ticker LastPrice")
    return price

def append_ticker(pair, server_ms, payload):
    price = validate_ticker(payload)
    record = {
        "server_time_ms": int(server_ms),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "last_price": price,
        "max_bid": float(payload.get("MaxBid", 0) or 0),
        "min_ask": float(payload.get("MinAsk", 0) or 0),
    }
    path = ticker_file(pair)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), allow_nan=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return record

def load_tickers(pair, limit=100000):
    path = ticker_file(pair)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            log.error("CORRUPT_TICKER_ROW | pair=%s | skipped=true", pair)
    return rows

def aggregate_hourly(rows):
    buckets = {}
    for row in sorted(rows, key=lambda x: int(x["server_time_ms"])):
        ts = int(row["server_time_ms"])
        price = float(row["last_price"])
        hour = (ts // 3_600_000) * 3_600_000
        bucket = buckets.setdefault(
            hour,
            {"timestamp": hour, "open": price, "high": price,
             "low": price, "close": price, "observations": 0},
        )
        bucket["high"] = max(bucket["high"], price)
        bucket["low"] = min(bucket["low"], price)
        bucket["close"] = price
        bucket["observations"] += 1
    return [buckets[k] for k in sorted(buckets)]

def validate_candles(candles):
    if not candles:
        return False, "no candles"
    previous = None
    for candle in candles:
        try:
            ts = int(candle["timestamp"])
            values = [float(candle[k]) for k in ("open", "high", "low", "close")]
        except (KeyError, TypeError, ValueError):
            return False, "malformed candle"
        if any(not math.isfinite(x) or x <= 0 for x in values):
            return False, "invalid OHLC"
        o, h, l, c = values
        if h < max(o, c) or l > min(o, c) or h < l:
            return False, "impossible OHLC"
        if previous is not None:
            delta = ts - previous
            if delta <= 0:
                return False, "non-monotonic timestamps"
            if delta > MAX_CANDLE_GAP_SECONDS * 1000:
                return False, f"candle gap {delta / 1000:.0f}s"
        previous = ts
    return True, "OK"

def contiguous_suffix(candles):
    if not candles:
        return []
    start = len(candles) - 1
    for i in range(len(candles) - 1, 0, -1):
        if int(candles[i]["timestamp"]) - int(candles[i - 1]["timestamp"]) > MAX_CANDLE_GAP_SECONDS * 1000:
            start = i
            break
    return candles[start:]

def persist_candles(pair, candles):
    path = candle_file(pair)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="._candles_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for candle in candles:
                f.write(json.dumps(candle, separators=(",", ":"), allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def completed_candles(candles, now_ms):
    return [
        candle for candle in candles
        if int(candle["timestamp"]) + 3_600_000 <= int(now_ms)
    ]

def refresh_pair(api, pair, previous_ticker_ms=None):
    data = api.ticker(pair)
    payload = data.get("Data", {}).get(pair)
    price = validate_ticker(payload)
    server_ms = int(data.get("ServerTime") or api.server_time())

    if previous_ticker_ms is not None and server_ms <= int(previous_ticker_ms):
        raise RuntimeError(
            f"stale/non-advancing ticker | pair={pair} | "
            f"previous={previous_ticker_ms} current={server_ms}"
        )

    expected_ms = int(time.time() * 1000) + int(getattr(api, "offset_ms", 0))
    age = abs(expected_ms - server_ms) / 1000
    if age > MAX_TICKER_AGE_SECONDS:
        raise RuntimeError(f"stale ticker | pair={pair} | age={age:.1f}s")

    row = append_ticker(pair, server_ms, payload)
    candles = aggregate_hourly(load_tickers(pair))
    ok, reason = validate_candles(candles)
    if not ok and reason.startswith("candle gap "):
        trimmed = contiguous_suffix(candles)
        log.warning(
            "CANDLE_GAP_RECOVERY | pair=%s | old=%d | retained=%d | reason=%s",
            pair, len(candles), len(trimmed), reason
        )
        candles = trimmed
    elif not ok:
        raise RuntimeError(f"candle validation failed | pair={pair} | {reason}")
    persist_candles(pair, candles)
    return row, candles
