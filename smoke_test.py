def main():
    from config import EMA_FAST, EMA_SLOW, PAIRS
    from indicators import EMATracker
    from strategy import crossover_decision
    assert EMA_FAST == 35 and EMA_SLOW == 60
    assert PAIRS == ("TRX/USD", "AVAX/USD", "DOT/USD","BNB/USD","SUI/USD","BTC/USD")
    tracker = EMATracker(EMA_FAST, EMA_SLOW)
    tracker.seed([100.0] * 60)
    signal, fast, slow = tracker.update(100.0)
    assert signal == 0
    assert fast == slow
    assert crossover_decision(1, 0, None, 100, 8, 2)[0] == "BUY"
    assert crossover_decision(-1, 1, 100, 97, 8, 2) == ("SELL", "STOP_LOSS")
    assert crossover_decision(1, 1, 100, 108, 8, 2) == ("SELL", "TAKE_PROFIT")
    print("SMOKE PASS")

if __name__ == "__main__":
    main()
