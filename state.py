import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from config import STATE_FILE

def default_position():
    return {"qty": 0.0, "entry_price": None}

def default_pair_state():
    return {
        "position": default_position(),
        "pending_signal": 0,
        "pending_signal_candle": None,
        "last_processed_execution_hour": None,
        "last_completed_candle": None,
        "last_ticker_server_time_ms": None,
        "last_action_key": None,
        "last_order": {},
    }

def default_state():
    return {
        "schema_version": 3,
        "halted": False,
        "halt_reason": None,
        "pairs": {},
        "server_offset_ms": 0,
        "updated_at": None,
    }

def load_state(path=None):
    path = Path(STATE_FILE if path is None else path)
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root must be an object")
        base = default_state()
        base.update(data)
        return base
    except Exception as exc:
        raise RuntimeError(f"Unreadable state file: {exc}") from exc

def ensure_pair(state, pair):
    state.setdefault("pairs", {})
    state["pairs"].setdefault(pair, default_pair_state())
    return state["pairs"][pair]

def save_state(state, path=None):
    path = Path(STATE_FILE if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(state)
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    fd, tmp = tempfile.mkstemp(prefix="._state_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def halt(state, reason):
    state["halted"] = True
    state["halt_reason"] = reason
