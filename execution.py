import math
import time
from logging_utils import get_logger
from config import PAIRS, CAPITAL_ALLOCATION_PCT, DRY_RUN
from risk import buy_quantity, sell_quantity
from state import save_state, halt
from roostoo_client import OrderOutcomeUnknown

log = get_logger("execution")

def wallet(api, coin):
    data = api.balance()
    wallet_data = data.get("Wallet", {})
    item = wallet_data.get(coin, {})
    free = float(item.get("Free", 0) or 0)
    locked = float(item.get("Lock", 0) or 0)
    if not all(math.isfinite(x) and x >= 0 for x in (free, locked)):
        raise RuntimeError(f"invalid wallet values | coin={coin}")
    return free, locked

def pair_rules(api, pair):
    info = api.exchange_info().get("TradePairs", {}).get(pair)
    if not isinstance(info, dict):
        raise RuntimeError(f"missing exchange rules | pair={pair}")
    if not bool(info.get("CanTrade")):
        raise RuntimeError(f"pair is not tradable | pair={pair}")
    try:
        amount_precision = int(info["AmountPrecision"])
        price_precision = int(info["PricePrecision"])
        mini_order = float(info["MiniOrder"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"malformed exchange rules | pair={pair}") from exc
    if amount_precision < 0 or price_precision < 0 or not math.isfinite(mini_order) or mini_order <= 0:
        raise RuntimeError(f"invalid exchange rules | pair={pair}")
    return amount_precision, price_precision, mini_order

def _matches(api, pair, side, qty, created_ms):
    data = api.query_order(pair=pair, pending_only=False)
    matches = []
    for raw in data.get("OrderMatched", []) or []:
        try:
            if str(raw.get("Side", "")).upper() != side:
                continue
            raw_qty = float(raw.get("Quantity", 0) or 0)
            if abs(raw_qty - qty) > max(1e-6, qty * 1e-6):
                continue
            raw_created = int(raw.get("CreateTimestamp", 0) or 0)
            if created_ms and raw_created and abs(raw_created - created_ms) > 120_000:
                continue
            matches.append(raw)
        except (TypeError, ValueError):
            continue
    return matches

def execute(api, state, pair, side, price, execution_hour):
    pair_state = state["pairs"][pair]
    if DRY_RUN:
        log.warning("DRY_RUN_ORDER | pair=%s | side=%s | price=%.8f", pair, side, price)
        return False

    amount_precision, _, mini = pair_rules(api, pair)
    base, quote = pair.split("/")
    if quote != "USD":
        raise RuntimeError(f"Final bot expects USD quote assets | pair={pair}")

    if side == "BUY":
        free_usd, _ = wallet(api, "USD")
        qty = buy_quantity(
            free_usd, price, CAPITAL_ALLOCATION_PCT,
            amount_precision, mini
        )
        if qty <= 0:
            raise RuntimeError(f"BUY below MiniOrder after precision | pair={pair}")
    elif side == "SELL":
        free_base, _ = wallet(api, base)
        qty = sell_quantity(free_base, amount_precision)
        if qty <= 0 or qty * price < mini:
            raise RuntimeError(f"SELL below MiniOrder | pair={pair}")
    else:
        raise ValueError("invalid order side")

    action_key = f"{pair}|{execution_hour}|{side}"
    if pair_state.get("last_action_key") == action_key:
        raise RuntimeError(f"duplicate action key | {action_key}")

    created_ms = int(time.time() * 1000)
    pair_state["last_action_key"] = action_key
    pair_state["last_order"] = {
        "status": "SUBMITTING", "side": side, "qty": qty,
        "created_ms": created_ms, "order_id": None
    }
    save_state(state)

    log.warning(
        "ORDER_SUBMIT | pair=%s | side=%s | qty=%.12g | price_ref=%.12g | hour=%s",
        pair, side, qty, price, execution_hour
    )

    try:
        response = api.place_market(pair, side, qty)
    except OrderOutcomeUnknown as exc:
        log.critical(
            "ORDER_OUTCOME_UNKNOWN | pair=%s | side=%s | qty=%.12g | reconciling",
            pair, side, qty
        )
        try:
            matches = _matches(api, pair, side, qty, created_ms)
        except Exception as reconcile_exc:
            halt(state, f"ORDER_OUTCOME_UNKNOWN_RECONCILIATION_FAILED:{pair}")
            save_state(state)
            raise RuntimeError("Cannot reconcile ambiguous order") from reconcile_exc
        if len(matches) != 1:
            halt(state, f"ORDER_OUTCOME_UNKNOWN:{pair}")
            save_state(state)
            raise RuntimeError(f"Ambiguous order outcome; pair={pair}; matches={len(matches)}") from exc
        response = {"Success": True, "OrderDetail": matches[0]}

    detail = response.get("OrderDetail")
    if not isinstance(detail, dict) or not detail.get("OrderID") or not detail.get("Status"):
        halt(state, f"MALFORMED_ORDER_RESPONSE:{pair}")
        save_state(state)
        raise RuntimeError(f"malformed OrderDetail | pair={pair}")

    order_id = detail["OrderID"]
    status = str(detail["Status"]).upper()
    pair_state["last_order"].update({"order_id": order_id, "status": status})
    save_state(state)

    if status == "PENDING":
        log.warning("ORDER_PENDING | pair=%s | order_id=%s", pair, order_id)
        return True
    if status != "FILLED":
        log.warning("ORDER_NOT_FILLED | pair=%s | order_id=%s | status=%s", pair, order_id, status)
        return True

    filled = float(detail.get("FilledQuantity", 0) or 0)
    avg = float(detail.get("FilledAverPrice", 0) or 0)
    if filled <= 0 or avg <= 0 or not math.isfinite(filled) or not math.isfinite(avg):
        halt(state, f"FILLED_ORDER_MISSING_EXECUTION_DATA:{pair}")
        save_state(state)
        raise RuntimeError(f"filled order missing execution data | pair={pair}")

    if side == "BUY":
        pair_state["position"] = {"qty": filled, "entry_price": avg}
    else:
        if filled + max(1e-8, filled * 1e-6) < float(pair_state["position"]["qty"]):
            halt(state, f"PARTIAL_SELL_FILL:{pair}")
            save_state(state)
            raise RuntimeError(f"SELL filled less than recorded position | pair={pair}")
        pair_state["position"] = {"qty": 0.0, "entry_price": None}

    pair_state["last_order"]["status"] = "FILLED"
    save_state(state)
    log.warning(
        "ORDER_FILLED | pair=%s | order_id=%s | side=%s | qty=%.12g | avg=%.12g",
        pair, order_id, side, filled, avg
    )
    return True
