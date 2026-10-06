import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def load_env_file():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)

load_env_file()

DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data")
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", "logs")
STATE_FILE = DATA_DIR / "bot_state.json"
LOCK_FILE = DATA_DIR / "bot.lock"

ROOSTOO_BASE_URL = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com").rstrip("/")
API_KEY = os.getenv("ROOSTOO_API_KEY", "")
SECRET_KEY = os.getenv("ROOSTOO_SECRET_KEY", "")

# These are the six assets selected by the research stage.
# Binance symbols are used only for historical EMA warm-up.
PAIRS = tuple(
    x.strip() for x in os.getenv(
        "ROOSTOO_PAIRS", "TRX/USD,AVAX/USD,DOT/USD,BNB/USD,SUI/USD,BTC/USD"
    ).split(",") if x.strip()
)
BINANCE_SYMBOLS = {
    "TRX/USD": "TRXUSDT",
    "AVAX/USD": "AVAXUSDT",
    "DOT/USD": "DOTUSDT",
    "BNB/USD": "BNBUSDT",
    "SUI/USD": "SUIUSDT",
    "BTC/USD": "BTCUSDT",
}

EMA_FAST = 35
EMA_SLOW = 60
TAKE_PROFIT_PCT = 8.0
STOP_LOSS_PCT = 2.0
CAPITAL_ALLOCATION_PCT = 10.0

POLL_SECONDS = float(os.getenv("POLL_SECONDS", "60"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
READ_RETRIES = int(os.getenv("READ_RETRIES", "2"))
RETRY_BASE_SECONDS = float(os.getenv("RETRY_BASE_SECONDS", "1"))
MAX_RETRY_DELAY_SECONDS = float(os.getenv("MAX_RETRY_DELAY_SECONDS", "8"))
SERVER_TIME_REFRESH_SECONDS = float(os.getenv("SERVER_TIME_REFRESH_SECONDS", "300"))
MAX_CLOCK_OFFSET_MS = int(os.getenv("MAX_CLOCK_OFFSET_MS", "30000"))
MAX_TICKER_AGE_SECONDS = float(os.getenv("MAX_TICKER_AGE_SECONDS", "90"))
MAX_CANDLE_GAP_SECONDS = int(os.getenv("MAX_CANDLE_GAP_SECONDS", "4500"))

# Historical 1-hour Binance data used only to initialize the EMA tracker.
BINANCE_WARMUP_DAYS = int(os.getenv("BINANCE_WARMUP_DAYS", "7"))
BINANCE_TIMEOUT_SECONDS = float(os.getenv("BINANCE_TIMEOUT_SECONDS", "10"))

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
ALLOW_LIVE_TRADING = os.getenv("ALLOW_LIVE_TRADING", "false").lower() in {"1", "true", "yes", "on"}

if not PAIRS:
    raise ValueError("ROOSTOO_PAIRS cannot be empty")
if len(set(PAIRS)) != len(PAIRS):
    raise ValueError("ROOSTOO_PAIRS contains duplicates")
if any(p not in BINANCE_SYMBOLS for p in PAIRS):
    raise ValueError("Every Roostoo pair needs an explicit Binance warm-up mapping")
if EMA_FAST >= EMA_SLOW or EMA_FAST <= 0:
    raise ValueError("EMA periods must satisfy 0 < fast < slow")
if TAKE_PROFIT_PCT <= 0 or STOP_LOSS_PCT <= 0:
    raise ValueError("TP/SL must be positive")
if not (0 < CAPITAL_ALLOCATION_PCT <= 100):
    raise ValueError("CAPITAL_ALLOCATION_PCT must be in (0, 100]")
if POLL_SECONDS < 30:
    raise ValueError("POLL_SECONDS must be at least 30 seconds")
if REQUEST_TIMEOUT_SECONDS <= 0 or BINANCE_TIMEOUT_SECONDS <= 0:
    raise ValueError("timeouts must be positive")
if READ_RETRIES < 0:
    raise ValueError("READ_RETRIES must be non-negative")
if MAX_CANDLE_GAP_SECONDS < 3600:
    raise ValueError("MAX_CANDLE_GAP_SECONDS must be at least one hour")
if BINANCE_WARMUP_DAYS < 3:
    raise ValueError("BINANCE_WARMUP_DAYS must be at least 3")
if not DRY_RUN and not ALLOW_LIVE_TRADING:
    raise RuntimeError("Live trading is disabled. Set ALLOW_LIVE_TRADING=true explicitly.")
