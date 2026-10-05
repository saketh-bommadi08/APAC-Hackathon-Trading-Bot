import math
from decimal import Decimal, ROUND_DOWN

def floor_precision(value, precision):
    if int(precision) < 0:
        raise ValueError("negative precision")
    quantum = Decimal("1").scaleb(-int(precision))
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN))

def buy_quantity(free_usd, price, allocation_pct, amount_precision, mini_order):
    values = (free_usd, price, allocation_pct, mini_order)
    if any(not math.isfinite(float(x)) for x in values):
        raise ValueError("non-finite risk input")
    if free_usd < 0 or price <= 0 or not (0 < allocation_pct <= 100) or mini_order <= 0:
        raise ValueError("invalid risk input")
    gross = float(free_usd) * allocation_pct / 100.0
    quantity = floor_precision(gross / price, amount_precision)
    if quantity <= 0 or quantity * price < mini_order:
        return 0.0
    return quantity

def sell_quantity(free_base, amount_precision):
    value = float(free_base)
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid sell balance")
    return floor_precision(value, amount_precision)
