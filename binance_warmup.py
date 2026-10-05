import math
from datetime import datetime, timezone, timedelta
from binance.client import Client
from logging_utils import get_logger
from config import EMA_SLOW, BINANCE_WARMUP_DAYS, BINANCE_TIMEOUT_SECONDS

log = get_logger("binance.warmup")

def load_hourly_closes(symbol):
    client = Client(requests_params={"timeout": BINANCE_TIMEOUT_SECONDS})
    start = (datetime.now(timezone.utc) - timedelta(days=BINANCE_WARMUP_DAYS)).strftime("%d %b, %Y %H:%M:%S UTC")
    log.info("BINANCE_WARMUP_START | symbol=%s | interval=1h | start=%s", symbol, start)
    klines = client.get_historical_klines(
        symbol,
        Client.KLINE_INTERVAL_1HOUR,
        start_str=start,
    )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    closes = []
    timestamps = []
    for row in klines:
        if len(row) < 7:
            continue
        open_ms = int(row[0])
        close_ms = int(row[6])
        close = float(row[4])
        # Only use completed Binance candles.
        if close_ms >= now_ms or not math.isfinite(close) or close <= 0:
            continue
        closes.append(close)
        timestamps.append(open_ms)
    if len(closes) < EMA_SLOW:
        raise RuntimeError(
            f"Binance warm-up for {symbol} returned only {len(closes)} completed "
            f"1h candles; need at least {EMA_SLOW}"
        )
    log.info(
        "BINANCE_WARMUP_OK | symbol=%s | completed_candles=%d | first=%s | last=%s",
        symbol, len(closes), timestamps[0], timestamps[-1]
    )
    return closes, timestamps[-1]
