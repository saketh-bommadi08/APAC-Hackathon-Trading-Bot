def crossover_decision(signal, position_qty, entry_price, execution_price,
                       tp_pct, sl_pct):
    # Risk exits are checked before crossover entries/exits.
    if position_qty > 0 and entry_price is not None:
        pnl = (execution_price - entry_price) / entry_price
        if pnl >= tp_pct / 100:
            return "SELL", "TAKE_PROFIT"
        if pnl <= -sl_pct / 100:
            return "SELL", "STOP_LOSS"

    # The strategy is long-only and does not reverse into a short position.
    if signal == 1 and position_qty <= 0:
        return "BUY", "GOLDEN_CROSS"
    if signal == -1 and position_qty > 0:
        return "SELL", "DEATH_CROSS"
    return "HOLD", "NONE"
