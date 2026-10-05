import math
from dataclasses import dataclass
from typing import Optional

@dataclass
class EMAState:
    fast: Optional[float] = None
    slow: Optional[float] = None
    prev_fast: Optional[float] = None
    prev_slow: Optional[float] = None

class EMATracker:
    """Exact crossover tracker: signal only when a strict cross occurs."""

    def __init__(self, fast_span, slow_span):
        if not (0 < fast_span < slow_span):
            raise ValueError("Invalid EMA spans")
        self.fast_span = int(fast_span)
        self.slow_span = int(slow_span)
        self.fast_alpha = 2.0 / (self.fast_span + 1.0)
        self.slow_alpha = 2.0 / (self.slow_span + 1.0)
        self.state = EMAState()

    def seed(self, closes):
        values = [float(x) for x in closes]
        if len(values) < self.slow_span:
            raise ValueError("Not enough closes to seed EMA")
        if any(not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("Invalid EMA seed price")
        self.state = EMAState()
        for price in values:
            self._update_no_signal(price)
        # A warm-up seed is not itself a trading signal.
        self.state.prev_fast = self.state.fast
        self.state.prev_slow = self.state.slow
        return self.state.fast, self.state.slow

    def _update_no_signal(self, price):
        if self.state.fast is None:
            self.state.fast = price
            self.state.slow = price
            return
        self.state.fast = price * self.fast_alpha + self.state.fast * (1 - self.fast_alpha)
        self.state.slow = price * self.slow_alpha + self.state.slow * (1 - self.slow_alpha)

    def update(self, price):
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid EMA price")
        self.state.prev_fast = self.state.fast
        self.state.prev_slow = self.state.slow
        self._update_no_signal(price)
        if self.state.prev_fast is None:
            return 0, self.state.fast, self.state.slow
        if self.state.prev_fast <= self.state.prev_slow and self.state.fast > self.state.slow:
            return 1, self.state.fast, self.state.slow
        if self.state.prev_fast >= self.state.prev_slow and self.state.fast < self.state.slow:
            return -1, self.state.fast, self.state.slow
        return 0, self.state.fast, self.state.slow
